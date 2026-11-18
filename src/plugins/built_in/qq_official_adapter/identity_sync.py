"""已绑定群内的用户身份推断。推断结果与人工 ID 映射隔离，不作为授权依据。"""

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from typing import OrderedDict as OrderedDictType

import json
import math
import time
import unicodedata


OBSERVATION_TTL = 120.0
SETTLE_SECONDS = 5.0
TIMESTAMP_TOLERANCE = 2.0
EVIDENCE_TTL = 600.0
MAX_OBSERVATIONS = 2000
MAX_GROUP_OBSERVATIONS = 128
MAX_CANDIDATES = 1000
MAX_PROFILES = 5000
PROFILE_TTL = 90 * 86400
REQUIRED_MATCHES = 3
# AppID、SnowLuma 账号、群 OpenID、真实群号、用户 OpenID，防止切号或重绑群污染身份。
IdentityScope = Tuple[str, str, str, str, str]


def text_fingerprint(segments: List[Dict[str, Any]]) -> str:
    """协议 v1：与 SnowLuma 的指纹规则一致，只匹配足够长的纯文本。"""
    if any(segment.get("type") not in {"text", "at", "reply"} for segment in segments):
        return ""
    parts = [segment["data"] for segment in segments if segment.get("type") == "text"]
    if not all(isinstance(part, str) for part in parts):
        return ""
    text = "".join(parts)
    if len(text) > 2048:
        return ""
    text = " ".join(unicodedata.normalize("NFKC", text).split())
    if len(text) < 8 or len(set(text)) < 4 or text.startswith("/"):
        return ""
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Observation:
    scope: IdentityScope
    message_id: str
    fingerprint: str
    timestamp: float
    received_at: float
    processed: bool = False


@dataclass
class Candidate:
    user_id: str
    created_at: float
    fingerprints: Set[str] = field(default_factory=set)
    conflict: bool = False


@dataclass
class UserProfile:
    scope: IdentityScope
    user_id: str
    nickname: str
    cardname: str
    timestamp: float
    updated_at: float
    conflict: bool = False


class SilentIdentitySync:
    def __init__(self) -> None:
        self.observations: OrderedDictType[Tuple[IdentityScope, str], Observation] = OrderedDict()
        self.candidates: OrderedDictType[IdentityScope, Candidate] = OrderedDict()
        self.profiles: OrderedDictType[IdentityScope, UserProfile] = OrderedDict()
        self._used_remote: OrderedDictType[str, float] = OrderedDict()

    def clear_pending(self) -> None:
        self.observations.clear()
        self.candidates.clear()
        self._used_remote.clear()

    def _prune(self) -> None:
        now = time.monotonic()
        for key, row in list(self.observations.items()):
            if now - row.received_at >= OBSERVATION_TTL:
                del self.observations[key]
        for key, row in list(self.candidates.items()):
            if now - row.created_at >= EVIDENCE_TTL:
                del self.candidates[key]
        for key, received_at in list(self._used_remote.items()):
            if now - received_at >= OBSERVATION_TTL:
                del self._used_remote[key]
        wall_time = time.time()
        for key, profile in list(self.profiles.items()):
            if wall_time - profile.updated_at >= PROFILE_TTL:
                del self.profiles[key]

    def observe(self, scope: IdentityScope, message: Dict[str, Any]) -> None:
        self._prune()
        fingerprint = text_fingerprint(message["raw_message"])
        timestamp = float(message["timestamp"])
        if not math.isfinite(timestamp) or abs(time.time() - timestamp) > OBSERVATION_TTL:
            return
        key = (scope, message["message_id"])
        if key in self.observations:
            return
        self.observations[key] = Observation(scope, message["message_id"], fingerprint, timestamp, time.monotonic())
        group_keys = [key for key, row in self.observations.items() if row.scope[:4] == scope[:4]]
        for old_key in group_keys[:-MAX_GROUP_OBSERVATIONS]:
            del self.observations[old_key]
        while len(self.observations) > MAX_OBSERVATIONS:
            self.observations.popitem(last=False)

    def groups(self, namespace: Tuple[str, str]) -> List[str]:
        self._prune()
        # 只查询有待配对消息的群，静默时不拉取整群历史或无限轮询。
        return sorted({row.scope[3] for row in self.observations.values()
                       if row.scope[:2] == namespace and not row.processed})

    def _remember_profile(self, profile: UserProfile) -> bool:
        existing = self.profiles.get(profile.scope)
        changed = (existing is None
                   or (existing.user_id, existing.nickname, existing.cardname) !=
                   (profile.user_id, profile.nickname, profile.cardname)
                   or profile.updated_at - existing.updated_at >= 3600)
        # 未改名时仅每小时刷新一次持久化活跃时间，避免每条消息重写整个映射文件。
        if not changed and existing is not None:
            profile.updated_at = existing.updated_at
        self.profiles[profile.scope] = profile
        self.profiles.move_to_end(profile.scope)
        while len(self.profiles) > MAX_PROFILES:
            self.profiles.popitem(last=False)
        return changed

    def reconcile(self, namespace: Tuple[str, str], rows: List[Dict[str, Any]]) -> List[UserProfile]:
        """等待乱序消息到齐，双向唯一且三条不同内容一致后确认；冲突不自动覆盖。"""
        self._prune()
        local_buckets: Dict[Tuple[str, str], List[Observation]] = {}
        remote_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        owners = {(*scope[:4], profile.user_id): scope for scope, profile in self.profiles.items()}
        for local in self.observations.values():
            if local.scope[:2] == namespace:
                local_buckets.setdefault((local.scope[3], local.fingerprint), []).append(local)
        # API 数据只取当前 SnowLuma 账号、近期有限条目，重复消息按 key 去重。
        remote = {str(row["key"]): row for row in rows[:MAX_OBSERVATIONS]
                  if row["account_id"] == namespace[1]
                  and math.isfinite(float(row["timestamp"]))
                  and abs(time.time() - float(row["timestamp"])) <= OBSERVATION_TTL}
        for row in remote.values():
            if row["fingerprint"]:
                remote_buckets.setdefault((row["group_id"], row["fingerprint"]), []).append(row)
        changes: Dict[IdentityScope, UserProfile] = {}
        for bucket_key, locals_ in local_buckets.items():
            remotes = remote_buckets.get(bucket_key, [])
            for local in locals_:
                if local.processed or time.monotonic() - local.received_at < SETTLE_SECONDS:
                    continue
                if not local.fingerprint:
                    local.processed = True
                    continue
                matches = [row for row in remotes
                           if abs(float(row["timestamp"]) - local.timestamp) <= TIMESTAMP_TOLERANCE]
                if len(matches) != 1:
                    continue
                row = matches[0]
                peers = [item for item in locals_
                         if abs(item.timestamp - float(row["timestamp"])) <= TIMESTAMP_TOLERANCE]
                if len(peers) != 1 or row["key"] in self._used_remote:
                    continue
                local.processed = True
                self._used_remote[row["key"]] = time.monotonic()
                while len(self._used_remote) > MAX_OBSERVATIONS:
                    self._used_remote.popitem(last=False)
                scope = local.scope
                user_id = str(row["user_id"])
                if not user_id.isdecimal() or len(user_id) > 32:
                    continue
                candidate = self.candidates.get(scope)
                if candidate is None:
                    candidate = Candidate(user_id, time.monotonic())
                    self.candidates[scope] = candidate
                if candidate.user_id != user_id:
                    candidate.conflict = True
                self.candidates.move_to_end(scope)
                while len(self.candidates) > MAX_CANDIDATES:
                    self.candidates.popitem(last=False)
                existing = self.profiles.get(scope)
                if existing is not None and existing.user_id != user_id:
                    candidate.conflict = True
                if candidate.conflict:
                    if existing is not None and not existing.conflict:
                        existing.conflict = True
                        changes[scope] = existing
                    continue
                # 三条不同正文才计为三份证据，重复轮询或重复发送不累计。
                if len(candidate.fingerprints) < REQUIRED_MATCHES:
                    candidate.fingerprints.add(local.fingerprint)
                if existing is not None and existing.conflict:
                    continue
                if existing is None and len(candidate.fingerprints) < REQUIRED_MATCHES:
                    continue
                # 同一群里两个 OpenID 不得自动归并为同一个 QQ 号。
                owner_key = (*scope[:4], user_id)
                if owner_key in owners and owners[owner_key] != scope:
                    candidate.conflict = True
                    continue
                profile = UserProfile(scope, user_id, str(row["nickname"])[:128], str(row["cardname"])[:128],
                                      float(row["timestamp"]), time.time())
                if existing is not None and profile.timestamp < existing.timestamp:
                    continue
                if self._remember_profile(profile):
                    changes[scope] = profile
                owners[owner_key] = scope
        # 身份确认后，名称可以直接按真实 QQ 号刷新；短消息也能更新昵称和群名片。
        latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in remote.values():
            key = (row["group_id"], row["user_id"])
            if key not in latest or float(row["timestamp"]) > float(latest[key]["timestamp"]):
                latest[key] = row
        for scope, profile in list(self.profiles.items()):
            if scope[:2] != namespace or self.get_profile(scope) is None:
                continue
            row = latest.get((scope[3], profile.user_id))
            if row is None or float(row["timestamp"]) <= profile.timestamp:
                continue
            refreshed = UserProfile(scope, profile.user_id, str(row["nickname"])[:128], str(row["cardname"])[:128],
                                    float(row["timestamp"]), time.time())
            if self._remember_profile(refreshed):
                changes[scope] = refreshed
        return list(changes.values())

    def get_profile(self, scope: IdentityScope) -> Optional[UserProfile]:
        profile = self.profiles.get(scope)
        candidate = self.candidates.get(scope)
        if candidate is not None and candidate.conflict:
            return None
        if profile is not None and not profile.conflict and time.time() - profile.updated_at < PROFILE_TTL:
            return profile
        return None

    def dump(self) -> List[Dict[str, Any]]:
        self._prune()
        return [asdict(profile) for profile in self.profiles.values()]

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("自动身份映射文件超过大小限制")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("自动身份映射文件必须是列表")
        loaded: OrderedDictType[IdentityScope, UserProfile] = OrderedDict()
        for row in payload[-MAX_PROFILES:]:
            scope = tuple(row["scope"])
            if len(scope) != 5 or not all(isinstance(value, str) and 0 < len(value) <= 128 for value in scope):
                raise ValueError("自动身份映射的作用域无效")
            profile = UserProfile(scope, str(row["user_id"]), str(row["nickname"])[:128],
                                  str(row["cardname"])[:128], float(row["timestamp"]), float(row["updated_at"]),
                                  row.get("conflict", False) is True)
            if (not profile.user_id.isdecimal() or len(profile.user_id) > 32
                    or not math.isfinite(profile.updated_at) or not math.isfinite(profile.timestamp)):
                raise ValueError("自动身份映射的 QQ 号或时间无效")
            loaded[scope] = profile
        self.profiles = loaded
        self._prune()


def save_profiles(path: Path, payload: List[Dict[str, Any]]) -> None:
    """由后台任务在线程中原子写入，避免占住事件循环或留下半份 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)

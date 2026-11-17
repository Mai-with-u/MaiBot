"""官方确认的 Union 身份、场景 OpenID 和历史 QQ 号；与消息指纹推断分开保存。"""

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import json
import os
import time


UNION_IDENTITIES_FILE = "union_user_ids.json"


@dataclass
class UnionBinding:
    app_id: str
    group_openid: str
    openid: str
    union_openid: str
    qq_id: str = ""
    qq_source: str = ""
    observed_at: float = 0.0


class UnionIdentities:
    def __init__(self, bindings: Optional[List[UnionBinding]] = None) -> None:
        self.bindings: Dict[Tuple[str, str, str], UnionBinding] = {}
        for binding in bindings or []:
            if not binding.app_id or not binding.openid or not binding.union_openid:
                raise ValueError("Union 身份缺少应用、OpenID 或 union_openid")
            if binding.qq_id and (not binding.qq_id.isascii() or not binding.qq_id.isdecimal()):
                raise ValueError("Union 身份的历史 QQ 号必须为数字")
            if binding.qq_source not in {"", "manual", "silent"}:
                raise ValueError("未知 QQ 号关联来源")
            if bool(binding.qq_id) != bool(binding.qq_source):
                raise ValueError("QQ 号与关联来源必须同时提供")
            key = (binding.app_id, binding.group_openid, binding.openid)
            if key in self.bindings:
                raise ValueError("Union 身份记录重复")
            self.bindings[key] = binding
        self._user_maps: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._reverse_maps: Dict[Tuple[str, str], Dict[str, str]] = {}
        for row in sorted(self.bindings.values(), key=lambda row: row.observed_at):
            scope = (row.app_id, row.group_openid)
            self._user_maps.setdefault(scope, {})[row.openid] = row.union_openid
            self._reverse_maps.setdefault(scope, {})[row.union_openid] = row.openid

    @classmethod
    def load(cls, path: Path) -> "UnionIdentities":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("bindings"), list):
            raise ValueError("不支持的 Union 身份文件格式")
        return cls([UnionBinding(**{key: value for key, value in row.items() if key != "converted"})
                    for row in payload["bindings"]])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"version": 1, "bindings": [asdict(row) for row in self.bindings.values()]}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def updated(self, app_id: str, group_openid: str, openid: str, union_openid: str,
                *, qq_id: Optional[str] = None, qq_source: str = "") -> "UnionIdentities":
        """新快照先持久化再发布；同一场景 OpenID 改指其它 Union 时拒绝覆盖。"""
        existing = self.bindings.get((app_id, group_openid, openid))
        if existing is not None and existing.union_openid != union_openid:
            raise ValueError(f"官方 OpenID 的 Union 身份发生冲突: {openid}")
        if existing is not None and (qq_id is None or (existing.qq_id, existing.qq_source) == (qq_id, qq_source)):
            return self
        binding = UnionBinding(app_id, group_openid, openid, union_openid, qq_id or "", qq_source if qq_id else "",
                               existing.observed_at if existing is not None else time.time())
        if existing is not None and qq_id is None:
            binding = replace(binding, qq_id=existing.qq_id, qq_source=existing.qq_source)
        entries = dict(self.bindings)
        entries[(app_id, group_openid, openid)] = binding
        return UnionIdentities(list(entries.values()))

    def user_map(self, app_id: str, group_openid: str = "") -> Dict[str, str]:
        return dict(self._user_maps.get((app_id, group_openid), {}))

    def reverse_map(self, app_id: str, group_openid: str = "") -> Dict[str, str]:
        # 同一身份存在历史 OpenID 时，使用最近确认的场景路由。
        return dict(self._reverse_maps.get((app_id, group_openid), {}))

    def legacy_qq_ids(self, app_id: str, union_openid: str) -> List[str]:
        return sorted({row.qq_id for row in self.bindings.values()
                       if row.app_id == app_id and row.union_openid == union_openid and row.qq_id})

    def relations(self, app_id: str = "") -> List[Dict[str, Any]]:
        """同一个旧 ID 指向多个 Union 时只报告冲突，不猜测合并方向。"""
        targets: Dict[str, Set[str]] = {}
        qq_owners: Dict[str, Set[str]] = {}
        for binding in self.bindings.values():
            if binding.qq_id:
                qq_owners.setdefault(binding.union_openid, set()).add(binding.qq_id)
        rows: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        for row in self.bindings.values():
            for source, evidence in ((row.openid, "official_openid"), (row.qq_id, row.qq_source)):
                if not source or source == row.union_openid:
                    continue
                targets.setdefault(source, set()).add(row.union_openid)
                if not app_id or row.app_id == app_id:
                    rows.setdefault((source, row.union_openid), []).append({
                        "app_id": row.app_id, "group_openid": row.group_openid, "evidence": evidence,
                    })
        return [{"source": source, "target": target, "evidence": evidence,
                 "conflict": len(targets[source]) != 1 or (
                     any(item["evidence"] in {"manual", "silent"} for item in evidence) and len(qq_owners[target]) > 1
                 ), "targets": sorted(targets[source]),
                 "reason": ("同一个旧 ID 对应多个 Union" if len(targets[source]) > 1 else
                            "同一个 Union 对应不同 QQ 号" if any(item["evidence"] in {"manual", "silent"} for item in evidence)
                            and len(qq_owners[target]) > 1 else "")}
                for (source, target), evidence in sorted(rows.items())]

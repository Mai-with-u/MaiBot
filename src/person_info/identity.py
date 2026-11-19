"""已确认的人物身份别名；与在线迁移事务一同提交，运行时读取最新版本。"""

from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import hashlib
import json
import sqlite3

from src.common.paths import get_instance_root

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage


# QQ 合并转发消息中无法获取真实 QQ 号时，协议端统一填入的占位号。
# 若放任该占位号参与身份归并，所有出现在合并转发中的不同群友会被
# 错误地合并为同一个 person_id，导致人物画像膨胀并拖垮画像刷新。
QQ_MERGED_FORWARD_PLACEHOLDER_USER_ID = "1094950020"


def is_placeholder_user_id(user_id: str) -> bool:
    """判断 user_id 是否为协议端占位号（不可用于身份归并）。"""
    return user_id == QQ_MERGED_FORWARD_PLACEHOLDER_USER_ID


def person_id_for(platform: str, user_id: str) -> str:
    """与历史人物 ID 算法保持一致。"""
    normalized_platform = platform.split("-")[1] if "-" in platform else platform
    return hashlib.md5(f"{normalized_platform}_{user_id}".encode()).hexdigest()


def load_aliases(path: Path) -> Dict[str, Any]:
    database = path.parent / "MaiBot.db"
    if database.exists():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='person_identity_state'").fetchone():
                row = conn.execute("SELECT payload FROM person_identity_state WHERE id=1").fetchone()
                if row is not None:
                    return _decode_aliases(row[0])
    raw = path.read_text(encoding="utf-8") if path.exists() else '{"version":1,"aliases":{},"private_routes":[]}'
    return _decode_aliases(raw)


@lru_cache(maxsize=16)
def _decode_aliases(raw: str) -> Dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("aliases"), dict):
        raise ValueError("人物身份别名文件必须为平台到用户映射的对象")
    for platform, mappings in value["aliases"].items():
        if not isinstance(platform, str) or not isinstance(mappings, dict):
            raise ValueError("人物身份别名文件结构错误")
        for source, target in mappings.items():
            if not isinstance(source, str) or not isinstance(target, str) or not source or not target:
                raise ValueError("人物身份别名必须包含非空字符串 ID")
            resolve_alias(mappings, source)
    routes = value.get("private_routes")
    if not isinstance(routes, list):
        raise ValueError("人物私聊路由必须为数组")
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or any(not isinstance(route.get(key), str) or not route[key]
                                              for key in ("platform", "user_id", "transport_user_id")):
            raise ValueError("人物私聊路由缺少平台或用户 ID")
        if any(route.get(key) is not None and not isinstance(route[key], str) for key in ("account_id", "scope")):
            raise ValueError("人物私聊路由的账号及作用域必须为字符串或空值")
        key = (route["platform"], route["user_id"], route.get("account_id") or "", route.get("scope") or "")
        if key in seen:
            raise ValueError("人物私聊路由重复")
        seen.add(key)
    transports = value.get("user_transports", {})
    if not isinstance(transports, dict):
        raise ValueError("人物默认发送目标必须为平台映射")
    for platform, users in transports.items():
        if not isinstance(platform, str) or not isinstance(users, dict) or any(
            not isinstance(user, str) or not user or not isinstance(physical, str) or not physical
            for user, physical in users.items()
        ):
            raise ValueError("人物默认发送目标格式错误")
    return value


def resolve_alias(mappings: Dict[str, str], user_id: str) -> str:
    visited = set()
    while user_id in mappings:
        if user_id in visited:
            raise ValueError("人物身份别名存在循环")
        visited.add(user_id)
        user_id = mappings[user_id]
    return user_id


def resolve_user_id(platform: str, user_id: str) -> str:
    mappings = load_aliases(get_instance_root() / "data" / "person_identity_aliases.json")["aliases"]
    return resolve_alias(mappings.get(platform, {}), user_id)


def resolve_person_id(person_id: str) -> str:
    mappings = load_aliases(get_instance_root() / "data" / "person_identity_aliases.json")["aliases"]
    for platform, users in mappings.items():
        for source in users:
            if person_id_for(platform, source) == person_id:
                return person_id_for(platform, resolve_alias(users, source))
    return person_id


def resolve_private_transport_user(platform: str, user_id: str, account_id: Optional[str], scope: Optional[str]) -> str:
    state = load_aliases(get_instance_root() / "data" / "person_identity_aliases.json")
    for route in state["private_routes"]:
        if (route["platform"], route["user_id"], route["account_id"] or "", route["scope"] or "") == (
            platform, user_id, account_id or "", scope or "",
        ):
            return route["transport_user_id"]
    return state.get("user_transports", {}).get(platform, {}).get(user_id, user_id)


def normalize_message_identity(message: "SessionMessage") -> None:
    """在会话注册前归一发送者和结构化引用，保留实际平台接收者。由线程池调用。"""
    from src.common.data_models.message_component_data_model import AtComponent, ForwardNodeComponent, ReplyComponent

    user = message.message_info.user_info
    original = user.user_id
    user.user_id = resolve_user_id(message.platform, original)
    if user.user_id != original:
        config = message.message_info.additional_config
        config["identity_original_user_id"] = original
        config["identity_canonical_user_id"] = user.user_id

    def rewrite(components: List[Any]) -> None:
        for component in components:
            if isinstance(component, AtComponent):
                component.target_user_id = resolve_user_id(message.platform, component.target_user_id)
            elif isinstance(component, ReplyComponent) and component.target_message_sender_id:
                component.target_message_sender_id = resolve_user_id(message.platform, component.target_message_sender_id)
            elif isinstance(component, ForwardNodeComponent):
                for node in component.forward_components:
                    if node.user_id:
                        node.user_id = resolve_user_id(message.platform, node.user_id)
                    rewrite(node.content)
    rewrite(message.raw_message.components)

"""模型可见的消息 ID 短别名。

部分平台的消息 ID 很长（如 QQ 官方机器人 ``ROBOT1.0_...`` 约 100 字符），整段放进上下文会让
输入 token 成倍增加。这里把长 ID 显示为 ``m`` + 6 位小写 base32 哈希，模型调用工具时再按当前
聊天历史换回原始 ID；数据库、日志与平台收发始终使用原始 ID。
"""

from typing import Any, Iterable, Mapping

import base64
import hashlib

# 不超过该长度的 ID（如 NapCat 的数字 ID）原样显示，也保证别名本身不会被再次缩短。
MAX_PLAIN_MESSAGE_ID_LENGTH = 12
ALIAS_PREFIX = "m"
ALIAS_HASH_LENGTH = 6


def to_display_message_id(message_id: Any) -> str:
    """返回展示给模型的消息 ID：长 ID 缩短为稳定的哈希别名，短 ID 原样返回。"""

    normalized_message_id = str(message_id or "").strip()
    if len(normalized_message_id) <= MAX_PLAIN_MESSAGE_ID_LENGTH:
        return normalized_message_id
    digest = hashlib.sha1(normalized_message_id.encode("utf-8")).digest()
    return ALIAS_PREFIX + base64.b32encode(digest).decode("ascii").lower()[:ALIAS_HASH_LENGTH]


def build_alias_map(message_ids: Iterable[Any]) -> dict[str, str]:
    """按给定顺序建立 别名 -> 原始 ID 映射；别名冲突时先出现的优先（调用方应按新到旧传入）。"""

    alias_map: dict[str, str] = {}
    for raw_message_id in message_ids:
        message_id = str(raw_message_id or "").strip()
        alias = to_display_message_id(message_id)
        if alias != message_id:
            alias_map.setdefault(alias, message_id)
    return alias_map


def expand_message_id_aliases(value: Any, alias_map: Mapping[str, str]) -> Any:
    """递归地把工具参数里等于别名的字符串换回原始消息 ID。"""

    if not alias_map:
        return value
    if isinstance(value, str):
        return alias_map.get(value.strip(), value)
    if isinstance(value, dict):
        return {key: expand_message_id_aliases(item, alias_map) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_message_id_aliases(item, alias_map) for item in value]
    return value

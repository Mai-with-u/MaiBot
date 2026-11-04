from typing import Any, Dict, List, Tuple

import base64
import hashlib
import json
import time

import msgpack

PLATFORM = "weixin_clawbot"


def message_id(account: str, msg: Dict[str, Any]) -> str:
    identity = str(
        msg.get("message_id")
        or msg.get("msg_id")
        or hashlib.sha256(json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    )
    return f"{account}:{msg['from_user_id']}:{identity}"


def inbound(account: str, msg: Dict[str, Any], segments: List[Dict[str, Any]], nickname: str = "") -> Dict[str, Any]:
    stamp = float(msg.get("create_time_ms") or msg.get("create_time") or time.time())
    if stamp > 1_000_000_000_000:
        stamp /= 1000
    result = {
        "platform": PLATFORM,
        "account_id": account,
        "message_id": message_id(account, msg),
        "timestamp": str(stamp),
        "message_info": {
            "user_info": {"user_id": str(msg["from_user_id"]), "user_nickname": nickname or str(msg["from_user_id"])},
            "additional_config": {},
        },
        "raw_message": segments,
        "is_picture": any(s["type"] == "image" for s in segments),
    }
    if len(msgpack.packb(result, use_bin_type=True)) > 15 * 1024**2:
        raise ValueError("消息超过麦麦 IPC 大小限制")
    return result


def outbound(message: Dict[str, Any], max_bytes: int) -> Tuple[str, List[Tuple[str, Any]]]:
    info = message["message_info"]
    extra = info.get("additional_config") or {}
    if info.get("group_info") or extra.get("platform_io_target_group_id"):
        raise ValueError("ClawBot 适配器不支持群聊")
    target = extra.get("platform_io_target_user_id")
    if not target:
        raise ValueError("缺少出站目标用户 ID")
    parts = []
    for segment in message["raw_message"]:
        kind = segment["type"]
        if kind == "reply":
            continue  # Send as a normal reply; native quote output is not implemented.
        if kind == "text":
            text = segment.get("data")
            if not isinstance(text, str):
                raise ValueError("文本消息段格式无效")
            if text:
                parts.append(("text", text))
        elif kind in {"image", "emoji"}:
            value = segment.get("binary_data_base64", "")
            if not isinstance(value, str) or len(value) > (max_bytes + 2) // 3 * 4:
                raise ValueError("图片超过大小限制")
            raw = base64.b64decode(value, validate=True)
            if not raw or len(raw) > max_bytes:
                raise ValueError("图片为空或超过大小限制")
            parts.append(("image", raw))
        else:
            raise ValueError(f"暂不支持发送 {kind} 消息")
    if not parts:
        raise ValueError("没有可发送的消息内容")
    return str(target), parts

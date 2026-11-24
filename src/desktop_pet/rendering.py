"""桌宠气泡消息转换。"""

from typing import Any, Dict, List
import base64

from src.core.internal_chat import INTERNAL_CHAT_USER_ID


def render_history_events(events: List[Dict[str, Any]], *, show_senders: bool = False) -> List[Dict[str, Any]]:
    """只适配聊天流浏览的消息事件展示，查询和分页由共享事件账本负责。"""
    from src.webui.routers.system import _resolve_monitor_media_file

    items = {}
    for event in events:
        kind = event["event"]
        if kind not in {"message.ingested", "message.sent", "message.updated"}:
            continue
        data = event["data"]
        parts = [{"kind": "text", "text": data["content"]}]
        for media in data.get("media", []):
            if media["kind"] in {"image", "emoji"}:
                path = _resolve_monitor_media_file(media["kind"], media["hash"])
                parts.append({"kind": "image", "src": image_src({
                    "binary_data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                })})
        items[data["message_id"]] = {"id": data["message_id"],
                                     "who": "mai" if kind == "message.sent" else "me", "parts": parts}
        if show_senders and kind != "message.sent" and data.get("user_id") != INTERNAL_CHAT_USER_ID:
            items[data["message_id"]].update(who="peer", sender=data.get("speaker_name", ""))
    return list(items.values())


def image_src(segment: Dict[str, Any]) -> str:
    """把图片/表情消息段转成页面可直接显示的 data URI。"""
    encoded = segment["binary_data_base64"]
    head = base64.b64decode(encoded[:24])
    if head.startswith(b"\x89PNG"):
        mime = "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif head.startswith(b"GIF8"):
        mime = "image/gif"
    elif head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("无法识别的图片格式")
    return f"data:{mime};base64,{encoded}"


def render_parts(segments: List[Dict[str, Any]], skipped: List[str]) -> List[Dict[str, str]]:
    """把消息段转成桌宠气泡能显示的文本与图片，气泡放不下的类型（语音、文件等）记入 skipped。"""
    parts: List[Dict[str, str]] = []
    for segment in segments:
        kind = segment["type"]
        if kind == "text":
            parts.append({"kind": "text", "text": segment["data"]})
        elif kind == "at":
            data = segment["data"]
            label = data.get("target_user_cardname") or data.get("target_user_nickname") or data["target_user_id"]
            parts.append({"kind": "text", "text": f"@{label} "})
        elif kind in {"image", "emoji"}:
            parts.append({"kind": "image", "src": image_src(segment)})
        elif kind == "reply":
            continue  # 桌宠只有一个对话对象，引用关系不需要展示。
        elif kind == "forward":
            for node in segment["data"]:
                parts.extend(render_parts(node["content"], skipped))
        else:
            skipped.append(kind)
    return parts



"""Telegram 与宿主标准消息段的转换；不依赖主程序内部模块。"""

from typing import Any, Dict, List, Mapping, Optional, Tuple

import base64
import binascii
import hashlib
import re

PLATFORM = "telegram"
MESSAGE_ID = re.compile(r"^(-?\d+):(\d+)$")


def external_id(chat_id: Any, message_id: Any) -> str:
    return f"{int(chat_id)}:{int(message_id)}"


def parse_message_id(value: str) -> Tuple[int, int]:
    match = MESSAGE_ID.fullmatch(value)
    if match is None:
        raise ValueError("Telegram 消息 ID 必须为 chat_id:message_id")
    return int(match[1]), int(match[2])


def display_name(user: Mapping[str, Any]) -> str:
    if "title" in user:
        return str(user["title"])
    return " ".join(str(user[key]) for key in ("first_name", "last_name") if user.get(key)) or str(user["id"])


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _utf16_index(text: str, offset: int) -> int:
    """Telegram entities 使用 UTF-16 索引，Python 的字符索引不能直接套用。"""
    current = 0
    for index, char in enumerate(text):
        if current == offset:
            return index
        current += utf16_length(char)
        if current > offset:
            raise ValueError("Telegram entity 位于代理对内部")
    if current == offset:
        return len(text)
    raise ValueError("Telegram entity 超出文本范围")


def decode_text(
    text: str,
    entities: List[Dict[str, Any]],
    bot_id: str,
    bot_username: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    segments: List[Dict[str, Any]] = []
    cursor = 0
    mentioned = False
    for entity in sorted(entities, key=lambda item: item["offset"]):
        kind = entity["type"]
        if kind not in {"mention", "text_mention"}:
            continue
        start = _utf16_index(text, entity["offset"])
        end = _utf16_index(text, entity["offset"] + entity["length"])
        if start < cursor:
            raise ValueError("Telegram @ entities 相互重叠")
        if start > cursor:
            segments.append({"type": "text", "data": text[cursor:start]})
        label = text[start:end]
        if kind == "text_mention":
            user = entity["user"]
            target = str(user["id"])
            name = display_name(user)
        else:
            target = bot_id if label.casefold() == f"@{bot_username}".casefold() else label
            name = label
        mentioned = mentioned or target == bot_id
        segments.append({"type": "at", "data": {"target_user_id": target, "target_user_nickname": name}})
        cursor = end
    if cursor < len(text):
        segments.append({"type": "text", "data": text[cursor:]})
    return segments, mentioned


def binary_segment(kind: str, binary: bytes, label: str = "") -> Dict[str, Any]:
    if not binary:
        raise ValueError("媒体内容为空")
    return {
        "type": kind,
        "data": label,
        "hash": hashlib.sha256(binary).hexdigest(),
        "binary_data_base64": base64.b64encode(binary).decode("ascii"),
    }


def parse_group_id(value: str) -> Tuple[int, Optional[int]]:
    """普通群沿用 chat_id，论坛话题使用 chat_id:thread_id 隔离聊天流。"""
    if ":" not in value:
        return int(value), None
    chat_id, thread_id = parse_message_id(value)
    if chat_id >= 0 or thread_id <= 0:
        raise ValueError("Telegram 话题目标必须为负群 ID 和正话题 ID")
    return chat_id, thread_id


def topic_group(payload: Mapping[str, Any], topic_name: str = "") -> Dict[str, str]:
    chat = payload["chat"]
    group_id = str(chat["id"])
    name = chat["title"]
    # General 消息不带 is_topic_message；不能把普通回复的 thread_id 当作论坛话题。
    if payload.get("is_topic_message"):
        thread_id = payload["message_thread_id"]
        parse_group_id(f"{group_id}:{thread_id}")
        group_id = f"{group_id}:{thread_id}"
        name = f"{name} / {topic_name or f'话题 {thread_id}'}"
    elif chat.get("is_forum"):
        name = f"{name} / General"
    return {"group_id": group_id, "group_name": name}


def decode_message(
    payload: Mapping[str, Any],
    bot_id: str,
    bot_username: str,
    media: List[Dict[str, Any]],
    topic_name: str = "",
) -> Optional[Dict[str, Any]]:
    chat = payload["chat"]
    if chat["type"] not in {"private", "group", "supergroup"}:
        return None
    sender = payload.get("sender_chat") or payload.get("from")
    if not isinstance(sender, Mapping):
        raise ValueError("Telegram 消息缺少发送者身份")
    if str(sender["id"]) == bot_id and not payload.get("sender_chat"):
        return None
    text = payload.get("text", payload.get("caption", ""))
    entities = payload.get("entities", payload.get("caption_entities", []))
    body, mentioned = decode_text(text, entities, bot_id, bot_username)
    body.extend(media)
    if not body:
        return None  # 加入/退出等服务消息不作为聊天正文。
    chat_id = str(chat["id"])
    message_id = external_id(chat_id, payload["message_id"])
    additional: Dict[str, Any] = {
        "telegram_chat_id": chat_id,
        "telegram_message_id": payload["message_id"],
    }
    if payload.get("is_topic_message"):
        additional["telegram_message_thread_id"] = payload["message_thread_id"]
    origin = payload.get("forward_origin")
    if isinstance(origin, Mapping):
        additional["telegram_forward_origin"] = dict(origin)
        origin_user = origin.get("sender_user") or origin.get("sender_chat") or origin.get("chat")
        nickname = display_name(origin_user) if isinstance(origin_user, Mapping) else str(origin["sender_user_name"])
        source_id = str(origin_user["id"]) if isinstance(origin_user, Mapping) else None
        # 使用机器人实际收到的副本再次转发；原频道未必允许机器人访问。
        source_message_id = message_id
        body = [
            {
                "type": "forward",
                "data": [
                    {
                        "user_id": source_id,
                        "user_nickname": nickname,
                        "message_id": source_message_id,
                        "content": body,
                    }
                ],
            }
        ]
    reply = payload.get("reply_to_message")
    reply_to: Optional[str] = None
    if isinstance(reply, Mapping):
        author = reply.get("sender_chat") or reply.get("from")
        reply_to = external_id(chat_id, reply["message_id"])
        body.insert(
            0,
            {
                "type": "reply",
                "data": {
                    "target_message_id": reply_to,
                    "target_message_content": reply.get("text", reply.get("caption", "")),
                    "target_message_sender_id": str(author["id"]) if isinstance(author, Mapping) else None,
                    "target_message_sender_nickname": display_name(author) if isinstance(author, Mapping) else None,
                },
            },
        )
        mentioned = mentioned or (isinstance(author, Mapping) and str(author["id"]) == bot_id)
    info: Dict[str, Any] = {
        "user_info": {"user_id": str(sender["id"]), "user_nickname": display_name(sender)},
        "additional_config": additional,
    }
    if chat["type"] != "private":
        info["group_info"] = topic_group(payload, topic_name)
    result: Dict[str, Any] = {
        "platform": PLATFORM,
        "account_id": bot_id,
        "scope": None,
        "message_id": message_id,
        "timestamp": str(payload["date"]),
        "message_info": info,
        "raw_message": body,
        "is_at": mentioned,
        "is_mentioned": mentioned,
        "is_picture": any(item["type"] == "image" for item in media),
        "is_emoji": any(item["type"] == "emoji" for item in media),
        "is_command": any(item["type"] == "bot_command" and item["offset"] == 0 for item in entities),
    }
    if reply_to is not None:
        result["reply_to"] = reply_to
    return result


def reaction_label(reaction: Mapping[str, Any]) -> str:
    kind = reaction["type"]
    if kind == "emoji":
        return str(reaction["emoji"])
    if kind == "custom_emoji":
        return f"自定义表情({reaction['custom_emoji_id']})"
    if kind == "paid":
        return "付费⭐"
    raise ValueError(f"不支持的 Telegram 表态类型: {kind}")


def decode_reaction(update: Mapping[str, Any], bot_id: str) -> Optional[Dict[str, Any]]:
    """表态以独立通知入站，引用原消息；update_id 避免多次表态被消息去重吞掉。"""
    event_type = "message_reaction" if "message_reaction" in update else "message_reaction_count"
    payload = update[event_type]
    chat = payload["chat"]
    if chat["type"] not in {"private", "group", "supergroup"}:
        return None
    target = external_id(chat["id"], payload["message_id"])
    if event_type == "message_reaction":
        actor = payload.get("user") or payload.get("actor_chat")
        if not isinstance(actor, Mapping):
            raise ValueError("Telegram 表态事件缺少发送者身份")
        if str(actor["id"]) == bot_id:
            return None
        old, new = payload["old_reaction"], payload["new_reaction"]
        added = [reaction_label(item) for item in new if item not in old]
        removed = [reaction_label(item) for item in old if item not in new]
        if not added and not removed:
            return None
        changes = []
        if added:
            changes.append("添加了 " + "、".join(added) + " 表态")
        if removed:
            changes.append("取消了 " + "、".join(removed) + " 表态")
        text = f"[事件-消息表态] {display_name(actor)} 对消息 {target} " + "；".join(changes)
        actor_name = display_name(actor)
    else:
        # 匿名计数事件不包含个人身份，不把群身份伪装成某个用户。
        actor = chat
        actor_name = "匿名表态统计"
        counts = [f"{reaction_label(item['type'])} × {item['total_count']}" for item in payload["reactions"]]
        text = f"[事件-消息表态] 消息 {target} 的匿名表态统计：" + ("、".join(counts) if counts else "无表态")
    info: Dict[str, Any] = {
        "user_info": {"user_id": str(actor["id"]), "user_nickname": actor_name},
        "additional_config": {
            "telegram_event_type": event_type,
            "telegram_event_payload": dict(payload),
            "telegram_chat_id": str(chat["id"]),
            "telegram_message_id": payload["message_id"],
            "telegram_reaction_target_message_id": target,
            "telegram_reaction_anonymous": event_type == "message_reaction_count" or "actor_chat" in payload,
        },
    }
    if chat["type"] != "private":
        info["group_info"] = {"group_id": str(chat["id"]), "group_name": chat["title"]}
    return {
        "platform": PLATFORM, "account_id": bot_id, "scope": None,
        "message_id": f"reaction:{chat['id']}:{update['update_id']}",
        "timestamp": str(payload["date"]), "message_info": info,
        "raw_message": [{"type": "text", "data": text}],
        "reply_to": target, "is_notify": True,
        "is_at": False, "is_mentioned": False,
    }


def outbound_target(message: Mapping[str, Any]) -> int:
    info = message["message_info"]
    group = info.get("group_info")
    config = info.get("additional_config") or {}
    # 私聊出站 user_info 是机器人自身，必须读取宿主给出的目标用户字段。
    target = group.get("group_id") if isinstance(group, Mapping) else None
    target = target or config.get("platform_io_target_group_id") or config.get("platform_io_target_user_id")
    if target is None:
        raise ValueError("出站消息缺少目标聊天 ID")
    return parse_group_id(str(target))[0]


def outbound_thread(message: Mapping[str, Any]) -> Optional[int]:
    info = message["message_info"]
    group = info.get("group_info")
    config = info.get("additional_config") or {}
    target = group.get("group_id") if isinstance(group, Mapping) else None
    target = target or config.get("platform_io_target_group_id")
    return parse_group_id(str(target))[1] if target is not None else None


def decode_binary(segment: Mapping[str, Any]) -> bytes:
    value = segment.get("binary_data_base64")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{segment.get('type')} 消息段缺少二进制内容")
    try:
        binary = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("媒体 Base64 无效") from exc
    if not binary:
        raise ValueError("媒体内容为空")
    return binary


def split_text(text: str, entities: List[Dict[str, Any]], limit: int = 4096) -> List[Dict[str, Any]]:
    """分段时保留 UTF-16 entities 完整性，不把 @ 拆成两半。"""
    chunks: List[Dict[str, Any]] = []
    offset = 0
    remaining = text
    while remaining:
        length = 0
        end = 0
        for char in remaining:
            size = utf16_length(char)
            if length + size > limit:
                break
            length += size
            end += 1
        boundary = offset + length
        for entity in entities:
            start = entity["offset"]
            if start < boundary < start + entity["length"]:
                boundary = start
        if boundary == offset:
            raise ValueError("单个 @ entity 超过 Telegram 文本上限")
        length = boundary - offset
        end = _utf16_index(remaining, length)
        local = [
            {**entity, "offset": entity["offset"] - offset}
            for entity in entities
            if offset <= entity["offset"] < boundary
        ]
        chunks.append({"method": "sendMessage", "body": {"text": remaining[:end], "entities": local}})
        remaining = remaining[end:]
        offset = boundary
    return chunks


def encode_segments(segments: List[Dict[str, Any]], depth: int = 0) -> List[Dict[str, Any]]:
    if depth > 8:
        raise ValueError("合并转发嵌套层数超过 8")
    parts: List[Dict[str, Any]] = []
    text = ""
    entities: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal text
        parts.extend(split_text(text, entities))
        text = ""
        entities.clear()

    for segment in segments:
        kind = segment["type"]
        data = segment.get("data")
        if kind == "text":
            if not isinstance(data, str):
                raise ValueError("文本段必须是字符串")
            text += data
        elif kind == "at":
            if not isinstance(data, Mapping):
                raise ValueError("@ 段必须包含目标用户信息")
            target = str(data["target_user_id"])
            label = str(data.get("target_user_cardname") or data.get("target_user_nickname") or target)
            if target.startswith("@"):
                label = target
                entity = {"type": "mention"}
            else:
                label = label if label.startswith("@") else f"@{label}"
                entity = {"type": "text_mention", "user": {"id": int(target), "is_bot": False, "first_name": label}}
            entities.append({**entity, "offset": utf16_length(text), "length": utf16_length(label)})
            text += label + " "
        elif kind == "reply":
            continue  # 回复参数由整个出站消息统一解析。
        elif kind in {"image", "emoji", "voice"}:
            flush()
            parts.append({"kind": kind, "binary": decode_binary(segment)})
        elif kind == "forward":
            flush()
            if not isinstance(data, list) or not data:
                raise ValueError("转发段必须包含节点")
            forwarded: List[Dict[str, Any]] = []
            for node in data:
                source = str(node.get("message_id") or "")
                if MESSAGE_ID.fullmatch(source):
                    chat_id, message_id = parse_message_id(source)
                    forwarded.append(
                        {
                            "method": "forwardMessage",
                            "body": {
                                "from_chat_id": chat_id,
                                "message_id": message_id,
                            },
                        }
                    )
                else:
                    # 自建转发使用相册和说明文字表达，不伪造 Telegram 原生转发来源栏。
                    content = node.get("content")
                    if not isinstance(content, list) or not content:
                        raise ValueError("转发节点缺少内容")
                    forwarded.extend(encode_segments(content, depth + 1))
            # 只合并同一个转发段内相邻的图片，文字、语音、贴纸和原生转发保持原顺序。
            images: List[bytes] = []
            rendered: List[Dict[str, Any]] = []

            def flush_images(images: List[bytes], rendered: List[Dict[str, Any]]) -> None:
                image_part: Optional[Dict[str, Any]] = None
                if len(images) == 1:
                    image_part = {"kind": "image", "binary": images[0]}
                elif images:
                    image_part = {"kind": "album", "images": images.copy()}
                if image_part is not None:
                    # Caption 上限为 1024 个 UTF-16 单位；较长说明保留完整独立消息，不截断。
                    if rendered and rendered[-1].get("method") == "sendMessage":
                        body = rendered[-1]["body"]
                        if utf16_length(body["text"]) <= 1024:
                            image_part["caption"] = body["text"]
                            image_part["caption_entities"] = body["entities"]
                            rendered.pop()
                    rendered.append(image_part)
                images.clear()

            for part in forwarded:
                if part.get("kind") == "image":
                    images.append(part["binary"])
                else:
                    flush_images(images, rendered)
                    rendered.append(part)
            flush_images(images, rendered)
            parts.extend(rendered)
        else:
            raise ValueError(f"不支持的出站消息段: {kind}")
    flush()
    return parts


def reply_parameters(message: Mapping[str, Any], chat_id: int) -> Optional[Dict[str, Any]]:
    target = ""
    for segment in message["raw_message"]:
        if segment["type"] == "reply":
            data = segment["data"]
            target = str(data["target_message_id"] if isinstance(data, Mapping) else data)
            break
    # reply_to 是宿主的上下文锚点，set_quote=false 时仍存在；只有显式 reply 段才表示平台引用。
    if not target:
        return None
    source_chat, source_message = parse_message_id(target)
    if source_chat != chat_id:
        raise ValueError("回复引用不属于目标聊天")
    return {"message_id": source_message}

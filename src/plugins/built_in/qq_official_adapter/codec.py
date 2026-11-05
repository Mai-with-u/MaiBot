from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

import base64
import binascii
import hashlib
import json
import re

PLATFORM = "qq"
GROUP_EVENT = "GROUP_AT_MESSAGE_CREATE"
GROUP_MESSAGE_EVENT = "GROUP_MESSAGE_CREATE"
PRIVATE_EVENT = "C2C_MESSAGE_CREATE"
GROUP_EVENTS = {GROUP_EVENT, GROUP_MESSAGE_EVENT}
SUPPORTED_EVENTS = GROUP_EVENTS | {PRIVATE_EVENT}

QQ_ATTACHMENT_TAG_PATTERN = re.compile(r"<attachmentType=.*?>", re.IGNORECASE)
QQ_FACE_TAG_PATTERN = re.compile(r"<faceType=.*?>", re.IGNORECASE)
QQ_MENTION_TOKEN_PATTERN = re.compile(r"<@!?([0-9A-Za-z_-]+)>")


def decode_message(
    event_type: str,
    payload: Mapping[str, Any],
    account_id: str,
    attachment_segments: Optional[List[Dict[str, Any]]] = None,
    attachment_labels: Optional[List[str]] = None,
    is_picture: bool = False,
    is_emoji: bool = False,
    bot_name: str = "",
    id_map: Optional[Mapping[str, Mapping[str, str]]] = None,
    unified_account_id: str = "",
) -> Tuple[Dict[str, Any], str, str]:
    """将官方群 @/全量群/单聊事件转换为 MaiBot 网关消息，并返回场景和目标 OpenID。

    附件由插件侧下载并转换为标准段后经 ``attachment_segments``/``attachment_labels``
    传入；没有文本但有附件段的消息同样放行。
    """
    if event_type not in SUPPORTED_EVENTS:
        raise ValueError(f"不支持的事件类型: {event_type}")

    message_id = str(payload.get("id") or "").strip()
    author = payload.get("author")
    if not message_id or not isinstance(author, Mapping):
        raise ValueError("消息缺少 id 或 author")

    is_group = event_type in GROUP_EVENTS
    group_id = str(payload.get("group_openid") or "").strip() if is_group else ""
    user_keys = ("member_openid", "openid", "id", "user_openid") if is_group else ("user_openid", "openid", "id")
    user_id = next(
        (candidate for key in user_keys if (candidate := str(author.get(key) or "").strip())),
        "",
    )
    if not user_id or (is_group and not group_id):
        raise ValueError("消息缺少用户或群 OpenID")

    # 统一 ID 映射：把群/用户 OpenID 替换为指定 ID（如真实 QQ 号），与其它 qq 适配器共享聊天流；
    # 原始 OpenID 保留在 additional_config，供出站发送时反查。
    group_map = (id_map or {}).get("group") or {}
    user_map = (id_map or {}).get("user") or {}
    unified_group = str(group_map.get(group_id) or "") if group_id else ""
    unified_user = str(user_map.get(user_id) or "")
    # 主程序通过正式 account_id 字段计算 session 归属，扩展配置只保存平台专属数据。
    additional_config: Dict[str, Any] = {}
    if is_group:
        additional_config["qq_official_group_openid"] = group_id
        additional_config["platform_io_target_group_id"] = unified_group or group_id
    else:
        additional_config["platform_io_target_user_id"] = unified_user or user_id
    additional_config["qq_official_user_openid"] = user_id
    # 官方引用索引（message_scene.ext 的 msg_idx）：出站 message_reference 只能用 REFIDX，
    # 不能用消息 id（ROBOT1.0_...）；ref_msg_idx 表示用户这条消息本身引用了谁。
    msg_idx, ref_msg_idx = extract_message_scene_fields(payload)
    if msg_idx:
        additional_config["qq_official_msg_idx"] = msg_idx
    if ref_msg_idx:
        additional_config["qq_official_ref_msg_idx"] = ref_msg_idx
    if unified_user:
        user_id = unified_user
    if unified_group:
        group_id = unified_group

    # 全量群消息天然携带表情/回复引用等附件元素；附件不阻断解码，文本与附件段皆可。
    content = _normalize_content(_extract_content(payload))
    segments = list(attachment_segments) if attachment_segments else []
    labels = [label for label in (attachment_labels or []) if label]
    # content 仅是表情协议标签的转写时，真实内容由 emoji 段承载，置空避免与图片重复展示。
    if is_emoji and content in {"[表情]", "[动画表情]"}:
        content = ""
    is_at = _is_bot_mentioned(event_type, payload, content, account_id)
    # <@id> 协议标记对用户不可读：@ 机器人由 is_at 表达，@ 他人转写为标准 at 段。
    content, body_segments, body_plain_text = _rewrite_mention_tokens(content, account_id, bot_name, payload, user_map)
    if not content and not segments and not body_segments:
        raise ValueError("消息不包含文本或可处理的附件")

    timestamp = payload.get("timestamp")
    try:
        received_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        raise ValueError("消息缺少有效的 timestamp") from None

    target_id = group_id if is_group else user_id
    nickname = str(author.get("nickname") or author.get("nick") or author.get("username") or user_id).strip() or user_id
    message_info: Dict[str, Any] = {
        "user_info": {"user_id": user_id, "user_nickname": nickname},
        "additional_config": additional_config,
    }
    if is_group:
        message_info["group_info"] = {
            "group_id": group_id,
            # 官方事件通常不带群名；传空表示未知，聊天流保留已记录的真实群名
            "group_name": str(payload.get("group_name") or ""),
        }

    raw_message: List[Dict[str, Any]] = [*body_segments, *segments]
    processed_plain_text = " ".join(part for part in [body_plain_text, *labels] if part)

    message = {
        "message_id": message_id,
        "timestamp": str(received_at),
        "platform": PLATFORM,
        "account_id": unified_account_id or account_id,
        "scope": None,
        "message_info": message_info,
        "raw_message": raw_message,
        "processed_plain_text": processed_plain_text,
        "is_at": is_at,
        "is_mentioned": is_at,
        "is_emoji": is_emoji,
        "is_picture": is_picture,
        "is_command": content.startswith("/"),
        "session_id": "",
    }
    return message, "group" if is_group else "private", target_id


def _normalize_content(content: str) -> str:
    """将 QQ 表情/附件协议标签转换为可读文本。"""

    content = QQ_ATTACHMENT_TAG_PATTERN.sub("", content)
    return QQ_FACE_TAG_PATTERN.sub("[表情]", content).strip()


def _extract_content(payload: Mapping[str, Any]) -> str:
    """顶层 content 优先；全量群消息（GROUP_MESSAGE_CREATE）的文本在 msg_elements 里。"""

    content = str(payload.get("content") or "").strip()
    if content:
        return content
    parts: List[str] = []
    for element in payload.get("msg_elements") or []:
        if not isinstance(element, Mapping):
            continue
        for value in (element.get("content"), element.get("text")):
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, Mapping):
                nested_content = value.get("content")
                if isinstance(nested_content, str) and nested_content.strip():
                    parts.append(nested_content.strip())
        text_element = element.get("text_element")
        if isinstance(text_element, Mapping):
            nested_content = text_element.get("content")
            if isinstance(nested_content, str) and nested_content.strip():
                parts.append(nested_content.strip())
    return "".join(parts).strip()


def extract_message_scene_fields(payload: Mapping[str, Any]) -> Tuple[str, str]:
    """解析 message_scene.ext，返回 (msg_idx, ref_msg_idx)。

    ext 形如 ["msg_idx=REFIDX_xxx==", "auth_token=...", "ref_msg_idx=TMP_xxx"]；
    msg_idx 是本条消息用于被引用的索引，ref_msg_idx 是本条消息引用的目标索引。
    字段缺失或结构异常时返回空串，由调用方按无引用降级。
    """

    scene = payload.get("message_scene")
    if not isinstance(scene, Mapping):
        return "", ""
    ext = scene.get("ext")
    if not isinstance(ext, list):
        return "", ""
    msg_idx = ""
    ref_msg_idx = ""
    for item in ext:
        if not isinstance(item, str):
            continue
        if item.startswith("msg_idx="):
            msg_idx = item[len("msg_idx=") :].strip()
        elif item.startswith("ref_msg_idx="):
            ref_msg_idx = item[len("ref_msg_idx=") :].strip()
    return msg_idx, ref_msg_idx


def summarize_payload(payload: Mapping[str, Any]) -> str:
    """返回用于拒绝日志的 payload 结构摘要，截断以避免刷屏。"""

    try:
        summary = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        summary = str(payload)
    return summary[:600]


def _rewrite_mention_tokens(
    content: str,
    account_id: str,
    bot_name: str,
    payload: Mapping[str, Any],
    user_map: Mapping[str, str],
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """将 content 里的 <@id> 协议标记转写为标准 at 段，避免 OpenID 原文进入聊天流。

    @ 机器人由 is_at 字段表达，标记删除并生成机器人的 at 段；
    @ 其他人时用 mentions 数组的名字生成 at 段并回填可读前缀，取不到名字则仅删除标记。
    at 段的目标用户同样应用统一 ID 映射。返回 (转写后文本, at 段列表, plain text 的 @ 前缀)。
    """

    display_names: Dict[str, str] = {}
    for mention in payload.get("mentions") or []:
        if not isinstance(mention, Mapping):
            continue
        mention_id = next(
            (
                candidate
                for key in ("id", "openid", "user_openid", "member_openid")
                if (candidate := str(mention.get(key) or "").strip())
            ),
            "",
        )
        name = str(mention.get("username") or mention.get("nickname") or mention.get("nick") or "").strip()
        if mention_id and name:
            display_names[mention_id] = name

    # 按原文顺序切分为文本片段与 at 段，保持 "文本A @x 文本B @y 文本C" 的穿插位置。
    # pieces 元素：str 为文本；tuple(at 段, 可读标签) 为 @。
    pieces: List[Any] = []
    cursor = 0
    for match in QQ_MENTION_TOKEN_PATTERN.finditer(content):
        pieces.append(content[cursor : match.start()])
        cursor = match.end()
        mention_id = match.group(1)
        if mention_id == account_id:
            pieces.append(
                (
                    {
                        "type": "at",
                        "data": {
                            "target_user_id": account_id,
                            "target_user_nickname": bot_name or None,
                            "target_user_cardname": None,
                        },
                    },
                    f"@{bot_name}" if bot_name else "",
                )
            )
            continue
        name = display_names.get(mention_id)
        if not name:
            continue
        pieces.append(
            (
                {
                    "type": "at",
                    "data": {
                        "target_user_id": user_map.get(mention_id, mention_id),
                        "target_user_nickname": name,
                        "target_user_cardname": None,
                    },
                },
                f"@{name}",
            )
        )
    pieces.append(content[cursor:])

    # 合并相邻文本（被丢弃的未知 @ 会让文本相邻），并去掉整体首尾空白。
    merged: List[Any] = []
    for piece in pieces:
        if isinstance(piece, str) and merged and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    if merged and isinstance(merged[0], str):
        merged[0] = merged[0].lstrip()
    if merged and isinstance(merged[-1], str):
        merged[-1] = merged[-1].rstrip()

    ordered_segments: List[Dict[str, Any]] = []
    plain_parts: List[str] = []
    text_parts: List[str] = []
    for index, piece in enumerate(merged):
        if isinstance(piece, str):
            if piece:
                ordered_segments.append({"type": "text", "data": piece})
                plain_parts.append(piece)
                text_parts.append(piece)
            continue
        at_segment, label = piece
        ordered_segments.append(at_segment)
        if label:
            following = merged[index + 1] if index + 1 < len(merged) else ""
            needs_space = isinstance(following, str) and following and not following[0].isspace()
            plain_parts.append(label + (" " if needs_space else ""))
    return "".join(text_parts).strip(), ordered_segments, "".join(plain_parts).strip()


def _is_bot_mentioned(event_type: str, payload: Mapping[str, Any], content: str, account_id: str) -> bool:
    """群 @ 事件天然指向机器人；全量群消息需检查 mentions、msg_elements 与文本协议标记。"""

    if event_type == GROUP_EVENT:
        return True
    if event_type != GROUP_MESSAGE_EVENT or not account_id:
        return False
    for mention in payload.get("mentions") or []:
        if isinstance(mention, Mapping) and any(
            str(mention.get(key) or "").strip() == account_id
            for key in ("id", "openid", "user_openid", "member_openid")
        ):
            return True
    if _tree_mentions_bot(payload.get("msg_elements"), account_id):
        return True
    # 频道式 <@id>/<@!id> 协议标记，以及 @机器人AppID/@裸 ID 的文本写法。
    if f"<@{account_id}>" in content or f"<@!{account_id}>" in content:
        return True
    return re.search(rf"@(?:机器人)?{re.escape(account_id)}(?:\s|$)", content) is not None


def _tree_mentions_bot(value: Any, account_id: str) -> bool:
    """递归检查全量群消息 msg_elements 树中是否包含指向机器人的 @ 元素。"""

    if isinstance(value, list):
        return any(_tree_mentions_bot(item, account_id) for item in value)
    if not isinstance(value, Mapping):
        return False
    element_type = str(value.get("type") or value.get("element_type") or "").lower()
    looks_like_mention = "at" in element_type or "mention" in element_type
    for key, raw_value in value.items():
        normalized_key = str(key).lower()
        if ("at" in normalized_key or "mention" in normalized_key) and isinstance(raw_value, Mapping):
            if any(
                str(raw_value.get(target_key) or "").strip() == account_id
                for target_key in ("id", "openid", "user_id", "target_id", "member_openid")
            ):
                return True
        if (
            looks_like_mention
            and normalized_key in {"id", "openid", "user_id", "target_id", "member_openid"}
            and str(raw_value or "").strip() == account_id
        ):
            return True
        if isinstance(raw_value, (list, Mapping)) and _tree_mentions_bot(raw_value, account_id):
            return True
    return False


def extract_command_text(payload: Mapping[str, Any]) -> str:
    """提取消息文本供管理命令解析，<@id> 标记原样保留。"""

    return _normalize_content(_extract_content(payload))


def encode_message(
    message: Mapping[str, Any],
    reverse_map: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> Tuple[str, str, List[Dict[str, Any]], str]:
    """提取出站目标并按原顺序切分为待发送的消息部分；不默默丢弃未支持的消息段。

    统一 ID 需经 ``reverse_map``（{"group": {统一ID: OpenID}, "user": {...}}）反查回
    OpenID 才能调用官方 API；无映射时按原值发送。

    官方一条消息只能承载一种内容，因此：
    - 连续的文本/@ 段合并为一个 ``{"kind": "body", "body": {...}}``，含 @ 时以 markdown
      （msg_type=2）发送，否则为纯文本（msg_type=0）；
    - 每个图片/表情段单独成为一个 ``{"kind": "media", "binary": bytes, "file_name": str}``，
      由插件上传后以富媒体（msg_type=7）发送。

    返回的第四项为 reply 段指向的被回复消息 ID（平台消息 id，非 REFIDX），
    由调用方转换为官方 message_reference；无 reply 段时为空串。
    """
    message_info = message.get("message_info")
    if not isinstance(message_info, Mapping):
        raise ValueError("出站消息缺少 message_info")
    group_info = message_info.get("group_info")
    config = message_info.get("additional_config")
    if not isinstance(config, Mapping):
        config = {}
    group_id = str(group_info.get("group_id") or "").strip() if isinstance(group_info, Mapping) else ""
    group_id = group_id or str(config.get("platform_io_target_group_id") or "").strip()
    user_id = str(config.get("platform_io_target_user_id") or "").strip()
    scene = "group" if group_id else "private"
    target_id = group_id or user_id
    if not target_id:
        raise ValueError("出站消息缺少目标 OpenID")
    target_id = str((reverse_map or {}).get("group" if scene == "group" else "user", {}).get(target_id) or target_id)

    segments = message.get("raw_message")
    if not isinstance(segments, list) or not segments:
        raise ValueError("出站消息缺少消息段")
    outbound: List[Dict[str, Any]] = []
    parts: List[str] = []
    use_markdown = False
    reply_target_id = ""

    def _flush_text() -> None:
        nonlocal use_markdown
        text = "".join(parts).strip()
        if text:
            body = {"msg_type": 2, "markdown": {"content": text}} if use_markdown else {"msg_type": 0, "content": text}
            outbound.append({"kind": "body", "body": body})
        parts.clear()
        use_markdown = False

    for segment in segments:
        if not isinstance(segment, Mapping):
            raise ValueError("出站消息段格式无效")
        if segment.get("type") == "reply":
            # reply 段不进入正文，但需把被回复消息 id 交给调用方，由其映射为
            # 官方 message_reference（REFIDX），否则 QQ 客户端不显示引用样式。
            reply_data = segment.get("data")
            if isinstance(reply_data, Mapping):
                reply_target_id = str(
                    reply_data.get("target_message_id") or reply_data.get("message_id") or ""
                ).strip()
            elif isinstance(reply_data, str):
                reply_target_id = reply_data.strip()
            continue
        if segment.get("type") == "at":
            # 实测（2026-09-30）：<@openid> 与 <qqbot-at-user /> 在纯文本消息里都显示成代码原文，
            # 只有 markdown 消息中的 <qqbot-at-user id="openid" /> 能渲染为真正的 @。
            data = segment.get("data")
            if isinstance(data, Mapping):
                at_user_id = str(data.get("target_user_id") or "").strip()
                at_nickname = str(data.get("target_user_cardname") or data.get("target_user_nickname") or "").strip()
            else:
                at_user_id = str(data or "").strip()
                at_nickname = ""
            at_openid = str((reverse_map or {}).get("user", {}).get(at_user_id) or at_user_id)
            if at_openid and not at_openid.isdigit():
                parts.append(f'<qqbot-at-user id="{at_openid}" /> ')
                use_markdown = True
            elif at_nickname:
                # 纯数字是未绑定 OpenID 的 QQ 号，无法 @，退化为可读 @昵称。
                parts.append(f"@{at_nickname} ")
            continue
        if segment.get("type") in {"image", "emoji"}:
            _flush_text()
            outbound.append({"kind": "media", **_decode_image_segment(segment)})
            continue
        if segment.get("type") != "text":
            raise ValueError(f"出站暂不支持 {segment.get('type')} 消息段，仅支持文本、@、图片与表情")
        data = segment.get("data")
        if not isinstance(data, str):
            raise ValueError("出站文本段的数据必须是字符串")
        parts.append(data)
    _flush_text()
    if not outbound:
        raise ValueError("出站消息为空")
    return scene, target_id, outbound, reply_target_id


def _decode_image_segment(segment: Mapping[str, Any]) -> Dict[str, Any]:
    """取出图片/表情段的二进制数据，并按文件头确定上传文件名。"""
    raw_base64 = str(segment.get("binary_data_base64") or "").strip()
    if not raw_base64:
        raise ValueError(f"出站{segment.get('type')}段缺少二进制数据")
    try:
        binary = base64.b64decode(raw_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"出站{segment.get('type')}段的二进制数据不是有效的 Base64") from None
    extension = _detect_image_extension(binary)
    if not extension:
        raise ValueError(f"出站{segment.get('type')}段不是可识别的图片格式")
    name = str(segment.get("hash") or "").strip() or hashlib.sha256(binary).hexdigest()
    return {"binary": binary, "file_name": f"{name}.{extension}"}


def _detect_image_extension(binary: bytes) -> str:
    """按文件头识别官方富媒体支持的图片格式，无法识别时返回空串。"""
    if binary.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if binary.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if binary.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if binary[:4] == b"RIFF" and binary[8:12] == b"WEBP":
        return "webp"
    if binary.startswith(b"BM"):
        return "bmp"
    return ""

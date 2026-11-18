"""观察面板语音引用及旧事件的只读回填。"""

from collections import defaultdict
from itertools import batched
from sqlmodel import Session, col, select
from typing import Any, Dict, List, Tuple

import msgpack

from src.common.database.database_model import BinaryData, Messages
from src.common.logger import get_logger


VOICE_MEDIA_SCHEMA_VERSION = 2
logger = get_logger("maisaka_monitor_voice")


def monitor_voice_item(digest: str, transcript: str, index: int) -> Dict[str, Any]:
    """生成监控事件使用的语音引用，音频从媒体接口读取。"""
    return {
        "kind": "voice",
        "hash": digest,
        "text": transcript,
        "url": f"/api/webui/system/maisaka-monitor/media/voice/{digest}",
        "index": index,
    }


def restore_legacy_voice_media(session: Session, events: List[Dict[str, Any]]) -> None:
    """旧事件只记录语音正文。按真实会话和消息 ID 补回已保存的音频引用。"""
    targets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        payload = event["data"]
        if event["event"] not in {"message.sent", "message.ingested", "message.updated"}:
            continue
        if payload["schema_version"] >= VOICE_MEDIA_SCHEMA_VERSION:
            continue
        session_id = payload.get("source_session_id") or payload.get("session_id")
        message_id = payload.get("message_id")
        if session_id and message_id:
            if payload.get("source_session_id"):
                message_id = message_id.removeprefix(f"{session_id}:")
            targets[session_id, message_id].append(payload)

    # 账本回放可能跨聊天流；先用有索引的消息 ID 筛选，再核对所属会话。
    messages: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    hashes = set()
    for ids in batched(dict.fromkeys(message_id for _, message_id in targets), 400):
        statement = select(Messages.session_id, Messages.message_id, Messages.raw_content).where(
            col(Messages.message_id).in_(ids)
        )
        for session_id, message_id, raw in session.exec(statement):
            key = (session_id, message_id)
            if key not in targets or not raw or b"voice" not in raw:
                continue
            try:
                segments = msgpack.unpackb(raw)
            except (ValueError, msgpack.UnpackException) as exc:
                logger.warning(f"无法读取旧观察事件的消息段: session_id={session_id} message_id={message_id} error={exc}")
                continue
            voices = [
                monitor_voice_item(segment["hash"], segment.get("data", ""), index)
                for index, segment in enumerate(segments)
                if isinstance(segment, dict) and segment.get("type") == "voice" and segment.get("hash")
            ]
            if voices:
                messages[key] = voices
                hashes.update(item["hash"] for item in voices)

    available = set()
    for batch in batched(hashes, 400):
        available.update(session.exec(select(BinaryData.data_hash).where(col(BinaryData.data_hash).in_(batch))))
    for key, voices in messages.items():
        stored = [item for item in voices if item["hash"] in available]
        if stored:
            for payload in targets[key]:
                media = [*payload.get("media", []), *stored]
                payload["media"] = sorted(media, key=lambda item: item.get("index", 0))

"""按表达意图匹配已注册表情。"""

from contextlib import closing
from hashlib import sha256
from math import isfinite
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

import asyncio
import json
import numpy as np
import sqlite3

from src.common.data_models.embedding_service_data_models import EmbeddingResult
from src.common.data_models.image_data_model import MaiEmoji
from src.common.llm_json import validate_llm_json
from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.runtime_loop import get_main_loop
from src.config.config import config_manager
from src.services.embedding_service import EmbeddingServiceClient, resolve_embedding_model_fingerprint

from .emoji_manager import _is_vlm_task_configured, emoji_manager

logger = get_logger("emoji_semantic_index")
_index_path = get_instance_root() / "data" / "emoji_semantic_index.sqlite3"
_index_lock = asyncio.Lock()
_tag_lock = asyncio.Lock()
_pending: Dict[str, asyncio.Task[None]] = {}
_backfill_cursor = 0
_BACKFILL_BATCH_SIZE = 4


class EmojiTags(BaseModel):
    emotion_tags: List[str] = Field(max_length=5)
    content_tags: List[str] = Field(min_length=1, max_length=5)

    @field_validator("emotion_tags", "content_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        if isinstance(value, list) and all(isinstance(tag, str) for tag in value):
            return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
        return value

    def embedding_texts(self) -> Dict[str, str]:
        texts = {"content": "、".join(self.content_tags)}
        if self.emotion_tags:
            texts["emotion"] = "、".join(self.emotion_tags)
        return texts


def _config_key() -> Optional[str]:
    config = config_manager.get_model_config()
    names = config.model_task_config.embedding.model_list
    if not names:
        return None
    models = [model for model in config.models if model.name in names]
    providers = {model.api_provider for model in models}
    payload = json.dumps([
        names,
        [model.model_dump(mode="json") for model in models],
        [(provider.name, provider.base_url) for provider in config.api_providers if provider.name in providers],
    ], sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _model_key(result: EmbeddingResult) -> str:
    return resolve_embedding_model_fingerprint(
        model_name=result.model_name,
        model_identifier=result.model_identifier,
        api_provider=result.api_provider,
        dimension=len(result.embedding),
    )["hash"]


def _score_matches(query: List[float], candidates: List[Tuple[str, List[float]]]) -> Dict[str, float]:
    if not candidates:
        return {}
    matrix = np.asarray([vector for _, vector in candidates], dtype=np.float64)
    query_vector = np.asarray(query, dtype=np.float64)
    query_norm = np.linalg.norm(query_vector)
    norms = np.linalg.norm(matrix, axis=1)
    valid = np.isfinite(matrix).all(axis=1) & np.isfinite(norms) & (norms > 0)
    if not isfinite(query_norm) or query_norm == 0 or not valid.any():
        raise ValueError("表情包语义匹配没有有效向量。")
    scores = (matrix[valid] / norms[valid, None]) @ (query_vector / query_norm)
    return {candidates[index][0]: float(score) for index, score in zip(np.flatnonzero(valid), scores, strict=True)}


def _open_index() -> sqlite3.Connection:
    _index_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_index_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "config TEXT, description TEXT, space TEXT, vector TEXT, "
            "PRIMARY KEY (config, description, space))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS emoji_tags (image_hash TEXT PRIMARY KEY, tags TEXT NOT NULL)"
        )
    except Exception:
        connection.close()
        raise
    return connection


def _read_index(config_key: str, space: Optional[str] = None) -> Dict[str, List[float]]:
    if not _index_path.exists():
        return {}
    with closing(_open_index()) as connection:
        if space is None:
            return {label: [] for (label,) in connection.execute(
                "SELECT DISTINCT description FROM vectors WHERE config = ?", (config_key,)
            )}
        return {label: json.loads(vector) for label, vector in connection.execute(
            "SELECT description, vector FROM vectors WHERE config = ? AND space = ?", (config_key, space)
        )}


def _save_vector(config_key: str, label: str, space: str, vector: List[float]) -> None:
    with closing(_open_index()) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
            (config_key, label, space, json.dumps(vector, allow_nan=False)),
        )


def _read_tags(emoji_hash: Optional[str] = None) -> Dict[str, EmojiTags]:
    if not _index_path.exists():
        return {}
    with closing(_open_index()) as connection:
        if emoji_hash is None:
            rows = connection.execute("SELECT image_hash, tags FROM emoji_tags")
        else:
            rows = connection.execute("SELECT image_hash, tags FROM emoji_tags WHERE image_hash = ?", (emoji_hash,))
        return {key: EmojiTags.model_validate_json(value) for key, value in rows}


def get_emoji_tags(emoji_hash: str) -> Optional[EmojiTags]:
    return _read_tags(emoji_hash).get(emoji_hash)


def _save_tags(emoji_hash: str, tags: EmojiTags) -> None:
    with closing(_open_index()) as connection, connection:
        connection.execute("INSERT OR REPLACE INTO emoji_tags VALUES (?, ?)", (emoji_hash, tags.model_dump_json()))


async def _index_emoji(emoji: MaiEmoji, config_key: Optional[str]) -> None:
    tags = await asyncio.to_thread(get_emoji_tags, emoji.file_hash)
    if tags is None and _is_vlm_task_configured():
        async with _tag_lock:
            tags = await asyncio.to_thread(get_emoji_tags, emoji.file_hash)
            if tags is None:
                raw = await emoji_manager.analyze_emoji_image(emoji, "emoji_grouped_tags")
                tags = validate_llm_json(raw, EmojiTags)
                await asyncio.to_thread(_save_tags, emoji.file_hash, tags)
    if config_key and tags is not None:
        for axis, label in tags.embedding_texts().items():
            await _index_description(label, config_key, axis)


def _axis_key(config_key: str, axis: str) -> str:
    # 旧的混合向量使用原配置键，不能当作任一维度的索引。
    return f"{config_key}:emoji-tags-v2:{axis}"


async def _index_description(label: str, config_key: str, axis: str) -> None:
    # 注册和定时补算可能同时命中同一个描述。串行去重也避免集中占用 Embedding 服务。
    async with _index_lock:
        try:
            axis_key = _axis_key(config_key, axis)
            if _config_key() != config_key or label in await asyncio.to_thread(_read_index, axis_key):
                return
            client = EmbeddingServiceClient(request_type="emoji.index")
            result = await client.embed_text(label)
            if not result.embedding or not all(map(isfinite, result.embedding)) or not any(result.embedding):
                raise ValueError("表情描述的嵌入向量无效。")
            if _config_key() == config_key:
                await asyncio.to_thread(_save_vector, axis_key, label, _model_key(result), result.embedding)
        except Exception:
            logger.warning("表情包描述索引计算失败，下次维护时重试", exc_info=True)


def schedule_emoji_index(emoji_hash: str) -> None:
    """注册提交后立即在主循环排队，注册流程无需等待模型请求。"""
    async def index_registered() -> None:
        try:
            emoji = await asyncio.to_thread(emoji_manager.get_emoji_by_hash_from_db, emoji_hash)
            if emoji is not None:
                config_key = _config_key()
                await _index_emoji(emoji, config_key)
        except Exception:
            logger.warning(f"表情包索引计算失败，下次维护时重试: hash={emoji_hash}", exc_info=True)

    def start() -> None:
        if emoji_hash in _pending or not (_is_vlm_task_configured() or _config_key()):
            return
        task = asyncio.create_task(index_registered())
        _pending[emoji_hash] = task
        task.add_done_callback(lambda _: _pending.pop(emoji_hash, None))

    loop = get_main_loop()
    if loop is None or loop.is_closed():
        loop = asyncio.get_running_loop()
    loop.call_soon_threadsafe(start)


async def backfill_emoji_index() -> None:
    """每轮只补一小批，失败条目下一轮轮转重试。"""
    global _backfill_cursor
    config_key = _config_key()
    can_tag = _is_vlm_task_configured()
    if not can_tag and not config_key:
        return
    tags_by_hash = await asyncio.to_thread(_read_tags)
    indexed = {
        axis: await asyncio.to_thread(_read_index, _axis_key(config_key, axis))
        for axis in ("emotion", "content")
    } if config_key else {}
    emojis = sorted(emoji_manager.emojis, key=lambda emoji: emoji.file_hash)
    if not emojis:
        return
    cursor = _backfill_cursor % len(emojis)
    attempted = 0
    for offset, emoji in enumerate(emojis[cursor:] + emojis[:cursor]):
        tags = tags_by_hash.get(emoji.file_hash)
        needs_vectors = tags is not None and config_key is not None and any(
            label not in indexed[axis] for axis, label in tags.embedding_texts().items()
        )
        if not ((can_tag and tags is None) or needs_vectors):
            continue
        try:
            await _index_emoji(emoji, config_key)
        except Exception:
            logger.warning(f"表情包回填失败: hash={emoji.file_hash}", exc_info=True)
        _backfill_cursor = cursor + offset + 1
        attempted += 1
        if attempted >= _BACKFILL_BATCH_SIZE:
            break


async def match_emoji_description(emotion: str, session_id: str, *, content: str = "") -> str:
    """只查询指定的维度。多个维度同时指定时对余弦相似度取平均。"""
    queries = {axis: text.strip() for axis, text in (("emotion", emotion), ("content", content)) if text.strip()}
    if not queries:
        raise ValueError("请提供表情的情绪或画面内容。")
    config_key = _config_key()
    if config_key is None:
        raise ValueError("未配置表情语义匹配所需的 Embedding 模型。")
    tags_by_hash = await asyncio.to_thread(_read_tags)
    candidates = {
        emoji.file_hash: tags_by_hash[emoji.file_hash].embedding_texts()
        for emoji in emoji_manager.emojis if emoji.file_hash in tags_by_hash
    }
    for axis in queries:
        indexed = await asyncio.to_thread(_read_index, _axis_key(config_key, axis))
        candidates = {key: labels for key, labels in candidates.items() if labels.get(axis) in indexed}
    if not candidates:
        raise ValueError("当前没有完成所需维度向量索引的可用表情包。")
    client = EmbeddingServiceClient(request_type="emoji.attach.semantic", session_id=session_id)
    query_vectors = await asyncio.gather(
        *(client.embed_text(text) for text in queries.values()), return_exceptions=True,
    )
    if _config_key() != config_key:
        raise ValueError("表情语义匹配期间 Embedding 模型配置发生变化。")
    totals = dict.fromkeys(candidates, 0.0)
    for axis, query in zip(queries, query_vectors, strict=True):
        if isinstance(query, BaseException):
            raise query
        vectors = await asyncio.to_thread(_read_index, _axis_key(config_key, axis), _model_key(query))
        entries = [(key, vectors[candidates[key][axis]]) for key in totals if candidates[key][axis] in vectors]
        scores = await asyncio.to_thread(_score_matches, query.embedding, entries)
        totals = {key: totals[key] + score for key, score in scores.items()}
    if not totals:
        raise ValueError("当前 Embedding 模型空间中没有已索引的可用表情包。")
    return max(totals, key=lambda key: totals[key] / len(queries))

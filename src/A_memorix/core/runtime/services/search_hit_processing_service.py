from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import time

from src.chat.message_receive.chat_manager import chat_manager
from src.common.logger import get_logger

from ...retrieval import RetrievalResult
from ...utils.feedback_policy import (
    feedback_cfg_episode_query_block_enabled,
    feedback_cfg_paragraph_hard_filter_enabled,
)
from ...utils.metadata import coerce_metadata_dict
from ...utils.runtime_payloads import optional_float, tokens
from ...utils.time_parser import format_timestamp, parse_query_datetime_to_timestamp
from ..models import _NormalizedSearchTimeWindow
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SearchHitProcessingService")


class MemorySearchHitProcessingService(KernelServiceBase):
    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        return optional_float(value)

    @staticmethod
    def _relation_status_is_inactive(status: Optional[Dict[str, Any]]) -> bool:
        if status is None:
            return True
        return bool(status.get("is_inactive"))

    def _load_paragraph_stale_marks(
        self,
        paragraph_hashes: Sequence[str],
    ) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
        if self.metadata_store is None:
            return {}, {}
        normalized = tokens(paragraph_hashes)
        if not normalized:
            return {}, {}
        marks_by_paragraph = self.metadata_store.get_paragraph_stale_relation_marks_batch(normalized)
        relation_hashes = tokens(
            mark.get("relation_hash", "")
            for marks in marks_by_paragraph.values()
            for mark in marks
            if isinstance(mark, dict)
        )
        status_map = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}
        return marks_by_paragraph, status_map

    def _paragraph_hidden_by_stale_marks(
        self,
        paragraph_hash: str,
        *,
        marks_by_paragraph: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        relation_status_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        token = str(paragraph_hash or "").strip()
        if not token or self.metadata_store is None or not feedback_cfg_paragraph_hard_filter_enabled():
            return False

        marks_map = marks_by_paragraph if isinstance(marks_by_paragraph, dict) else {}
        status_map = relation_status_map if isinstance(relation_status_map, dict) else {}
        if not marks_map:
            marks_map, status_map = self._load_paragraph_stale_marks([token])
        elif not status_map:
            relation_hashes = tokens(
                mark.get("relation_hash", "") for mark in marks_map.get(token, []) if isinstance(mark, dict)
            )
            status_map = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}

        for mark in marks_map.get(token, []):
            relation_hash = str((mark or {}).get("relation_hash", "") or "").strip()
            if not relation_hash:
                continue
            if self._relation_status_is_inactive(status_map.get(relation_hash)):
                return True
        return False

    def _filter_episode_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None or not feedback_cfg_episode_query_block_enabled():
            return hits
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            if str(item.get("type", "") or "").strip() != "episode":
                filtered.append(item)
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            source = str(metadata.get("source", "") or item.get("source", "") or "").strip()
            if source and self.metadata_store.is_episode_source_query_blocked(source):
                continue
            filtered.append(item)
        return filtered

    def _filter_user_visible_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._filter_current_effective_hits(self._filter_active_relation_hits(self._filter_episode_hits(hits)))

    def _filter_current_effective_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None:
            return self._filter_hits_by_memory_change_metadata(hits)

        if not self._current_effective_filter_store_check_needed(hits):
            return self._filter_hits_by_memory_change_metadata(hits)

        now = time.time()
        paragraph_hashes: List[str] = []
        relation_hashes: List[str] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            if item_type == "paragraph" and hash_value:
                paragraph_hashes.append(hash_value)
            elif item_type == "relation" and hash_value:
                relation_hashes.append(hash_value)

        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(paragraph_hashes) if paragraph_hashes else {}
        relation_map = self.metadata_store.get_relations_by_hashes(relation_hashes) if relation_hashes else {}
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            metadata = coerce_metadata_dict(item.get("metadata"))
            item_type = str(item.get("type", "") or "").strip()
            hash_value = str(item.get("hash", "") or "").strip()
            if hash_value:
                stored: Optional[Dict[str, Any]] = None
                if item_type == "paragraph":
                    stored = paragraph_map.get(hash_value)
                elif item_type == "relation":
                    stored = relation_map.get(hash_value)
                if stored is not None:
                    metadata = coerce_metadata_dict(stored.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            next_item = dict(item)
            next_item["metadata"] = metadata
            filtered.append(next_item)
        return filtered

    def _current_effective_filter_store_check_needed(self, hits: List[Dict[str, Any]]) -> bool:
        if any(isinstance(coerce_metadata_dict(item.get("metadata")).get("memory_change"), dict) for item in hits):
            return True
        cache = self._current_effective_filter_cache
        now = time.time()
        if now - float(cache.get("checked_at", 0.0) or 0.0) < 60.0:
            return bool(cache.get("needed", False))
        needed = False
        try:
            plans = self.metadata_store.list_fuzzy_modify_plans(
                limit=1,
                statuses=["executing", "executed", "rolled_back", "rollback_failed"],
            )
            needed = bool(plans)
        except Exception as exc:
            logger.warning(f"检查当前有效记忆过滤状态失败，将保守启用回表过滤: {exc}")
            needed = True
        cache["checked_at"] = now
        cache["needed"] = needed
        return needed

    def _filter_hits_by_memory_change_metadata(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            metadata = coerce_metadata_dict(item.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            next_item = dict(item)
            next_item["metadata"] = metadata
            filtered.append(next_item)
        return filtered

    @staticmethod
    def _chat_source(chat_id: str) -> Optional[str]:
        clean = str(chat_id or "").strip()
        return f"chat_summary:{clean}" if clean else None

    @classmethod
    def _chat_source_for_search_scope(cls, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> Optional[str]:
        allowed_chat_ids = cls._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if len(allowed_chat_ids) > 1:
            return None
        return cls._chat_source(chat_id)

    @staticmethod
    def _scoped_search_limit(limit: int, *, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> int:
        safe_limit = max(1, int(limit or 5))
        allowed_chat_ids = MemorySearchHitProcessingService._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if not allowed_chat_ids:
            return safe_limit
        multiplier = max(5, len(allowed_chat_ids) * 5)
        return min(50, max(safe_limit, safe_limit * multiplier))

    @classmethod
    def _resolve_allowed_chat_ids(cls, chat_id: str, shared_chat_ids: Sequence[str] = ()) -> set[str]:
        allowed_chat_ids = {str(item or "").strip() for item in shared_chat_ids if str(item or "").strip()}
        clean_chat_id = str(chat_id or "").strip()
        if clean_chat_id:
            allowed_chat_ids.add(clean_chat_id)
        return allowed_chat_ids

    @staticmethod
    def _rank_score_from_item(item: Any) -> float:
        if isinstance(item, dict):
            raw_score = item.get("score", item.get("final_score", item.get("relevance", 0.0)))
        else:
            raw_score = getattr(item, "score", 0.0)
        try:
            return float(raw_score or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _dedupe_ranked_items(cls, items: Sequence[Any], *, limit: int) -> List[Any]:
        ranked: Dict[str, Any] = {}
        for index, item in enumerate(items):
            if isinstance(item, dict):
                item_hash = str(item.get("hash", "") or "").strip()
                item_type = str(item.get("type", "") or "").strip()
                content = str(item.get("content", "") or "").strip()
            else:
                item_hash = str(getattr(item, "hash_value", "") or "").strip()
                item_type = str(getattr(item, "result_type", "") or "").strip()
                content = str(getattr(item, "content", "") or "").strip()
            key = item_hash or f"{item_type}:{content}"
            if not key:
                key = f"item:{index}"
            current = ranked.get(key)
            if current is None or cls._rank_score_from_item(item) > cls._rank_score_from_item(current):
                ranked[key] = item
        return sorted(ranked.values(), key=cls._rank_score_from_item, reverse=True)[: max(1, int(limit or 5))]

    @classmethod
    def _paragraph_matches_chat_scope(cls, paragraph: Optional[Dict[str, Any]], allowed_chat_ids: set[str]) -> bool:
        if not paragraph:
            return False

        if not allowed_chat_ids:
            return True

        metadata = coerce_metadata_dict(paragraph.get("metadata"))
        if cls._metadata_chat_scope_ids(metadata) & allowed_chat_ids:
            return True

        source = str(paragraph.get("source", "") or metadata.get("source", "") or "").strip()
        return any(source == str(cls._chat_source(allowed_chat_id) or "") for allowed_chat_id in allowed_chat_ids)

    @classmethod
    def _hit_metadata_matches_chat_scope(cls, hit: Dict[str, Any], allowed_chat_ids: set[str]) -> Optional[bool]:
        if not allowed_chat_ids:
            return True

        metadata = coerce_metadata_dict(hit.get("metadata"))
        hit_type = str(hit.get("type", "") or "").strip()
        metadata_chat_ids = cls._metadata_chat_scope_ids(metadata)
        if metadata_chat_ids:
            if metadata_chat_ids & allowed_chat_ids:
                return True
            if hit_type in {"paragraph", "relation"}:
                return None
            return False

        source = str(metadata.get("source", "") or hit.get("source", "") or "").strip()
        chat_sources = {str(cls._chat_source(allowed_chat_id) or "") for allowed_chat_id in allowed_chat_ids}
        if hit_type == "episode":
            return source in chat_sources
        if source.startswith("chat_summary:"):
            return source in chat_sources
        return None

    @staticmethod
    def _extend_chat_scope_ids(tokens: set[str], value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                MemorySearchHitProcessingService._extend_chat_scope_ids(tokens, item)
            return

        token = str(value or "").strip()
        if token:
            tokens.add(token)

    @classmethod
    def _metadata_chat_scope_ids(cls, metadata: Dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        for key in ("chat_id", "session_id", "stream_id", "chat_ids", "session_ids", "stream_ids"):
            cls._extend_chat_scope_ids(tokens, metadata.get(key))
        return tokens

    def _filter_hits_by_chat_scope(
        self,
        hits: List[Dict[str, Any]],
        chat_id: str,
        shared_chat_ids: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        allowed_chat_ids = self._resolve_allowed_chat_ids(chat_id, shared_chat_ids)
        if not allowed_chat_ids or self.metadata_store is None:
            return hits

        allowed_indexes: set[int] = set()
        unresolved_paragraph_hashes: List[str] = []
        unresolved_relation_hashes: List[str] = []
        pending_indexes: Dict[int, Dict[str, str]] = {}

        for index, item in enumerate(hits):
            hit = dict(item)
            hit_type = str(hit.get("type", "") or "").strip()
            metadata_decision = self._hit_metadata_matches_chat_scope(hit, allowed_chat_ids)
            if metadata_decision is True:
                allowed_indexes.add(index)
                continue
            if metadata_decision is False:
                continue

            hit_hash = str(hit.get("hash", "") or "").strip()
            if hit_type == "paragraph" and hit_hash:
                unresolved_paragraph_hashes.append(hit_hash)
                pending_indexes[index] = {"type": hit_type, "hash": hit_hash}
                continue
            if hit_type == "relation" and hit_hash:
                unresolved_relation_hashes.append(hit_hash)
                pending_indexes[index] = {"type": hit_type, "hash": hit_hash}

        paragraph_map = self.metadata_store.get_paragraphs_by_hashes(unresolved_paragraph_hashes)
        relation_paragraph_map = self.metadata_store.get_paragraphs_by_relation_hashes(unresolved_relation_hashes)
        for index, pending in pending_indexes.items():
            hit_hash = pending["hash"]
            if pending["type"] == "paragraph":
                if self._paragraph_matches_chat_scope(paragraph_map.get(hit_hash), allowed_chat_ids):
                    allowed_indexes.add(index)
                continue
            if any(
                self._paragraph_matches_chat_scope(paragraph, allowed_chat_ids)
                for paragraph in relation_paragraph_map.get(hit_hash, [])
            ):
                allowed_indexes.add(index)

        return [dict(hit) for index, hit in enumerate(hits) if index in allowed_indexes]

    def _filter_hits_by_retrieval_type_scope(
        self,
        hits: List[Dict[str, Any]],
        *,
        current_stream_id: str = "",
        current_group_id: str = "",
        current_user_id: str = "",
    ) -> List[Dict[str, Any]]:
        """按检索结果类型应用跨聊天流过滤，不改变本聊天流读取自身记忆。"""

        if not hits or not self._has_enabled_retrieval_type_filter():
            return hits
        current_context = self._current_retrieval_filter_context(
            stream_id=current_stream_id,
            group_id=current_group_id,
            user_id=current_user_id,
        )

        paragraph_hashes: List[str] = []
        relation_hashes: List[str] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            item_hash = str(item.get("hash", "") or "").strip()
            if not item_hash:
                continue
            if item_type == "paragraph":
                paragraph_hashes.append(item_hash)
            elif item_type == "relation":
                relation_hashes.append(item_hash)

        paragraph_map: Dict[str, Dict[str, Any]] = {}
        relation_paragraph_map: Dict[str, List[Dict[str, Any]]] = {}
        if self.metadata_store is not None:
            paragraph_map = self.metadata_store.get_paragraphs_by_hashes(paragraph_hashes)
            relation_paragraph_map = self.metadata_store.get_paragraphs_by_relation_hashes(relation_hashes)

        filtered: List[Dict[str, Any]] = []
        for item in hits:
            contexts = self._retrieval_filter_contexts_for_hit(
                item,
                paragraph_map=paragraph_map,
                relation_paragraph_map=relation_paragraph_map,
            )
            if any(self._retrieval_filter_context_is_current_source(context, current_context) for context in contexts):
                filtered.append(dict(item))
                continue
            if any(self._retrieval_filter_context_allowed(context) for context in contexts):
                filtered.append(dict(item))
        return filtered

    def _has_enabled_retrieval_type_filter(self) -> bool:
        retrieval_config = self._retrieval_type_filter_root()
        if not retrieval_config:
            return False
        for kind in ("chat_stream", "chat_summary", "episode"):
            type_config = retrieval_config.get(kind)
            if isinstance(type_config, dict) and bool(type_config.get("enabled", False)):
                return True
        return False

    def _retrieval_type_filter_root(self) -> Dict[str, Any]:
        filter_config = self._cfg("filter", {}) or {}
        if not isinstance(filter_config, dict):
            return {}
        retrieval_config = filter_config.get("retrieval") or {}
        return retrieval_config if isinstance(retrieval_config, dict) else {}

    def _retrieval_type_filter_config(self, kind: str) -> Dict[str, Any]:
        retrieval_config = self._retrieval_type_filter_root()
        type_config = retrieval_config.get(str(kind or "").strip())
        return type_config if isinstance(type_config, dict) else {}

    def _retrieval_filter_contexts_for_hit(
        self,
        hit: Dict[str, Any],
        *,
        paragraph_map: Dict[str, Dict[str, Any]],
        relation_paragraph_map: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, str]]:
        hit_type = str(hit.get("type", "") or "").strip()
        hit_hash = str(hit.get("hash", "") or "").strip()

        if hit_type == "paragraph" and hit_hash in paragraph_map:
            return [self._retrieval_filter_context_from_paragraph(paragraph_map[hit_hash])]

        if hit_type == "relation" and hit_hash in relation_paragraph_map:
            contexts = [
                self._retrieval_filter_context_from_paragraph(paragraph)
                for paragraph in relation_paragraph_map.get(hit_hash, [])
                if isinstance(paragraph, dict)
            ]
            if contexts:
                return contexts

        return [self._retrieval_filter_context_from_hit(hit)]

    def _retrieval_filter_context_from_hit(self, hit: Dict[str, Any]) -> Dict[str, str]:
        metadata = coerce_metadata_dict(hit.get("metadata"))
        source = str(metadata.get("source", "") or hit.get("source", "") or "").strip()
        source_type = str(metadata.get("source_type", "") or "").strip()
        hit_type = str(hit.get("type", "") or "").strip()
        stream_id = str(metadata.get("chat_id", "") or "").strip()
        if not stream_id:
            stream_id = self._source_stream_id(source)
        return self._retrieval_filter_context(
            kind=self._retrieval_filter_kind(hit_type=hit_type, source_type=source_type, source=source),
            stream_id=stream_id,
        )

    def _retrieval_filter_context_from_paragraph(self, paragraph: Dict[str, Any]) -> Dict[str, str]:
        metadata = coerce_metadata_dict(paragraph.get("metadata"))
        source = str(paragraph.get("source", "") or metadata.get("source", "") or "").strip()
        source_type = str(metadata.get("source_type", "") or "").strip()
        stream_id = str(metadata.get("chat_id", "") or "").strip()
        if not stream_id:
            stream_id = self._source_stream_id(source)
        return self._retrieval_filter_context(
            kind=self._retrieval_filter_kind(hit_type="paragraph", source_type=source_type, source=source),
            stream_id=stream_id,
        )

    @staticmethod
    def _retrieval_filter_kind(*, hit_type: str, source_type: str, source: str) -> str:
        if str(hit_type or "").strip() == "episode":
            return "episode"
        clean_source_type = str(source_type or "").strip()
        clean_source = str(source or "").strip()
        if clean_source_type == "chat_summary" or clean_source.startswith("chat_summary:"):
            return "chat_summary"
        if clean_source_type in {"chat_history", "chat_stream", "maibot.chat_history"}:
            return "chat_stream"
        if clean_source.startswith("chat_stream:") or clean_source.startswith("maibot.chat_history:"):
            return "chat_stream"
        return ""

    @staticmethod
    def _source_stream_id(source: str) -> str:
        token = str(source or "").strip()
        for prefix in ("chat_summary:", "chat_stream:", "maibot.chat_history:"):
            if token.startswith(prefix):
                return token[len(prefix) :].strip()
        return ""

    @staticmethod
    def _retrieval_filter_context(*, kind: str, stream_id: str) -> Dict[str, str]:
        stream_token = str(stream_id or "").strip()
        group_id = ""
        user_id = ""
        if stream_token:
            session = chat_manager.get_existing_session_by_session_id(stream_token)
            if session is not None:
                group_id = str(getattr(session, "group_id", "") or "").strip()
                user_id = str(getattr(session, "user_id", "") or "").strip()
        return {
            "kind": str(kind or "").strip(),
            "stream_id": stream_token,
            "group_id": group_id,
            "user_id": user_id,
        }

    def _current_retrieval_filter_context(
        self,
        *,
        stream_id: str,
        group_id: str,
        user_id: str,
    ) -> Dict[str, str]:
        resolved_context = self._retrieval_filter_context(kind="", stream_id=stream_id)
        resolved_context["group_id"] = str(group_id or "").strip() or resolved_context["group_id"]
        resolved_context["user_id"] = str(user_id or "").strip() or resolved_context["user_id"]
        return resolved_context

    @staticmethod
    def _retrieval_filter_context_is_current_source(
        context: Dict[str, str],
        current_context: Dict[str, str],
    ) -> bool:
        current_stream_id = str(current_context.get("stream_id", "") or "").strip()
        source_stream_id = str(context.get("stream_id", "") or "").strip()
        if current_stream_id and source_stream_id and current_stream_id == source_stream_id:
            return True

        current_group_id = str(current_context.get("group_id", "") or "").strip()
        source_group_id = str(context.get("group_id", "") or "").strip()
        if current_group_id and source_group_id and current_group_id == source_group_id:
            return True

        current_user_id = str(current_context.get("user_id", "") or "").strip()
        source_user_id = str(context.get("user_id", "") or "").strip()
        current_is_private = bool(current_user_id) and not current_group_id
        source_is_private = bool(source_user_id) and not source_group_id
        return current_is_private and source_is_private and current_user_id == source_user_id

    def _retrieval_filter_context_allowed(self, context: Dict[str, str]) -> bool:
        kind = str(context.get("kind", "") or "").strip()
        if not kind:
            return True
        type_config = self._retrieval_type_filter_config(kind)
        if not type_config or not bool(type_config.get("enabled", False)):
            return True
        return self._chat_filter_config_allows(
            type_config,
            stream_id=str(context.get("stream_id", "") or "").strip(),
            group_id=str(context.get("group_id", "") or "").strip(),
            user_id=str(context.get("user_id", "") or "").strip(),
            default_when_empty=True,
        )

    @classmethod
    def _normalize_search_time_bound(cls, value: Any, *, is_end: bool) -> tuple[Optional[float], Optional[str]]:
        if value in {None, ""}:
            return None, None
        if isinstance(value, (int, float)):
            ts = float(value)
            return ts, format_timestamp(ts)

        text = str(value or "").strip()
        if not text:
            return None, None

        numeric = cls._optional_float(text)
        if numeric is not None:
            return numeric, format_timestamp(numeric)

        try:
            ts = parse_query_datetime_to_timestamp(text, is_end=is_end)
        except ValueError as exc:
            raise ValueError(f"时间参数错误: {exc}") from exc
        return ts, text

    @classmethod
    def _normalize_search_time_window(cls, time_start: Any, time_end: Any) -> _NormalizedSearchTimeWindow:
        numeric_start, query_start = cls._normalize_search_time_bound(time_start, is_end=False)
        numeric_end, query_end = cls._normalize_search_time_bound(time_end, is_end=True)
        if numeric_start is not None and numeric_end is not None and numeric_start > numeric_end:
            raise ValueError("时间参数错误: time_start 不能晚于 time_end")
        return _NormalizedSearchTimeWindow(
            numeric_start=numeric_start,
            numeric_end=numeric_end,
            query_start=query_start,
            query_end=query_end,
        )

    @staticmethod
    def _retrieval_result_hit(item: RetrievalResult) -> Dict[str, Any]:
        payload = item.to_dict()
        return {
            "hash": payload.get("hash", ""),
            "content": payload.get("content", ""),
            "score": payload.get("score", 0.0),
            "type": payload.get("type", ""),
            "source": payload.get("source", ""),
            "metadata": payload.get("metadata", {}) or {},
        }

    @staticmethod
    def _episode_hit(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "episode",
            "episode_id": str(row.get("episode_id", "") or ""),
            "title": str(row.get("title", "") or ""),
            "content": str(row.get("summary", "") or ""),
            "score": float(row.get("lexical_score", 0.0) or 0.0),
            "source": "episode",
            "metadata": {
                "participants": row.get("participants", []) or [],
                "keywords": row.get("keywords", []) or [],
                "source": row.get("source"),
                "event_time_start": row.get("event_time_start"),
                "event_time_end": row.get("event_time_end"),
            },
        }

    @staticmethod
    def _summary(hits: Sequence[Dict[str, Any]]) -> str:
        if not hits:
            return ""
        lines = []
        for index, item in enumerate(hits[:5], start=1):
            content = str(item.get("content", "") or "").strip().replace("\n", " ")
            lines.append(f"{index}. {(content[:120] + '...') if len(content) > 120 else content}")
        return "\n".join(lines)

    @staticmethod
    def _filter_hits(hits: List[Dict[str, Any]], person_id: str) -> List[Dict[str, Any]]:
        if not person_id:
            return hits
        filtered = []
        for item in hits:
            metadata = item.get("metadata", {}) or {}
            if person_id in (metadata.get("person_ids", []) or []):
                filtered.append(item)
                continue
            if person_id and person_id in str(item.get("content", "") or ""):
                filtered.append(item)
        return filtered or hits

    def _filter_active_relation_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.metadata_store is None:
            return hits
        relation_hashes: List[str] = []
        paragraph_relation_cache: Dict[str, List[str]] = {}
        paragraph_hashes: List[str] = []
        seen_relation_hashes: set[str] = set()

        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            item_hash = str(item.get("hash", "") or "").strip()
            if item_type == "relation" and item_hash and item_hash not in seen_relation_hashes:
                seen_relation_hashes.add(item_hash)
                relation_hashes.append(item_hash)
                continue
            if item_type != "paragraph" or not item_hash:
                continue
            paragraph_hashes.append(item_hash)
            linked_relations = self.metadata_store.get_paragraph_relations(item_hash)
            linked_hashes: List[str] = []
            for relation in linked_relations:
                linked_hash = str(relation.get("hash", "") or "").strip()
                if not linked_hash:
                    continue
                linked_hashes.append(linked_hash)
                if linked_hash not in seen_relation_hashes:
                    seen_relation_hashes.add(linked_hash)
                    relation_hashes.append(linked_hash)
            if linked_hashes:
                paragraph_relation_cache[item_hash] = linked_hashes

        marks_by_paragraph, _ = self._load_paragraph_stale_marks(paragraph_hashes)
        stale_relation_hashes = tokens(
            mark.get("relation_hash", "")
            for marks in marks_by_paragraph.values()
            for mark in marks
            if isinstance(mark, dict)
        )
        for relation_hash in stale_relation_hashes:
            if relation_hash in seen_relation_hashes:
                continue
            seen_relation_hashes.add(relation_hash)
            relation_hashes.append(relation_hash)

        if not relation_hashes and not marks_by_paragraph:
            return hits

        status_map = self.metadata_store.get_relation_status_batch(relation_hashes)
        filtered: List[Dict[str, Any]] = []
        for item in hits:
            item_type = str(item.get("type", "") or "").strip()
            if item_type == "paragraph":
                paragraph_hash = str(item.get("hash", "") or "").strip()
                if self._paragraph_hidden_by_stale_marks(
                    paragraph_hash,
                    marks_by_paragraph=marks_by_paragraph,
                    relation_status_map=status_map,
                ):
                    continue
                linked_hashes = paragraph_relation_cache.get(paragraph_hash, [])
                if not linked_hashes:
                    filtered.append(item)
                    continue
                if any(
                    not bool((status_map.get(linked_hash) or {}).get("is_inactive")) for linked_hash in linked_hashes
                ):
                    filtered.append(item)
                continue
            if item_type != "relation":
                filtered.append(item)
                continue
            hash_value = str(item.get("hash", "") or "").strip()
            status = status_map.get(hash_value) if isinstance(status_map, dict) else None
            if status is None:
                continue
            if bool(status.get("is_inactive")):
                continue
            filtered.append(item)
        return filtered

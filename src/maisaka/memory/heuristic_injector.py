"""Maisaka 启发式长期记忆自然拉起服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, Sequence

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import AtComponent, ReplyComponent
from src.common.logger import get_logger
from src.common.message_repository import count_messages, find_messages
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.person_info.person_info import get_person_id
from src.services.llm_service import LLMServiceClient
from src.services.memory_service import MemoryHit, memory_service

logger = get_logger("maisaka_heuristic_memory")

HEURISTIC_MEMORY_REFERENCE_MARKER = "【启发式记忆-内部参考】"
_SOURCE_CHAT_SUMMARY_PREFIX = "chat_summary:"
_SOURCE_PERSON_FACT_PREFIX = "person_fact:"


def _get_int_config(config: Any, name: str, default: int) -> int:
    value = getattr(config, name, default)
    if value in {None, ""}:
        return int(default)
    return int(value)


@dataclass
class HeuristicMemoryRecallState:
    """单个聊天流的启发式记忆运行时状态。"""

    last_recall_at: float = 0.0
    last_message_count: int = 0
    cached_reference: str = ""
    cache_expires_at: float = 0.0


@dataclass(frozen=True)
class HeuristicMemoryContext:
    """一次启发式记忆召回的上下文。"""

    session: BotChatSession
    recent_messages: list[SessionMessage]
    active_person_ids: set[str] = field(default_factory=set)
    total_message_count: int = 0


class HeuristicMemoryInjector:
    """根据当前聊天流印象自然拉起长期记忆。"""

    def __init__(self) -> None:
        self._states: dict[str, HeuristicMemoryRecallState] = {}
        self._impression_client = LLMServiceClient(
            task_name="utils",
            request_type="heuristic_memory_impression",
        )

    async def build_injection_message(
        self,
        *,
        session_id: str,
    ) -> str:
        """构造给 Planner/Replyer 共享的一次性启发式记忆参考。"""

        config = global_config.a_memorix.integration
        if not bool(getattr(config, "heuristic_memory_recall_enabled", False)):
            self.clear_session_reference(session_id)
            return ""

        session = chat_manager.get_existing_session_by_session_id(session_id)
        if session is None:
            logger.debug(f"启发式记忆跳过：无法解析真实聊天流 session_id={session_id!r}")
            return ""

        window_size = max(1, int(getattr(config, "heuristic_memory_recall_window_size", 20) or 20))
        total_message_count = count_messages(session_id=session.session_id)
        if total_message_count < window_size:
            self.clear_session_reference(session.session_id)
            return ""

        state = self._states.setdefault(session.session_id, HeuristicMemoryRecallState())
        now = time()
        cache_ttl = max(0, _get_int_config(config, "heuristic_memory_recall_cache_ttl_seconds", 300))
        if state.cached_reference and cache_ttl > 0 and now < state.cache_expires_at:
            return state.cached_reference

        if not self._can_trigger(
            state=state,
            total_message_count=total_message_count,
            now=now,
            min_interval_seconds=max(
                0,
                _get_int_config(config, "heuristic_memory_recall_min_interval_seconds", 180),
            ),
            min_new_messages=max(
                1,
                _get_int_config(config, "heuristic_memory_recall_min_new_messages", 60),
            ),
        ):
            self.clear_session_reference(session.session_id)
            return ""

        recent_messages = find_messages(
            session_id=session.session_id,
            limit=window_size,
            limit_mode="latest",
            filter_command=True,
        )
        if len(recent_messages) < window_size:
            self.clear_session_reference(session.session_id)
            return ""

        context = HeuristicMemoryContext(
            session=session,
            recent_messages=recent_messages,
            active_person_ids=self._collect_active_person_ids(recent_messages),
            total_message_count=total_message_count,
        )
        try:
            impression = await self._build_chat_impression(context)
            if not impression:
                self.clear_session_reference(session.session_id)
                return ""
            hits = await self._search_related_memory(impression, context)
            reference = self._format_reference(
                hits,
                max_chars=max(100, _get_int_config(config, "heuristic_memory_recall_max_chars", 900)),
            )
        except Exception as exc:
            self.clear_session_reference(session.session_id)
            logger.debug(f"启发式记忆自然拉起失败，已跳过: {exc}", exc_info=True)
            return ""

        state.last_recall_at = now
        state.last_message_count = total_message_count
        state.cached_reference = reference
        state.cache_expires_at = now + cache_ttl if cache_ttl > 0 else 0.0
        if reference:
            logger.info(
                f"启发式记忆自然拉起成功: session_id={session.session_id} "
                f"hits={len(hits)} total_messages={total_message_count}"
            )
        return reference

    def clear_session_reference(self, session_id: str) -> None:
        """清理当前会话的本轮注入参考。"""

        state = self._states.get(str(session_id or "").strip())
        if state is None:
            return
        state.cached_reference = ""
        state.cache_expires_at = 0.0

    @staticmethod
    def _can_trigger(
        *,
        state: HeuristicMemoryRecallState,
        total_message_count: int,
        now: float,
        min_interval_seconds: int,
        min_new_messages: int,
    ) -> bool:
        if state.last_message_count <= 0:
            return True
        if min_interval_seconds > 0 and now - state.last_recall_at < min_interval_seconds:
            return False
        return total_message_count - state.last_message_count >= min_new_messages

    async def _build_chat_impression(self, context: HeuristicMemoryContext) -> str:
        prompt = load_prompt(
            "heuristic_memory_impression",
            chat_identity=self._format_chat_identity(context.session),
            message_window=self._format_message_window(context.recent_messages),
        )
        result = await self._impression_client.generate_response(prompt)
        return str(result.response or "").strip()

    async def _search_related_memory(
        self,
        impression: str,
        context: HeuristicMemoryContext,
    ) -> list[MemoryHit]:
        config = global_config.a_memorix.integration
        limit = max(1, int(getattr(config, "heuristic_memory_recall_limit", 3) or 3))
        search_limit = max(limit * 4, limit)
        cross_chat_enabled = bool(getattr(config, "heuristic_memory_cross_chat_enabled", False))
        result = await memory_service.search(
            impression,
            limit=min(20, search_limit),
            mode="search",
            chat_id="" if cross_chat_enabled else context.session.session_id,
            person_id="",
            respect_filter=not cross_chat_enabled,
            user_id=str(context.session.user_id or ""),
            group_id=str(context.session.group_id or ""),
        )
        if not result.success or result.filtered:
            return []

        resolved_sources = await self._resolve_hit_sources_by_hash(result.hits)
        filtered: list[MemoryHit] = []
        seen_hashes: set[str] = set()
        for hit in result.hits:
            resolved_source = resolved_sources.get(hit.hash_value, "")
            if not self._is_hit_allowed(hit, context, resolved_source=resolved_source):
                continue
            dedup_key = hit.hash_value or hit.content
            if dedup_key in seen_hashes:
                continue
            seen_hashes.add(dedup_key)
            filtered.append(hit)
            if len(filtered) >= limit:
                break
        return filtered

    async def _resolve_hit_sources_by_hash(self, hits: Sequence[MemoryHit]) -> dict[str, str]:
        paragraph_hashes = [hit.hash_value for hit in hits if hit.hash_value and not self._hit_has_scope_hint(hit)]
        if not paragraph_hashes:
            return {}

        try:
            payload = await memory_service.delete_admin(
                action="preview",
                mode="paragraph",
                selector={"hashes": paragraph_hashes},
                timeout_ms=10000,
            )
        except Exception as exc:
            logger.debug(f"启发式记忆命中来源解析失败，已按未知来源处理: {exc}")
            return {}
        if not isinstance(payload, dict) or not bool(payload.get("success", False)):
            return {}

        sources: dict[str, str] = {}
        for item in payload.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_type", "") or "").strip() != "paragraph":
                continue
            paragraph_hash = str(item.get("item_hash", "") or "").strip()
            source = str(item.get("source", "") or "").strip()
            if paragraph_hash and source:
                sources[paragraph_hash] = source
        return sources

    @staticmethod
    def _hit_has_scope_hint(hit: MemoryHit) -> bool:
        metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
        if any(metadata.get(key) for key in ("source", "chat_id", "person_id", "person_ids")):
            return True
        source = str(hit.source or "").strip()
        return source.startswith(_SOURCE_CHAT_SUMMARY_PREFIX) or source.startswith(_SOURCE_PERSON_FACT_PREFIX)

    def _is_hit_allowed(
        self,
        hit: MemoryHit,
        context: HeuristicMemoryContext,
        *,
        resolved_source: str = "",
    ) -> bool:
        metadata = hit.metadata if isinstance(hit.metadata, dict) else {}
        source = str(metadata.get("source") or resolved_source or hit.source or "").strip()
        source_type = str(metadata.get("source_type") or "").strip()
        chat_id = str(metadata.get("chat_id") or "").strip()
        person_id = str(metadata.get("person_id") or "").strip()
        if not person_id:
            person_ids = metadata.get("person_ids")
            if isinstance(person_ids, list):
                person_id = next((str(item).strip() for item in person_ids if str(item).strip()), "")
        if not person_id and source.startswith(_SOURCE_PERSON_FACT_PREFIX):
            person_id = source[len(_SOURCE_PERSON_FACT_PREFIX) :].strip()

        if hit.hit_type == "episode":
            episode_source = str(metadata.get("source") or "").strip()
            return episode_source == f"{_SOURCE_CHAT_SUMMARY_PREFIX}{context.session.session_id}"

        if source_type == "person_fact" or source.startswith(_SOURCE_PERSON_FACT_PREFIX):
            return bool(person_id and person_id in context.active_person_ids)

        if source_type == "chat_summary" or source.startswith(_SOURCE_CHAT_SUMMARY_PREFIX):
            source_session_id = self._resolve_source_session_id(source=source, chat_id=chat_id)
            return self._is_chat_memory_allowed(source_session_id, context.session)

        if chat_id:
            return self._is_chat_memory_allowed(chat_id, context.session)
        return False

    @staticmethod
    def _resolve_source_session_id(*, source: str, chat_id: str) -> str:
        if chat_id:
            return chat_id
        if source.startswith(_SOURCE_CHAT_SUMMARY_PREFIX):
            return source[len(_SOURCE_CHAT_SUMMARY_PREFIX) :].strip()
        return ""

    def _is_chat_memory_allowed(self, source_session_id: str, current_session: BotChatSession) -> bool:
        clean_source_session_id = str(source_session_id or "").strip()
        if not clean_source_session_id:
            return False
        if clean_source_session_id == current_session.session_id:
            return True

        config = global_config.a_memorix.integration
        if not bool(getattr(config, "heuristic_memory_cross_chat_enabled", False)):
            return False

        source_session = chat_manager.get_existing_session_by_session_id(clean_source_session_id)
        if source_session is None:
            return False

        if source_session.is_group_session and not current_session.is_group_session:
            return bool(getattr(config, "heuristic_memory_group_to_private_enabled", False))
        if not source_session.is_group_session and current_session.is_group_session:
            return bool(getattr(config, "heuristic_memory_private_to_group_enabled", False))
        return True

    @staticmethod
    def _collect_active_person_ids(messages: Sequence[SessionMessage]) -> set[str]:
        person_ids: set[str] = set()
        for message in messages:
            platform = str(getattr(message, "platform", "") or "").strip()
            user_id = str(getattr(message.message_info.user_info, "user_id", "") or "").strip()
            if platform and user_id:
                person_ids.add(get_person_id(platform, user_id))

            raw_message = getattr(message, "raw_message", None)
            for component in getattr(raw_message, "components", []) or []:
                if isinstance(component, AtComponent):
                    target_user_id = str(component.target_user_id or "").strip()
                    if platform and target_user_id:
                        person_ids.add(get_person_id(platform, target_user_id))
                elif isinstance(component, ReplyComponent):
                    target_user_id = str(component.target_message_sender_id or "").strip()
                    if platform and target_user_id:
                        person_ids.add(get_person_id(platform, target_user_id))
        return person_ids

    @staticmethod
    def _format_chat_identity(session: BotChatSession) -> str:
        chat_type = "group" if session.is_group_session else "private"
        display_name = chat_manager.get_session_name(session.session_id) or session.session_id
        parts = [
            f"chat_type: {chat_type}",
            f"display_name: {display_name}",
            f"platform: {session.platform}",
        ]
        if session.group_id:
            parts.append(f"group_id: {session.group_id}")
        if session.user_id:
            parts.append(f"user_id: {session.user_id}")
        return "\n".join(parts)

    @classmethod
    def _format_message_window(cls, messages: Sequence[SessionMessage]) -> str:
        lines: list[str] = []
        for message in messages:
            sender = cls._message_sender_name(message)
            text = str(getattr(message, "processed_plain_text", "") or "").strip()
            if not text:
                continue
            text = text.replace("\r", " ").replace("\n", " ")
            if len(text) > 220:
                text = text[:220].rstrip() + "..."
            lines.append(f"- {sender}: {text}")
        return "\n".join(lines) if lines else "no text messages available"

    @staticmethod
    def _message_sender_name(message: SessionMessage) -> str:
        user_info = message.message_info.user_info
        return str(
            getattr(user_info, "user_cardname", "")
            or getattr(user_info, "user_nickname", "")
            or getattr(user_info, "user_id", "")
            or "unknown_user"
        ).strip()

    @staticmethod
    def _format_reference(hits: Sequence[MemoryHit], *, max_chars: int) -> str:
        if not hits:
            return ""
        lines = [
            HEURISTIC_MEMORY_REFERENCE_MARKER,
            "Internal long-term memory recalled from the current chat impression. Use it only as reasoning context; do not quote it verbatim to the user.",
            "",
        ]
        for index, hit in enumerate(hits, start=1):
            content = str(hit.content or "").strip().replace("\r", " ").replace("\n", " ")
            if not content:
                continue
            if len(content) > 180:
                content = content[:180].rstrip() + "..."
            lines.append(f"{index}. {content}")

        reference = "\n".join(lines).strip()
        if len(reference) <= max_chars:
            return reference
        return reference[:max_chars].rstrip() + "..."


heuristic_memory_injector = HeuristicMemoryInjector()

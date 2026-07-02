from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from json_repair import repair_json
from typing import Any, List, Optional

import asyncio
import json
import pickle
import time

from src.common.logger import get_logger
from src.common.message_repository import count_messages, find_messages
from src.chat.utils.utils import is_bot_self
from src.config.config import global_config
from src.person_info.person_info import Person, get_person_id, store_person_memory_from_answer
from src.services import memory_service as memory_service_module
from src.services.memory_service import memory_service

logger = get_logger("memory_flow_service")


@dataclass
class PersonFactEvidence:
    target_messages: List[Any]
    context_messages: List[Any]


class PersonFactWritebackService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._extractor: Any | None = None

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(self._worker_loop(), name="A_Memorix.person_fact_writeback")

    async def shutdown(self) -> None:
        self._stopping = True
        worker = self._worker_task
        self._worker_task = None
        if worker is None:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"关闭人物事实写回 worker 失败: {exc}")

    async def enqueue(self, message: Any) -> None:
        if not bool(global_config.a_memorix.integration.person_fact_writeback_enabled):
            return
        if self._stopping:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("人物事实写回队列已满，跳过本次回复")

    async def _worker_loop(self) -> None:
        try:
            while not self._stopping:
                message = await self._queue.get()
                try:
                    await self._handle_message(message)
                except Exception as exc:
                    logger.warning(f"人物事实写回处理失败: {exc}", exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, message: Any) -> None:
        reply_text = str(getattr(message, "processed_plain_text", "") or "").strip()
        if not reply_text:
            return
        if self._looks_ephemeral(reply_text):
            return

        target_person = self._resolve_target_person(message)
        if target_person is None or not target_person.is_known:
            return

        evidence = self._collect_user_evidence(message, target_person)
        if not evidence.target_messages:
            return
        user_evidence_text = self._format_user_evidence(evidence)

        facts = await self._extract_facts(target_person, reply_text, user_evidence_text)
        if not facts:
            return

        session_id = str(
            getattr(message, "session_id", "")
            or getattr(getattr(message, "session", None), "session_id", "")
            or ""
        ).strip()
        if not session_id:
            return

        person_name = str(
            getattr(target_person, "person_name", "")
            or getattr(target_person, "nickname", "")
            or ""
        ).strip()
        if not person_name:
            return

        evidence_message_ids = [
            str(getattr(item, "message_id", "") or "").strip()
            for item in evidence.target_messages
            if str(getattr(item, "message_id", "") or "").strip()
        ]
        for fact in facts:
            await store_person_memory_from_answer(
                person_name,
                fact,
                session_id,
                person_id=str(getattr(target_person, "person_id", "") or "").strip(),
                evidence_source="user_supported",
                evidence_message_ids=evidence_message_ids,
            )

    def _resolve_target_person(self, message: Any) -> Optional[Person]:
        session = getattr(message, "session", None)
        session_platform = str(getattr(session, "platform", "") or getattr(message, "platform", "") or "").strip()
        session_user_id = str(getattr(session, "user_id", "") or "").strip()
        group_id = str(getattr(session, "group_id", "") or "").strip()

        if session_platform and session_user_id and not group_id:
            if is_bot_self(session_platform, session_user_id):
                return None
            person_id = get_person_id(session_platform, session_user_id)
            person = Person(person_id=person_id)
            return person if person.is_known else None

        reply_to = str(getattr(message, "reply_to", "") or "").strip()
        if reply_to:
            try:
                replies = find_messages(message_id=reply_to, limit=1)
            except Exception as exc:
                logger.debug(f"查询 reply_to 目标失败: {exc}")
                replies = []
            if replies:
                person = self._person_from_user_message(replies[0], fallback_platform=session_platform)
                if person is not None:
                    return person

        session_id = str(
            getattr(message, "session_id", "")
            or getattr(session, "session_id", "")
            or ""
        ).strip()
        timestamp = self._extract_message_timestamp(message)
        if not session_id:
            return None
        try:
            candidates = find_messages(
                session_id=session_id,
                before_time=timestamp,
                limit=6,
                limit_mode="latest",
                filter_bot=True,
            )
        except Exception as exc:
            logger.debug(f"查询最近用户消息目标失败: {exc}")
            return None
        for candidate in reversed(candidates):
            person = self._person_from_user_message(candidate, fallback_platform=session_platform)
            if person is not None:
                return person
        return None

    @staticmethod
    def _person_from_user_message(message: Any, *, fallback_platform: str = "") -> Optional[Person]:
        platform = str(getattr(message, "platform", "") or fallback_platform or "").strip()
        user_info = getattr(getattr(message, "message_info", None), "user_info", None)
        user_id = str(getattr(user_info, "user_id", "") or getattr(message, "user_id", "") or "").strip()

        if not platform or not user_id or is_bot_self(platform, user_id):
            return None
        person_id = get_person_id(platform, user_id)
        person = Person(person_id=person_id)
        return person if person.is_known else None

    def _collect_user_evidence(self, message: Any, person: Person) -> PersonFactEvidence:
        session = getattr(message, "session", None)
        session_id = str(
            getattr(message, "session_id", "")
            or getattr(session, "session_id", "")
            or ""
        ).strip()
        if not session_id:
            return PersonFactEvidence(target_messages=[], context_messages=[])

        target_messages: List[Any] = []
        seen_ids = set()
        timestamp = self._extract_message_timestamp(message)

        reply_to = str(getattr(message, "reply_to", "") or "").strip()
        if reply_to:
            try:
                replies = find_messages(message_id=reply_to, limit=1)
            except Exception as exc:
                logger.debug("查询人物事实 reply_to 证据失败: %s", exc)
                replies = []
            target_messages.extend(self._filter_target_user_messages(replies, person, seen_ids))

        if target_messages:
            context_messages = self._collect_context_messages(session_id=session_id, trigger_message=message, limit=8)
            return PersonFactEvidence(target_messages=target_messages[:3], context_messages=context_messages)

        try:
            candidates = find_messages(
                session_id=session_id,
                before_time=timestamp,
                limit=6,
                limit_mode="latest",
                filter_bot=True,
            )
        except Exception as exc:
            logger.debug("查询人物事实近期用户证据失败: %s", exc)
            return PersonFactEvidence(target_messages=[], context_messages=[])
        target_messages = self._filter_target_user_messages(candidates, person, seen_ids)
        if len(target_messages) > 3:
            target_messages = target_messages[-3:]
        return PersonFactEvidence(target_messages=target_messages, context_messages=candidates)

    def _collect_context_messages(self, *, session_id: str, trigger_message: Any, limit: int = 8) -> List[Any]:
        timestamp = self._extract_message_timestamp(trigger_message)
        try:
            return find_messages(
                session_id=session_id,
                before_time=timestamp,
                limit=max(1, int(limit)),
                limit_mode="latest",
            )
        except Exception as exc:
            logger.debug("查询人物事实邻近上下文失败: %s", exc)
            return []

    @staticmethod
    def _extract_message_timestamp(message: Any) -> float | None:
        raw_timestamp = getattr(message, "timestamp", None)
        if hasattr(raw_timestamp, "timestamp") and callable(raw_timestamp.timestamp):
            try:
                return float(raw_timestamp.timestamp())
            except Exception:
                return None
        if isinstance(raw_timestamp, (int, float)):
            return float(raw_timestamp)
        return None

    @staticmethod
    def _filter_target_user_messages(messages: List[Any], person: Person, seen_ids: set) -> List[Any]:
        filtered: List[Any] = []
        target_person_id = str(getattr(person, "person_id", "") or "").strip()
        for item in messages:
            platform = str(getattr(item, "platform", "") or "").strip()
            user_info = getattr(getattr(item, "message_info", None), "user_info", None)
            user_id = str(getattr(user_info, "user_id", "") or getattr(item, "user_id", "") or "").strip()
            if not platform or not user_id or is_bot_self(platform, user_id):
                continue
            if target_person_id and get_person_id(platform, user_id) != target_person_id:
                continue
            text = str(getattr(item, "processed_plain_text", "") or "").strip()
            if not text:
                continue
            message_id = str(getattr(item, "message_id", "") or "").strip()
            dedup_key = message_id or f"{platform}:{user_id}:{text}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)
            filtered.append(item)
        return filtered

    @staticmethod
    def _format_user_evidence(evidence: PersonFactEvidence) -> str:
        target_lines: List[str] = []
        for item in evidence.target_messages[:3]:
            line = PersonFactWritebackService._format_evidence_message_line(item, include_sender=False)
            if line:
                target_lines.append(f"- {line}")

        context_lines: List[str] = []
        target_ids = {
            str(getattr(item, "message_id", "") or "").strip()
            for item in evidence.target_messages
            if str(getattr(item, "message_id", "") or "").strip()
        }
        for item in evidence.context_messages[:8]:
            line = PersonFactWritebackService._format_evidence_message_line(
                item,
                include_sender=True,
                mark_target=str(getattr(item, "message_id", "") or "").strip() in target_ids,
            )
            if line:
                context_lines.append(f"- {line}")

        parts: List[str] = []
        if target_lines:
            parts.append("目标用户原始发言（事实值必须来自这里）：\n" + "\n".join(target_lines))
        if context_lines:
            parts.append("邻近上下文（只用于理解省略、追问和指代，不能单独作为事实来源）：\n" + "\n".join(context_lines))
        return "\n\n".join(parts)

    @staticmethod
    def _format_evidence_message_line(item: Any, *, include_sender: bool, mark_target: bool = False) -> str:
        text = str(getattr(item, "processed_plain_text", "") or "").strip()
        if not text:
            return ""
        if not include_sender:
            return text

        user_info = getattr(getattr(item, "message_info", None), "user_info", None)
        sender_name = str(
            getattr(user_info, "user_cardname", "")
            or getattr(user_info, "user_nickname", "")
            or getattr(user_info, "user_id", "")
            or getattr(item, "user_id", "")
            or ""
        ).strip()
        prefix = f"{sender_name}: " if sender_name else ""
        target_marker = " [目标用户发言]" if mark_target else ""
        return f"{prefix}{text}{target_marker}"

    async def _extract_facts(self, person: Person, reply_text: str, user_evidence_text: str) -> List[str]:
        person_name = str(getattr(person, "person_name", "") or getattr(person, "nickname", "") or person.person_id)
        prompt = f"""你要从用户原始发言中提取“关于{person_name}的稳定事实”。

目标人物：{person_name}
用户原始发言证据：
{user_evidence_text}

机器人回复：
{reply_text}

请只提取满足以下条件的事实：
1. 事实值必须能被“目标用户原始发言”直接支持，不能只来自机器人回复或邻近上下文。
2. 明确是关于目标人物本人的信息。
3. 具有相对稳定性，可以作为长期记忆保存。
4. 用简洁中文陈述句表达。
5. 如果用户原始发言中出现“我/我的/自己”，默认指目标人物，请先改写成关于目标人物的第三人称事实再输出。
6. 邻近上下文只能用于补全目标用户短答、省略、被追问的问题或代词指向。例如上下文问“你对什么过敏？”，目标用户答“青霉素”，可以提取“目标人物对青霉素过敏”。
7. 如果完整事实只能靠机器人回复或邻近上下文中的新增事实值成立，而目标用户原始发言没有确认或给出该事实值，不要提取。

不要提取：
- 机器人的情绪、计划、临时动作、客套话
- 仅由机器人提出的建议、猜测、玩笑、回忆或承诺
- 只适用于当前时刻的短期安排
- 不确定、猜测、反问
- 与目标人物无关的信息

严格输出 JSON 数组，例如：
["他喜欢深夜打游戏", "他养了一只猫"]
如果没有可写入的事实，输出 []"""
        try:
            if self._extractor is None:
                from src.services.llm_service import LLMServiceClient

                self._extractor = LLMServiceClient(task_name="utils", request_type="A_Memorix.person_fact_writeback")
            response_result = await self._extractor.generate_response(prompt)
        except Exception as exc:
            logger.debug(f"人物事实提取模型调用失败: {exc}")
            return []
        return self._parse_fact_list(response_result.response)

    @staticmethod
    def _parse_fact_list(raw: str) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            repaired = repair_json(text)
            payload = json.loads(repaired) if isinstance(repaired, str) else repaired
        except Exception:
            payload = None
        if not isinstance(payload, list):
            return []

        items: List[str] = []
        seen = set()
        for item in payload:
            fact = str(item or "").strip().strip("- ")
            if not fact or len(fact) < 4:
                continue
            if fact in seen:
                continue
            seen.add(fact)
            items.append(fact)
        return items[:5]

    @staticmethod
    def _looks_ephemeral(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        ephemeral_markers = (
            "哈哈",
            "好的",
            "收到",
            "嗯嗯",
            "晚安",
            "早安",
            "拜拜",
            "谢谢",
            "在吗",
            "？",
        )
        if len(content) <= 8 and any(marker in content for marker in ephemeral_markers):
            return True
        return False


@dataclass
class ChatSummaryWritebackState:
    last_trigger_message_count: int = 0
    last_trigger_time: float = 0.0


class ChatSummaryWritebackService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._states: dict[str, ChatSummaryWritebackState] = {}

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(self._worker_loop(), name="memory_chat_summary_writeback")

    async def shutdown(self) -> None:
        self._stopping = True
        worker = self._worker_task
        self._worker_task = None
        if worker is None:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"关闭聊天摘要写回 worker 失败: {exc}")

    async def enqueue(self, message: Any) -> None:
        if not bool(global_config.a_memorix.integration.chat_summary_writeback_enabled):
            return
        if self._stopping:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("聊天摘要写回队列已满，跳过本次触发")

    async def _worker_loop(self) -> None:
        try:
            while not self._stopping:
                message = await self._queue.get()
                try:
                    await self._handle_message(message)
                except Exception as exc:
                    logger.warning(f"聊天摘要写回处理失败: {exc}", exc_info=True)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, message: Any) -> None:
        session_id = self._resolve_session_id(message)
        if not session_id:
            return

        message_time = self._extract_message_timestamp(message)
        total_message_count = self._count_messages_until_trigger(session_id=session_id, message_time=message_time)
        if total_message_count <= 0:
            return

        threshold = self._message_threshold()
        state = self._states.get(session_id)
        if state is None:
            restored_count = await self._load_last_trigger_message_count(
                session_id=session_id,
                total_message_count=total_message_count,
            )
            state = ChatSummaryWritebackState(
                last_trigger_message_count=restored_count,
                last_trigger_time=time.time() if restored_count > 0 else 0.0,
            )
            self._states[session_id] = state
        pending_message_count = max(0, total_message_count - state.last_trigger_message_count)
        if pending_message_count < threshold:
            return

        configured_context_length = self._context_length()
        context_length = self._effective_context_length(
            configured_context_length=configured_context_length,
            pending_message_count=pending_message_count,
        )
        result = await memory_service.ingest_summary(
            external_id=f"chat_auto_summary:{session_id}:{total_message_count}",
            chat_id=session_id,
            text="",
            participants=[],
            time_end=message_time,
            metadata={
                "generate_from_chat": True,
                "context_length": context_length,
                "configured_context_length": configured_context_length,
                "writeback_source": "memory_flow_service",
                "trigger": "message_threshold",
                "previous_trigger_message_count": state.last_trigger_message_count,
                "pending_message_count": pending_message_count,
                "trigger_message_count": total_message_count,
                "summary_review_count": 2,
            },
            respect_filter=True,
            user_id=self._extract_session_user_id(message),
            group_id=self._extract_session_group_id(message),
        )
        if not getattr(result, "success", False):
            logger.warning(
                f"聊天摘要自动写回失败: session_id={session_id} detail={getattr(result, 'detail', '')}",
            )
            return

        state.last_trigger_message_count = total_message_count
        state.last_trigger_time = time.time()
        logger.info(
            f"聊天摘要自动写回成功: session_id={session_id} trigger=message_threshold "
            f"total_messages={total_message_count} context_length={context_length} "
            f"detail={getattr(result, 'detail', '')}",
        )

    async def _load_last_trigger_message_count(self, *, session_id: str, total_message_count: int) -> int:
        """从已落库的聊天摘要恢复触发游标，避免服务重启后重复摘要。"""
        try:
            runtime_manager = getattr(memory_service_module, "a_memorix_host_service", None)
            ensure_kernel = getattr(runtime_manager, "_ensure_kernel", None)
            if not callable(ensure_kernel):
                return 0

            kernel = await ensure_kernel()
            metadata_store = getattr(kernel, "metadata_store", None)
            if metadata_store is None:
                return 0

            paragraphs = metadata_store.get_paragraphs_by_source(f"chat_summary:{session_id}")
            if not paragraphs:
                return 0

            latest_paragraph = max(paragraphs, key=self._paragraph_created_at)
            metadata = self._paragraph_metadata(latest_paragraph)
            trigger_message_count = self._coerce_positive_int(metadata.get("trigger_message_count"))
            if trigger_message_count > 0:
                return min(total_message_count, trigger_message_count)

            # 兼容旧摘要数据：没有触发计数时，只能退化为对齐当前计数，
            # 至少避免重启后立刻重复写入一条相近摘要。
            return total_message_count
        except Exception as exc:
            logger.debug(f"恢复聊天摘要写回游标失败: session_id={session_id} error={exc}")
            return 0

    @staticmethod
    def _paragraph_created_at(paragraph: dict[str, Any]) -> float:
        try:
            return float(paragraph.get("created_at") or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _paragraph_metadata(paragraph: dict[str, Any]) -> dict[str, Any]:
        metadata = paragraph.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, (bytes, bytearray)):
            try:
                parsed = pickle.loads(metadata)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _coerce_positive_int(value: Any) -> int:
        try:
            number = int(value or 0)
        except Exception:
            return 0
        return max(0, number)

    @staticmethod
    def _resolve_session_id(message: Any) -> str:
        return str(
            getattr(message, "session_id", "")
            or getattr(getattr(message, "session", None), "session_id", "")
            or ""
        ).strip()

    @staticmethod
    def _extract_session_user_id(message: Any) -> str:
        return str(
            getattr(getattr(message, "session", None), "user_id", "")
            or getattr(message, "user_id", "")
            or ""
        ).strip()

    @staticmethod
    def _extract_session_group_id(message: Any) -> str:
        return str(
            getattr(getattr(message, "session", None), "group_id", "")
            or getattr(message, "group_id", "")
            or ""
        ).strip()

    @staticmethod
    def _extract_message_timestamp(message: Any) -> float | None:
        raw_timestamp = getattr(message, "timestamp", None)
        if isinstance(raw_timestamp, datetime):
            return raw_timestamp.timestamp()
        if hasattr(raw_timestamp, "timestamp") and callable(raw_timestamp.timestamp):
            try:
                return float(raw_timestamp.timestamp())
            except Exception:
                return None
        if isinstance(raw_timestamp, (int, float)):
            return float(raw_timestamp)
        return None

    @staticmethod
    def _message_threshold() -> int:
        return max(1, int(global_config.a_memorix.integration.chat_summary_writeback_message_threshold))

    @staticmethod
    def _context_length() -> int:
        return max(1, int(global_config.a_memorix.integration.chat_summary_writeback_context_length))

    @staticmethod
    def _count_messages_until_trigger(*, session_id: str, message_time: float | None) -> int:
        if message_time is None:
            return count_messages(session_id=session_id)
        return count_messages(session_id=session_id, end_time=message_time)

    @staticmethod
    def _effective_context_length(*, configured_context_length: int, pending_message_count: int) -> int:
        """摘要只覆盖本轮新增消息，避免重叠窗口反复消耗 token。"""
        configured = max(1, int(configured_context_length))
        pending = max(1, int(pending_message_count))
        return min(configured, pending)


class MemoryAutomationService:
    def __init__(self) -> None:
        self.fact_writeback = PersonFactWritebackService()
        self.chat_summary_writeback = ChatSummaryWritebackService()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.fact_writeback.start()
        await self.chat_summary_writeback.start()
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self.chat_summary_writeback.shutdown()
        await self.fact_writeback.shutdown()
        self._started = False

    async def on_incoming_message(self, message: Any) -> None:
        del message
        if not self._started:
            await self.start()

    async def on_message_sent(self, message: Any) -> None:
        if not self._started:
            await self.start()
        await self.fact_writeback.enqueue(message)
        await self.chat_summary_writeback.enqueue(message)


memory_automation_service = MemoryAutomationService()

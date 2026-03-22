from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from json_repair import repair_json

from src.chat.utils.utils import is_bot_self
from src.common.message_repository import find_messages
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.memory_system.chat_history_summarizer import ChatHistorySummarizer
from src.person_info.person_info import Person, get_person_id, store_person_memory_from_answer

logger = get_logger("memory_flow_service")


class LongTermMemorySessionManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._summarizers: Dict[str, ChatHistorySummarizer] = {}

    async def on_message(self, message: Any) -> None:
        if not bool(getattr(global_config.memory, "long_term_auto_summary_enabled", True)):
            return
        session_id = str(getattr(message, "session_id", "") or "").strip()
        if not session_id:
            return

        created = False
        async with self._lock:
            summarizer = self._summarizers.get(session_id)
            if summarizer is None:
                summarizer = ChatHistorySummarizer(session_id=session_id)
                self._summarizers[session_id] = summarizer
                created = True
        if created:
            await summarizer.start()

    async def shutdown(self) -> None:
        async with self._lock:
            items = list(self._summarizers.items())
            self._summarizers.clear()
        for session_id, summarizer in items:
            try:
                await summarizer.stop()
            except Exception as exc:
                logger.warning("停止聊天总结器失败: session=%s err=%s", session_id, exc)


class PersonFactWritebackService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)
        self._worker_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._extractor = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="person_fact_writeback",
        )

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(self._worker_loop(), name="memory_person_fact_writeback")

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
            logger.warning("关闭人物事实写回 worker 失败: %s", exc)

    async def enqueue(self, message: Any) -> None:
        if not bool(getattr(global_config.memory, "person_fact_writeback_enabled", True)):
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
                    logger.warning("人物事实写回处理失败: %s", exc, exc_info=True)
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

        facts = await self._extract_facts(target_person, reply_text)
        if not facts:
            return

        session_id = str(
            getattr(message, "session_id", "")
            or getattr(getattr(message, "session", None), "session_id", "")
            or ""
        ).strip()
        if not session_id:
            return

        person_name = str(getattr(target_person, "person_name", "") or getattr(target_person, "nickname", "") or "").strip()
        if not person_name:
            return

        for fact in facts:
            await store_person_memory_from_answer(person_name, fact, session_id)

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
        if not reply_to:
            return None
        try:
            replies = find_messages(message_id=reply_to, limit=1)
        except Exception as exc:
            logger.debug("查询 reply_to 目标失败: %s", exc)
            return None
        if not replies:
            return None
        reply_message = replies[0]
        reply_platform = str(getattr(reply_message, "platform", "") or session_platform or "").strip()
        reply_user_info = getattr(getattr(reply_message, "message_info", None), "user_info", None)
        reply_user_id = str(getattr(reply_user_info, "user_id", "") or "").strip()
        if not reply_platform or not reply_user_id or is_bot_self(reply_platform, reply_user_id):
            return None
        person_id = get_person_id(reply_platform, reply_user_id)
        person = Person(person_id=person_id)
        return person if person.is_known else None

    async def _extract_facts(self, person: Person, reply_text: str) -> List[str]:
        person_name = str(getattr(person, "person_name", "") or getattr(person, "nickname", "") or person.person_id)
        prompt = f"""你要从一条机器人刚刚发送的回复中，提取“关于{person_name}的稳定事实”。

目标人物：{person_name}
机器人回复：
{reply_text}

请只提取满足以下条件的事实：
1. 明确是关于目标人物本人的信息。
2. 具有相对稳定性，可以作为长期记忆保存。
3. 用简洁中文陈述句表达。

不要提取：
- 机器人的情绪、计划、临时动作、客套话
- 只适用于当前时刻的短期安排
- 不确定、猜测、反问
- 与目标人物无关的信息

严格输出 JSON 数组，例如：
["他喜欢深夜打游戏", "他养了一只猫"]
如果没有可写入的事实，输出 []"""
        try:
            response, _ = await self._extractor.generate_response_async(prompt)
        except Exception as exc:
            logger.debug("人物事实提取模型调用失败: %s", exc)
            return []
        return self._parse_fact_list(response)

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


class MemoryAutomationService:
    def __init__(self) -> None:
        self.session_manager = LongTermMemorySessionManager()
        self.fact_writeback = PersonFactWritebackService()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.fact_writeback.start()
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        await self.session_manager.shutdown()
        await self.fact_writeback.shutdown()
        self._started = False

    async def on_incoming_message(self, message: Any) -> None:
        if not self._started:
            await self.start()
        await self.session_manager.on_message(message)

    async def on_message_sent(self, message: Any) -> None:
        if not self._started:
            await self.start()
        await self.fact_writeback.enqueue(message)


memory_automation_service = MemoryAutomationService()

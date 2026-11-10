from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, NotRequired, Optional, Set, Tuple, TypedDict

from sqlalchemy import text
from sqlmodel import col, delete, select

import asyncio
import json
import time

from src.common.data_models.jargon_data_model import MaiJargon
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.database.database import get_db_session
from src.common.database.database_model import (
    Jargon,
    JargonCreatedBy,
    JargonInferenceState,
    JargonMeaningHistory,
    Messages,
)
from src.common.llm_json import parse_llm_json
from src.common.logger import get_logger
from src.common.utils.utils_config import JargonConfigUtils
from src.common.utils.utils_message import MessageUtils
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_dispatcher import HookAbortedError
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

from .expression_utils import is_single_char_jargon
from .learning_text import extract_learning_text
from .jargon_inference_result import InferenceResult, evidence_hash

logger = get_logger("jargon")

llm_inference = LLMServiceClient(task_name="learner", request_type="jargon.inference")


class JargonEvidenceMessageRef(TypedDict):
    platform: str
    message_id: str
    session_id: NotRequired[str]
    database_id: NotRequired[int]


JargonEvidenceMessageGroup = List[JargonEvidenceMessageRef]


class JargonEntry(TypedDict):
    content: str
    raw_content: Set[str]
    evidence_messages: List[JargonEvidenceMessageGroup]


class JargonMeaningEntry(TypedDict):
    content: str
    meaning: str


def register_jargon_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册 jargon 系统内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    return registry.register_hook_specs(
        [
            HookSpec(
                name="jargon.extract.before_persist",
                description="黑话条目准备写入数据库前触发，可改写去重后的条目列表或跳过本次持久化。",
                parameters_schema=build_object_schema(
                    {
                        "session_id": {"type": "string", "description": "当前会话 ID。"},
                        "session_name": {"type": "string", "description": "当前会话展示名称。"},
                        "entries": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "即将持久化的黑话条目列表。",
                        },
                    },
                    required=["session_id", "session_name", "entries"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="jargon.inference.before_finalize",
                description="黑话含义推断完成、写回数据库前触发，可改写最终判定与含义结果。",
                parameters_schema=build_object_schema(
                    {
                        "session_id": {"type": "string", "description": "当前会话 ID。"},
                        "session_name": {"type": "string", "description": "当前会话展示名称。"},
                        "content": {"type": "string", "description": "当前黑话词条。"},
                        "count": {"type": "integer", "description": "当前词条累计命中次数。"},
                        "raw_content_list": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "用于推断的原始上下文片段列表。",
                        },
                        "comparison_result": {"type": "object", "description": "比较阶段输出结果。"},
                        "is_jargon": {"type": "boolean", "description": "当前推断是否判定为黑话。"},
                        "meaning": {"type": "string", "description": "当前推断出的黑话含义。"},
                        "is_complete": {"type": "boolean", "description": "当前是否已完成全部推断流程。"},
                        "last_inference_count": {
                            "type": "integer",
                            "description": "本次推断完成后应写回的 last_inference_count。",
                        },
                    },
                    required=[
                        "session_id",
                        "session_name",
                        "content",
                        "count",
                        "raw_content_list",
                        "comparison_result",
                        "is_jargon",
                        "meaning",
                        "is_complete",
                        "last_inference_count",
                    ],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
        ]
    )


class JargonMiner:
    def __init__(self, session_id: str, session_name: str) -> None:
        """初始化黑话学习器。

        Args:
            session_id: 当前会话 ID。
            session_name: 当前会话展示名称。
        """

        self.session_id = session_id
        self.session_name = session_name

        # Cache 相关
        self.cache_limit = 50
        self.cache: OrderedDict[str, None] = OrderedDict()
        # 黑话提取锁，防止并发执行
        self._extraction_lock = asyncio.Lock()

    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器。

        Returns:
            Any: 插件运行时管理器单例。
        """

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        """将任意值安全转换为整数。

        Args:
            value: 待转换的值。
            default: 转换失败时使用的默认值。

        Returns:
            int: 转换后的整数结果。
        """

        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _serialize_jargon_entries(entries: List[JargonEntry]) -> List[Dict[str, object]]:
        """将黑话条目列表序列化为 Hook 可传输结构。

        Args:
            entries: 原始黑话条目列表。

        Returns:
            List[Dict[str, object]]: 序列化后的条目列表。
        """

        return [
            {
                "content": str(entry["content"]).strip(),
                "raw_content": sorted(str(item).strip() for item in entry["raw_content"] if str(item).strip()),
                "evidence_messages": entry["evidence_messages"],
            }
            for entry in entries
            if str(entry["content"]).strip()
        ]

    @classmethod
    def _deserialize_jargon_entries(cls, raw_entries: Any) -> List[JargonEntry]:
        """从 Hook 载荷恢复黑话条目列表。

        Args:
            raw_entries: Hook 返回的条目数据。

        Returns:
            List[JargonEntry]: 恢复后的黑话条目列表。
        """

        if not isinstance(raw_entries, list):
            return []

        normalized_entries: List[JargonEntry] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            content = str(raw_entry.get("content") or "").strip()
            if not content:
                continue
            raw_content_values = raw_entry.get("raw_content")
            raw_content: Set[str] = set()
            if isinstance(raw_content_values, list):
                raw_content = {str(item).strip() for item in raw_content_values if str(item).strip()}
            evidence_messages = cls._normalize_evidence_message_groups(raw_entry.get("evidence_messages"))
            normalized_entries.append(
                {"content": content, "raw_content": raw_content, "evidence_messages": evidence_messages}
            )
        return normalized_entries

    @staticmethod
    def _normalize_evidence_message_groups(raw_groups: Any) -> List[JargonEvidenceMessageGroup]:
        """规范化黑话证据消息引用。"""

        if not isinstance(raw_groups, list):
            return []

        normalized_groups: List[JargonEvidenceMessageGroup] = []
        seen_groups: Set[str] = set()
        for raw_group in raw_groups:
            if not isinstance(raw_group, list):
                continue
            group: JargonEvidenceMessageGroup = []
            seen_refs: Set[str] = set()
            for raw_ref in raw_group:
                if not isinstance(raw_ref, dict):
                    continue
                platform = str(raw_ref.get("platform") or "").strip()
                message_id = str(raw_ref.get("message_id") or "").strip()
                if not platform or not message_id:
                    continue
                ref: JargonEvidenceMessageRef = {"platform": platform, "message_id": message_id}
                if raw_ref.get("session_id"):
                    ref["session_id"] = str(raw_ref["session_id"])
                if isinstance(raw_ref.get("database_id"), int) and raw_ref["database_id"] > 0:
                    ref["database_id"] = raw_ref["database_id"]
                ref_key = json.dumps(ref, sort_keys=True)
                if ref_key in seen_refs:
                    continue
                seen_refs.add(ref_key)
                group.append(ref)
            if not group:
                continue
            group_key = json.dumps(group, sort_keys=True)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            normalized_groups.append(group)
        return normalized_groups

    def get_cached_jargons(self) -> List[str]:
        """获取缓存中的所有黑话列表"""
        return list(self.cache.keys())

    @staticmethod
    def _format_evidence_context_segments(context_texts: List[str]) -> str:
        """把多个证据上下文组格式化为明确分隔的对话片段。"""

        return "\n\n".join(
            f"【对话片段 {index}】\n{context_text}" for index, context_text in enumerate(context_texts, start=1)
        )

    def _evidence_session_ids(self, jargon_obj: MaiJargon) -> Set[str]:
        """只使用词条已记录的来源会话及这些会话配置的共享组。"""
        session_ids = set(jargon_obj.session_id_list)
        for session_id in tuple(session_ids):
            related_ids, _ = JargonConfigUtils.resolve_jargon_group_scope(session_id)
            session_ids.update(related_ids)
        return session_ids

    def _recover_evidence_groups(
        self, jargon_obj: MaiJargon, session_ids: Set[str]
    ) -> List[JargonEvidenceMessageGroup]:
        """按时间和 ID 分页查找有效来源，再按相同边界还原相邻消息。"""
        if not session_ids or not jargon_obj.content.strip():
            return []
        limit = global_config.jargon.inference.evidence_limit
        page_size = max(limit, 100)
        groups: List[JargonEvidenceMessageGroup] = []
        excluded_sources = {(user.platform, user.user_id) for user in global_config.jargon.learning_blacklist}
        cursor: Optional[Tuple[datetime, int]] = None
        with get_db_session() as session:
            while len(groups) < limit:
                query = (
                    select(Messages)
                    .where(col(Messages.session_id).in_(session_ids))
                    .where(col(Messages.processed_plain_text).contains(jargon_obj.content, autoescape=True))
                )
                if cursor is not None:
                    query = query.where(
                        (col(Messages.timestamp) < cursor[0])
                        | ((col(Messages.timestamp) == cursor[0]) & (col(Messages.id) < cursor[1]))
                    )
                matches = session.exec(
                    query.order_by(col(Messages.timestamp).desc(), col(Messages.id).desc()).limit(page_size)
                ).all()
                if not matches:
                    break
                for message in matches:
                    if (message.platform, message.user_id) in excluded_sources:
                        continue
                    # 搜索列可能包含媒体描述，只有原始正文真正包含词条才作为来源。
                    try:
                        sequence = MessageUtils.from_db_record_msg_to_MaiSeq(message.raw_content)
                        message_text = extract_learning_text(sequence)
                    except Exception:
                        logger.exception("恢复黑话证据时无法解码消息 id=%s", message.id)
                        continue
                    if jargon_obj.content not in message_text:
                        continue
                    before = session.exec(
                        select(Messages)
                        .where(col(Messages.session_id) == message.session_id)
                        .where(
                            (col(Messages.timestamp) < message.timestamp)
                            | ((col(Messages.timestamp) == message.timestamp) & (col(Messages.id) < message.id))
                        )
                        .order_by(col(Messages.timestamp).desc(), col(Messages.id).desc())
                        .limit(3)
                    ).all()
                    after = session.exec(
                        select(Messages)
                        .where(col(Messages.session_id) == message.session_id)
                        .where(
                            (col(Messages.timestamp) > message.timestamp)
                            | ((col(Messages.timestamp) == message.timestamp) & (col(Messages.id) > message.id))
                        )
                        .order_by(col(Messages.timestamp), col(Messages.id))
                        .limit(2)
                    ).all()
                    # 黑名单只限制学习来源，仍允许其消息作为相邻上下文。
                    refs = [
                        {
                            "platform": item.platform,
                            "message_id": item.message_id,
                            "session_id": item.session_id,
                            "database_id": item.id,
                        }
                        for item in [*reversed(before), message, *after]
                    ]
                    groups.extend(self._normalize_evidence_message_groups([refs]))
                    if len(groups) >= limit:
                        break
                cursor = (matches[-1].timestamp, matches[-1].id)
        # 推断使用由旧到新的上下文组；分页查询由新到旧以保留最近的有效证据。
        return self._normalize_evidence_message_groups(list(reversed(groups)))

    def recoverable_evidence_hash(self, jargon_obj: MaiJargon) -> str:
        """只读检查当前可用的文字证据，供定期任务发现失效引用后的新消息。"""
        _, evidence_messages, _ = self._load_evidence_contexts(jargon_obj)
        return evidence_hash(evidence_messages)

    def _load_evidence_contexts(self, jargon_obj: MaiJargon) -> Tuple[List[str], Optional[str], bool]:
        """还原有效文字上下文，限定来源范围，缺失时重建并保留有界引用。"""
        limit = global_config.jargon.inference.evidence_limit
        evidence_groups = self._normalize_evidence_message_groups(
            self._load_json_list(jargon_obj.evidence_messages, "evidence_messages")
        )[-limit:]
        session_ids = self._evidence_session_ids(jargon_obj)
        context_texts: List[str] = []
        valid_groups: List[JargonEvidenceMessageGroup] = []
        excluded_sources = {(user.platform, user.user_id) for user in global_config.jargon.learning_blacklist}

        def load(groups: List[JargonEvidenceMessageGroup]) -> None:
            with get_db_session() as session:
                for group in groups:
                    messages: List[Messages] = []
                    for ref in group:
                        query = (
                            select(Messages)
                            .where(col(Messages.platform) == ref["platform"])
                            .where(col(Messages.message_id) == ref["message_id"])
                            .where(col(Messages.session_id).in_(session_ids))
                        )
                        if "session_id" in ref:
                            query = query.where(col(Messages.session_id) == ref["session_id"])
                        if "database_id" in ref:
                            query = query.where(col(Messages.id) == ref["database_id"])
                        candidates = session.exec(query.limit(2)).all()
                        # 旧引用只有平台和消息号，存在歧义时重建，避免取错共享会话的同号消息。
                        if len(candidates) != 1:
                            break
                        messages.append(candidates[0])
                    if len(messages) != len(group):
                        continue
                    context_lines: List[str] = []
                    valid_group: JargonEvidenceMessageGroup = []
                    has_valid_source = False
                    for ref, message in zip(group, messages, strict=True):
                        # 与候选提取共用文字白名单，避免完整渲染文本重新带入引用或媒体描述。
                        try:
                            sequence = MessageUtils.from_db_record_msg_to_MaiSeq(message.raw_content)
                            message_text = extract_learning_text(sequence)
                        except Exception:
                            logger.exception("载入黑话证据时无法解码消息 id=%s", message.id)
                            continue
                        if message_text:
                            context_lines.append(f"[{len(context_lines) + 1}] {message_text}")
                            valid_group.append(ref)
                            if (
                                jargon_obj.content in message_text
                                and (message.platform, message.user_id) not in excluded_sources
                            ):
                                has_valid_source = True
                    # 邻近消息可以保留，但来源本身必须仍有可解码的文字证据。
                    if context_lines and has_valid_source:
                        context_texts.append("\n".join(context_lines))
                        valid_groups.append(valid_group)

        load(evidence_groups)
        if not context_texts:
            load(self._recover_evidence_groups(jargon_obj, session_ids))
        cleaned_evidence_messages = json.dumps(valid_groups, ensure_ascii=False) if valid_groups else None
        return context_texts, cleaned_evidence_messages, cleaned_evidence_messages != jargon_obj.evidence_messages

    @staticmethod
    def _load_json_list(value: Optional[str], field_name: str) -> List[Any]:
        """读取 JSON 列表字段，格式错误时返回空列表并记录日志。"""

        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"无法解析黑话 {field_name} 字段，按空列表处理")
            return []
        return parsed if isinstance(parsed, list) else []

    def _cleanup_jargon_evidence_messages(
        self, jargon_obj: MaiJargon, cleaned_evidence_messages: Optional[str]
    ) -> None:
        """替换已检查的引用快照，同时保留推断期间新增的证据引用。"""
        if jargon_obj.item_id is None:
            return
        original_groups = self._normalize_evidence_message_groups(
            self._load_json_list(jargon_obj.evidence_messages, "evidence_messages")
        )
        original_keys = {json.dumps(group, sort_keys=True) for group in original_groups}
        cleaned_groups = self._normalize_evidence_message_groups(
            self._load_json_list(cleaned_evidence_messages, "evidence_messages")
        )
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            db_record = session.get(Jargon, jargon_obj.item_id)
            state = session.get(JargonInferenceState, jargon_obj.item_id)
            if (
                db_record is None
                or db_record.created_by == JargonCreatedBy.MANUAL
                or db_record.created_timestamp != jargon_obj.created_timestamp
                or db_record.content != jargon_obj.content
                or db_record.is_global != jargon_obj.is_global
                or set(json.loads(db_record.session_id_dict)) != set(jargon_obj.session_id_list)
                or (state is not None and state.locked)
            ):
                return
            current_groups = self._normalize_evidence_message_groups(
                self._load_json_list(db_record.evidence_messages, "evidence_messages")
            )
            new_groups = [group for group in current_groups if json.dumps(group, sort_keys=True) not in original_keys]
            merged = self._merge_evidence_message_groups(cleaned_groups, new_groups)
            db_record.evidence_messages = json.dumps(merged, ensure_ascii=False) if merged else None
            db_record.updated_timestamp = datetime.now()
            session.add(db_record)
            jargon_obj.evidence_messages = db_record.evidence_messages

    def _log_inference_prompt_preview(
        self,
        *,
        jargon_content: str,
        stage_name: str,
        prompt: str,
        generation_result: LLMResponseResult,
    ) -> None:
        """保存黑话含义推断阶段的可重放 LLM Prompt。"""

        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                [ContextItemBuilder().set_role(RoleType.User).add_text_content(prompt).build()],
                category="jargon_learning_update",
                chat_id=self.session_id,
                request_kind="jargon_learning_update",
                selection_reason=(
                    f"会话ID: {self.session_id}\n"
                    f"会话名: {self.session_name}\n"
                    f"词条: {jargon_content}\n"
                    f"推断阶段: {stage_name}\n"
                    "用途: 基于数据库消息证据推断或更新黑话含义，本记录保存单次 LLM prompt，可在推理过程页面直接重放。"
                ),
                output_title=f"黑话含义推断输出 - {stage_name}",
                output_items=generation_result.output_items,
                metadata={"model_name": generation_result.model_name},
                generation_attempts=generation_result.generation_attempts,
            )
        except Exception as exc:
            logger.warning(f"jargon {jargon_content} 推断 Prompt 保存失败: stage={stage_name}, error={exc}")
            return

        logger.debug(
            f"jargon {jargon_content} 推断 Prompt 已生成: stage={stage_name} "
            f"WebUI={preview_access.preview_web_uri} "
            f"推理详情={preview_access.reasoning_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    async def infer_meaning(
        self,
        jargon_obj: MaiJargon,
        *,
        before_request: Optional[Callable[[], Awaitable[None]]] = None,
        source: str = "automatic",
        job_id: str = "",
    ) -> InferenceResult:
        """对已有词条推断含义，保留证据并返回可用于后台任务统计的结果。"""
        requests = [0]
        input_evidence_hash = [""]
        try:
            result = await self._run_inference(
                jargon_obj, requests, input_evidence_hash, before_request=before_request, source=source, job_id=job_id
            )
            return replace(result, evidence_hash=input_evidence_hash[0])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"jargon {jargon_obj.content} 推断失败: {exc}")
            return InferenceResult("failed", str(exc), requests=requests[0], evidence_hash=input_evidence_hash[0])

    async def _run_inference(
        self,
        jargon_obj: MaiJargon,
        requests: List[int],
        input_evidence_hash: List[str],
        *,
        before_request: Optional[Callable[[], Awaitable[None]]],
        source: str,
        job_id: str,
    ) -> InferenceResult:
        """用限定会话的证据推断含义，在事务中复核保护状态后写回。"""
        content = jargon_obj.content
        if jargon_obj.created_by == JargonCreatedBy.MANUAL or await asyncio.to_thread(
            self._is_locked, jargon_obj.item_id
        ):
            logger.debug(f"jargon {content} 是手动维护或锁定记录，跳过含义推断")
            return InferenceResult("protected", "人工维护或已锁定")

        # 保存开始时的业务值，写回时重新核对，保护期间发生的人工编辑或较新推断。
        expected = {
            "content": content,
            "meaning": jargon_obj.meaning,
            "is_jargon": jargon_obj.is_jargon,
            "last_inference_count": jargon_obj.last_inference_count,
            "is_global": jargon_obj.is_global,
            "session_id_list": dict(jargon_obj.session_id_list),
            "created_timestamp": jargon_obj.created_timestamp,
        }
        raw_content_list, cleaned_evidence_messages, evidence_changed = await asyncio.to_thread(
            self._load_evidence_contexts, jargon_obj
        )
        # 记录真正参与本轮推断的引用；并发新增的引用仍是下一轮的新证据。
        input_evidence_hash[0] = evidence_hash(cleaned_evidence_messages)
        if evidence_changed:
            await asyncio.to_thread(self._cleanup_jargon_evidence_messages, jargon_obj, cleaned_evidence_messages)
        if not raw_content_list:
            logger.warning(f"jargon {content} 没有可用证据上下文，跳过推断")
            return InferenceResult("missing_evidence", "所属会话内没有可用的文字证据")

        async def generate(prompt: str, stage_name: str) -> Optional[Dict[str, Any]]:
            if before_request is not None:
                await before_request()
            requests[0] += 1
            generation_result = await llm_inference.generate_response(prompt, session_id=self.session_id)
            await asyncio.to_thread(
                self._log_inference_prompt_preview,
                jargon_content=content,
                stage_name=stage_name,
                prompt=prompt,
                generation_result=generation_result,
            )
            if not generation_result.response:
                return None
            return self._parse_result(generation_result.response)

        # 获取当前count和上一次的meaning
        current_count = jargon_obj.count
        previous_meaning = jargon_obj.meaning.strip()
        # 步骤1: 基于raw_content和content推断
        raw_content_text = self._format_evidence_context_segments(raw_content_list)
        # 只要已有上一次推断含义，就放入 prompt 作为参考。
        previous_meaning_section = ""
        previous_meaning_instruction = ""
        if previous_meaning:
            previous_meaning_section = f"\n**上一次推断的含义（仅供参考）**\n{previous_meaning}"
            previous_meaning_instruction = "- 请参考上一次推断的含义，结合新的上下文信息，给出更准确或更新的推断结果"
        prompt1_template = prompt_manager.get_prompt("jargon_inference_with_context")
        prompt1_template.add_context("bot_name", global_config.bot.nickname)
        prompt1_template.add_context("content", content)
        prompt1_template.add_context("raw_content_list", raw_content_text)
        prompt1_template.add_context("previous_meaning_section", previous_meaning_section)
        prompt1_template.add_context("previous_meaning_instruction", previous_meaning_instruction)
        inference1 = await generate(await prompt_manager.render_prompt(prompt1_template), "with_context")
        if inference1 is None:
            return InferenceResult("failed", "上下文推断无响应或解析失败", requests=requests[0])
        no_info = inference1.get("no_info", False)
        if not isinstance(no_info, bool):
            return InferenceResult("failed", "上下文推断 no_info 必须是布尔值", requests=requests[0])
        if no_info:
            # 更新最后一次判定的count值，避免在同一阈值重复尝试；证据仍保留供新证据到来后复用。
            jargon_obj.last_inference_count = current_count
            outcome = await self._commit_inference(jargon_obj, expected, source, job_id, False)
            if outcome != "succeeded":
                return InferenceResult(outcome, "词条已删除、被保护或已更新", requests=requests[0])
            return InferenceResult("insufficient_info", "模型认为现有上下文不足", requests=requests[0])
        meaning1 = inference1.get("meaning")
        if not isinstance(meaning1, str) or not meaning1.strip():
            return InferenceResult("failed", "上下文推断缺少有效 meaning", requests=requests[0])

        # 步骤2: 基于content-only进行推断
        prompt2_template = prompt_manager.get_prompt("jargon_inference_content_only")
        prompt2_template.add_context("content", content)
        inference2 = await generate(await prompt_manager.render_prompt(prompt2_template), "content_only")
        if inference2 is None or not isinstance(inference2.get("meaning"), str) or not inference2["meaning"].strip():
            return InferenceResult("failed", "词条推断无响应或缺少有效 meaning", requests=requests[0])

        # 步骤3: 比较两个推断结果
        prompt3_template = prompt_manager.get_prompt("jargon_compare_inference")
        prompt3_template.add_context("inference1", json.dumps(inference1, ensure_ascii=False))
        prompt3_template.add_context("inference2", json.dumps(inference2, ensure_ascii=False))
        comparison_result = await generate(await prompt_manager.render_prompt(prompt3_template), "compare")
        if comparison_result is None or not isinstance(comparison_result.get("is_similar"), bool):
            return InferenceResult("failed", "比较结果缺少有效 is_similar", requests=requests[0])
        is_jargon = not comparison_result["is_similar"]  # 如果相似，说明不是黑话；如果有差异，说明是黑话
        finalized_meaning = meaning1.strip()
        config = global_config.jargon.inference
        is_complete = config.stop_after_last_threshold and current_count >= config.thresholds[-1]
        last_inference_count = current_count
        finalize_result = await self._get_runtime_manager().invoke_hook(
            "jargon.inference.before_finalize",
            session_id=self.session_id,
            session_name=self.session_name,
            content=content,
            count=current_count,
            raw_content_list=list(raw_content_list),
            comparison_result=dict(comparison_result),
            is_jargon=is_jargon,
            meaning=finalized_meaning,
            is_complete=is_complete,
            last_inference_count=last_inference_count,
        )
        if finalize_result.aborted:
            logger.info(f"jargon {content} 的推断结果中止写回：{HookAbortedError(finalize_result)}")
            return InferenceResult("protected", "插件中止推断写回", requests=requests[0])
        finalize_kwargs = finalize_result.kwargs
        is_jargon = bool(finalize_kwargs.get("is_jargon", is_jargon))
        finalized_meaning = str(finalize_kwargs.get("meaning", finalized_meaning) or "").strip()
        is_complete = bool(finalize_kwargs.get("is_complete", is_complete))
        last_inference_count = self._coerce_int(finalize_kwargs.get("last_inference_count"), last_inference_count)
        # 更新数据库记录，最后一次判定计数供重启后的阈值判断使用。
        jargon_obj.is_jargon = is_jargon
        jargon_obj.meaning = finalized_meaning
        jargon_obj.last_inference_count = last_inference_count
        # 达到配置的最后阶段时标记完成；手动和定期任务仍可显式重新推断。
        jargon_obj.is_complete = is_complete
        outcome = await self._commit_inference(jargon_obj, expected, source, job_id, True)
        if outcome != "succeeded":
            return InferenceResult(outcome, "词条已删除、被保护或已更新", requests=requests[0])
        logger.debug(
            f"jargon {content} 推断完成: is_jargon={is_jargon}, meaning={jargon_obj.meaning}, "
            f"last_inference_count={jargon_obj.last_inference_count}, is_complete={jargon_obj.is_complete}"
        )
        # 固定输出推断结果，格式化为可读形式
        if is_jargon:
            # 是黑话，输出格式：[聊天名]xxx的含义是 xxxxxxxxxxx
            meaning = jargon_obj.meaning or "无详细说明"
            if jargon_obj.is_global:
                logger.info(f"[黑话]{content}的含义是 {meaning}")
            else:
                logger.info(f"[{self.session_name}]{content}的含义是 {meaning}")
        else:
            # 不是黑话，输出格式：[聊天名]xxx 不是黑话
            logger.info(f"[{self.session_name}]{content} 不是黑话")
        return InferenceResult("succeeded", meaning=finalized_meaning, is_jargon=is_jargon, requests=requests[0])

    async def process_extracted_entries(
        self, entries: List[JargonEntry], person_name_filter: Optional[Callable[[str], bool]] = None
    ) -> Tuple[int, int]:
        """
        处理已提取的黑话条目（从 expression_learner 路由过来的）

        Args:
            entries: 黑话条目列表
            person_name_filter: 可选的过滤函数，用于检查内容是否包含人物名称
        """
        if not entries:
            return 0, 0
        merged_entries: Dict[str, JargonEntry] = {}
        for entry in entries:
            content = entry["content"].strip()

            if person_name_filter and person_name_filter(content):
                logger.info(f"条目 '{content}' 包含人物名称，已过滤")
                continue
            raw_list = entry["raw_content"] or set()
            evidence_groups = entry["evidence_messages"]
            if content in merged_entries:
                merged_entries[content]["raw_content"].update(raw_list)
                merged_entries[content]["evidence_messages"] = self._merge_evidence_message_groups(
                    merged_entries[content]["evidence_messages"],
                    evidence_groups,
                )
            else:
                merged_entries[content] = {
                    "content": content,
                    "raw_content": set(raw_list),
                    "evidence_messages": self._merge_evidence_message_groups([], evidence_groups),
                }

        uniq_entries: List[JargonEntry] = list(merged_entries.values())
        before_persist_result = await self._get_runtime_manager().invoke_hook(
            "jargon.extract.before_persist",
            session_id=self.session_id,
            session_name=self.session_name,
            entries=self._serialize_jargon_entries(uniq_entries),
        )
        if before_persist_result.aborted:
            logger.info(f"[{self.session_name}] 黑话提取中止写入：{HookAbortedError(before_persist_result)}")
            return 0, 0

        raw_hook_entries = before_persist_result.kwargs.get("entries")
        if raw_hook_entries is not None:
            uniq_entries = self._deserialize_jargon_entries(raw_hook_entries)
            if not uniq_entries:
                logger.info(f"[{self.session_name}] Hook 过滤后没有可写入的黑话条目")
                return 0, 0

        saved = 0
        updated = 0
        from .jargon_inference_service import get_jargon_inference_service

        for entry in uniq_entries:
            content = entry["content"]
            try:
                outcome, jargon = await asyncio.to_thread(self._persist_extracted_entry, entry)
            except Exception as exc:
                logger.exception(f"保存黑话 '{content}' 失败: {exc}")
                continue
            self._add_to_cache(content)
            if outcome == "created":
                saved += 1
            elif outcome == "updated":
                updated += 1
            if jargon is not None and self._should_infer_meaning(jargon):
                await get_jargon_inference_service().enqueue_auto(jargon.id, self.session_id, self.session_name)
        # 固定输出提取的jargon结果，格式化为可读形式（只要有提取结果就输出）
        if uniq_entries:
            # 收集所有提取的jargon内容
            jargon_list = [entry["content"] for entry in uniq_entries]
            jargon_str = ",".join(jargon_list)
            logger.info(f"[{self.session_name}]疑似黑话: {jargon_str}")

        if saved or updated:
            logger.debug(f"jargon写入: 新增 {saved} 条，更新 {updated} 条，session_id={self.session_id}")

        return saved, updated

    def _persist_extracted_entry(self, entry: JargonEntry) -> Tuple[str, Optional[Jargon]]:
        """串行化候选匹配与证据合并，避免不同学习任务丢失新增次数和引用。"""
        related_session_ids, _ = JargonConfigUtils.resolve_jargon_group_scope(self.session_id)
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            jargon_items = session.exec(select(Jargon).filter_by(content=entry["content"])).all()
            matched: Optional[Jargon] = None
            for item in jargon_items:
                if not item.is_global and not related_session_ids.intersection(json.loads(item.session_id_dict)):
                    continue
                if item.created_by == JargonCreatedBy.MANUAL:
                    return "protected", None
                if matched is None:
                    matched = item
            if matched is not None:
                # 已存在记录，更新 count 和证据消息引用，不覆盖推断刚写回的判定字段。
                self._merge_jargon_update(matched, entry["evidence_messages"])
                session.add(matched)
                session.flush()
                return "updated", matched
            # 没找到匹配记录，创建新记录；阈值为 1 时由调用者立即排队。
            evidence = self._merge_evidence_message_groups([], entry["evidence_messages"])
            new_jargon = Jargon(
                content=entry["content"],
                evidence_messages=json.dumps(evidence, ensure_ascii=False) if evidence else None,
                session_id_dict=json.dumps({self.session_id: 1}),
                is_global=False,
                count=1,
                is_jargon=False,
                meaning="",
                created_by=JargonCreatedBy.AI,
            )
            session.add(new_jargon)
            session.flush()
            return "created", new_jargon

    def _add_to_cache(self, content: str):
        """将黑话内容添加到缓存，并维护缓存大小"""
        content = content.strip()
        if is_single_char_jargon(content):
            return
        if content in self.cache:
            # 已存在，移动到末尾表示最近使用
            self.cache.move_to_end(content)
        else:
            # 新内容，添加到缓存
            self.cache[content] = None
            # 如果超过限制，移除最旧的项
            if len(self.cache) > self.cache_limit:
                removed_content, _ = self.cache.popitem(last=False)
                logger.debug(f"缓存已满，移除最旧的黑话: {removed_content}")

    @classmethod
    def _merge_evidence_message_groups(
        cls,
        current_groups: List[JargonEvidenceMessageGroup],
        new_groups: List[JargonEvidenceMessageGroup],
    ) -> List[JargonEvidenceMessageGroup]:
        """合并证据消息引用组并保持顺序去重。"""

        merged_groups = cls._normalize_evidence_message_groups(current_groups)
        seen_groups = {tuple((ref["platform"], ref["message_id"]) for ref in group) for group in merged_groups}
        for group in cls._normalize_evidence_message_groups(new_groups):
            group_key = tuple((ref["platform"], ref["message_id"]) for ref in group)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            merged_groups.append(group)
        return merged_groups[-global_config.jargon.inference.evidence_limit :]

    def _merge_jargon_update(
        self, db_jargon: Jargon, evidence_message_groups: List[JargonEvidenceMessageGroup]
    ) -> None:
        """在调用者持有的写事务内累加一次候选命中，并保留最新有限证据。"""
        db_jargon.count += 1
        db_jargon.updated_timestamp = datetime.now()
        existing = self._normalize_evidence_message_groups(
            self._load_json_list(db_jargon.evidence_messages, "evidence_messages")
        )
        merged = self._merge_evidence_message_groups(existing, evidence_message_groups)
        db_jargon.evidence_messages = json.dumps(merged, ensure_ascii=False) if merged else None
        session_counts: Dict[str, int] = json.loads(db_jargon.session_id_dict)
        session_counts[self.session_id] = session_counts.get(self.session_id, 0) + 1
        db_jargon.session_id_dict = json.dumps(session_counts)

    def _update_jargon(self, db_jargon: Jargon, evidence_message_groups: List[JargonEvidenceMessageGroup]) -> None:
        """读取最新的已有黑话记录，再合并写回，避免旧对象覆盖并发更新。"""
        if db_jargon.id is None:
            raise ValueError("黑话记录缺少 id，无法更新数据库")
        with get_db_session() as session:
            session.connection().execute(text("BEGIN IMMEDIATE"))
            persisted = session.get(Jargon, db_jargon.id)
            if persisted is None or persisted.created_by == JargonCreatedBy.MANUAL:
                return
            self._merge_jargon_update(persisted, evidence_message_groups)
            session.add(persisted)
            db_jargon.count = persisted.count
            db_jargon.evidence_messages = persisted.evidence_messages
            db_jargon.session_id_dict = persisted.session_id_dict
            db_jargon.updated_timestamp = persisted.updated_timestamp
            db_jargon.last_inference_count = persisted.last_inference_count
            db_jargon.is_complete = persisted.is_complete

    def _parse_result(self, response: str) -> Optional[Dict[str, Any]]:
        try:
            result = parse_llm_json(response)
        except Exception:
            try:
                result = parse_llm_json(response, repair=True)
            except Exception as e2:
                logger.error(f"推断结果解析失败: {e2}")
                return None
        if not isinstance(result, dict):
            logger.warning("推断结果格式错误")
            return None
        return result

    @staticmethod
    def _is_locked(jargon_id: Optional[int]) -> bool:
        if jargon_id is None:
            return False
        with get_db_session() as session:
            state = session.get(JargonInferenceState, jargon_id)
            return state is not None and state.locked

    async def _commit_inference(
        self,
        jargon_obj: MaiJargon,
        expected: Dict[str, Any],
        source: str,
        job_id: str,
        record_history: bool,
    ) -> str:
        """写回线程启动后等待其终态，关停取消不能越过尚未完成的事务。"""
        commit = asyncio.create_task(
            asyncio.to_thread(self._modify_jargon_entry, jargon_obj, expected, source, job_id, record_history)
        )
        while True:
            try:
                return await asyncio.shield(commit)
            except asyncio.CancelledError:
                if commit.done():
                    return commit.result()

    def _modify_jargon_entry(
        self,
        jargon_obj: MaiJargon,
        expected: Dict[str, Any],
        source: str,
        job_id: str,
        record_history: bool,
    ) -> str:
        """在同一写事务中校验人工保护、保存推断及有限的含义变更历史。"""
        if jargon_obj.item_id is None:
            return "missing"
        with get_db_session() as session:
            # SQLite 写锁覆盖读取和写回，避免 SELECT 后的人工编辑被旧结果覆盖。
            session.connection().execute(text("BEGIN IMMEDIATE"))
            db_record = session.get(Jargon, jargon_obj.item_id)
            if db_record is None:
                return "missing"
            state = session.get(JargonInferenceState, jargon_obj.item_id)
            if db_record.created_by == JargonCreatedBy.MANUAL or (state is not None and state.locked):
                return "protected"
            if (
                db_record.content != expected["content"]
                or db_record.meaning != expected["meaning"]
                or db_record.is_jargon != expected["is_jargon"]
                or db_record.last_inference_count != expected["last_inference_count"]
                or db_record.is_global != expected["is_global"]
                or set(json.loads(db_record.session_id_dict)) != set(expected["session_id_list"])
                or db_record.created_timestamp != expected["created_timestamp"]
            ):
                return "stale"
            if record_history and (
                db_record.meaning != jargon_obj.meaning or db_record.is_jargon != jargon_obj.is_jargon
            ):
                session.add(
                    JargonMeaningHistory(
                        jargon_id=jargon_obj.item_id,
                        job_id=job_id,
                        source=source,
                        previous_meaning=db_record.meaning,
                        meaning=jargon_obj.meaning,
                        previous_is_jargon=db_record.is_jargon,
                        is_jargon=jargon_obj.is_jargon,
                        created_at=time.time(),
                    )
                )
                session.flush()
                old_ids = session.exec(
                    select(JargonMeaningHistory.id)
                    .where(col(JargonMeaningHistory.jargon_id) == jargon_obj.item_id)
                    .order_by(col(JargonMeaningHistory.id).desc())
                    .offset(global_config.jargon.inference.history_limit)
                ).all()
                if old_ids:
                    session.exec(delete(JargonMeaningHistory).where(col(JargonMeaningHistory.id).in_(old_ids)))
            db_record.is_jargon = jargon_obj.is_jargon
            db_record.meaning = jargon_obj.meaning
            db_record.last_inference_count = jargon_obj.last_inference_count
            db_record.is_complete = jargon_obj.is_complete
            db_record.updated_timestamp = datetime.now()
            session.add(db_record)
        return "succeeded"

    def _should_infer_meaning(self, jargon_obj: Jargon) -> bool:
        """在跨过尚未处理的累计阈值时推断；末阶段后可按末阈值的增量继续。"""
        if jargon_obj.created_by == JargonCreatedBy.MANUAL:
            return False
        config = global_config.jargon.inference
        thresholds = config.thresholds
        count = jargon_obj.count or 0
        last_inference = jargon_obj.last_inference_count or 0
        # 完成标记只是按次数推断的优化；提高阈值或关闭停止后重新按当前配置判断。
        if count < thresholds[0] or count <= last_inference:
            return False
        next_threshold = next((threshold for threshold in thresholds if threshold > last_inference), None)
        if next_threshold is None:
            return not config.stop_after_last_threshold and count >= last_inference + thresholds[-1]
        return count >= next_threshold

    @staticmethod
    def _load_jargon_by_id(jargon_id: int) -> Optional[MaiJargon]:
        """读取本轮推断的业务快照，保留数据库中的原始时间戳。"""
        with get_db_session() as session:
            db_record = session.get(Jargon, jargon_id)
            return MaiJargon.from_db_instance(db_record) if db_record is not None else None

    async def _infer_meaning_by_id(
        self,
        jargon_id: int,
        *,
        before_request: Optional[Callable[[], Awaitable[None]]] = None,
        source: str = "automatic",
        job_id: str = "",
    ) -> InferenceResult:
        """从任务 ID 对应的词条启动推断，将删除和查询失败区分为不同终态。"""
        try:
            jargon_obj = await asyncio.to_thread(self._load_jargon_by_id, jargon_id)
            if jargon_obj is None:
                return InferenceResult("missing", "词条已删除")
            return await self.infer_meaning(jargon_obj, before_request=before_request, source=source, job_id=job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"查询Jargon id={jargon_id}失败: {exc}")
            return InferenceResult("failed", str(exc))

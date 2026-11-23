from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypedDict

from weakref import WeakValueDictionary

from sqlmodel import col, select

import asyncio
import json

from src.common.data_models.jargon_data_model import JargonEvidenceMessageGroup, MaiJargon, normalize_evidence_message_groups
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.database.database import get_db_session
from src.common.database.database_model import Jargon, Messages
from src.common.llm_json import parse_llm_json
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_dispatcher import HookAbortedError
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.prompt.prompt_manager import prompt_manager
from src.services.jargon_scope_service import (
    collect_learning, deferred_chats, finish_deferred_inference, learning_material,
    prepare_deferred_inference, should_infer_meaning,
)
from src.services.llm_service import LLMServiceClient

from .expression_utils import is_single_char_jargon
from .learning_text import extract_learning_text

logger = get_logger("jargon")

llm_inference = LLMServiceClient(task_name="learner", request_type="jargon.inference")


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
    _inference_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

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

    @staticmethod
    def _deserialize_jargon_entries(raw_entries: Any) -> List[JargonEntry]:
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
            evidence_messages = normalize_evidence_message_groups(raw_entry.get("evidence_messages"))
            normalized_entries.append(
                {"content": content, "raw_content": raw_content, "evidence_messages": evidence_messages}
            )
        return normalized_entries

    def get_cached_jargons(self) -> List[str]:
        """获取缓存中的所有黑话列表"""
        return list(self.cache.keys())

    @staticmethod
    def _format_evidence_context_segments(context_texts: List[str]) -> str:
        """把多个证据上下文组格式化为明确分隔的对话片段。"""

        return "\n\n".join(
            f"【对话片段 {index}】\n{context_text}" for index, context_text in enumerate(context_texts, start=1)
        )

    def _load_evidence_contexts(self, jargon_obj: MaiJargon) -> Tuple[List[str], Optional[str], bool]:
        """从证据消息还原本次文字正文，剔除无文字消息和包含缺失消息的证据组。"""

        evidence_groups = normalize_evidence_message_groups(
            self._load_json_list(jargon_obj.evidence_messages, "evidence_messages")
        )
        if not evidence_groups:
            return [], jargon_obj.evidence_messages, False

        context_texts: List[str] = []
        valid_groups: List[JargonEvidenceMessageGroup] = []
        removed_count = 0

        with get_db_session() as session:
            for group in evidence_groups:
                messages: List[Messages] = []
                group_missing = False
                for ref in group:
                    message = session.exec(
                        select(Messages)
                        .where(col(Messages.platform) == ref["platform"])
                        .where(col(Messages.message_id) == ref["message_id"])
                        .limit(1)
                    ).first()
                    if message is None:
                        group_missing = True
                        break
                    messages.append(message)

                if group_missing:
                    removed_count += 1
                    continue

                context_lines: List[str] = []
                valid_group: JargonEvidenceMessageGroup = []
                for ref, message in zip(group, messages, strict=True):
                    # 与候选提取共用文字白名单，避免完整渲染文本重新带入引用或媒体描述。
                    sequence = MessageUtils.from_db_record_msg_to_MaiSeq(message.raw_content)
                    message_text = extract_learning_text(sequence)
                    if message_text:
                        context_lines.append(f"[{len(context_lines) + 1}] {message_text}")
                        valid_group.append(ref)

                context_text = "\n".join(context_lines)
                if context_text:
                    context_texts.append(context_text)
                    valid_groups.append(valid_group)
                else:
                    removed_count += 1

        cleaned_evidence_messages = json.dumps(valid_groups, ensure_ascii=False) if valid_groups else None
        changed = removed_count > 0 or cleaned_evidence_messages != jargon_obj.evidence_messages
        if removed_count:
            logger.info(f"jargon {jargon_obj.content} 移除了 {removed_count} 组缺失或无有效文本的证据消息引用")
        return context_texts, cleaned_evidence_messages, changed

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

    async def infer_meaning(self, jargon_obj: MaiJargon) -> None:
        """对黑话条目执行含义推断。

        Args:
            jargon_obj: 待推断的黑话数据对象。
        """
        content = jargon_obj.content
        if not jargon_obj.allow_update:
            logger.debug(f"jargon {content} 已暂停更新，跳过含义推断")
            return

        excerpts, refs = await asyncio.to_thread(learning_material, jargon_obj.item_id)
        jargon_obj.evidence_messages = json.dumps(refs, ensure_ascii=False)
        resolved, _, _ = await asyncio.to_thread(self._load_evidence_contexts, jargon_obj)
        raw_content_list = list(dict.fromkeys([*excerpts, *resolved]))

        if not raw_content_list:
            logger.warning(f"jargon {content} 没有可用证据上下文，跳过推断")
            return

        # 获取当前count和上一次的meaning
        current_count = jargon_obj.count
        previous_meaning = jargon_obj.meaning

        # 步骤1: 基于raw_content和content推断
        raw_content_text = self._format_evidence_context_segments(raw_content_list)

        # 只要已有上一次推断含义，就放入 prompt 作为参考。
        previous_meaning_section = ""
        previous_meaning_instruction = ""
        previous_meaning = previous_meaning.strip()
        if previous_meaning:
            previous_meaning_section = f"\n**上一次推断的含义（仅供参考）**\n{previous_meaning}"
            previous_meaning_instruction = "- 请参考上一次推断的含义，结合新的上下文信息，给出更准确或更新的推断结果"

        prompt1_template = prompt_manager.get_prompt("jargon_inference_with_context")
        prompt1_template.add_context("bot_name", global_config.bot.nickname)
        prompt1_template.add_context("content", str(content))
        prompt1_template.add_context("raw_content_list", raw_content_text)
        prompt1_template.add_context("previous_meaning_section", previous_meaning_section)
        prompt1_template.add_context("previous_meaning_instruction", previous_meaning_instruction)
        prompt1 = await prompt_manager.render_prompt(prompt1_template)

        generation_result_1 = await llm_inference.generate_response(prompt1, session_id=self.session_id)
        llm_response_1 = generation_result_1.response
        self._log_inference_prompt_preview(
            jargon_content=content,
            stage_name="with_context",
            prompt=prompt1,
            generation_result=generation_result_1,
        )
        if not llm_response_1:
            logger.warning(f"jargon {content} 推断1失败：无响应")
            return

        # 解析推断1结果
        inference1 = self._parse_result(llm_response_1)
        if not inference1:
            logger.warning(f"jargon {content} 推断1解析失败")
            return

        no_info = inference1.get("no_info", False)
        meaning1: str = inference1.get("meaning", "").strip()
        if no_info or not meaning1:
            logger.info(f"jargon {content} 推断1表示信息不足无法推断，放弃本次推断，待下次更新")
            # 更新最后一次判定的count值，避免在同一阈值重复尝试
            jargon_obj.last_inference_count = jargon_obj.count or 0

            try:
                await asyncio.to_thread(self._modify_jargon_entry, jargon_obj)
            except Exception as e:
                logger.error(f"jargon {content} 推断1更新last_inference_count失败: {e}")
            return

        # 步骤2: 基于content-only进行推断
        prompt2_template = prompt_manager.get_prompt("jargon_inference_content_only")
        prompt2_template.add_context("content", content)
        prompt2 = await prompt_manager.render_prompt(prompt2_template)

        generation_result_2 = await llm_inference.generate_response(prompt2, session_id=self.session_id)
        llm_response_2 = generation_result_2.response
        self._log_inference_prompt_preview(
            jargon_content=content,
            stage_name="content_only",
            prompt=prompt2,
            generation_result=generation_result_2,
        )
        if not llm_response_2:
            logger.warning(f"jargon {content} 推断2失败：无响应")
            return

        # 解析推断2结果
        inference2 = self._parse_result(llm_response_2)
        if not inference2:
            logger.warning(f"jargon {content} 推断2解析失败")
            return

        # 步骤3: 比较两个推断结果
        prompt3_template = prompt_manager.get_prompt("jargon_compare_inference")
        prompt3_template.add_context("inference1", json.dumps(inference1, ensure_ascii=False))
        prompt3_template.add_context("inference2", json.dumps(inference2, ensure_ascii=False))
        prompt3 = await prompt_manager.render_prompt(prompt3_template)

        generation_result_3 = await llm_inference.generate_response(prompt3, session_id=self.session_id)
        llm_response_3 = generation_result_3.response
        self._log_inference_prompt_preview(
            jargon_content=content,
            stage_name="compare",
            prompt=prompt3,
            generation_result=generation_result_3,
        )
        if not llm_response_3:
            logger.warning(f"jargon {content} 比较失败：无响应")
            return

        comparison_result = self._parse_result(llm_response_3)
        if not comparison_result:
            logger.warning(f"jargon {content} 比较解析失败")
            return

        is_similar = comparison_result.get("is_similar", False)
        is_jargon = not is_similar  # 如果相似，说明不是黑话；如果有差异，说明是黑话

        inferred_meaning = str(inference1.get("meaning", "") or "").strip()
        finalized_meaning = inferred_meaning if is_jargon else previous_meaning
        is_complete = jargon_obj.scope_kind != "global" and current_count >= 100
        last_inference_count = jargon_obj.count or 0
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
            return

        finalize_kwargs = finalize_result.kwargs
        is_jargon = bool(finalize_kwargs.get("is_jargon", is_jargon))
        finalized_meaning = str(finalize_kwargs.get("meaning", finalized_meaning) or "").strip()
        is_complete = bool(finalize_kwargs.get("is_complete", is_complete))
        last_inference_count = self._coerce_int(
            finalize_kwargs.get("last_inference_count"),
            last_inference_count,
        )

        # 更新数据库记录
        jargon_obj.is_jargon = is_jargon
        jargon_obj.meaning = finalized_meaning
        # 更新最后一次判定的count值，避免重启后重复判定
        jargon_obj.last_inference_count = last_inference_count

        # 普通词条达到最后一个门槛后完成，通用词条继续积累下一轮线索。
        jargon_obj.is_complete = is_complete

        try:
            await asyncio.to_thread(self._modify_jargon_entry, jargon_obj)
        except Exception as e:
            logger.error(f"jargon {content} 推断结果更新失败: {e}")
        logger.debug(
            f"jargon {content} 推断完成: is_jargon={is_jargon}, meaning={jargon_obj.meaning}, last_inference_count={jargon_obj.last_inference_count}, is_complete={jargon_obj.is_complete}"
        )

        # 固定输出推断结果，格式化为可读形式
        if is_jargon:
            # 是黑话，输出格式：[聊天名]xxx的含义是 xxxxxxxxxxx
            meaning = jargon_obj.meaning or "无详细说明"
            is_global = jargon_obj.is_global  # 是否为全局的
            if is_global:
                logger.info(f"[黑话]{content}的含义是 {meaning}")
            else:
                logger.info(f"[{self.session_name}]{content}的含义是 {meaning}")
        else:
            # 不是黑话，输出格式：[聊天名]xxx 不是黑话
            logger.info(f"[{self.session_name}]{content} 不是黑话")

    async def process_extracted_entries(
        self, entries: List[JargonEntry], person_name_filter: Optional[Callable[[str], bool]] = None
    ) -> Tuple[int, int, int]:
        """
        处理已提取的黑话条目（从 expression_learner 路由过来的）

        Args:
            entries: 黑话条目列表
            person_name_filter: 可选的过滤函数，用于检查内容是否包含人物名称
        """
        if not entries:
            return 0, 0, 0
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
            return 0, 0, 0

        raw_hook_entries = before_persist_result.kwargs.get("entries")
        if raw_hook_entries is not None:
            uniq_entries = self._deserialize_jargon_entries(raw_hook_entries)
            if not uniq_entries:
                logger.info(f"[{self.session_name}] Hook 过滤后没有可写入的黑话条目")
                return 0, 0, 0

        saved = 0
        updated = 0
        deferred = 0
        for entry in uniq_entries:
            content = entry["content"]
            evidence_messages = entry["evidence_messages"]
            row, created, common = await asyncio.to_thread(
                collect_learning, content, self.session_id, evidence_messages, entry["raw_content"]
            )
            self._add_to_cache(content)
            if common and should_infer_meaning(common):
                asyncio.create_task(self._infer_meaning_by_id(common.id))
            if row is None:
                deferred += 1
                continue
            saved += int(created)
            updated += int(not created)
            if should_infer_meaning(row):
                asyncio.create_task(self._infer_meaning_by_id(row.id))
        # 固定输出提取的jargon结果，格式化为可读形式（只要有提取结果就输出）
        if uniq_entries:
            # 收集所有提取的jargon内容
            jargon_list = [entry["content"] for entry in uniq_entries]
            jargon_str = ",".join(jargon_list)
            logger.info(f"[{self.session_name}]疑似黑话: {jargon_str}")

        if saved or updated:
            logger.debug(f"jargon写入: 新增 {saved} 条，更新 {updated} 条，session_id={self.session_id}")

        return saved, updated, deferred

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

    @staticmethod
    def _merge_evidence_message_groups(
        current_groups: List[JargonEvidenceMessageGroup],
        new_groups: List[JargonEvidenceMessageGroup],
    ) -> List[JargonEvidenceMessageGroup]:
        """合并证据消息引用组并保持顺序去重。"""

        merged_groups = normalize_evidence_message_groups(current_groups)
        seen_groups = {tuple((ref["platform"], ref["message_id"]) for ref in group) for group in merged_groups}
        for group in normalize_evidence_message_groups(new_groups):
            group_key = tuple((ref["platform"], ref["message_id"]) for ref in group)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            merged_groups.append(group)
        return merged_groups

    def _parse_result(self, response: str) -> Optional[Dict[str, str]]:
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

    def _modify_jargon_entry(self, jargon_obj: MaiJargon) -> None:
        if not jargon_obj.allow_update:
            return

        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if not jargon_obj.item_id:
                raise ValueError("jargon_obj must have item_id to update")
            statement = select(Jargon).filter_by(id=jargon_obj.item_id).limit(1)
            if db_record := session.exec(statement).first():
                if db_record.archived or db_record.revision != jargon_obj.revision or not db_record.allow_update:
                    return
                db_record.revision += 1
                db_record.is_jargon = jargon_obj.is_jargon
                db_record.meaning = jargon_obj.meaning
                db_record.last_inference_count = jargon_obj.last_inference_count
                db_record.is_complete = jargon_obj.is_complete
                db_record.updated_timestamp = datetime.now()
                session.add(db_record)
                if db_record.scope_kind != "global":
                    finish_deferred_inference(session, db_record.id)

    async def _infer_meaning_by_id(self, jargon_id: int) -> None:
        def load() -> Optional[MaiJargon]:
            with get_db_session(auto_commit=False) as session:
                row = session.get(Jargon, jargon_id)
                return MaiJargon.from_db_instance(row) if row and not row.archived and should_infer_meaning(row) else None

        # 正常学习与冲突恢复可能同时跨过同一门槛；等前一次写回后重新检查。
        lock = self._inference_locks.setdefault(jargon_id, asyncio.Lock())
        async with lock:
            jargon_obj = await asyncio.to_thread(load)
            if jargon_obj:
                await self.infer_meaning(jargon_obj)


async def replay_deferred_learning() -> None:
    """按来源聊天恢复线索，沿用正常学习的推断门槛。"""
    from src.chat.message_receive.chat_manager import chat_manager
    from src.common.utils.utils_config import JargonConfigUtils

    for chat in await asyncio.to_thread(deferred_chats):
        jobs = await asyncio.to_thread(prepare_deferred_inference, chat)
        if not jobs:
            continue
        _, learn = await asyncio.to_thread(JargonConfigUtils.get_jargon_config_for_chat, chat)
        if not learn:
            continue
        miner = JargonMiner(chat, chat_manager.get_session_name(chat) or chat)
        for jargon_id in jobs:
            try:
                await miner._infer_meaning_by_id(jargon_id)
            except Exception:
                logger.exception(f"处理聊天 {chat} 的暂存黑话线索失败，词条 {jargon_id}")


async def periodically_replay_deferred_learning() -> None:
    while True:
        try:
            await replay_deferred_learning()
        except Exception:
            logger.exception("恢复暂存黑话线索失败")
        await asyncio.sleep(60)

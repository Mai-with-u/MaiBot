"""
聊天总结与知识导入工具

该模块负责从聊天记录中提取信息，生成总结，并将总结内容及提取的实体/关系
导入到 A_memorix 的存储组件中。
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from weakref import WeakValueDictionary

import asyncio
import json
import re
import time
import traceback

import numpy as np

from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import config_manager, global_config
from src.config.model_configs import TaskConfig
from src.services import llm_service as llm_api
from src.services import message_service as message_api

from ..storage import (
    GraphStore,
    KnowledgeType,
    MetadataStore,
    VectorStore,
    resolve_stored_knowledge_type,
)
from ..image.component_paths import build_chat_external_ref, iter_message_image_components
from ..embedding import EmbeddingAPIAdapter
from .model_routing import (
    find_text_generation_task_for_model,
    get_text_generation_model_tasks,
)
from .metadata import coerce_metadata_dict
from .relation_write_service import RelationWriteService
from .runtime_payloads import optional_float
from .runtime_self_check import ensure_runtime_self_check, run_embedding_runtime_self_check

logger = get_logger("A_Memorix.SummaryImporter")


@dataclass(frozen=True)
class SummaryImportResult:
    """聊天摘要导入结果。

    保留二元组解包兼容：旧调用方仍可使用 ``success, detail = result``。
    """

    success: bool
    detail: str
    paragraph_hash: str = ""
    source: str = ""
    skipped: bool = False

    def __iter__(self) -> Iterator[bool | str]:
        yield self.success
        yield self.detail


# 默认总结提示词模版
SUMMARY_PROMPT_NAME = "a_memorix_chat_summary"
# 保留只读模板常量供既有扩展与测试检查结构；实际模型调用按当前语言动态加载。
SUMMARY_PROMPT_TEMPLATE = load_prompt(SUMMARY_PROMPT_NAME, locale="zh-CN")


def _normalize_entity_items(raw_entities: Any) -> List[str]:
    if not isinstance(raw_entities, list):
        return []
    entities: List[str] = []
    seen = set()
    for item in raw_entities:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or item.get("entity") or "").strip()
        else:
            name = ""
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(name)
    return entities


def _normalize_relation_items(raw_relations: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_relations, list):
        return []
    relations: List[Dict[str, str]] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "") or "").strip()
        predicate = str(item.get("predicate", "") or "").strip()
        obj = str(item.get("object", "") or "").strip()
        if not (subject and predicate and obj):
            continue
        relations.append({"subject": subject, "predicate": predicate, "object": obj})
    return relations


def _normalize_summary_facts(raw_facts: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_facts, list):
        return []
    facts: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        fact_id = str(item.get("fact_id") or "").strip()
        text = str(item.get("text") or "").strip()
        if not fact_id or not text or fact_id in seen_ids:
            continue
        evidence = []
        for raw_evidence in item.get("image_evidence") or []:
            if not isinstance(raw_evidence, dict):
                continue
            message_id = str(raw_evidence.get("message_id") or "").strip()
            component_path = str(raw_evidence.get("component_path") or "").strip()
            if message_id and component_path:
                evidence.append({"message_id": message_id, "component_path": component_path})
        seen_ids.add(fact_id)
        facts.append({"fact_id": fact_id, "text": text, "image_evidence": evidence})
    return facts


def _message_timestamp(message: Any) -> Optional[float]:
    for attr_name in ("timestamp", "time"):
        value = getattr(message, attr_name, None)
        if value is None:
            continue
        timestamp_func = getattr(value, "timestamp", None)
        if callable(timestamp_func):
            try:
                return float(timestamp_func())
            except Exception:
                continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _paragraph_created_at(paragraph: Dict[str, Any]) -> float:
    try:
        return float(paragraph.get("created_at") or 0.0)
    except Exception:
        return 0.0


class SummaryImporter:
    """总结并导入知识的工具类"""

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        plugin_config: dict,
    ):
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.plugin_config = plugin_config
        self._import_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.relation_write_service: Optional[RelationWriteService] = (
            plugin_config.get("relation_write_service") if isinstance(plugin_config, dict) else None
        )

    def _plugin_instance(self) -> Any:
        return self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None

    def _image_evidence_catalog(self, messages: Sequence[Any], *, stream_id: str) -> Tuple[str, set[Tuple[str, str]]]:
        lines: List[str] = []
        allowed: set[Tuple[str, str]] = set()
        for message in messages:
            message_id = str(getattr(message, "message_id", "") or "").strip()
            components = getattr(getattr(message, "raw_message", None), "components", []) or []
            for component_path, _ in iter_message_image_components(components):
                if not message_id:
                    continue
                allowed.add((message_id, component_path))
                external_ref = build_chat_external_ref(
                    chat_id=stream_id,
                    message_id=message_id,
                    component_path=component_path,
                )
                occurrence = self.metadata_store.get_image_occurrence_by_ref(external_ref)
                observations = (
                    self.metadata_store.list_image_observations([str(occurrence["occurrence_id"])])
                    if occurrence is not None
                    else []
                )
                descriptions = [
                    f"{item['source_kind']}({item['confirm_status']}): {item['text']}"
                    for item in observations
                ]
                suffix = f"；图片上下文={' | '.join(descriptions)}" if descriptions else ""
                lines.append(f"- message_id={message_id}, component_path={component_path}{suffix}")
        return ("\n".join(lines) if lines else "无", allowed)

    def _link_summary_image_evidence(
        self,
        *,
        stream_id: str,
        paragraph_hash: str,
        facts: Sequence[Dict[str, Any]],
        allowed: set[Tuple[str, str]],
    ) -> int:
        linked = 0
        for fact in facts:
            for evidence in fact.get("image_evidence") or []:
                key = (str(evidence["message_id"]), str(evidence["component_path"]))
                if key not in allowed:
                    raise ValueError(f"总结模型返回了窗口外图片证据: {key[0]}:{key[1]}")
                occurrence = self.metadata_store.get_image_occurrence_by_ref(
                    build_chat_external_ref(
                        chat_id=stream_id,
                        message_id=key[0],
                        component_path=key[1],
                    )
                )
                if occurrence is None:
                    continue
                with self.metadata_store.transaction(immediate=True) as database:
                    self.metadata_store.upsert_image_memory_link(
                        occurrence_id=str(occurrence["occurrence_id"]),
                        target_type="paragraph",
                        target_id=paragraph_hash,
                        link_kind="fact_evidence",
                        evidence={"fact_id": fact["fact_id"], "fact_text": fact["text"]},
                        conn=database,
                    )
                linked += 1
        return linked

    def _allow_metadata_only_write(self) -> bool:
        plugin_instance = self._plugin_instance()
        getter = getattr(plugin_instance, "get_config", None)
        if callable(getter):
            return bool(getter("embedding.fallback.allow_metadata_only_write", True))
        if isinstance(self.plugin_config, dict):
            embedding_cfg = self.plugin_config.get("embedding", {}) or {}
            fallback_cfg = embedding_cfg.get("fallback", {}) if isinstance(embedding_cfg, dict) else {}
            if isinstance(fallback_cfg, dict):
                return bool(fallback_cfg.get("allow_metadata_only_write", True))
        return True

    def _dual_vector_pools_enabled(self) -> bool:
        plugin_instance = self._plugin_instance()
        checker = getattr(plugin_instance, "_dual_vector_pools_enabled", None)
        if callable(checker):
            return bool(checker())
        if isinstance(self.plugin_config, dict):
            retrieval_cfg = self.plugin_config.get("retrieval", {}) or {}
            vector_pools_cfg = retrieval_cfg.get("vector_pools", {}) if isinstance(retrieval_cfg, dict) else {}
            configured_mode = (
                str(vector_pools_cfg.get("mode", "dual") or "dual").strip().lower()
                if isinstance(vector_pools_cfg, dict)
                else "dual"
            )
            runtime_cfg = self.plugin_config.get("runtime", {}) or {}
            if isinstance(runtime_cfg, dict) and "vector_pools_ready" in runtime_cfg:
                return configured_mode == "dual" and bool(runtime_cfg.get("vector_pools_ready"))
            return configured_mode == "dual"
        return False

    def _graph_vector_store(self) -> VectorStore:
        if self._dual_vector_pools_enabled() and isinstance(self.plugin_config, dict):
            store = self.plugin_config.get("graph_vector_store")
            if store is not None:
                return store
        return self.vector_store

    def _persist_vector_store(self, store: VectorStore) -> None:
        plugin_instance = self._plugin_instance()
        if plugin_instance is not None:
            plugin_instance.persist_vector_store(store)
            return

        embedding_fingerprint = self.embedding_manager.get_embedding_fingerprint(
            dimension=int(store.dimension)
        )
        store.save(embedding_fingerprint=embedding_fingerprint)

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    def _embedding_write_batch_size(self) -> int:
        batch_size = max(1, int(getattr(self.embedding_manager, "batch_size", 32)))
        max_concurrent = max(1, int(getattr(self.embedding_manager, "max_concurrent", 1)))
        return min(512, batch_size * max_concurrent)

    async def _ensure_entity_vector(self, *, entity_hash: str, name: str) -> None:
        await self._ensure_entity_vectors([(entity_hash, name)])

    async def _ensure_entity_vectors(self, entities: List[Tuple[str, str]]) -> None:
        """批量补齐实体向量，减少导入阶段的串行远程等待。"""
        if not entities or not self._dual_vector_pools_enabled():
            return

        target_store = self._graph_vector_store()
        pending_entities: List[Tuple[str, str, str]] = []
        for entity_hash, name in entities:
            token = str(entity_hash or "").strip()
            text = str(name or "").strip()
            vector_id = self._graph_vector_id("entity", token)
            if token and text and target_store is not None and vector_id not in target_store:
                pending_entities.append((token, text, vector_id))
        if not pending_entities:
            return

        write_batch_size = self._embedding_write_batch_size()
        for offset in range(0, len(pending_entities), write_batch_size):
            batch_entities = pending_entities[offset : offset + write_batch_size]
            try:
                embeddings = np.asarray(
                    await self.embedding_manager.encode_batch(
                        [name for _, name, _ in batch_entities]
                    ),
                    dtype=np.float32,
                )
                if embeddings.ndim == 1:
                    embeddings = embeddings.reshape(1, -1)
                if embeddings.shape[0] != len(batch_entities):
                    raise ValueError(
                        "实体批量向量数量不匹配: "
                        f"{embeddings.shape[0]} vs {len(batch_entities)}"
                    )
                target_store.add(
                    vectors=embeddings,
                    ids=[vector_id for _, _, vector_id in batch_entities],
                )
            except Exception as exc:
                if not self._allow_metadata_only_write():
                    raise
                logger.warning(
                    "总结导入实体批量向量写入失败，保留 metadata/graph: "
                    f"count={len(batch_entities)} error={exc}"
                )

    def _normalize_summary_model_selectors(self, raw_value: Any) -> List[str]:
        """标准化 summarization.model_name 配置。"""
        if raw_value is None:
            return ["auto"]
        if isinstance(raw_value, list):
            if any(not isinstance(item, str) for item in raw_value):
                raise ValueError(
                    "summarization.model_name 必须为 List[str]。 请执行 scripts/release_vnext_migrate.py migrate。"
                )
            selectors = [str(x).strip() for x in raw_value if str(x).strip()]
            return selectors or ["auto"]
        raise ValueError(
            "summarization.model_name 必须为 List[str]。 请执行 scripts/release_vnext_migrate.py migrate。"
        )

    def _pick_default_summary_task(
        self, available_tasks: Dict[str, TaskConfig]
    ) -> Tuple[Optional[str], Optional[TaskConfig]]:
        """
        选择总结默认任务，避免错误落到 embedding/voice/vlm 等非文本生成任务。
        优先级：memory > utils > planner > tool_use。
        """
        for task_name in ("memory", "utils", "planner", "tool_use"):
            task_cfg = available_tasks.get(task_name)
            model_list = getattr(task_cfg, "model_list", []) if task_cfg is not None else []
            if any(str(model_name).strip() for model_name in (model_list or [])):
                return task_name, task_cfg
        return None, None

    @staticmethod
    def _current_model_dict() -> Dict[str, Any]:
        try:
            return getattr(config_manager.get_model_config(), "models_dict", {}) or {}
        except Exception as exc:
            logger.warning(f"读取当前模型字典失败: {exc}")
            return {}

    def _resolve_summary_model_task(self) -> Optional[Tuple[str, TaskConfig]]:
        """
        解析 summarization.model_name 为 (task_name, TaskConfig)。
        支持：
        - ["utils:model1", "utils:model2", "replyer"]（数组混合语法）
        - ["auto"]
        - ["some-model-name"]（具体模型名）
        """
        available_tasks = get_text_generation_model_tasks(llm_api)
        if not available_tasks:
            return None

        # vNext 要求该字段为 List[str]；当配置缺失时回退到 ["auto"]，
        # 避免默认值本身触发类型校验异常。
        raw_cfg = self.plugin_config.get("summarization", {}).get("model_name", ["auto"])
        selectors = self._normalize_summary_model_selectors(raw_cfg)
        default_task_name, default_task_cfg = self._pick_default_summary_task(available_tasks)

        base_cfg: Optional[TaskConfig] = None
        base_task_name: Optional[str] = None
        model_dict = self._current_model_dict()

        def _find_task_for_model(model_name: str) -> Tuple[Optional[str], Optional[TaskConfig]]:
            return find_text_generation_task_for_model(available_tasks, model_name)

        for raw_selector in selectors:
            selector = raw_selector.strip()
            if not selector:
                continue

            if selector.lower() == "auto":
                if default_task_cfg:
                    if base_cfg is None:
                        base_cfg = default_task_cfg
                        base_task_name = default_task_name
                continue

            if ":" in selector:
                task_name, model_name = selector.split(":", 1)
                task_name = task_name.strip()
                model_name = model_name.strip()
                task_cfg = available_tasks.get(task_name)
                if not task_cfg:
                    logger.warning(f"总结模型选择器 '{selector}' 的任务 '{task_name}' 不存在，已跳过")
                    continue

                if not model_name or model_name.lower() == "auto":
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    continue

                if model_name in task_cfg.model_list:
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    logger.info(
                        f"总结模型选择器 '{selector}' 已定位到任务 '{task_name}'；"
                        "当前 LLM 服务按任务候选列表执行，不单独覆盖具体模型。"
                    )
                else:
                    logger.warning(
                        f"总结模型选择器 '{selector}' 的模型 '{model_name}' 不在任务 '{task_name}' 中，已跳过"
                    )
                continue

            task_cfg = available_tasks.get(selector)
            if task_cfg:
                if base_cfg is None:
                    base_cfg = task_cfg
                    base_task_name = selector
                continue

            if selector in model_dict:
                task_name, task_cfg = _find_task_for_model(selector)
                if task_name and task_cfg:
                    if base_cfg is None:
                        base_cfg = task_cfg
                        base_task_name = task_name
                    logger.info(
                        f"总结模型选择器 '{selector}' 已映射到任务 '{task_name}'；"
                        "当前 LLM 服务按任务候选列表执行，不单独覆盖具体模型。"
                    )
                    continue
                logger.warning(f"总结模型选择器 '{selector}' 未归属于任何任务，已跳过")
                continue

            logger.warning(f"总结模型选择器 '{selector}' 无法识别，已跳过")

        if base_cfg is None or not base_task_name:
            if default_task_cfg:
                if base_cfg is None:
                    base_cfg = default_task_cfg
                    base_task_name = default_task_name

        if base_cfg is None or not base_task_name:
            return None

        template_cfg = base_cfg
        task_name_to_use = base_task_name
        return task_name_to_use, TaskConfig(
            model_list=list(template_cfg.model_list),
            max_tokens=template_cfg.max_tokens,
            temperature=template_cfg.temperature,
            slow_threshold=template_cfg.slow_threshold,
            selection_strategy=template_cfg.selection_strategy,
            hard_timeout=template_cfg.hard_timeout,
        )

    def _resolve_summary_model_config(self) -> Optional[TaskConfig]:
        resolved = self._resolve_summary_model_task()
        if resolved is None:
            return None
        return resolved[1]

    def _summary_review_count(self, metadata: Optional[Dict[str, Any]]) -> int:
        raw_value: Any = None
        if isinstance(metadata, dict):
            raw_value = metadata.get("summary_review_count")
        if raw_value is None:
            raw_value = self.plugin_config.get("summarization", {}).get("history_review_count", 2)
        try:
            return max(0, int(raw_value or 0))
        except Exception:
            return 2

    @staticmethod
    def _clean_review_summary(text: str) -> str:
        """规范化历史摘要，不按词面猜测事实是否有效。

        事实失效必须由结构化 supersession 状态表达。中文子串无法可靠区分
        “旧值”和“旧金山”、“测试内容”和“测试工程师”，删除命中句会造成
        合法负向事实、职业和地名在递归摘要中无声消失。
        """

        return " ".join(str(text or "").strip().split())[:500]

    def _existing_summary_result(
        self,
        *,
        stream_id: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[SummaryImportResult]:
        """在模型调用前解析 external ID，避免重试重复生成摘要。"""

        if not isinstance(metadata, dict):
            return None
        external_id = str(metadata.get("external_id", "") or "").strip()
        if not external_id:
            return None
        existing = self.metadata_store.get_external_memory_ref(external_id)
        if existing is None:
            return None
        paragraph_hash = str(existing.get("paragraph_hash", "") or "").strip()
        paragraph = self.metadata_store.get_paragraph(paragraph_hash) if paragraph_hash else None
        if not isinstance(paragraph, dict) or bool(paragraph.get("is_deleted", 0)):
            raise RuntimeError(
                f"摘要 external ID 指向无效段落: external_id={external_id} paragraph={paragraph_hash}"
            )
        expected_source = f"chat_summary:{stream_id}"
        actual_source = str(paragraph.get("source", "") or "").strip()
        if actual_source != expected_source:
            raise RuntimeError(
                f"摘要 external ID 已绑定到其他聊天流: external_id={external_id} "
                f"expected={expected_source} actual={actual_source}"
            )
        return SummaryImportResult(
            True,
            "总结已存在，跳过重复生成",
            paragraph_hash=paragraph_hash,
            source=expected_source,
        )

    def _build_previous_summary_context(
        self,
        stream_id: str,
        *,
        limit: int,
    ) -> str:
        if limit <= 0:
            return ""
        try:
            paragraphs = self.metadata_store.get_live_paragraphs_by_source(f"chat_summary:{stream_id}")
        except Exception as exc:
            logger.debug(f"读取历史摘要回顾失败: stream_id={stream_id} error={exc}")
            return ""
        if not paragraphs:
            return ""

        ordered = sorted(paragraphs, key=_paragraph_created_at, reverse=True)
        lines: List[str] = []
        now = time.time()
        for paragraph in ordered:
            paragraph_metadata = coerce_metadata_dict(paragraph.get("metadata"))
            memory_change = (
                paragraph_metadata.get("memory_change")
                if isinstance(paragraph_metadata.get("memory_change"), dict)
                else {}
            )
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is not None and valid_to <= now:
                continue
            cleaned = self._clean_review_summary(str(paragraph.get("content", "") or ""))
            if not cleaned:
                continue
            lines.append(f"- {cleaned}")
            if len(lines) >= limit:
                break
        if not lines:
            return ""

        return (
            "\n\n历史净化摘要回顾（只用于理解上下文和识别变化，不得直接复制到本轮增量）：\n"
            + "\n".join(lines)
            + "\n"
        )

    async def import_from_stream(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryImportResult:
        """串行化同一聊天流的摘要生成，使 external ID 检查覆盖并发重试。"""

        stream_token = str(stream_id or "").strip()
        if not stream_token:
            return SummaryImportResult(False, "stream_id 不能为空")
        external_id = str((metadata or {}).get("external_id", "") or "").strip()
        lock_key = f"external:{external_id}" if external_id else f"stream:{stream_token}"
        lock = self._import_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            return await self._import_from_stream_unlocked(
                stream_token,
                context_length=context_length,
                include_personality=include_personality,
                time_end=time_end,
                metadata=metadata,
            )

    async def _import_from_stream_unlocked(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SummaryImportResult:
        """
        从指定的聊天流中提取记录并执行总结导入

        Args:
            stream_id: 聊天流 ID
            context_length: 总结的历史消息条数
            include_personality: 是否包含人设
            time_end: 用于截取聊天记录的时间上界（闭区间）

        Returns:
            SummaryImportResult: 导入结果；有增量时包含新增段落 hash，无增量时标记为 skipped。
        """
        try:
            existing_result = self._existing_summary_result(
                stream_id=stream_id,
                metadata=metadata,
            )
            if existing_result is not None:
                return existing_result

            self_check_ok, self_check_msg = await self._ensure_runtime_self_check()
            if not self_check_ok:
                return SummaryImportResult(False, f"导入前自检失败: {self_check_msg}")

            # 1. 获取配置
            if context_length is None:
                context_length = self.plugin_config.get("summarization", {}).get("context_length", 50)

            if include_personality is None:
                include_personality = self.plugin_config.get("summarization", {}).get("include_personality", True)

            # 2. 获取历史消息
            query_time_end = time.time() if time_end is None else float(time_end)
            messages = message_api.get_messages_by_time_in_chat(
                chat_id=stream_id,
                start_time=0.0,
                end_time=query_time_end,
                limit=context_length,
                limit_mode="latest",
            )

            if not messages:
                return SummaryImportResult(False, "未找到有效的聊天记录进行总结")

            # 转换为可读文本
            chat_history_text = message_api.build_readable_messages(messages)
            image_evidence_catalog, allowed_image_evidence = self._image_evidence_catalog(
                messages,
                stream_id=stream_id,
            )
            review_count = self._summary_review_count(metadata)
            previous_summary_context = self._build_previous_summary_context(
                stream_id,
                limit=review_count,
            )

            # 3. 准备提示词内容
            bot_name = global_config.bot.nickname
            personality_context = ""
            if include_personality:
                personality = global_config.personality.personality
                if personality:
                    personality_context = f"你的性格设定是：{personality}"

            # 4. 调用 LLM
            prompt = load_prompt(
                SUMMARY_PROMPT_NAME,
                bot_name=bot_name,
                personality_context=personality_context,
                previous_summary_context=previous_summary_context,
                chat_history=chat_history_text,
                image_evidence_catalog=image_evidence_catalog,
            )

            resolved_model = self._resolve_summary_model_task()
            if resolved_model is None:
                return SummaryImportResult(False, "未找到可用的总结模型配置")
            task_name_to_use, model_config_to_use = resolved_model

            logger.info(
                f"正在为流 {stream_id} 执行总结，消息条数: {len(messages)}|总结模型任务: {task_name_to_use}|候选列表: {model_config_to_use.model_list}"
            )

            result = await llm_api.generate(
                llm_api.LLMServiceRequest(
                    task_name=task_name_to_use,
                    request_type="A_Memorix.ChatSummarization",
                    prompt=prompt,
                    temperature=model_config_to_use.temperature,
                    max_tokens=model_config_to_use.max_tokens,
                )
            )
            success = bool(result.success)
            response = str(result.completion.response or "")

            if not success or not response:
                return SummaryImportResult(False, "LLM 生成总结失败")

            # 5. 解析结果
            data = self._parse_llm_response(response)
            if not data or "summary" not in data:
                return SummaryImportResult(False, "解析 LLM 响应失败或总结为空")

            raw_summary = data["summary"]
            if not isinstance(raw_summary, str):
                return SummaryImportResult(False, "增量总结 summary 必须是字符串")
            summary_text = raw_summary.strip()
            entities = _normalize_entity_items(data.get("entities"))
            relations = _normalize_relation_items(data.get("relations"))
            facts = _normalize_summary_facts(data.get("facts"))
            if not summary_text:
                if data.get("entities") != [] or data.get("relations") != [] or facts:
                    return SummaryImportResult(False, "空增量必须同时返回空实体、空关系和空事实数组")
                return SummaryImportResult(
                    True,
                    "当前窗口没有新增长期记忆，跳过段落写入",
                    source=f"chat_summary:{stream_id}",
                    skipped=True,
                )
            msg_times = [timestamp for msg in messages if (timestamp := _message_timestamp(msg)) is not None]
            time_meta = {}
            if msg_times:
                time_meta = {
                    "event_time_start": min(msg_times),
                    "event_time_end": max(msg_times),
                    "time_granularity": "minute",
                    "time_confidence": 0.95,
                }

            # 6. 执行导入
            paragraph_hash = await self._execute_import(
                summary_text,
                entities,
                relations,
                stream_id,
                time_meta=time_meta,
                metadata=metadata,
            )
            self._link_summary_image_evidence(
                stream_id=stream_id,
                paragraph_hash=paragraph_hash,
                facts=facts,
                allowed=allowed_image_evidence,
            )

            # 7. 持久化
            self._persist_vector_store(self.vector_store)
            self.graph_store.save()

            external_id = str((metadata or {}).get("external_id", "") or "").strip()
            if external_id:
                self.metadata_store.upsert_external_memory_ref(
                    external_id=external_id,
                    paragraph_hash=paragraph_hash,
                    source_type="chat_summary",
                    metadata={"chat_id": stream_id},
                )

            result_msg = f"总结导入成功|长度: {len(summary_text)}|实体: {len(entities)}|关系: {len(relations)}"
            return SummaryImportResult(
                True,
                result_msg,
                paragraph_hash=paragraph_hash,
                source=f"chat_summary:{stream_id}",
            )

        except Exception as e:
            logger.error(f"总结导入过程中出错: {e}\n{traceback.format_exc()}")
            return SummaryImportResult(False, f"错误: {str(e)}")

    async def _ensure_runtime_self_check(self) -> Tuple[bool, str]:
        plugin_instance = self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None
        if plugin_instance is not None:
            report = await ensure_runtime_self_check(plugin_instance)
        else:
            report = await run_embedding_runtime_self_check(
                config=self.plugin_config,
                vector_store=self.vector_store,
                embedding_manager=self.embedding_manager,
            )
        if bool(report.get("ok", False)):
            return True, ""
        if self._allow_metadata_only_write():
            msg = (
                f"{report.get('message', 'unknown')} "
                f"(configured={report.get('configured_dimension', 0)}, "
                f"store={report.get('vector_store_dimension', 0)}, "
                f"encoded={report.get('encoded_dimension', 0)})"
            )
            logger.warning(f"总结导入进入 metadata-only 回退模式: {msg}")
            return True, "embedding_degraded_metadata_only"
        return (
            False,
            f"{report.get('message', 'unknown')} "
            f"(configured={report.get('configured_dimension', 0)}, "
            f"store={report.get('vector_store_dimension', 0)}, "
            f"encoded={report.get('encoded_dimension', 0)})",
        )

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """解析 LLM 返回的 JSON"""
        try:
            # 尝试查找 JSON
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {}
        except Exception as e:
            logger.warning(f"解析总结 JSON 失败: {e}")
            return {}

    async def _execute_import(
        self,
        summary: str,
        entities: List[str],
        relations: List[Dict[str, str]],
        stream_id: str,
        time_meta: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """将数据写入存储"""
        external_id = str((metadata or {}).get("external_id", "") or "").strip()
        plugin_instance = self._plugin_instance()
        common_ingest = getattr(plugin_instance, "ingest_text", None)
        if external_id and callable(common_ingest):
            normalized_relations = _normalize_relation_items(relations)
            result = await common_ingest(
                external_id=external_id,
                source_type="chat_summary",
                text=summary,
                chat_id=stream_id,
                time_start=(time_meta or {}).get("event_time_start"),
                time_end=(time_meta or {}).get("event_time_end"),
                metadata=metadata,
                entities=_normalize_entity_items(entities),
                relations=normalized_relations,
                respect_filter=False,
            )
            existing = self.metadata_store.get_external_memory_ref(external_id)
            paragraph_hash = str((existing or {}).get("paragraph_hash", "") or "").strip()
            if paragraph_hash:
                return paragraph_hash
            stored_ids = [str(item or "").strip() for item in result.get("stored_ids", []) if str(item or "").strip()]
            if stored_ids:
                return stored_ids[0]
            raise RuntimeError(f"公共 ingest 未返回摘要段落: external_id={external_id}")

        # 获取默认知识类型
        type_str = self.plugin_config.get("summarization", {}).get("default_knowledge_type", "narrative")
        try:
            knowledge_type = resolve_stored_knowledge_type(type_str, content=summary)
        except ValueError:
            logger.warning(f"非法 summarization.default_knowledge_type={type_str}，回退 narrative")
            knowledge_type = KnowledgeType.NARRATIVE

        # 导入总结文本
        hash_value = self.metadata_store.add_paragraph(
            content=summary,
            source=f"chat_summary:{stream_id}",
            metadata=metadata,
            knowledge_type=knowledge_type.value,
            time_meta=time_meta,
        )

        vector_writer = getattr(plugin_instance, "write_paragraph_vector_or_enqueue", None)
        if callable(vector_writer):
            result = await vector_writer(
                paragraph_hash=hash_value,
                content=summary,
                context="summary_import",
            )
            if str(result.get("warning", "") or "").strip():
                logger.warning(f"总结导入段落进入回退写入: {result}")
        else:
            try:
                embedding = await self.embedding_manager.encode(summary)
                self.vector_store.add(vectors=embedding.reshape(1, -1), ids=[hash_value])
            except Exception as exc:
                if not self._allow_metadata_only_write():
                    raise
                logger.warning(f"总结导入段落向量写入失败，改为回填队列: {exc}")
                self.metadata_store.enqueue_paragraph_vector_backfill(hash_value, error=str(exc))

        # 导入实体
        normalized_entities = _normalize_entity_items(entities)
        if normalized_entities:
            with self.metadata_store.transaction(immediate=True), self.graph_store.batch_update():
                self.graph_store.add_nodes(normalized_entities)
                entity_hashes = self.metadata_store.add_entities_batch(
                    normalized_entities,
                    source_paragraph=hash_value,
                )
            entity_vector_items = list(zip(entity_hashes, normalized_entities, strict=True))
            await self._ensure_entity_vectors(entity_vector_items)

        # 导入关系
        rv_cfg = self.plugin_config.get("retrieval", {}).get("relation_vectorization", {})
        if not isinstance(rv_cfg, dict):
            rv_cfg = {}
        write_vector = bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))
        normalized_relations = _normalize_relation_items(relations)
        relation_tuples = [
            (rel["subject"], rel["predicate"], rel["object"])
            for rel in normalized_relations
        ]
        if relation_tuples:
            if self.relation_write_service is not None:
                await self.relation_write_service.upsert_relations_with_vectors(
                    relation_tuples,
                    confidence=1.0,
                    source_paragraph=hash_value,
                    write_vector=write_vector,
                )
            else:
                with self.metadata_store.transaction(immediate=True), self.graph_store.batch_update():
                    # 写入元数据和图数据库（保留 relation_hashes，确保后续可按关系精确修剪）
                    relation_hashes = self.metadata_store.add_relations_batch(
                        relation_tuples,
                        confidence=1.0,
                        source_paragraph=hash_value,
                    )
                    self.graph_store.add_edges(
                        [(subject, obj) for subject, _, obj in relation_tuples],
                        relation_hashes=relation_hashes,
                    )

        logger.info(f"总结导入完成: hash={hash_value[:8]}")
        return hash_value

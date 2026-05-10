"""
聊天总结与知识导入工具

该模块负责从聊天记录中提取信息，生成总结，并将总结内容及提取的实体/关系
导入到 A_memorix 的存储组件中。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
import re
import time
import traceback

from src.common.logger import get_logger
from src.services import llm_service as llm_api
from src.services import message_service as message_api
from src.config.config import config_manager, global_config
from src.config.model_configs import TaskConfig

from ..storage import (
    KnowledgeType,
    VectorStore,
    GraphStore,
    MetadataStore,
    resolve_stored_knowledge_type,
)
from ..embedding import EmbeddingAPIAdapter
from .model_routing import (
    find_text_generation_task_for_model,
    get_text_generation_model_tasks,
    pick_text_generation_task,
)
from .relation_write_service import RelationWriteService
from .runtime_self_check import ensure_runtime_self_check, run_embedding_runtime_self_check

logger = get_logger("A_Memorix.SummaryImporter")

# 默认总结提示词模版
SUMMARY_PROMPT_TEMPLATE = """
你是 {bot_name}。{personality_context}
现在你需要对以下一段聊天记录进行总结，并提取其中的重要知识。

聊天记录内容：
{chat_history}

请完成以下任务：
1. **生成总结**：以第三人称或机器人的视角，简洁明了地总结这段对话的主要内容、发生的事件或讨论的主题。
2. **提取实体与关系**：识别并提取对话中提到的重要实体以及它们之间的关系。
3. **区分事实来源**：用户自己明确表达的稳定人物事实可以记录；机器人发言只能作为上下文，不能单独作为用户画像事实来源。

请严格以 JSON 格式输出，格式如下：
{{
  "summary": "总结文本内容",
  "entities": ["张三", "李四"],
  "relations": [
    {{"subject": "张三", "predicate": "认识", "object": "李四"}}
  ]
}}

注意：总结应具有叙事性，能够作为长程记忆的一部分。直接使用实体的实际名称，不要使用 e1/e2 等代号。
不要把机器人提出的建议、猜测、玩笑、承诺或复述，写成用户的稳定偏好、身份或长期事实。
"""


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


class SummaryImporter:
    """总结并导入知识的工具类"""

    def __init__(
        self,
        vector_store: VectorStore,
        graph_store: GraphStore,
        metadata_store: MetadataStore,
        embedding_manager: EmbeddingAPIAdapter,
        plugin_config: dict
    ):
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.embedding_manager = embedding_manager
        self.plugin_config = plugin_config
        self.relation_write_service: Optional[RelationWriteService] = (
            plugin_config.get("relation_write_service")
            if isinstance(plugin_config, dict)
            else None
        )

    def _allow_metadata_only_write(self) -> bool:
        plugin_instance = self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None
        getter = getattr(plugin_instance, "get_config", None)
        if callable(getter):
            return bool(getter("embedding.fallback.allow_metadata_only_write", True))
        if isinstance(self.plugin_config, dict):
            embedding_cfg = self.plugin_config.get("embedding", {}) or {}
            fallback_cfg = embedding_cfg.get("fallback", {}) if isinstance(embedding_cfg, dict) else {}
            if isinstance(fallback_cfg, dict):
                return bool(fallback_cfg.get("allow_metadata_only_write", True))
        return True

    def _normalize_summary_model_selectors(self, raw_value: Any) -> List[str]:
        """标准化 summarization.model_name 配置。"""
        if raw_value is None:
            return ["auto"]
        if isinstance(raw_value, list):
            selectors = [str(x).strip() for x in raw_value if str(x).strip()]
            return selectors or ["auto"]
        if isinstance(raw_value, str):
            selector = raw_value.strip()
            if selector:
                logger.warning("summarization.model_name 建议使用 List[str]，当前字符串配置已兼容处理。")
                return [selector]
            return ["auto"]
        raise ValueError(
            "summarization.model_name 必须为 List[str] 或 str。"
            " 请执行 scripts/release_vnext_migrate.py migrate。"
        )

    def _pick_default_summary_task(self, available_tasks: Dict[str, TaskConfig]) -> Tuple[Optional[str], Optional[TaskConfig]]:
        """
        选择总结默认任务，避免错误落到 embedding/voice/vlm 等非文本生成任务。
        优先级：memory > utils > planner > tool_use > replyer > 其他文本生成任务。
        """
        return pick_text_generation_task(
            available_tasks,
            preferred=("memory", "utils", "planner", "tool_use", "replyer"),
        )

    @staticmethod
    def _current_model_dict() -> Dict[str, Any]:
        try:
            return getattr(config_manager.get_model_config(), "models_dict", {}) or {}
        except Exception as exc:
            logger.warning(f"读取当前模型字典失败: {exc}")
            return {}

    def _resolve_summary_model_config(self) -> Optional[Tuple[str, TaskConfig]]:
        """
        解析 summarization.model_name 为 (task_name, TaskConfig)。
        支持：
        - "auto"
        - "replyer"（任务名）
        - "some-model-name"（具体模型名）
        - ["utils:model1", "utils:model2", "replyer"]（数组混合语法）
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

                if base_cfg is None:
                    base_cfg = task_cfg
                    base_task_name = task_name

                if not model_name or model_name.lower() == "auto":
                    continue

                if model_name in task_cfg.model_list:
                    logger.info(
                        f"总结模型选择器 '{selector}' 已定位到任务 '{task_name}'；"
                        "当前 LLM 服务按任务候选列表执行，不单独覆盖具体模型。"
                    )
                else:
                    logger.warning(f"总结模型选择器 '{selector}' 的模型 '{model_name}' 不在任务 '{task_name}' 中，已跳过")
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
            else:
                base_task_name, first_cfg = next(iter(available_tasks.items()))
                if base_cfg is None:
                    base_cfg = first_cfg

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
        )

    async def import_from_stream(
        self,
        stream_id: str,
        context_length: Optional[int] = None,
        include_personality: Optional[bool] = None,
        time_end: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        从指定的聊天流中提取记录并执行总结导入

        Args:
            stream_id: 聊天流 ID
            context_length: 总结的历史消息条数
            include_personality: 是否包含人设
            time_end: 用于截取聊天记录的时间上界（闭区间）

        Returns:
            Tuple[bool, str]: (是否成功, 结果消息)
        """
        try:
            self_check_ok, self_check_msg = await self._ensure_runtime_self_check()
            if not self_check_ok:
                return False, f"导入前自检失败: {self_check_msg}"

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
                return False, "未找到有效的聊天记录进行总结"

            # 转换为可读文本
            chat_history_text = message_api.build_readable_messages(messages)
            
            # 3. 准备提示词内容
            bot_name = global_config.bot.nickname or "机器人"
            personality_context = ""
            if include_personality:
                personality = getattr(global_config.bot, "personality", "")
                if personality:
                    personality_context = f"你的性格设定是：{personality}"

            # 4. 调用 LLM
            prompt = SUMMARY_PROMPT_TEMPLATE.format(
                bot_name=bot_name,
                personality_context=personality_context,
                chat_history=chat_history_text
            )

            resolved_model = self._resolve_summary_model_config()
            if resolved_model is None:
                return False, "未找到可用的总结模型配置"
            task_name_to_use, model_config_to_use = resolved_model

            logger.info(f"正在为流 {stream_id} 执行总结，消息条数: {len(messages)}")
            logger.info(f"总结模型任务: {task_name_to_use}")
            logger.info(f"总结模型候选列表: {model_config_to_use.model_list}")

            result = await llm_api.generate(
                llm_api.LLMServiceRequest(
                    task_name=task_name_to_use,
                    request_type="A_Memorix.ChatSummarization",
                    prompt=prompt,
                    temperature=getattr(model_config_to_use, "temperature", None),
                    max_tokens=getattr(model_config_to_use, "max_tokens", None),
                )
            )
            success = bool(result.success)
            response = str(result.completion.response or "")

            if not success or not response:
                return False, "LLM 生成总结失败"

            # 5. 解析结果
            data = self._parse_llm_response(response)
            if not data or "summary" not in data:
                return False, "解析 LLM 响应失败或总结为空"

            summary_text = str(data["summary"] or "").strip()
            if not summary_text:
                return False, "解析 LLM 响应失败或总结为空"
            entities = _normalize_entity_items(data.get("entities"))
            relations = _normalize_relation_items(data.get("relations"))
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
            await self._execute_import(
                summary_text,
                entities,
                relations,
                stream_id,
                time_meta=time_meta,
                metadata=metadata,
            )

            # 7. 持久化
            self.vector_store.save()
            self.graph_store.save()

            result_msg = (
                f"✅ 总结导入成功\n"
                f"📝 总结长度: {len(summary_text)}\n"
                f"📌 提取实体: {len(entities)}\n"
                f"🔗 提取关系: {len(relations)}"
            )
            return True, result_msg

        except Exception as e:
            logger.error(f"总结导入过程中出错: {e}\n{traceback.format_exc()}")
            return False, f"错误: {str(e)}"

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
    ):
        """将数据写入存储"""
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

        plugin_instance = self.plugin_config.get("plugin_instance") if isinstance(self.plugin_config, dict) else None
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
                self.vector_store.add(
                    vectors=embedding.reshape(1, -1),
                    ids=[hash_value]
                )
            except Exception as exc:
                if not self._allow_metadata_only_write():
                    raise
                logger.warning(f"总结导入段落向量写入失败，改为回填队列: {exc}")
                self.metadata_store.enqueue_paragraph_vector_backfill(hash_value, error=str(exc))

        # 导入实体
        if entities:
            self.graph_store.add_nodes(entities)

        # 导入关系
        rv_cfg = self.plugin_config.get("retrieval", {}).get("relation_vectorization", {})
        if not isinstance(rv_cfg, dict):
            rv_cfg = {}
        write_vector = bool(rv_cfg.get("enabled", False)) and bool(rv_cfg.get("write_on_import", True))
        for rel in _normalize_relation_items(relations):
            s, p, o = rel["subject"], rel["predicate"], rel["object"]
            if all([s, p, o]):
                if self.relation_write_service is not None:
                    await self.relation_write_service.upsert_relation_with_vector(
                        subject=s,
                        predicate=p,
                        obj=o,
                        confidence=1.0,
                        source_paragraph=hash_value,
                        write_vector=write_vector,
                    )
                else:
                    # 写入元数据
                    rel_hash = self.metadata_store.add_relation(
                        subject=s,
                        predicate=p,
                        obj=o,
                        confidence=1.0,
                        source_paragraph=hash_value
                    )
                    # 写入图数据库（写入 relation_hashes，确保后续可按关系精确修剪）
                    self.graph_store.add_edges([(s, o)], relation_hashes=[rel_hash])
                    try:
                        self.metadata_store.set_relation_vector_state(rel_hash, "none")
                    except Exception:
                        pass
                
        logger.info(f"总结导入完成: hash={hash_value[:8]}")

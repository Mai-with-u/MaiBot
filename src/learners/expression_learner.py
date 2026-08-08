from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Tuple

import asyncio
import json

from sqlmodel import select

from src.chat.replyer.expression_vector_index import ExpressionVectorIndexUpsertItem, expression_vector_index
from src.chat.utils.utils import is_bot_self
from src.common.data_models.expression_data_model import MaiExpression
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.database.database import get_db_session
from src.common.database.database_model import Expression, ModifiedBy
from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

from .expression_review_store import append_ai_review_log
from .expression_style_utils import normalize_expression_style_for_learning
from .expression_utils import check_expression_suitability, parse_expression_response

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.context.messages import LLMContextMessage


logger = get_logger("expressor")

express_learn_model = LLMServiceClient(
    task_name="learner", request_type="expression.learner"
)
summary_model = LLMServiceClient(task_name="utils", request_type="expression.summary")


@dataclass(frozen=True)
class ExpressionLearningAcquireResult:
    """表达学习批次并发闸门的申请结果。"""

    acquired: bool
    reason: str = ""
    active_count: int = 0
    max_count: int = 0


class ExpressionLearningBatchGate:
    """控制表达学习批次的聊天流互斥与全局并发上限。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_session_ids: set[str] = set()

    async def acquire(self, session_id: str) -> ExpressionLearningAcquireResult:
        max_count = int(global_config.expression.max_expression_learner)
        if max_count <= 0:
            return ExpressionLearningAcquireResult(False, "max_expression_learner <= 0", 0, max_count)

        async with self._lock:
            active_count = len(self._active_session_ids)
            if session_id in self._active_session_ids:
                return ExpressionLearningAcquireResult(False, "session_busy", active_count, max_count)
            if active_count >= max_count:
                return ExpressionLearningAcquireResult(False, "global_limit", active_count, max_count)

            self._active_session_ids.add(session_id)
            return ExpressionLearningAcquireResult(True, active_count=active_count + 1, max_count=max_count)

    async def release(self, session_id: str) -> None:
        async with self._lock:
            self._active_session_ids.discard(session_id)


expression_learning_batch_gate = ExpressionLearningBatchGate()


def register_expression_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册表达方式系统内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    return registry.register_hook_specs(
        [
            HookSpec(
                name="expression.select.before_select",
                description="表达方式选择流程开始前触发，可改写会话上下文、选择参数或中止本次选择。",
                parameters_schema=build_object_schema(
                    {
                        "chat_id": {"type": "string", "description": "当前聊天流 ID。"},
                        "chat_info": {"type": "string", "description": "用于选择表达方式的聊天上下文。"},
                        "max_num": {"type": "integer", "description": "最大可选表达方式数量。"},
                        "target_message": {"type": "string", "description": "当前目标回复消息文本。"},
                        "reply_reason": {"type": "string", "description": "规划器给出的回复理由。"},
                        "think_level": {"type": "integer", "description": "表达方式选择思考级别。"},
                    },
                    required=["chat_id", "chat_info", "max_num", "think_level"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="expression.select.after_selection",
                description="表达方式选择完成后触发，可改写最终选中的表达方式列表与 ID。",
                parameters_schema=build_object_schema(
                    {
                        "chat_id": {"type": "string", "description": "当前聊天流 ID。"},
                        "chat_info": {"type": "string", "description": "用于选择表达方式的聊天上下文。"},
                        "max_num": {"type": "integer", "description": "最大可选表达方式数量。"},
                        "target_message": {"type": "string", "description": "当前目标回复消息文本。"},
                        "reply_reason": {"type": "string", "description": "规划器给出的回复理由。"},
                        "think_level": {"type": "integer", "description": "表达方式选择思考级别。"},
                        "selected_expressions": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "当前已选中的表达方式列表。",
                        },
                        "selected_expression_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "当前已选中的表达方式 ID 列表。",
                        },
                    },
                    required=[
                        "chat_id",
                        "chat_info",
                        "max_num",
                        "think_level",
                        "selected_expressions",
                        "selected_expression_ids",
                    ],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="expression.learn.after_extract",
                description="表达方式学习解析出表达候选后触发，可改写候选集或直接终止本轮学习。",
                parameters_schema=build_object_schema(
                    {
                        "session_id": {"type": "string", "description": "当前会话 ID。"},
                        "message_count": {"type": "integer", "description": "本轮参与学习的消息数量。"},
                        "expressions": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "解析出的表达方式候选列表。",
                        },
                        "jargon_entries": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "兼容字段。黑话学习已由独立 learner 处理，此处固定为空列表。",
                        },
                    },
                    required=["session_id", "message_count", "expressions", "jargon_entries"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="expression.learn.before_upsert",
                description="表达方式写入数据库前触发，可改写情景/风格文本或跳过本条写入。",
                parameters_schema=build_object_schema(
                    {
                        "session_id": {"type": "string", "description": "当前会话 ID。"},
                        "situation": {"type": "string", "description": "即将写入的情景文本。"},
                        "style": {"type": "string", "description": "即将写入的风格文本。"},
                    },
                    required=["session_id", "situation", "style"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
        ]
    )


class ExpressionLearner:
    def __init__(self, session_id: str) -> None:
        """初始化表达方式学习器。

        Args:
            session_id: 当前会话 ID。
        """

        self.session_id = session_id
        self.min_messages_for_extraction = 10

    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器。

        Returns:
            Any: 插件运行时管理器单例。
        """

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    @staticmethod
    def _get_session_display_name(session_id: str) -> str:
        """获取聊天流展示名称，无法解析时回退到 session_id。"""

        from src.chat.message_receive.chat_manager import chat_manager

        session_name = chat_manager.get_session_name(session_id)
        if session_name:
            return session_name

        chat_manager.get_existing_session_by_session_id(session_id)
        return chat_manager.get_session_name(session_id) or session_id

    @staticmethod
    def _serialize_expressions(expressions: List[Tuple[str, str, str]]) -> List[dict[str, str]]:
        """将表达方式候选序列化为 Hook 载荷。

        Args:
            expressions: 原始表达方式候选列表。

        Returns:
            List[dict[str, str]]: 序列化后的表达方式候选。
        """

        serialized_expressions: List[dict[str, str]] = []
        for situation, style, source_id in expressions:
            normalized_situation = str(situation).strip()
            normalized_style = normalize_expression_style_for_learning(str(style).strip())
            if not normalized_situation or not normalized_style:
                continue
            serialized_expressions.append(
                {
                    "situation": normalized_situation,
                    "style": normalized_style,
                    "source_id": str(source_id).strip(),
                }
            )
        return serialized_expressions

    @staticmethod
    def _deserialize_expressions(raw_expressions: Any) -> List[Tuple[str, str, str]]:
        """从 Hook 载荷恢复表达方式候选列表。

        Args:
            raw_expressions: Hook 返回的表达方式候选。

        Returns:
            List[Tuple[str, str, str]]: 恢复后的表达方式候选列表。
        """

        if not isinstance(raw_expressions, list):
            return []

        normalized_expressions: List[Tuple[str, str, str]] = []
        for raw_expression in raw_expressions:
            if not isinstance(raw_expression, dict):
                continue
            situation = str(raw_expression.get("situation") or "").strip()
            style = normalize_expression_style_for_learning(str(raw_expression.get("style") or "").strip())
            source_id = str(raw_expression.get("source_id") or "").strip()
            if not situation or not style:
                continue
            normalized_expressions.append((situation, style, source_id))
        return normalized_expressions

    async def learn_from_context_messages(
        self,
        context_messages: Sequence["LLMContextMessage"],
    ) -> bool:
        """从 Maisaka 被裁切的上下文消息中学习表达方式。

        只保留真实聊天消息：用户发言和 SELF 发言。工具结果、参考消息、记忆注入、
        规划器思考等上下文消息不会进入表达学习。
        """

        source_messages = self._extract_session_messages_from_context(context_messages)
        if not source_messages:
            logger.debug("裁切历史中没有可用于表达学习的真实聊天消息")
            return False
        if len(source_messages) < self.min_messages_for_extraction:
            logger.debug(
                f"裁切历史可学习消息不足: 可学习={len(source_messages)} 阈值={self.min_messages_for_extraction}"
            )
            return False

        return await self._learn_from_session_messages(
            source_messages,
        )

    @staticmethod
    def _extract_session_messages_from_context(
        context_messages: Sequence["LLMContextMessage"],
    ) -> List["SessionMessage"]:
        """从上下文消息中过滤出真实聊天消息。"""

        from src.maisaka.context.messages import SessionBackedMessage

        source_messages: List["SessionMessage"] = []
        seen_message_ids: set[str] = set()
        seen_object_ids: set[int] = set()

        for context_message in context_messages:
            if not isinstance(context_message, SessionBackedMessage):
                continue
            if context_message.source_kind not in {"user", "guided_reply", "outbound_send"}:
                continue

            original_message = context_message.original_message
            if original_message is None:
                continue

            message_id = str(original_message.message_id or "").strip()
            if message_id:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
            else:
                object_id = id(original_message)
                if object_id in seen_object_ids:
                    continue
                seen_object_ids.add(object_id)

            source_messages.append(original_message)

        return source_messages

    async def _learn_from_session_messages(
        self,
        pending_messages: List["SessionMessage"],
    ) -> bool:
        """对一批真实会话消息执行表达学习。"""

        learning_session_id = self._resolve_learning_session_id(pending_messages)
        if learning_session_id is None:
            logger.warning(
                f"表达学习已跳过：无法解析到有效聊天流，learner_session_id={self.session_id}"
            )
            return False
        if learning_session_id != self.session_id:
            logger.info(
                f"表达学习会话 ID 已按真实消息修正: learner_session_id={self.session_id} "
                f"learning_session_id={learning_session_id}"
            )

        acquire_result = await expression_learning_batch_gate.acquire(learning_session_id)
        if not acquire_result.acquired:
            if acquire_result.reason == "session_busy":
                logger.info(f"{learning_session_id} 已有表达学习批次正在运行，放弃新的批次")
            elif acquire_result.reason == "global_limit":
                logger.info(
                    f"表达学习全局并发已满，放弃新的批次: "
                    f"active={acquire_result.active_count}, max={acquire_result.max_count}, "
                    f"session_id={learning_session_id}"
                )
            else:
                logger.warning(
                    f"表达学习并发配置无效，放弃新的批次: "
                    f"max_expression_learner={acquire_result.max_count}, session_id={learning_session_id}"
                )
            return False

        try:
            return await self._run_learning_batch(
                pending_messages,
                learning_session_id=learning_session_id,
            )
        finally:
            await expression_learning_batch_gate.release(learning_session_id)

    async def _run_learning_batch(
        self,
        pending_messages: List["SessionMessage"],
        *,
        learning_session_id: str,
    ) -> bool:
        """执行已经获得并发闸门的表达学习批次。"""

        readable_message = "聊天记录将在后续多条 user message 中给出；请以每条消息中的 source_id 作为来源行编号。"
        prompt_template = prompt_manager.get_prompt("learn_style")
        prompt_template.add_context("bot_name", global_config.bot.nickname)
        prompt_template.add_context("chat_str", readable_message)
        prompt = await prompt_manager.render_prompt(prompt_template)

        try:
            learning_messages = await self._build_multi_learning_messages(pending_messages, prompt)
            generation_result = await express_learn_model.generate_response_with_messages(
                lambda _client: learning_messages,
                options=LLMGenerationOptions(temperature=0.3),
                session_id=learning_session_id,
            )
            self._log_learning_context_preview(
                learning_messages,
                session_id=learning_session_id,
                source_message_count=len(pending_messages),
                source_type="trimmed_history",
                output_content=generation_result.response or "",
                provider_response=generation_result.provider_response,
            )
            response = generation_result.response
        except Exception as e:
            logger.error(f"学习表达方式失败: {e}")
            return False

        expressions: List[Tuple[str, str, str]]
        expressions, _ = parse_expression_response(response)

        if len(expressions) > 20:
            logger.info(f"表达方式数量超过20: {len(expressions)}")
            expressions = []

        after_extract_result = await self._get_runtime_manager().invoke_hook(
            "expression.learn.after_extract",
            session_id=learning_session_id,
            message_count=len(pending_messages),
            expressions=self._serialize_expressions(expressions),
            jargon_entries=[],
        )
        if after_extract_result.aborted:
            logger.info(f"{self.session_id} 表达方式选择 Hook 中止")
            return False

        after_extract_kwargs = after_extract_result.kwargs
        raw_expressions = after_extract_kwargs.get("expressions")
        if raw_expressions is not None:
            expressions = self._deserialize_expressions(raw_expressions)

        if not expressions:
            logger.info("没有可学习的表达方式")
            return False

        # logger.info(f"可学习的表达方式: {expressions}")

        learnt_expressions = self._filter_expressions(expressions, pending_messages)
        if not learnt_expressions:
            logger.info("没有可学习的表达方式通过过滤")
            return False

        learnt_expressions_str = "\n".join(f"{situation}->{style}" for situation, style in learnt_expressions)
        session_display_name = self._get_session_display_name(learning_session_id)
        expression_log_title = "待优化的表达方式" if global_config.expression.expression_self_reflect else "学习到的表达"
        logger.info(f"[{session_display_name}] {expression_log_title}：\n{learnt_expressions_str}")

        written_expressions: List[MaiExpression] = []
        for situation, style in learnt_expressions:
            before_upsert_result = await self._get_runtime_manager().invoke_hook(
                "expression.learn.before_upsert",
                session_id=learning_session_id,
                situation=situation,
                style=style,
            )
            if before_upsert_result.aborted:
                logger.info(f"{self.session_id} 表达方式写入 Hook 中止: situation={situation!r}")
                continue

            upsert_kwargs = before_upsert_result.kwargs
            situation = str(upsert_kwargs.get("situation", situation) or "").strip()
            style = normalize_expression_style_for_learning(str(upsert_kwargs.get("style", style) or "").strip())
            if not situation or not style:
                logger.info(f"{self.session_id} 表达方式写入 Hook 中止: situation={situation!r}")
                continue

            expression_self_reflect = global_config.expression.expression_self_reflect
            if expression_self_reflect and not await self._check_expression_before_upsert(
                situation,
                style,
                session_id=learning_session_id,
            ):
                continue

            expression = await self._upsert_expression_to_db(
                situation,
                style,
                session_id=learning_session_id,
                checked=False,
                modified_by=ModifiedBy.AI if expression_self_reflect else None,
            )
            if expression is not None:
                written_expressions.append(expression)

        if written_expressions:
            await self._sync_expression_vector_index_batch(written_expressions)
        return bool(written_expressions)

    def _resolve_learning_session_id(self, messages: List["SessionMessage"]) -> Optional[str]:
        """根据真实消息解析本轮表达学习应该归属的会话 ID。"""

        from collections import Counter

        from src.chat.message_receive.chat_manager import chat_manager

        candidates = [
            str(getattr(message, "session_id", "") or "").strip()
            for message in messages
            if str(getattr(message, "session_id", "") or "").strip()
        ]

        def session_exists(session_id: str) -> bool:
            if not session_id:
                return False
            return chat_manager.get_existing_session_by_session_id(session_id) is not None

        for session_id, _ in Counter(candidates).most_common():
            if session_exists(session_id):
                return session_id

        if session_exists(self.session_id):
            return self.session_id

        logger.warning(
            f"表达学习无法从真实消息中找到已注册聊天流，也无法确认 learner_session_id; "
            f"learner_session_id={self.session_id} "
            f"候选 session_id={dict(Counter(candidates))}"
        )
        return None

    async def _build_multi_learning_messages(
        self,
        messages: List["SessionMessage"],
        system_prompt: str,
    ) -> List[Message]:
        """构造表达学习使用的多 message 请求。"""

        learning_messages = [
            MessageBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天记录会在后续多条 user message 中给出。每条消息内的 source_id "
                "是本轮学习的来源编号；speaker=SELF 的消息只作为上下文，不要从 SELF 的发言中学习。"
            )
            .build()
        ]

        for index, message in enumerate(messages, start=1):
            await message.process()
            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"
            speaker_kind = "SELF" if is_bot_self(message.platform, user_info.user_id) else "USER"
            content = (message.processed_plain_text or "").strip()
            if not content:
                content = "[空消息]"
            learning_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(
                    "\n".join(
                        [
                            f"[source_id:{index}]",
                            f"[speaker:{speaker_kind}]",
                            f"[name:{speaker_name}]",
                            f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                            "[content]",
                            content,
                        ]
                    )
                )
                .build()
            )

        learning_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content("请根据以上聊天消息输出 JSON。")
            .build()
        )
        return learning_messages

    def _log_learning_context_preview(
        self,
        messages: List[Message],
        *,
        session_id: str,
        source_message_count: int,
        source_type: str,
        output_content: str,
        provider_response: dict[str, Any] | None,
    ) -> None:
        """保存表达学习上下文预览，并在日志中输出查看入口。"""

        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                messages,
                category="expression_learner",
                chat_id=session_id,
                request_kind="expression_learner",
                selection_reason=(
                    f"会话ID: {session_id}\n"
                    f"Learner会话ID: {self.session_id}\n"
                    f"来源: {source_type}\n"
                    f"真实聊天消息数: {source_message_count}\n"
                    f"构建消息数: {len(messages)}"
                ),
                output_content=output_content,
                provider_response=provider_response,
            )
        except Exception as exc:
            logger.warning(f"{self.session_id} 表达学习上下文预览保存失败: {exc}")
            return

        logger.info(
            f"{self.session_id} 表达学习上下文预览已生成: "
            f"WebUI={preview_access.preview_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    # ====== 过滤方法 ======
    def _filter_expressions(
        self,
        expressions: List[Tuple[str, str, str]],
        messages: List["SessionMessage"],
    ) -> List[Tuple[str, str]]:
        """
        过滤表达方式，移除不符合条件的条目

        Args:
            expressions: 表达方式列表，每个元素是 (situation, style, source_id)

        Returns:
            过滤后的表达方式列表，每个元素是 (situation, style)
        """
        filtered_expressions: List[Tuple[str, str]] = []

        # 准备机器人名称集合（用于过滤 style 与机器人名称重复的表达）
        # TODO: 完善这里的机器人名称检测逻辑（考虑别名、不同平台的名称等）
        banned_names: set[str] = set()
        bot_nickname = global_config.bot.nickname
        if bot_nickname:
            banned_names.add(bot_nickname)
        alias_names = global_config.bot.alias_names or []
        for alias in alias_names:
            if alias_stripped := alias.strip():
                banned_names.add(alias_stripped)
        banned_casefold = {name.casefold() for name in banned_names if name}

        for situation, style, source_id in expressions:
            source_id_str = source_id.strip()
            if not source_id_str.isdigit():
                continue  # 无效的来源行编号，跳过
            line_index = int(source_id_str) - 1  # 多 message 构造时的 source_id 从 1 开始
            if line_index < 0 or line_index >= len(messages):
                continue  # 超出范围，跳过
            # 当前行的原始消息
            current_msg = messages[line_index]
            # 过滤掉从 bot 自己发言中提取到的表达方式
            if is_bot_self(current_msg.platform, current_msg.message_info.user_info.user_id):
                continue
            # 过滤掉无上下文的表达方式
            context = (current_msg.processed_plain_text or "").strip()
            if not context:
                continue
            # 过滤掉包含 SELF 的内容（不学习）
            # 过滤掉 style 与机器人名称/昵称重复的表达
            normalized_style = normalize_expression_style_for_learning(style)
            if not normalized_style:
                logger.info(f"跳过清洗后为空的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue
            if "SELF" in situation or "SELF" in normalized_style or "SELF" in context:
                logger.info(f"跳过包含 SELF 的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue
            if normalized_style and normalized_style.casefold() in banned_casefold:
                logger.debug(
                    f"跳过 style 与机器人名称重复的表达方式：situation={situation}, style={style}, source_id={source_id}"
                )
                continue
            # 过滤掉包含 "[表情" 的内容
            if "[表情包" in situation or "[表情包" in normalized_style or "[表情包" in context:
                logger.info(f"跳过包含表情标记的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue
            # 过滤掉包含 "[图片" 的内容
            if "[图片" in situation or "[图片" in normalized_style or "[图片" in context:
                logger.info(f"跳过包含图片标记的表达方式：situation={situation}, style={style}, source_id={source_id}")
                continue

            filtered_expressions.append((situation, normalized_style))

        return filtered_expressions

    # ====== DB 操作相关 ======
    async def _upsert_expression_to_db(
        self,
        situation: str,
        style: str,
        *,
        session_id: str,
        checked: bool = False,
        modified_by: Optional[ModifiedBy] = None,
    ) -> Optional[MaiExpression]:
        """将表达方式写入数据库，存在时更新，不存在时新增。

        Args:
            situation: 表达方式对应的使用情景。
            style: 表达方式风格。
            session_id: 表达方式归属的真实会话 ID。
            checked: 是否已经完成人工审核。
            modified_by: 最后修改者标记。
        """
        expr, similarity = self._find_similar_expression(situation, style, session_id=session_id) or (None, 0)
        if expr:
            # 只有完全一致的表达才会合并，因此不再触发相似表达的 LLM 情景概括。
            use_llm_summary = similarity < 1.0
            expression = await self._update_existing_expression(
                expr,
                situation,
                use_llm_summary=use_llm_summary,
                session_id=session_id,
                checked=checked,
                modified_by=modified_by,
            )
        else:
            # 没有找到匹配的记录，创建新记录
            expression = self._create_expression(
                situation,
                style,
                session_id=session_id,
                checked=checked,
                modified_by=modified_by,
            )

        return expression

    @staticmethod
    def _should_sync_expression_vector_index() -> bool:
        return global_config.expression.expression_selection_mode in {"vector", "vector_intent"}

    async def _sync_expression_vector_index_batch(self, expressions: Sequence[MaiExpression]) -> None:
        """表达学习批次写库成功后，同步维护表达向量索引并重聚类。"""

        if not self._should_sync_expression_vector_index():
            return

        index_items: List[ExpressionVectorIndexUpsertItem] = []
        for expression in expressions:
            if expression.item_id is None:
                raise ValueError("表达方式对象缺少 item_id，无法同步向量索引")
            modified_by = expression.modified_by.value if isinstance(expression.modified_by, ModifiedBy) else ""
            index_items.append(
                ExpressionVectorIndexUpsertItem(
                    id=expression.item_id,
                    situation=expression.situation,
                    style=expression.style,
                    count=expression.count,
                    session_id=expression.session_id,
                    checked=expression.checked,
                    modified_by=modified_by,
                )
            )

        await expression_vector_index.upsert_expressions_and_recluster(
            index_path=global_config.expression.expression_vector_index_path,
            expressions=index_items,
        )

    def _create_expression(
        self,
        situation: str,
        style: str,
        *,
        session_id: str,
        checked: bool = False,
        modified_by: Optional[ModifiedBy] = None,
    ) -> Optional[MaiExpression]:
        """创建新的表达方式记录。

        Args:
            situation: 表达方式对应的使用情景。
            style: 表达方式风格。
            session_id: 表达方式归属的真实会话 ID。
            checked: 是否已经完成人工审核。
            modified_by: 最后修改者标记。
        """
        content_list = [situation]
        try:
            with get_db_session() as db:
                new_expr = Expression(
                    situation=situation,
                    style=style,
                    content_list=json.dumps(content_list),
                    count=1,
                    session_id=session_id,
                    last_active_time=datetime.now(),
                    checked=checked,
                    modified_by=modified_by,
                )
                db.add(new_expr)
                db.flush()
                db.refresh(new_expr)
                return MaiExpression.from_db_instance(new_expr)
        except Exception as e:
            logger.error(f"创建表达方式失败: {e}")
        return None

    async def _update_existing_expression(
        self,
        expr: "MaiExpression",
        situation: str,
        *,
        session_id: str,
        use_llm_summary: bool = True,
        checked: bool = False,
        modified_by: Optional[ModifiedBy] = None,
    ) -> Optional[MaiExpression]:
        expr.content.append(situation)
        expr.count += 1
        expr.checked = checked
        expr.modified_by = modified_by
        expr.last_active_time = datetime.now()

        if use_llm_summary:
            # 相似匹配时，使用 LLM 重新组合 situation
            new_situation = await self._compose_situation_text(expr.content, session_id=session_id)
            if new_situation:
                expr.situation = new_situation

        try:
            with get_db_session() as session:
                if expr.item_id is None:
                    raise ValueError("表达方式对象缺少 item_id，无法更新数据库记录")
                statement = select(Expression).filter_by(id=expr.item_id).limit(1)
                if db_expr := session.exec(statement).first():
                    db_expr.content_list = json.dumps(expr.content)
                    db_expr.count = expr.count
                    db_expr.checked = expr.checked
                    db_expr.modified_by = expr.modified_by
                    db_expr.last_active_time = expr.last_active_time
                    db_expr.situation = expr.situation  # 更新 situation
                    session.add(db_expr)
                    return expr
                else:
                    logger.warning(f"表达方式 ID {expr.item_id} 在数据库中未找到，无法更新")
        except Exception as e:
            logger.error(f"更新表达方式失败: {e}")
        return None

    async def _check_expression_before_upsert(
        self,
        situation: str,
        style: str,
        *,
        session_id: Optional[str] = None,
    ) -> bool:
        """在表达方式写入数据库前执行 AI 审核，只有通过时才允许写入。"""

        review_session_id = session_id or self.session_id
        suitable, reason, error = await check_expression_suitability(
            situation,
            style,
            session_id=review_session_id,
        )
        if error:
            append_ai_review_log(
                session_id=review_session_id,
                situation=situation,
                style=style,
                passed=False,
                reason=reason or error,
                source="learn_before_upsert",
                error=error,
            )
            logger.error(f"检查表达方式时发生错误: {error}")
            return False

        append_ai_review_log(
            session_id=review_session_id,
            situation=situation,
            style=style,
            passed=suitable,
            reason=reason,
            source="learn_before_upsert",
        )

        status = "通过" if suitable else "不通过"
        logger.info(
            f"表达方式检查 - {status} | "
            f"Situation: {situation} | "
            f"Style: {style} || "
            f"Reason: {reason[:100] if reason else '无'}..."
        )
        return suitable

    # ====== 概括方法 ======
    async def _compose_situation_text(self, content_list: List[str], *, session_id: str) -> Optional[str]:
        texts = [c.strip() for c in content_list if c.strip()]
        if not texts:
            return None
        description = "\n".join(f"- {s}" for s in texts[-10:])  # 只取最近10条进行概括
        prompt = (
            "请阅读以下多个聊天情境描述，并将它们概括成一句简短的话，长度不超过20个字，保留共同特点：\n"
            f"{description}\n"
            "只输出概括内容。"
        )
        try:
            summary_result = await summary_model.generate_response(
                prompt, options=LLMGenerationOptions(temperature=0.2), session_id=session_id
            )
            summary = summary_result.response
            if summary := summary.strip():
                return summary
        except Exception as e:
            logger.error(f"使用 LLM 生成表达方式概括失败: {e}")
        return None

    def _find_similar_expression(
        self,
        situation: str,
        style: str,
        *,
        session_id: str,
    ) -> Optional[Tuple[MaiExpression, float]]:
        """在数据库中查找完全一致的表达方式。

        Args:
            situation: 当前待匹配的情景描述。
            style: 当前待匹配的表达风格。
            session_id: 表达方式归属的真实会话 ID。

        Returns:
            Optional[Tuple[MaiExpression, float]]: 若找到完全一致的表达方式，则返回
            ``(表达方式对象, 1.0)``；否则返回 ``None``。
        """
        normalized_situation = situation.strip()
        normalized_style = normalize_expression_style_for_learning(style)
        if not normalized_situation or not normalized_style:
            return None

        try:
            with get_db_session(auto_commit=False) as session:
                statement = select(Expression).filter_by(session_id=session_id)
                expressions = session.exec(statement).all()

                for db_expression in expressions:
                    expression = MaiExpression.from_db_instance(db_expression)
                    expression_style = normalize_expression_style_for_learning(expression.style)
                    if expression_style != normalized_style:
                        continue

                    candidate_situations = [expression.situation, *expression.content]
                    for candidate_situation in candidate_situations:
                        if candidate_situation.strip() == normalized_situation:
                            logger.debug(f"找到完全一致表达方式 [ID: {expression.item_id}]")
                            return expression, 1.0

        except Exception as e:
            logger.error(f"查找相似表达方式失败: {e}")
        return None

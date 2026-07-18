"""聊天消息入口与主链路调度。"""

from typing import Any, Dict, List, Optional

import os
import traceback

from maim_message import MessageBase

from src.chat.heart_flow.heartflow_message_processor import HeartFCMessageReceiver
from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.common.utils.utils_session import SessionUtils
from src.config.config import global_config
from src.core.announcement_manager import global_announcement_manager
from src.platform_io.route_key_factory import RouteKeyFactory
from src.plugin_runtime.component_query import component_query_service
from src.plugin_runtime.hook_payloads import deserialize_session_message, serialize_session_message
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.maisaka.context.clear_context import is_clear_context_command, mark_clear_context_command

from .chat_manager import chat_manager
from .image_receive_compressor import process_received_images_in_message
from .message import SessionMessage

# 定义日志配置

# 获取项目根目录（假设本文件在src/chat/message_receive/下，根目录为上上上级目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))

# 配置主程序日志格式
logger = get_logger("chat")


def register_chat_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册聊天消息主链内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    return registry.register_hook_specs(
        [
            HookSpec(
                name="chat.receive.before_process",
                description="在入站消息执行 `SessionMessage.process()` 之前触发，可拦截或改写消息。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "当前入站消息的序列化 SessionMessage。",
                        },
                    },
                    required=["message"],
                ),
                default_timeout_ms=0,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="chat.receive.after_process",
                description="在入站消息完成轻量预处理后触发，可改写文本、消息体或中止后续链路。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "已完成 `process()` 的序列化 SessionMessage。",
                        },
                    },
                    required=["message"],
                ),
                default_timeout_ms=0,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="chat.command.before_execute",
                description="在命令匹配成功、实际执行前触发，可拦截命令或改写命令上下文。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "当前命令消息的序列化 SessionMessage。",
                        },
                        "command_name": {
                            "type": "string",
                            "description": "命中的命令名称。",
                        },
                        "plugin_id": {
                            "type": "string",
                            "description": "命令所属插件 ID。",
                        },
                        "matched_groups": {
                            "type": "object",
                            "description": "命令正则命名捕获结果。",
                        },
                    },
                    required=["message", "command_name", "plugin_id", "matched_groups"],
                ),
                default_timeout_ms=0,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="chat.command.after_execute",
                description="在命令执行结束后触发，可调整返回文本和是否继续主链处理。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "当前命令消息的序列化 SessionMessage。",
                        },
                        "command_name": {
                            "type": "string",
                            "description": "命令名称。",
                        },
                        "plugin_id": {
                            "type": "string",
                            "description": "命令所属插件 ID。",
                        },
                        "matched_groups": {
                            "type": "object",
                            "description": "命令正则命名捕获结果。",
                        },
                        "success": {
                            "type": "boolean",
                            "description": "命令执行是否成功。",
                        },
                        "response": {
                            "type": "string",
                            "description": "命令返回文本。",
                        },
                        "intercept_message_level": {
                            "type": "integer",
                            "description": "命令拦截等级。",
                        },
                        "continue_process": {
                            "type": "boolean",
                            "description": "命令执行后是否继续后续消息处理。",
                        },
                    },
                    required=[
                        "message",
                        "command_name",
                        "plugin_id",
                        "matched_groups",
                        "success",
                        "intercept_message_level",
                        "continue_process",
                    ],
                ),
                default_timeout_ms=0,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
        ]
    )


class ChatBot:
    """聊天机器人入口协调器。"""

    def __init__(self) -> None:
        """初始化聊天机器人入口。"""

        self.bot = None  # bot 实例引用
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()

    async def _ensure_started(self) -> None:
        """确保所有后台任务已启动。"""
        if not self._started:
            logger.debug("确保ChatBot所有任务已启动")

            self._started = True

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
            default: 转换失败时的默认值。

        Returns:
            int: 转换后的整数结果。
        """

        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _invoke_message_hook(
        self,
        hook_name: str,
        message: SessionMessage,
        **kwargs: Any,
    ) -> tuple[HookDispatchResult, SessionMessage]:
        """触发携带会话消息的命名 Hook。

        Args:
            hook_name: 目标 Hook 名称。
            message: 当前会话消息。
            **kwargs: 需要附带传递的额外参数。

        Returns:
            tuple[HookDispatchResult, SessionMessage]: Hook 聚合结果以及可能被改写后的消息对象。
        """

        hook_result = await self._get_runtime_manager().invoke_hook(
            hook_name,
            message=serialize_session_message(message),
            **kwargs,
        )
        mutated_message = message
        raw_message = hook_result.kwargs.get("message")
        if raw_message is not None:
            try:
                mutated_message = deserialize_session_message(raw_message)
            except Exception as exc:
                logger.warning(f"Hook {hook_name} 返回的 message 无法反序列化，已忽略: {exc}")
        return hook_result, mutated_message

    async def _process_commands(self, message: SessionMessage) -> tuple[bool, Optional[str], bool]:
        """使用统一组件注册表处理命令。

        Args:
            message: 当前待处理的会话消息。

        Returns:
            tuple[bool, Optional[str], bool]: ``(是否命中命令, 命令响应文本, 是否继续后续处理)``。
        """
        if not message.processed_plain_text:
            return False, None, True  # 没有文本内容，继续处理消息
        try:
            text = message.processed_plain_text

            # 使用插件运行时统一查询服务查找命令
            command_result = component_query_service.find_command_by_text(text)
            if command_result:
                command_executor, matched_groups, command_info = command_result
                plugin_name = command_info.plugin_name
                command_name = command_info.name
                if message.session_id and command_name in global_announcement_manager.get_disabled_chat_commands(
                    message.session_id
                ):
                    logger.info("用户禁用的命令，跳过处理")
                    return False, None, True

                message.is_command = True
                before_result, message = await self._invoke_message_hook(
                    "chat.command.before_execute",
                    message,
                    command_name=command_name,
                    plugin_id=plugin_name,
                    matched_groups=dict(matched_groups),
                )
                if before_result.aborted:
                    logger.info(f"命令 {command_name} 被 Hook 中止，跳过命令执行")
                    return True, None, False

                hook_kwargs = before_result.kwargs
                command_name = str(hook_kwargs.get("command_name", command_name) or command_name)
                plugin_name = str(hook_kwargs.get("plugin_id", plugin_name) or plugin_name)
                matched_groups = (
                    dict(hook_kwargs["matched_groups"])
                    if isinstance(hook_kwargs.get("matched_groups"), dict)
                    else dict(matched_groups)
                )

                # 获取插件配置
                plugin_config = component_query_service.get_plugin_config(plugin_name)

                try:
                    # 调用命令执行器
                    success, response, intercept_message_level = await command_executor(
                        message=message,
                        plugin_config=plugin_config,
                        matched_groups=matched_groups,
                    )
                    continue_process = not bool(intercept_message_level)
                except Exception as exc:
                    logger.error(f"执行命令时出错: {command_name} - {exc}")
                    logger.error(traceback.format_exc())
                    success = False
                    response = str(exc)
                    intercept_message_level = 1
                    continue_process = False

                after_result, message = await self._invoke_message_hook(
                    "chat.command.after_execute",
                    message,
                    command_name=command_name,
                    plugin_id=plugin_name,
                    matched_groups=dict(matched_groups),
                    success=success,
                    response=response,
                    intercept_message_level=intercept_message_level,
                    continue_process=continue_process,
                )
                after_kwargs = after_result.kwargs
                success = bool(after_kwargs.get("success", success))
                raw_response = after_kwargs.get("response", response)
                response = None if raw_response is None else str(raw_response)
                intercept_message_level = self._coerce_int(
                    after_kwargs.get("intercept_message_level", intercept_message_level),
                    intercept_message_level,
                )
                continue_process = bool(after_kwargs.get("continue_process", continue_process))
                self._mark_command_message(message, intercept_message_level)

                if success:
                    logger.info(f"命令执行成功: {command_name} (拦截等级: {intercept_message_level})")
                else:
                    logger.warning(f"命令执行失败: {command_name} - {response}")

                return True, response, continue_process

            return False, None, True

        except Exception as e:
            logger.error(f"处理命令时出错: {e}")
            return False, None, True  # 出错时继续处理消息

    async def _process_clear_context_command(self, message: SessionMessage) -> bool:
        """处理内置 ``/clear`` 指令并清空当前聊天流的 Maisaka 上下文。"""

        if not global_config.debug.enable_clear_context_command:
            return False
        if not is_clear_context_command(message.processed_plain_text):
            return False

        mark_clear_context_command(message)
        await MessageUtils.store_message_to_db_async(message)
        had_runtime = await heartflow_manager.clear_chat_history_context(message.session_id)

        from src.services.send_service import text_to_stream

        sent = await text_to_stream(
            "已清空当前聊天的 Maisaka 历史上下文。",
            message.session_id,
            storage_message=False,
        )
        if not sent:
            logger.warning(f"Maisaka 历史上下文已清空，但确认消息发送失败: session_id={message.session_id}")
        logger.info(
            f"已通过 /clear 清空 Maisaka 历史上下文: "
            f"session_id={message.session_id} 运行时是否存在={had_runtime}"
        )
        return True

    @staticmethod
    def _mark_command_message(message: SessionMessage, intercept_message_level: int) -> None:
        """标记消息已经被命令链消费。

        Args:
            message: 待标记的会话消息。
            intercept_message_level: 命令设置的拦截级别。
        """

        message.is_command = True
        message.message_info.additional_config["intercept_message_level"] = intercept_message_level

    @staticmethod
    async def _store_intercepted_command_message(message: SessionMessage) -> None:
        """将被命令链拦截的消息写入数据库。

        Args:
            message: 已完成命令处理的会话消息。
        """

        await MessageUtils.store_message_to_db_async(message)

    async def _handle_command_processing_result(
        self,
        message: SessionMessage,
        cmd_result: Optional[str],
        continue_process: bool,
    ) -> bool:
        """处理命令链结果并决定是否终止主消息链。

        Args:
            message: 当前命令消息。
            cmd_result: 命令响应文本。
            continue_process: 是否继续后续主链处理。

        Returns:
            bool: ``True`` 表示已经终止后续主链。
        """

        if continue_process:
            return False

        await self._store_intercepted_command_message(message)
        logger.info(f"命令处理完成，跳过后续消息处理: {cmd_result}")
        return True

    async def handle_notice_message(self, message: SessionMessage) -> bool:
        """处理通知类消息（戳一戳、撤回、禁言、入群退群等）。

        适配器通过 ``is_notify`` 字段标识通知消息，同时通过
        ``additional_config`` 中的 ``napcat_notice_type``、``napcat_notice_sub_type``
        和 ``napcat_notice_payload`` 携带原始通知事件的详细信息。

        Args:
            message: 当前通知消息（已由适配器设置 ``is_notify=True``）。

        Returns:
            bool: 当前消息是通知消息时返回 ``True``，否则返回 ``False``。
        """

        if not message.is_notify:
            return False

        additional_config = message.message_info.additional_config
        if not isinstance(additional_config, dict):
            return False

        # 通知消息由适配器完整格式化（含 [事件-xxx] 前缀及详情），
        # 此处仅做类型识别，具体文本由消息正常链路输出，避免重复日志。
        return True

    async def echo_message_process(self, raw_data: Dict[str, Any]) -> None:
        """处理消息回送 ID 对应关系。

        Args:
            raw_data: 平台适配器上报的原始回送载荷。
        """

        message_data: Dict[str, Any] = raw_data.get("content", {})
        if not message_data:
            return
        message_type = message_data.get("type")
        if message_type != "echo":
            return
        mmc_message_id = message_data.get("echo")
        actual_message_id = message_data.get("actual_id")
        normalized_mmc_message_id = str(mmc_message_id or "").strip()
        normalized_actual_message_id = str(actual_message_id or "").strip()
        if not normalized_mmc_message_id or not normalized_actual_message_id:
            return

        updated = await MessageUtils.update_message_id_async(
            old_message_id=normalized_mmc_message_id,
            new_message_id=normalized_actual_message_id,
        )
        if updated:
            logger.debug(f"收到回送消息ID: {normalized_mmc_message_id} -> {normalized_actual_message_id}")
            return

        logger.debug(
            f"收到回送消息 ID，但未找到可回填的本地消息: {normalized_mmc_message_id} -> {normalized_actual_message_id}"
        )

    async def message_process(self, message_data: Dict[str, Any]) -> None:
        """处理统一格式的入站消息字典。

        Args:
            message_data: 适配器整理后的统一消息字典。
        """
        try:
            # 确保所有任务已启动
            await self._ensure_started()

            if message_data["message_info"].get("group_info") is not None:
                message_data["message_info"]["group_info"]["group_id"] = str(
                    message_data["message_info"]["group_info"]["group_id"]
                )
            if message_data["message_info"].get("user_info") is not None:
                message_data["message_info"]["user_info"]["user_id"] = str(
                    message_data["message_info"]["user_info"]["user_id"]
                )
            # print(message_data)
            # logger.debug(str(message_data))
            maim_raw_message = MessageBase.from_dict(message_data)
            message = SessionMessage.from_maim_message(maim_raw_message)
            await self.receive_message(message)

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()

    async def receive_message(self, message: SessionMessage) -> None:
        """处理单条入站会话消息。

        Args:
            message: 待处理的会话消息。
        """

        try:
            group_info = message.message_info.group_info
            user_info = message.message_info.user_info
            account_id = None
            scope = None
            additional_config = message.message_info.additional_config
            if isinstance(additional_config, dict):
                account_id, scope = RouteKeyFactory.extract_components(additional_config)

            session_id = SessionUtils.calculate_session_id(
                message.platform,
                user_id=message.message_info.user_info.user_id,
                group_id=group_info.group_id if group_info else None,
                account_id=account_id,
                scope=scope,
            )

            message.session_id = session_id  # 正确初始化session_id
            image_process_report = process_received_images_in_message(message.raw_message.components)
            if image_process_report.compressed_count or image_process_report.discarded_count:
                image_process_details = []
                if image_process_report.compressed_count:
                    image_process_details.append(
                        f"压缩 {image_process_report.compressed_count} 张，"
                        f"{image_process_report.original_bytes / 1024:.1f}KB -> "
                        f"{image_process_report.compressed_bytes / 1024:.1f}KB"
                    )
                if image_process_report.discarded_count:
                    image_process_details.append(
                        f"丢弃 {image_process_report.discarded_count} 张，"
                        f"{image_process_report.discarded_bytes / 1024:.1f}KB"
                    )
                logger.info(
                    f"消息 {message.message_id} 入站过大图片处理完成: "
                    f"{'；'.join(image_process_details)}"
                )

            before_process_result, message = await self._invoke_message_hook(
                "chat.receive.before_process",
                message,
            )
            if before_process_result.aborted:
                logger.info(f"消息 {message.message_id} 在预处理前被 Hook 中止")
                return

            group_info = message.message_info.group_info
            user_info = message.message_info.user_info
            additional_config = message.message_info.additional_config
            if isinstance(additional_config, dict):
                account_id, scope = RouteKeyFactory.extract_components(additional_config)

            # 通知消息（戳一戳、撤回、禁言等）由适配器标记 is_notify=True，
            await self.handle_notice_message(message)

            # 处理消息内容，识别表情包等二进制数据并转化为文本描述。
            # 如果 Maisaka 需要直接消费图片，会在后续构建 prompt 时按需回填图片二进制数据，
            # 这里不再复制整条原始消息。
            # 入站主链优先保证消息尽快入队，避免图片、表情包、语音分析阻塞适配器超时。
            await message.process(
                enable_heavy_media_analysis=False,
                enable_voice_transcription=global_config.voice.enable_asr,
            )
            after_process_result, message = await self._invoke_message_hook(
                "chat.receive.after_process",
                message,
            )
            if after_process_result.aborted:
                logger.info(f"消息 {message.message_id} 在预处理后被 Hook 中止")
                return

            group_info = message.message_info.group_info
            user_info = message.message_info.user_info

            # 平台层的 @ 检测由底层 is_mentioned_bot_in_message 统一处理；此处不做用户名硬编码匹配

            # 过滤检查
            text = message.processed_plain_text or ""
            is_banned, word = MessageUtils.check_ban_words(text)
            if is_banned:
                chat_name = group_info.group_name if group_info else "私聊"
                logger.info(f"[{chat_name}]{user_info.user_nickname}:{text}")
                logger.info(f"[过滤词识别]消息中含有{word}，filtered")
                return
            is_banned_regex, pattern = MessageUtils.check_ban_regex(text)
            if is_banned_regex:
                chat_name = group_info.group_name if group_info else "私聊"
                logger.info(f"[{chat_name}]{user_info.user_nickname}:{text}")
                logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
                return

            chat_manager.register_message(message)

            platform = message.platform
            user_id = user_info.user_id
            group_id = group_info.group_id if group_info else None
            _ = await chat_manager.get_or_create_session(
                platform,
                user_id,
                group_id,
                account_id=account_id,
                scope=scope,
            )  # 确保会话存在

            # message.update_chat_stream(chat)

            # 调试用内置指令需要先写入持久化清理边界，再停止当前运行时，
            # 避免并发消息或进程重启重新带回清理前的短期上下文。
            if await self._process_clear_context_command(message):
                return

            # 命令处理 - 使用新插件系统检查并处理命令。
            # 命令处理器内部自行决定是否回复消息，这里只负责流程分发与拦截。
            is_command, cmd_result, continue_process = await self._process_commands(message)

            # 如果是命令且不需要继续处理，则直接返回，避免落入 HeartFlow / MaiSaka。
            if is_command and await self._handle_command_processing_result(message, cmd_result, continue_process):
                return

            # continue_flag, modified_message = await events_manager.handle_mai_events(EventType.ON_MESSAGE, message)
            # if not continue_flag:
            #     return
            # if modified_message and modified_message._modify_flags.modify_plain_text:
            #     message.processed_plain_text = modified_message.plain_text

            async def preprocess():
                if group_info is None:
                    logger.debug("[私聊]检测到私聊消息，路由到 Maisaka")
                    await self.heartflow_message_receiver.process_message(message)
                else:
                    logger.debug("[群聊]检测到群聊消息，路由到 Maisaka")
                    await self.heartflow_message_receiver.process_message(message)

            await preprocess()

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()


# 创建全局ChatBot实例
chat_bot = ChatBot()

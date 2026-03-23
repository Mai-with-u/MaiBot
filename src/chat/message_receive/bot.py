from contextlib import suppress
from typing import Any, Dict, Optional

import os
import traceback

from maim_message import MessageBase

from src.chat.heart_flow.heartflow_message_processor import HeartFCMessageReceiver
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.common.utils.utils_session import SessionUtils

# from src.chat.brain_chat.PFC.pfc_manager import PFCManager
from src.core.announcement_manager import global_announcement_manager
from src.plugin_runtime.component_query import component_query_service

from .message import SessionMessage
from .chat_manager import chat_manager

# 定义日志配置

# 获取项目根目录（假设本文件在src/chat/message_receive/下，根目录为上上上级目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))

# 配置主程序日志格式
logger = get_logger("chat")


class ChatBot:
    def __init__(self):
        self.bot = None  # bot 实例引用
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()  # 新增
        # self.pfc_manager = PFCManager.get_instance()  # PFC管理器 # TODO: PFC恢复

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            logger.debug("确保ChatBot所有任务已启动")

            self._started = True

    async def _create_pfc_chat(self, message: SessionMessage):
        """创建或获取PFC对话实例

        Args:
            message: 消息对象
        """
        try:
            chat_id = message.session_id
            private_name = str(message.message_info.user_info.user_nickname)

            logger.debug(f"[私聊][{private_name}]创建或获取PFC对话: {chat_id}")
            await self.pfc_manager.get_or_create_conversation(chat_id, private_name)

        except Exception as e:
            logger.error(f"创建PFC聊天失败: {e}")
            logger.error(traceback.format_exc())

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

                # 获取插件配置
                plugin_config = component_query_service.get_plugin_config(plugin_name)

                try:
                    # 调用命令执行器
                    success, response, intercept_message_level = await command_executor(
                        message=message,
                        plugin_config=plugin_config,
                        matched_groups=matched_groups,
                    )
                    self._mark_command_message(message, intercept_message_level)

                    # 记录命令执行结果
                    if success:
                        logger.info(f"命令执行成功: {command_name} (拦截等级: {intercept_message_level})")
                    else:
                        logger.warning(f"命令执行失败: {command_name} - {response}")

                    # 根据命令的拦截设置决定是否继续处理消息
                    return (
                        True,
                        response,
                        not bool(intercept_message_level),
                    )  # 找到命令，根据intercept_message决定是否继续

                except Exception as e:
                    logger.error(f"执行命令时出错: {command_name} - {e}")
                    logger.error(traceback.format_exc())

                    # 命令出错时，根据命令的拦截设置决定是否继续处理消息
                    return True, str(e), False  # 出错时继续处理消息

            return False, None, True

        except Exception as e:
            logger.error(f"处理命令时出错: {e}")
            return False, None, True  # 出错时继续处理消息

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
    def _store_intercepted_command_message(message: SessionMessage) -> None:
        """将被命令链拦截的消息写入数据库。

        Args:
            message: 已完成命令处理的会话消息。
        """

        MessageUtils.store_message_to_db(message)

    async def _handle_command_processing_result(
        self,
        message: SessionMessage,
        cmd_result: Optional[str],
        continue_process: bool,
    ) -> bool:
        if continue_process:
            return False

        self._store_intercepted_command_message(message)
        logger.info(f"命令处理完成，跳过后续消息处理: {cmd_result}")
        return True

    async def handle_notice_message(self, message: SessionMessage):
        if message.message_id != "notice":
            return

        message.is_notify = True
        logger.debug("notice消息")
        try:
            seg = getattr(message, "message_segment", None)  # SessionMessage 没有 message_segment
            mi = message.message_info
            sub_type = None
            scene = None
            msg_id = None
            recalled: Dict[str, Any] = {}
            recalled_id = None

            if getattr(seg, "type", None) == "notify" and isinstance(getattr(seg, "data", None), dict):
                sub_type = seg.data.get("sub_type")
                scene = seg.data.get("scene")
                msg_id = seg.data.get("message_id")
                recalled = seg.data.get("recalled_user_info") or {}
                if isinstance(recalled, dict):
                    recalled_id = recalled.get("user_id")

            op = mi.user_info
            gid = mi.group_info.group_id if mi.group_info else None

            # 撤回事件打印；无法获取被撤回者则省略
            if sub_type == "recall":
                op_name = (
                    getattr(op, "user_cardname", None)
                    or getattr(op, "user_nickname", None)
                    or str(getattr(op, "user_id", None))
                )
                recalled_name = None
                with suppress(Exception):
                    if isinstance(recalled, dict):
                        recalled_name = (
                            recalled.get("user_cardname")
                            or recalled.get("user_nickname")
                            or str(recalled.get("user_id"))
                        )

                if recalled_name and str(recalled_id) != str(getattr(op, "user_id", None)):
                    logger.info(f"{op_name} 撤回了 {recalled_name} 的消息")
                else:
                    logger.info(f"{op_name} 撤回了消息")
            else:
                logger.debug(
                    f"[notice] sub_type={sub_type} scene={scene} op={getattr(op, 'user_nickname', None)}({getattr(op, 'user_id', None)}) "
                    f"gid={gid} msg_id={msg_id} recalled={recalled_id}"
                )
        except Exception:
            logger.info("[notice] (简略) 收到一条通知事件")

        return True

    async def echo_message_process(self, raw_data: Dict[str, Any]) -> None:
        """
        用于专门处理回送消息ID的函数
        """
        message_data: Dict[str, Any] = raw_data.get("content", {})
        if not message_data:
            return
        message_type = message_data.get("type")
        if message_type != "echo":
            return
        mmc_message_id = message_data.get("echo")
        actual_message_id = message_data.get("actual_id")
        # TODO: Implement message ID update in new architecture
        logger.debug(f"收到回送消息ID: {mmc_message_id} -> {actual_message_id}")

    async def message_process(self, message_data: Dict[str, Any]) -> None:
        """处理转化后的统一格式消息
        这个函数本质是预处理一些数据，根据配置信息和消息内容，预处理消息，并分发到合适的消息处理器中
        heart_flow模式：使用思维流系统进行回复
        - 包含思维流状态管理
        - 在回复前进行观察和状态更新
        - 回复后更新思维流状态
        - 消息过滤
        - 记忆激活
        - 意愿计算
        - 消息生成和发送
        - 表情包处理
        - 性能计时
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

    async def receive_message(self, message: SessionMessage):
        try:
            group_info = message.message_info.group_info
            user_info = message.message_info.user_info

            session_id = SessionUtils.calculate_session_id(
                message.platform,
                user_id=message.message_info.user_info.user_id,
                group_id=group_info.group_id if group_info else None,
            )

            message.session_id = session_id  # 正确初始化session_id

            # TODO: 修复事件预处理部分
            # continue_flag, modified_message = await events_manager.handle_mai_events(
            #     EventType.ON_MESSAGE_PRE_PROCESS, message
            # )
            # if not continue_flag:
            #     return
            # if modified_message and modified_message._modify_flags.modify_message_segments:
            #     message.message_segment = Seg(type="seglist", data=modified_message.message_segments)

            # TODO: notice消息处理
            # if await self.handle_notice_message(message):
            #     pass

            # 处理消息内容，识别表情包等二进制数据并转化为文本描述
            await message.process()

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
            _ = await chat_manager.get_or_create_session(platform, user_id, group_id)  # 确保会话存在

            # message.update_chat_stream(chat)

            # 命令处理 - 使用新插件系统检查并处理命令
            # 注意：命令返回的 response 当前只用于日志记录和流程判断，
            # 不会在这里自动作为回复消息发送回会话。
            # is_command, cmd_result, continue_process = await self._process_commands(message)

            # # 如果是命令且不需要继续处理，则直接返回
            # if is_command and await self._handle_command_processing_result(message, cmd_result, continue_process):
            #     return

            # continue_flag, modified_message = await events_manager.handle_mai_events(EventType.ON_MESSAGE, message)
            # if not continue_flag:
            #     return
            # if modified_message and modified_message._modify_flags.modify_plain_text:
            #     message.processed_plain_text = modified_message.plain_text

            # # 确认从接口发来的message是否有自定义的prompt模板信息
            # if message.message_info.template_info and not message.message_info.template_info.template_default:
            #     template_group_name: Optional[str] = message.message_info.template_info.template_name  # type: ignore
            #     template_items = message.message_info.template_info.template_items
            #     async with global_prompt_manager.async_message_scope(template_group_name):
            #         if isinstance(template_items, dict):
            #             for k in template_items.keys():
            #                 await Prompt.create_async(template_items[k], k)
            #                 logger.debug(f"注册{template_items[k]},{k}")
            # else:
            #     template_group_name = None

            # async def preprocess():
            #     # 根据聊天类型路由消息
            #     if group_info is None:
            #         # 私聊消息 -> PFC系统
            #         logger.debug("[私聊]检测到私聊消息，路由到PFC系统")
            #         await MessageStorage.store_message(message, chat)
            #         await self._create_pfc_chat(message)
            #     else:
            #         # 群聊消息 -> HeartFlow系统
            #         logger.debug("[群聊]检测到群聊消息，路由到HeartFlow系统")
            #         await self.heartflow_message_receiver.process_message(message)

            # if template_group_name:
            #     async with global_prompt_manager.async_message_scope(template_group_name):
            #         await preprocess()
            # else:
            #     await preprocess()
            async def preprocess():
                if group_info is None:
                    # logger.debug("[私聊]检测到私聊消息，路由到PFC系统")
                    # MessageUtils.store_message_to_db(message)  # 存储消息到数据库
                    # await self._create_pfc_chat(message)
                    logger.critical("暂时禁用私聊")
                else:
                    logger.debug("[群聊]检测到群聊消息，路由到HeartFlow系统")
                    await self.heartflow_message_receiver.process_message(message)

            await preprocess()

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()


# 创建全局ChatBot实例
chat_bot = ChatBot()

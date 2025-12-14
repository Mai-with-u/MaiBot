import time
import asyncio
import datetime

# from .message_storage import MongoDBMessageStorage
from src.plugins.utils.chat_message_builder import build_readable_messages, get_raw_msg_before_timestamp_with_chat

# from ...config.config import global_config
from typing import Dict, Any, Optional
from ..chat.message import Message
from .pfc_types import ConversationState
from .pfc import ChatObserver, GoalAnalyzer
from .message_sender import DirectMessageSender
from src.common.logger_manager import get_logger
from .action_planner import ActionPlanner
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo  # 确保导入 ConversationInfo
from .reply_generator import ReplyGenerator
from ..chat.chat_stream import ChatStream
from maim_message import UserInfo
from src.plugins.chat.chat_stream import chat_manager
from .pfc_KnowledgeFetcher import KnowledgeFetcher
from .waiter import Waiter

import traceback
from rich.traceback import install

install(extra_lines=3)

logger = get_logger("pfc")


class Conversation:
    """对话类，负责管理单个对话的状态和行为"""

    def __init__(self, stream_id: str, private_name: str):
        """初始化对话实例

        Args:
            stream_id: 聊天流ID
        """
        self.stream_id = stream_id
        self.private_name = private_name
        self.state = ConversationState.INIT
        self.should_continue = False
        self.ignore_until_timestamp: Optional[float] = None

        # 回复相关
        self.generated_reply = ""

    async def _initialize(self):
        """初始化实例，注册所有组件"""

        try:
            self.action_planner = ActionPlanner(self.stream_id, self.private_name)
            self.goal_analyzer = GoalAnalyzer(self.stream_id, self.private_name)
            self.reply_generator = ReplyGenerator(self.stream_id, self.private_name)
            self.knowledge_fetcher = KnowledgeFetcher(self.private_name)
            self.waiter = Waiter(self.stream_id, self.private_name)
            self.direct_sender = DirectMessageSender(self.private_name)

            # 获取聊天流信息
            self.chat_stream = chat_manager.get_stream(self.stream_id)

            self.stop_action_planner = False
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]初始化对话实例：注册运行组件失败: {e}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
            raise

        try:
            # 决策所需要的信息，包括自身自信和观察信息两部分
            # 注册观察器和观测信息
            self.chat_observer = ChatObserver.get_instance(self.stream_id, self.private_name)
            self.chat_observer.start()
            self.observation_info = ObservationInfo(self.private_name)
            self.observation_info.bind_to_chat_observer(self.chat_observer)
            # print(self.chat_observer.get_cached_messages(limit=)

            self.conversation_info = ConversationInfo()
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]初始化对话实例：注册信息组件失败: {e}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
            raise
        try:
            logger.info(f"[私聊][{self.private_name}]为 {self.stream_id} 加载初始聊天记录...")
            initial_messages = get_raw_msg_before_timestamp_with_chat(  #
                chat_id=self.stream_id,
                timestamp=time.time(),
                limit=30,  # 加载最近30条作为初始上下文，可以调整
            )
            chat_talking_prompt = await build_readable_messages(
                initial_messages,
                replace_bot_name=True,
                merge_messages=False,
                timestamp_mode="relative",
                read_mark=0.0,
            )
            if initial_messages:
                # 将加载的消息填充到 ObservationInfo 的 chat_history
                self.observation_info.chat_history = initial_messages
                self.observation_info.chat_history_str = chat_talking_prompt + "\n"
                self.observation_info.chat_history_count = len(initial_messages)

                # 更新 ObservationInfo 中的时间戳等信息
                last_msg = initial_messages[-1]
                self.observation_info.last_message_time = last_msg.get("time")
                last_user_info = UserInfo.from_dict(last_msg.get("user_info", {}))
                self.observation_info.last_message_sender = last_user_info.user_id
                self.observation_info.last_message_content = last_msg.get("processed_plain_text", "")

                logger.info(
                    f"[私聊][{self.private_name}]成功加载 {len(initial_messages)} 条初始聊天记录。最后一条消息时间: {self.observation_info.last_message_time}"
                )

                # 让 ChatObserver 从加载的最后一条消息之后开始同步
                self.chat_observer.last_message_time = self.observation_info.last_message_time
                self.chat_observer.last_message_read = last_msg  # 更新 observer 的最后读取记录
            else:
                logger.info(f"[私聊][{self.private_name}]没有找到初始聊天记录。")

        except Exception as load_err:
            logger.error(f"[私聊][{self.private_name}]加载初始聊天记录时出错: {load_err}")
            # 出错也要继续，只是没有历史记录而已
        # 组件准备完成，启动该论对话
        self.should_continue = True
        asyncio.create_task(self.start())

    async def start(self):
        """开始对话流程"""
        try:
            logger.info(f"[私聊][{self.private_name}]对话系统启动中...")
            asyncio.create_task(self._plan_and_action_loop())
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]启动对话系统失败: {e}")
            raise

    async def _plan_and_action_loop(self):
        """思考步，PFC核心循环模块"""
        while self.should_continue:
            # 忽略逻辑
            if self.ignore_until_timestamp and time.time() < self.ignore_until_timestamp:
                await asyncio.sleep(30)
                continue
            elif self.ignore_until_timestamp and time.time() >= self.ignore_until_timestamp:
                logger.info(f"[私聊][{self.private_name}]忽略时间已到 {self.stream_id}，准备结束对话。")
                self.ignore_until_timestamp = None
                self.should_continue = False
                continue
            try:
                # --- 在规划前记录当前新消息数量 ---
                initial_new_message_count = 0
                if hasattr(self.observation_info, "new_messages_count"):
                    initial_new_message_count = self.observation_info.new_messages_count + 1  # 算上麦麦自己发的那一条
                else:
                    logger.warning(
                        f"[私聊][{self.private_name}]ObservationInfo missing 'new_messages_count' before planning."
                    )

                # --- 调用 Action Planner ---
                # 传递 self.conversation_info.last_successful_reply_action
                action, reason = await self.action_planner.plan(
                    self.observation_info, self.conversation_info, self.conversation_info.last_successful_reply_action
                )

                # --- 规划后检查是否有 *更多* 新消息到达 ---
                current_new_message_count = 0
                if hasattr(self.observation_info, "new_messages_count"):
                    current_new_message_count = self.observation_info.new_messages_count
                else:
                    logger.warning(
                        f"[私聊][{self.private_name}]ObservationInfo missing 'new_messages_count' after planning."
                    )

                if current_new_message_count > initial_new_message_count + 2:
                    logger.info(
                        f"[私聊][{self.private_name}]规划期间发现新增消息 ({initial_new_message_count} -> {current_new_message_count})，跳过本次行动，重新规划"
                    )
                    # 如果规划期间有新消息，也应该重置上次回复状态，因为现在要响应新消息了
                    self.conversation_info.last_successful_reply_action = None
                    await asyncio.sleep(0.1)
                    continue

                #  包含 send_new_message
                if initial_new_message_count > 0 and action in ["direct_reply", "send_new_message"]:
                    if hasattr(self.observation_info, "clear_unprocessed_messages"):
                        logger.debug(
                            f"[私聊][{self.private_name}]准备执行 {action}，清理 {initial_new_message_count} 条规划时已知的新消息。"
                        )
                        await self.observation_info.clear_unprocessed_messages()
                        if hasattr(self.observation_info, "new_messages_count"):
                            self.observation_info.new_messages_count = 0
                    else:
                        logger.error(
                            f"[私聊][{self.private_name}]无法清理未处理消息: ObservationInfo 缺少 clear_unprocessed_messages 方法！"
                        )

                await self._handle_action(action, reason, self.observation_info, self.conversation_info)

                # 检查是否需要结束对话 (逻辑不变)
                goal_ended = False
                if hasattr(self.conversation_info, "goal_list") and self.conversation_info.goal_list:
                    for goal_item in self.conversation_info.goal_list:
                        if isinstance(goal_item, dict):
                            current_goal = goal_item.get("goal")

                        if current_goal == "结束对话":
                            goal_ended = True
                            break

                if goal_ended:
                    self.should_continue = False
                    logger.info(f"[私聊][{self.private_name}]检测到'结束对话'目标，停止循环。")

            except Exception as loop_err:
                logger.error(f"[私聊][{self.private_name}]PFC主循环出错: {loop_err}")
                logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
                await asyncio.sleep(1)

            if self.should_continue:
                await asyncio.sleep(0.1)

        logger.info(f"[私聊][{self.private_name}]PFC 循环结束 for stream_id: {self.stream_id}")

    def _check_new_messages_after_planning(self):
        """检查在规划后是否有新消息"""
        # 检查 ObservationInfo 是否已初始化并且有 new_messages_count 属性
        if not hasattr(self, "observation_info") or not hasattr(self.observation_info, "new_messages_count"):
            logger.warning(
                f"[私聊][{self.private_name}]ObservationInfo 未初始化或缺少 'new_messages_count' 属性，无法检查新消息。"
            )
            return False  # 或者根据需要抛出错误

        if self.observation_info.new_messages_count > 2:
            logger.info(
                f"[私聊][{self.private_name}]生成/执行动作期间收到 {self.observation_info.new_messages_count} 条新消息，取消当前动作并重新规划"
            )
            # 如果有新消息，也应该重置上次回复状态
            if hasattr(self, "conversation_info"):  # 确保 conversation_info 已初始化
                self.conversation_info.last_successful_reply_action = None
            else:
                logger.warning(
                    f"[私聊][{self.private_name}]ConversationInfo 未初始化，无法重置 last_successful_reply_action。"
                )
            return True
        return False

    def _convert_to_message(self, msg_dict: Dict[str, Any]) -> Message:
        """将消息字典转换为Message对象"""
        try:
            # 尝试从 msg_dict 直接获取 chat_stream，如果失败则从全局 chat_manager 获取
            chat_info = msg_dict.get("chat_info")
            if chat_info and isinstance(chat_info, dict):
                chat_stream = ChatStream.from_dict(chat_info)
            elif self.chat_stream:  # 使用实例变量中的 chat_stream
                chat_stream = self.chat_stream
            else:  # Fallback: 尝试从 manager 获取 (可能需要 stream_id)
                chat_stream = chat_manager.get_stream(self.stream_id)
                if not chat_stream:
                    raise ValueError(f"无法确定 ChatStream for stream_id {self.stream_id}")

            user_info = UserInfo.from_dict(msg_dict.get("user_info", {}))

            return Message(
                message_id=msg_dict.get("message_id", f"gen_{time.time()}"),  # 提供默认 ID
                chat_stream=chat_stream,  # 使用确定的 chat_stream
                time=msg_dict.get("time", time.time()),  # 提供默认时间
                user_info=user_info,
                processed_plain_text=msg_dict.get("processed_plain_text", ""),
                detailed_plain_text=msg_dict.get("detailed_plain_text", ""),
            )
        except Exception as e:
            logger.warning(f"[私聊][{self.private_name}]转换消息时出错: {e}")
            # 可以选择返回 None 或重新抛出异常，这里选择重新抛出以指示问题
            raise ValueError(f"无法将字典转换为 Message 对象: {e}") from e

    async def _handle_action(
        self, action: str, reason: str, observation_info: ObservationInfo, conversation_info: ConversationInfo
    ):
        """处理规划的行动"""

        logger.debug(f"[私聊][{self.private_name}]执行行动: {action}, 原因: {reason}")

        # 记录action历史 (逻辑不变)
        current_action_record = {
            "action": action,
            "plan_reason": reason,
            "status": "start",
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "final_reason": None,
        }
        # 确保 done_action 列表存在
        if not hasattr(conversation_info, "done_action"):
            conversation_info.done_action = []
        conversation_info.done_action.append(current_action_record)
        action_index = len(conversation_info.done_action) - 1

        action_successful = False  # 用于标记动作是否成功完成

        # --- 根据不同的 action 执行 ---

        # send_new_message 失败后执行 wait
        if action == "send_new_message":
            max_reply_attempts = 3
            reply_attempt_count = 0
            is_suitable = False
            need_replan = False
            check_reason = "未进行尝试"
            final_reply_to_send = ""

            while reply_attempt_count < max_reply_attempts and not is_suitable:
                reply_attempt_count += 1
                logger.info(
                    f"[私聊][{self.private_name}]尝试生成追问回复 (第 {reply_attempt_count}/{max_reply_attempts} 次)..."
                )
                self.state = ConversationState.GENERATING

                # 1. 生成回复 (调用 generate 时传入 action_type)
                self.generated_reply = await self.reply_generator.generate(
                    observation_info, conversation_info, action_type="send_new_message"
                )
                logger.info(
                    f"[私聊][{self.private_name}]第 {reply_attempt_count} 次生成的追问回复: {self.generated_reply}"
                )

                # 2. 检查回复 (逻辑不变)
                self.state = ConversationState.CHECKING
                try:
                    current_goal_str = conversation_info.goal_list[0]["goal"] if conversation_info.goal_list else ""
                    is_suitable, check_reason, need_replan = await self.reply_generator.check_reply(
                        reply=self.generated_reply,
                        goal=current_goal_str,
                        chat_history=observation_info.chat_history,
                        chat_history_str=observation_info.chat_history_str,
                        retry_count=reply_attempt_count - 1,
                    )
                    logger.info(
                        f"[私聊][{self.private_name}]第 {reply_attempt_count} 次追问检查结果: 合适={is_suitable}, 原因='{check_reason}', 需重新规划={need_replan}"
                    )
                    if is_suitable:
                        final_reply_to_send = self.generated_reply
                        break
                    elif need_replan:
                        logger.warning(
                            f"[私聊][{self.private_name}]第 {reply_attempt_count} 次追问检查建议重新规划，停止尝试。原因: {check_reason}"
                        )
                        break
                except Exception as check_err:
                    logger.error(
                        f"[私聊][{self.private_name}]第 {reply_attempt_count} 次调用 ReplyChecker (追问) 时出错: {check_err}"
                    )
                    check_reason = f"第 {reply_attempt_count} 次检查过程出错: {check_err}"
                    break

            # 循环结束，处理最终结果
            if is_suitable:
                # 检查是否有新消息
                if self._check_new_messages_after_planning():
                    logger.info(f"[私聊][{self.private_name}]生成追问回复期间收到新消息，取消发送，重新规划行动")
                    conversation_info.done_action[action_index].update(
                        {"status": "recall", "final_reason": f"有新消息，取消发送追问: {final_reply_to_send}"}
                    )
                    return  # 直接返回，重新规划

                # 发送合适的回复
                self.generated_reply = final_reply_to_send
                # --- 在这里调用 _send_reply ---
                await self._send_reply()  # <--- 调用恢复后的函数

                # 更新状态: 标记上次成功是 send_new_message
                self.conversation_info.last_successful_reply_action = "send_new_message"
                action_successful = True  # 标记动作成功

            elif need_replan:
                # 打回动作决策
                logger.warning(
                    f"[私聊][{self.private_name}]经过 {reply_attempt_count} 次尝试，追问回复决定打回动作决策。打回原因: {check_reason}"
                )
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"追问尝试{reply_attempt_count}次后打回: {check_reason}"}
                )

            else:
                # 追问失败
                logger.warning(
                    f"[私聊][{self.private_name}]经过 {reply_attempt_count} 次尝试，未能生成合适的追问回复。最终原因: {check_reason}"
                )
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"追问尝试{reply_attempt_count}次后失败: {check_reason}"}
                )
                # 重置状态: 追问失败，下次用初始 prompt
                self.conversation_info.last_successful_reply_action = None

                # 执行 Wait 操作
                logger.info(f"[私聊][{self.private_name}]由于无法生成合适追问回复，执行 'wait' 操作...")
                self.state = ConversationState.WAITING
                await self.waiter.wait(self.conversation_info)
                wait_action_record = {
                    "action": "wait",
                    "plan_reason": "因 send_new_message 多次尝试失败而执行的后备等待",
                    "status": "done",
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    "final_reason": None,
                }
                conversation_info.done_action.append(wait_action_record)

        elif action == "direct_reply":
            max_reply_attempts = 3
            reply_attempt_count = 0
            is_suitable = False
            need_replan = False
            check_reason = "未进行尝试"
            final_reply_to_send = ""

            while reply_attempt_count < max_reply_attempts and not is_suitable:
                reply_attempt_count += 1
                logger.info(
                    f"[私聊][{self.private_name}]尝试生成首次回复 (第 {reply_attempt_count}/{max_reply_attempts} 次)..."
                )
                self.state = ConversationState.GENERATING

                # 1. 生成回复
                self.generated_reply = await self.reply_generator.generate(
                    observation_info, conversation_info, action_type="direct_reply"
                )
                logger.info(
                    f"[私聊][{self.private_name}]第 {reply_attempt_count} 次生成的首次回复: {self.generated_reply}"
                )

                # 2. 检查回复
                self.state = ConversationState.CHECKING
                try:
                    current_goal_str = conversation_info.goal_list[0]["goal"] if conversation_info.goal_list else ""
                    is_suitable, check_reason, need_replan = await self.reply_generator.check_reply(
                        reply=self.generated_reply,
                        goal=current_goal_str,
                        chat_history=observation_info.chat_history,
                        chat_history_str=observation_info.chat_history_str,
                        retry_count=reply_attempt_count - 1,
                    )
                    logger.info(
                        f"[私聊][{self.private_name}]第 {reply_attempt_count} 次首次回复检查结果: 合适={is_suitable}, 原因='{check_reason}', 需重新规划={need_replan}"
                    )
                    if is_suitable:
                        final_reply_to_send = self.generated_reply
                        break
                    elif need_replan:
                        logger.warning(
                            f"[私聊][{self.private_name}]第 {reply_attempt_count} 次首次回复检查建议重新规划，停止尝试。原因: {check_reason}"
                        )
                        break
                except Exception as check_err:
                    logger.error(
                        f"[私聊][{self.private_name}]第 {reply_attempt_count} 次调用 ReplyChecker (首次回复) 时出错: {check_err}"
                    )
                    check_reason = f"第 {reply_attempt_count} 次检查过程出错: {check_err}"
                    break

            # 循环结束，处理最终结果
            if is_suitable:
                # 检查是否有新消息
                if self._check_new_messages_after_planning():
                    logger.info(f"[私聊][{self.private_name}]生成首次回复期间收到新消息，取消发送，重新规划行动")
                    conversation_info.done_action[action_index].update(
                        {"status": "recall", "final_reason": f"有新消息，取消发送首次回复: {final_reply_to_send}"}
                    )
                    return  # 直接返回，重新规划

                # 发送合适的回复
                self.generated_reply = final_reply_to_send
                # --- 在这里调用 _send_reply ---
                await self._send_reply()  # <--- 调用恢复后的函数

                # 更新状态: 标记上次成功是 direct_reply
                self.conversation_info.last_successful_reply_action = "direct_reply"
                action_successful = True  # 标记动作成功

            elif need_replan:
                # 打回动作决策
                logger.warning(
                    f"[私聊][{self.private_name}]经过 {reply_attempt_count} 次尝试，首次回复决定打回动作决策。打回原因: {check_reason}"
                )
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"首次回复尝试{reply_attempt_count}次后打回: {check_reason}"}
                )

            else:
                # 首次回复失败
                logger.warning(
                    f"[私聊][{self.private_name}]经过 {reply_attempt_count} 次尝试，未能生成合适的首次回复。最终原因: {check_reason}"
                )
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"首次回复尝试{reply_attempt_count}次后失败: {check_reason}"}
                )
                # 重置状态: 首次回复失败，下次还是用初始 prompt
                self.conversation_info.last_successful_reply_action = None

                # 执行 Wait 操作 (保持原有逻辑)
                logger.info(f"[私聊][{self.private_name}]由于无法生成合适首次回复，执行 'wait' 操作...")
                self.state = ConversationState.WAITING
                await self.waiter.wait(self.conversation_info)
                wait_action_record = {
                    "action": "wait",
                    "plan_reason": "因 direct_reply 多次尝试失败而执行的后备等待",
                    "status": "done",
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    "final_reason": None,
                }
                conversation_info.done_action.append(wait_action_record)

        elif action == "fetch_knowledge":
            self.state = ConversationState.FETCHING
            knowledge_query = reason
            try:
                # 检查 knowledge_fetcher 是否存在
                if not hasattr(self, "knowledge_fetcher"):
                    logger.error(f"[私聊][{self.private_name}]KnowledgeFetcher 未初始化，无法获取知识。")
                    raise AttributeError("KnowledgeFetcher not initialized")

                knowledge, source = await self.knowledge_fetcher.fetch(knowledge_query, observation_info.chat_history)
                logger.info(f"[私聊][{self.private_name}]获取到知识: {knowledge[:100]}..., 来源: {source}")
                if knowledge:
                    # 确保 knowledge_list 存在
                    if not hasattr(conversation_info, "knowledge_list"):
                        conversation_info.knowledge_list = []
                    conversation_info.knowledge_list.append(
                        {"query": knowledge_query, "knowledge": knowledge, "source": source}
                    )
                action_successful = True
            except Exception as fetch_err:
                logger.error(f"[私聊][{self.private_name}]获取知识时出错: {str(fetch_err)}")
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"获取知识失败: {str(fetch_err)}"}
                )
                self.conversation_info.last_successful_reply_action = None  # 重置状态

        elif action == "rethink_goal":
            self.state = ConversationState.RETHINKING
            try:
                # 检查 goal_analyzer 是否存在
                if not hasattr(self, "goal_analyzer"):
                    logger.error(f"[私聊][{self.private_name}]GoalAnalyzer 未初始化，无法重新思考目标。")
                    raise AttributeError("GoalAnalyzer not initialized")
                await self.goal_analyzer.analyze_goal(conversation_info, observation_info)
                action_successful = True
            except Exception as rethink_err:
                logger.error(f"[私聊][{self.private_name}]重新思考目标时出错: {rethink_err}")
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"重新思考目标失败: {rethink_err}"}
                )
                self.conversation_info.last_successful_reply_action = None  # 重置状态

        elif action == "listening":
            self.state = ConversationState.LISTENING
            logger.info(f"[私聊][{self.private_name}]倾听对方发言...")
            try:
                # 检查 waiter 是否存在
                if not hasattr(self, "waiter"):
                    logger.error(f"[私聊][{self.private_name}]Waiter 未初始化，无法倾听。")
                    raise AttributeError("Waiter not initialized")
                await self.waiter.wait_listening(conversation_info)
                action_successful = True  # Listening 完成就算成功
            except Exception as listen_err:
                logger.error(f"[私聊][{self.private_name}]倾听时出错: {listen_err}")
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"倾听失败: {listen_err}"}
                )
                self.conversation_info.last_successful_reply_action = None  # 重置状态

        elif action == "say_goodbye":
            self.state = ConversationState.GENERATING  # 也可以定义一个新的状态，如 ENDING
            logger.info(f"[私聊][{self.private_name}]执行行动: 生成并发送告别语...")
            try:
                # 1. 生成告别语 (使用 'say_goodbye' action_type)
                self.generated_reply = await self.reply_generator.generate(
                    observation_info, conversation_info, action_type="say_goodbye"
                )
                logger.info(f"[私聊][{self.private_name}]生成的告别语: {self.generated_reply}")

                # 2. 直接发送告别语 (不经过检查)
                if self.generated_reply:  # 确保生成了内容
                    await self._send_reply()  # 调用发送方法
                    # 发送成功后，标记动作成功
                    action_successful = True
                    logger.info(f"[私聊][{self.private_name}]告别语已发送。")
                else:
                    logger.warning(f"[私聊][{self.private_name}]未能生成告别语内容，无法发送。")
                    action_successful = False  # 标记动作失败
                    conversation_info.done_action[action_index].update(
                        {"status": "recall", "final_reason": "未能生成告别语内容"}
                    )

                # 3. 无论是否发送成功，都准备结束对话
                self.should_continue = False
                logger.info(f"[私聊][{self.private_name}]发送告别语流程结束，即将停止对话实例。")

            except Exception as goodbye_err:
                logger.error(f"[私聊][{self.private_name}]生成或发送告别语时出错: {goodbye_err}")
                logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
                # 即使出错，也结束对话
                self.should_continue = False
                action_successful = False  # 标记动作失败
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"生成或发送告别语时出错: {goodbye_err}"}
                )

        elif action == "end_conversation":
            # 这个分支现在只会在 action_planner 最终决定不告别时被调用
            self.should_continue = False
            logger.info(f"[私聊][{self.private_name}]收到最终结束指令，停止对话...")
            action_successful = True  # 标记这个指令本身是成功的

        elif action == "block_and_ignore":
            logger.info(f"[私聊][{self.private_name}]不想再理你了...")
            ignore_duration_seconds = 10 * 60
            self.ignore_until_timestamp = time.time() + ignore_duration_seconds
            logger.info(
                f"[私聊][{self.private_name}]将忽略此对话直到: {datetime.datetime.fromtimestamp(self.ignore_until_timestamp)}"
            )
            self.state = ConversationState.IGNORED
            action_successful = True  # 标记动作成功

        else:  # 对应 'wait' 动作
            self.state = ConversationState.WAITING
            logger.info(f"[私聊][{self.private_name}]等待更多信息...")
            try:
                # 检查 waiter 是否存在
                if not hasattr(self, "waiter"):
                    logger.error(f"[私聊][{self.private_name}]Waiter 未初始化，无法等待。")
                    raise AttributeError("Waiter not initialized")
                _timeout_occurred = await self.waiter.wait(self.conversation_info)
                action_successful = True  # Wait 完成就算成功
            except Exception as wait_err:
                logger.error(f"[私聊][{self.private_name}]等待时出错: {wait_err}")
                conversation_info.done_action[action_index].update(
                    {"status": "recall", "final_reason": f"等待失败: {wait_err}"}
                )
                self.conversation_info.last_successful_reply_action = None  # 重置状态

        # --- 更新 Action History 状态 ---
        # 只有当动作本身成功时，才更新状态为 done
        if action_successful:
            conversation_info.done_action[action_index].update(
                {
                    "status": "done",
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                }
            )
            # 重置状态: 对于非回复类动作的成功，清除上次回复状态
            if action not in ["direct_reply", "send_new_message"]:
                self.conversation_info.last_successful_reply_action = None
                logger.debug(f"[私聊][{self.private_name}]动作 {action} 成功完成，重置 last_successful_reply_action")
        # 如果动作是 recall 状态，在各自的处理逻辑中已经更新了 done_action

    async def _send_reply(self):
        """发送回复"""
        if not self.generated_reply:
            logger.warning(f"[私聊][{self.private_name}]没有生成回复内容，无法发送。")
            return

        try:
            _current_time = time.time()
            reply_content = self.generated_reply

            # 发送消息 (确保 direct_sender 和 chat_stream 有效)
            if not hasattr(self, "direct_sender") or not self.direct_sender:
                logger.error(f"[私聊][{self.private_name}]DirectMessageSender 未初始化，无法发送回复。")
                return
            if not self.chat_stream:
                logger.error(f"[私聊][{self.private_name}]ChatStream 未初始化，无法发送回复。")
                return

            await self.direct_sender.send_message(chat_stream=self.chat_stream, content=reply_content)

            # 发送成功后，手动触发 observer 更新可能导致重复处理自己发送的消息
            # 更好的做法是依赖 observer 的自动轮询或数据库触发器（如果支持）
            # 暂时注释掉，观察是否影响 ObservationInfo 的更新
            # self.chat_observer.trigger_update()
            # if not await self.chat_observer.wait_for_update():
            #     logger.warning(f"[私聊][{self.private_name}]等待 ChatObserver 更新完成超时")

            self.state = ConversationState.ANALYZING  # 更新状态

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]发送消息或更新状态时失败: {str(e)}")
            logger.error(f"[私聊][{self.private_name}]{traceback.format_exc()}")
            self.state = ConversationState.ANALYZING

    async def _send_timeout_message(self):
        """发送超时结束消息"""
        try:
            messages = self.chat_observer.get_cached_messages(limit=1)
            if not messages:
                return

            latest_message = self._convert_to_message(messages[0])
            await self.direct_sender.send_message(
                chat_stream=self.chat_stream, content="TODO:超时消息", reply_to_message=latest_message
            )
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]发送超时消息失败: {str(e)}")

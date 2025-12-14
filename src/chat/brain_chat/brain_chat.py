import asyncio
import time
import traceback
import random
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from rich.traceback import install

from src.config.config import global_config
from src.common.logger import get_logger
from src.common.data_models.info_data_model import ActionPlannerInfo
from src.common.data_models.message_data_model import ReplyContentType
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.prompt_builder import global_prompt_manager
from src.chat.utils.timer_calculator import Timer
from src.chat.brain_chat.brain_planner import BrainPlanner
from src.chat.planner_actions.action_modifier import ActionModifier
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.heart_flow.hfc_utils import CycleDetail
from src.bw_learner.expression_learner import expression_learner_manager
from src.bw_learner.message_recorder import extract_and_distribute_messages
from src.person_info.person_info import Person
from src.plugin_system.base.component_types import EventType, ActionInfo
from src.plugin_system.core import events_manager
from src.plugin_system.apis import generator_api, send_api, message_api, database_api
from src.chat.utils.chat_message_builder import (
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages
    from src.common.data_models.message_data_model import ReplySetModel


ERROR_LOOP_INFO = {
    "loop_plan_info": {
        "action_result": {
            "action_type": "error",
            "action_data": {},
            "reasoning": "循环处理失败",
        },
    },
    "loop_action_info": {
        "action_taken": False,
        "reply_text": "",
        "command": "",
        "taken_time": time.time(),
    },
}


install(extra_lines=3)

# 注释：原来的动作修改超时常量已移除，因为改为顺序执行

logger = get_logger("bc")  # Logger Name Changed


class BrainChatting:
    """
    管理一个连续的私聊Brain Chat循环
    用于在特定聊天流中生成回复。
    """

    def __init__(self, chat_id: str):
        """
        BrainChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            on_stop_focus_chat: 当收到stop_focus_chat命令时调用的回调函数
            performance_version: 性能记录版本号，用于区分不同启动版本
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream: ChatStream = get_chat_manager().get_stream(self.stream_id)  # type: ignore
        if not self.chat_stream:
            raise ValueError(f"无法找到聊天流: {self.stream_id}")
        self.log_prefix = f"[{get_chat_manager().get_stream_name(self.stream_id) or self.stream_id}]"

        self.expression_learner = expression_learner_manager.get_expression_learner(self.stream_id)

        self.action_manager = ActionManager()
        self.action_planner = BrainPlanner(chat_id=self.stream_id, action_manager=self.action_manager)
        self.action_modifier = ActionModifier(action_manager=self.action_manager, chat_id=self.stream_id)

        # 循环控制内部状态
        self.running: bool = False
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self.history_loop: List[CycleDetail] = []
        self._cycle_counter = 0
        self._current_cycle_detail: CycleDetail = None  # type: ignore

        self.last_read_time = time.time() - 2

        self.more_plan = False

        # 最近一次是否成功进行了 reply，用于选择 BrainPlanner 的 Prompt
        self._last_successful_reply: bool = False

    async def start(self):
        """检查是否需要启动主循环，如果未激活则启动。"""

        # 如果循环已经激活，直接返回
        if self.running:
            logger.debug(f"{self.log_prefix} BrainChatting 已激活，无需重复启动")
            return

        try:
            # 标记为活动状态，防止重复启动
            self.running = True

            self._loop_task = asyncio.create_task(self._main_chat_loop())
            self._loop_task.add_done_callback(self._handle_loop_completion)
            logger.info(f"{self.log_prefix} BrainChatting 启动完成")

        except Exception as e:
            # 启动失败时重置状态
            self.running = False
            self._loop_task = None
            logger.error(f"{self.log_prefix} BrainChatting 启动失败: {e}")
            raise

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            if exception := task.exception():
                logger.error(f"{self.log_prefix} BrainChatting: 脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} BrainChatting: 脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} BrainChatting: 结束了聊天")

    def start_cycle(self) -> Tuple[Dict[str, float], str]:
        self._cycle_counter += 1
        self._current_cycle_detail = CycleDetail(self._cycle_counter)
        self._current_cycle_detail.thinking_id = f"tid{str(round(time.time(), 2))}"
        cycle_timers = {}
        return cycle_timers, self._current_cycle_detail.thinking_id

    def end_cycle(self, loop_info, cycle_timers):
        self._current_cycle_detail.set_loop_info(loop_info)
        self.history_loop.append(self._current_cycle_detail)
        self._current_cycle_detail.timers = cycle_timers
        self._current_cycle_detail.end_time = time.time()

    def print_cycle_info(self, cycle_timers):
        # 记录循环信息和计时器结果
        timer_strings = []
        for name, elapsed in cycle_timers.items():
            formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
            timer_strings.append(f"{name}: {formatted_time}")

        logger.info(
            f"{self.log_prefix} 第{self._current_cycle_detail.cycle_id}次思考,"
            f"耗时: {self._current_cycle_detail.end_time - self._current_cycle_detail.start_time:.1f}秒"  # type: ignore
            + (f"\n详情: {'; '.join(timer_strings)}" if timer_strings else "")
        )

    async def _loopbody(self):  # sourcery skip: hoist-if-from-if
        # 获取最新消息（用于上下文，但不影响是否调用 observe）
        recent_messages_list = message_api.get_messages_by_time_in_chat(
            chat_id=self.stream_id,
            start_time=self.last_read_time,
            end_time=time.time(),
            limit=20,
            limit_mode="latest",
            filter_mai=True,
            filter_command=False,
            filter_intercept_message_level=1,
        )

        # 如果有新消息，更新 last_read_time
        if len(recent_messages_list) >= 1:
            self.last_read_time = time.time()

        # 总是执行一次思考迭代（不管有没有新消息）
        # wait 动作会在其内部等待，不需要在这里处理
        should_continue = await self._observe(recent_messages_list=recent_messages_list)

        if not should_continue:
            # 选择了 complete_talk，返回 False 表示需要等待新消息
            return False

        # 继续下一次迭代（除非选择了 complete_talk）
        # 短暂等待后再继续，避免过于频繁的循环
        await asyncio.sleep(0.1)

        return True

    async def _send_and_store_reply(
        self,
        response_set: "ReplySetModel",
        action_message: "DatabaseMessages",
        cycle_timers: Dict[str, float],
        thinking_id,
        actions,
        selected_expressions: Optional[List[int]] = None,
    ) -> Tuple[Dict[str, Any], str, Dict[str, float]]:
        with Timer("回复发送", cycle_timers):
            reply_text = await self._send_response(
                reply_set=response_set,
                message_data=action_message,
                selected_expressions=selected_expressions,
            )

        # 获取 platform，如果不存在则从 chat_stream 获取，如果还是 None 则使用默认值
        platform = action_message.chat_info.platform
        if platform is None:
            platform = getattr(self.chat_stream, "platform", "unknown")

        person = Person(platform=platform, user_id=action_message.user_info.user_id)
        person_name = person.person_name
        action_prompt_display = f"你对{person_name}进行了回复：{reply_text}"

        await database_api.store_action_info(
            chat_stream=self.chat_stream,
            action_build_into_prompt=False,
            action_prompt_display=action_prompt_display,
            action_done=True,
            thinking_id=thinking_id,
            action_data={"reply_text": reply_text},
            action_name="reply",
        )

        # 构建循环信息
        loop_info: Dict[str, Any] = {
            "loop_plan_info": {
                "action_result": actions,
            },
            "loop_action_info": {
                "action_taken": True,
                "reply_text": reply_text,
                "command": "",
                "taken_time": time.time(),
            },
        }

        return loop_info, reply_text, cycle_timers

    async def _observe(
        self,  # interest_value: float = 0.0,
        recent_messages_list: Optional[List["DatabaseMessages"]] = None,
    ) -> bool:  # sourcery skip: merge-else-if-into-elif, remove-redundant-if
        if recent_messages_list is None:
            recent_messages_list = []
        _reply_text = ""  # 初始化reply_text变量，避免UnboundLocalError

        # -------------------------------------------------------------------------
        # ReflectTracker Check
        # 在每次回复前检查一次上下文，看是否有反思问题得到了解答
        # -------------------------------------------------------------------------
        from src.bw_learner.reflect_tracker import reflect_tracker_manager

        tracker = reflect_tracker_manager.get_tracker(self.stream_id)
        if tracker:
            resolved = await tracker.trigger_tracker()
            if resolved:
                reflect_tracker_manager.remove_tracker(self.stream_id)
                logger.info(f"{self.log_prefix} ReflectTracker resolved and removed.")

        # -------------------------------------------------------------------------
        # Expression Reflection Check
        # 检查是否需要提问表达反思
        # -------------------------------------------------------------------------
        from src.bw_learner.expression_reflector import expression_reflector_manager

        reflector = expression_reflector_manager.get_or_create_reflector(self.stream_id)
        asyncio.create_task(reflector.check_and_ask())

        async with global_prompt_manager.async_message_scope(self.chat_stream.context.get_template_name()):
            # 通过 MessageRecorder 统一提取消息并分发给 expression_learner 和 jargon_miner
            # 在 replyer 执行时触发，统一管理时间窗口，避免重复获取消息
            asyncio.create_task(extract_and_distribute_messages(self.stream_id))

            cycle_timers, thinking_id = self.start_cycle()
            logger.info(f"{self.log_prefix} 开始第{self._cycle_counter}次思考")

            # 第一步：动作检查
            available_actions: Dict[str, ActionInfo] = {}
            try:
                await self.action_modifier.modify_actions()
                available_actions = self.action_manager.get_using_actions()
            except Exception as e:
                logger.error(f"{self.log_prefix} 动作修改失败: {e}")

            # 获取必要信息
            is_group_chat, chat_target_info, _ = self.action_planner.get_necessary_info()

            # 一次思考迭代：Think - Act - Observe
            # 获取聊天上下文
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=self.stream_id,
                timestamp=time.time(),
                limit=int(global_config.chat.max_context_size * 0.6),
                filter_intercept_message_level=1,
            )
            chat_content_block, message_id_list = build_readable_messages_with_id(
                messages=message_list_before_now,
                timestamp_mode="normal_no_YMD",
                read_mark=self.action_planner.last_obs_time_mark,
                truncate=True,
                show_actions=True,
            )

            prompt_info = await self.action_planner.build_planner_prompt(
                chat_target_info=chat_target_info,
                current_available_actions=available_actions,
                chat_content_block=chat_content_block,
                message_id_list=message_id_list,
                prompt_key="brain_planner_prompt_react",
            )
            continue_flag, modified_message = await events_manager.handle_mai_events(
                EventType.ON_PLAN, None, prompt_info[0], None, self.chat_stream.stream_id
            )
            if not continue_flag:
                return False
            if modified_message and modified_message._modify_flags.modify_llm_prompt:
                prompt_info = (modified_message.llm_prompt, prompt_info[1])

            with Timer("规划器", cycle_timers):
                action_to_use_info = await self.action_planner.plan(
                    loop_start_time=self.last_read_time,
                    available_actions=available_actions,
                )

            # 检查是否有 complete_talk 动作（会停止后续迭代）
            has_complete_talk = any(action.action_type == "complete_talk" for action in action_to_use_info)

            # 并行执行所有动作
            action_tasks = [
                asyncio.create_task(
                    self._execute_action(action, action_to_use_info, thinking_id, available_actions, cycle_timers)
                )
                for action in action_to_use_info
            ]

            # 并行执行所有任务
            results = await asyncio.gather(*action_tasks, return_exceptions=True)

            # 处理执行结果
            reply_loop_info = None
            reply_text_from_reply = ""
            action_success = False
            action_reply_text = ""

            for result in results:
                if isinstance(result, BaseException):
                    logger.error(f"{self.log_prefix} 动作执行异常: {result}")
                    continue

                if result["action_type"] != "reply":
                    action_success = result["success"]
                    action_reply_text = result["reply_text"]
                elif result["action_type"] == "reply":
                    if result["success"]:
                        reply_loop_info = result["loop_info"]
                        reply_text_from_reply = result["reply_text"]
                    else:
                        logger.warning(f"{self.log_prefix} 回复动作执行失败")

            # 更新观察时间标记
            self.action_planner.last_obs_time_mark = time.time()

            # 如果选择了 complete_talk，标记为完成，不再继续迭代
            if has_complete_talk:
                logger.info(f"{self.log_prefix} 检测到 complete_talk 动作，本次思考完成")

            # 构建循环信息
            if reply_loop_info:
                # 如果有回复信息，使用回复的loop_info作为基础
                loop_info = reply_loop_info
                # 更新动作执行信息
                loop_info["loop_action_info"].update(
                    {
                        "action_taken": action_success,
                        "taken_time": time.time(),
                    }
                )
                _reply_text = reply_text_from_reply
            else:
                # 没有回复信息，构建纯动作的loop_info
                loop_info = {
                    "loop_plan_info": {
                        "action_result": action_to_use_info,
                    },
                    "loop_action_info": {
                        "action_taken": action_success,
                        "reply_text": action_reply_text,
                        "taken_time": time.time(),
                    },
                }
                _reply_text = action_reply_text

            # 如果选择了 complete_talk，返回 False 以停止 _loopbody 的循环
            # 否则返回 True，让 _loopbody 继续下一次迭代
            should_continue = not has_complete_talk

            self.end_cycle(loop_info, cycle_timers)
            self.print_cycle_info(cycle_timers)

            # 如果选择了 complete_talk，返回 False 停止循环
            # 否则返回 True，继续下一次思考迭代
            return should_continue

    async def _main_chat_loop(self):
        """主循环，持续进行计划并可能回复消息，直到被外部取消。"""
        try:
            while self.running:
                # 主循环
                success = await self._loopbody()
                if not success:
                    # 选择了 complete，等待新消息
                    logger.info(f"{self.log_prefix} 选择了 complete，等待新消息...")
                    await self._wait_for_new_message()
                    # 有新消息后继续循环
                    continue
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # 设置了关闭标志位后被取消是正常流程
            logger.info(f"{self.log_prefix} 麦麦已关闭聊天")
        except Exception:
            logger.error(f"{self.log_prefix} 麦麦聊天意外错误，将于3s后尝试重新启动")
            print(traceback.format_exc())
            await asyncio.sleep(3)
            self._loop_task = asyncio.create_task(self._main_chat_loop())
        logger.error(f"{self.log_prefix} 结束了当前聊天循环")

    async def _wait_for_new_message(self):
        """等待新消息到达"""
        last_check_time = self.last_read_time
        check_interval = 1.0  # 每秒检查一次

        while self.running:
            # 检查是否有新消息
            recent_messages_list = message_api.get_messages_by_time_in_chat(
                chat_id=self.stream_id,
                start_time=last_check_time,
                end_time=time.time(),
                limit=20,
                limit_mode="latest",
                filter_mai=True,
                filter_command=False,
                filter_intercept_message_level=1,
            )

            # 如果有新消息，更新 last_read_time 并返回
            if len(recent_messages_list) >= 1:
                self.last_read_time = time.time()
                logger.info(f"{self.log_prefix} 检测到新消息，恢复循环")
                return

            # 等待一段时间后再次检查
            await asyncio.sleep(check_interval)

    async def _handle_action(
        self,
        action: str,
        reasoning: str,
        action_data: dict,
        cycle_timers: Dict[str, float],
        thinking_id: str,
        action_message: Optional["DatabaseMessages"] = None,
    ) -> tuple[bool, str, str]:
        """
        处理规划动作，使用动作工厂创建相应的动作处理器

        参数:
            action: 动作类型
            reasoning: 决策理由
            action_data: 动作数据，包含不同动作需要的参数
            cycle_timers: 计时器字典
            thinking_id: 思考ID

        返回:
            tuple[bool, str, str]: (是否执行了动作, 思考消息ID, 命令)
        """
        try:
            # 使用工厂创建动作处理器实例
            try:
                action_handler = self.action_manager.create_action(
                    action_name=action,
                    action_data=action_data,
                    action_reasoning=reasoning,
                    cycle_timers=cycle_timers,
                    thinking_id=thinking_id,
                    chat_stream=self.chat_stream,
                    log_prefix=self.log_prefix,
                    action_message=action_message,
                )
            except Exception as e:
                logger.error(f"{self.log_prefix} 创建动作处理器时出错: {e}")
                traceback.print_exc()
                return False, "", ""

            if not action_handler:
                logger.warning(f"{self.log_prefix} 未能创建动作处理器: {action}")
                return False, "", ""

            # 处理动作并获取结果（固定记录一次动作信息）
            # BaseAction 定义了异步方法 execute() 作为统一执行入口
            # 这里调用 execute() 以兼容所有 Action 实现
            result = await action_handler.execute()
            success, action_text = result
            command = ""

            return success, action_text, command

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, "", ""

    async def _send_response(
        self,
        reply_set: "ReplySetModel",
        message_data: "DatabaseMessages",
        selected_expressions: Optional[List[int]] = None,
    ) -> str:
        new_message_count = message_api.count_new_messages(
            chat_id=self.chat_stream.stream_id, start_time=self.last_read_time, end_time=time.time()
        )

        need_reply = new_message_count >= random.randint(2, 4)

        if need_reply:
            logger.info(f"{self.log_prefix} 从思考到回复，共有{new_message_count}条新消息，使用引用回复")

        reply_text = ""
        first_replied = False
        for reply_content in reply_set.reply_data:
            if reply_content.content_type != ReplyContentType.TEXT:
                continue
            data: str = reply_content.content  # type: ignore
            if not first_replied:
                await send_api.text_to_stream(
                    text=data,
                    stream_id=self.chat_stream.stream_id,
                    reply_message=message_data,
                    set_reply=need_reply,
                    typing=False,
                    selected_expressions=selected_expressions,
                )
                first_replied = True
            else:
                await send_api.text_to_stream(
                    text=data,
                    stream_id=self.chat_stream.stream_id,
                    reply_message=message_data,
                    set_reply=False,
                    typing=True,
                    selected_expressions=selected_expressions,
                )
            reply_text += data

        return reply_text

    async def _execute_action(
        self,
        action_planner_info: ActionPlannerInfo,
        chosen_action_plan_infos: List[ActionPlannerInfo],
        thinking_id: str,
        available_actions: Dict[str, ActionInfo],
        cycle_timers: Dict[str, float],
    ):
        """执行单个动作的通用函数"""
        try:
            with Timer(f"动作{action_planner_info.action_type}", cycle_timers):
                if action_planner_info.action_type == "complete_talk":
                    # 直接处理complete_talk逻辑，不再通过动作系统
                    reason = action_planner_info.reasoning or "选择完成对话"
                    logger.info(f"{self.log_prefix} 选择完成对话，原因: {reason}")

                    # 存储complete_talk信息到数据库
                    await database_api.store_action_info(
                        chat_stream=self.chat_stream,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                        action_done=True,
                        thinking_id=thinking_id,
                        action_data={"reason": reason},
                        action_name="complete_talk",
                    )
                    return {"action_type": "complete_talk", "success": True, "reply_text": "", "command": ""}

                elif action_planner_info.action_type == "reply":
                    try:
                        success, llm_response = await generator_api.generate_reply(
                            chat_stream=self.chat_stream,
                            reply_message=action_planner_info.action_message,
                            available_actions=available_actions,
                            chosen_actions=chosen_action_plan_infos,
                            reply_reason=action_planner_info.reasoning or "",
                            enable_tool=global_config.tool.enable_tool,
                            request_type="replyer",
                            from_plugin=False,
                        )

                        if not success or not llm_response or not llm_response.reply_set:
                            if action_planner_info.action_message:
                                logger.info(
                                    f"对 {action_planner_info.action_message.processed_plain_text} 的回复生成失败"
                                )
                            else:
                                logger.info("回复生成失败")
                            return {
                                "action_type": "reply",
                                "success": False,
                                "reply_text": "",
                                "loop_info": None,
                            }

                    except asyncio.CancelledError:
                        logger.debug(f"{self.log_prefix} 并行执行：回复生成任务已被取消")
                        return {"action_type": "reply", "success": False, "reply_text": "", "loop_info": None}

                    response_set = llm_response.reply_set
                    selected_expressions = llm_response.selected_expressions
                    loop_info, reply_text, _ = await self._send_and_store_reply(
                        response_set=response_set,
                        action_message=action_planner_info.action_message,  # type: ignore
                        cycle_timers=cycle_timers,
                        thinking_id=thinking_id,
                        actions=chosen_action_plan_infos,
                        selected_expressions=selected_expressions,
                    )
                    # 标记这次循环已经成功进行了回复
                    self._last_successful_reply = True
                    return {
                        "action_type": "reply",
                        "success": True,
                        "reply_text": reply_text,
                        "loop_info": loop_info,
                    }

                # 其他动作
                else:
                    # 内建 wait / listening：不通过插件系统，直接在这里处理
                    if action_planner_info.action_type in ["wait", "listening"]:
                        reason = action_planner_info.reasoning or ""
                        action_data = action_planner_info.action_data or {}

                        if action_planner_info.action_type == "wait":
                            # 获取等待时间（必填）
                            wait_seconds = action_data.get("wait_seconds")
                            if wait_seconds is None:
                                logger.warning(f"{self.log_prefix} wait 动作缺少 wait_seconds 参数，使用默认值 5 秒")
                                wait_seconds = 5
                            else:
                                try:
                                    wait_seconds = float(wait_seconds)
                                    if wait_seconds < 0:
                                        logger.warning(f"{self.log_prefix} wait_seconds 不能为负数，使用默认值 5 秒")
                                        wait_seconds = 5
                                except (ValueError, TypeError):
                                    logger.warning(f"{self.log_prefix} wait_seconds 参数格式错误，使用默认值 5 秒")
                                    wait_seconds = 5

                            logger.info(f"{self.log_prefix} 执行 wait 动作，等待 {wait_seconds} 秒")

                            # 记录动作信息
                            await database_api.store_action_info(
                                chat_stream=self.chat_stream,
                                action_build_into_prompt=False,
                                action_prompt_display=reason or f"等待 {wait_seconds} 秒",
                                action_done=True,
                                thinking_id=thinking_id,
                                action_data={"reason": reason, "wait_seconds": wait_seconds},
                                action_name="wait",
                            )

                            # 等待指定时间
                            await asyncio.sleep(wait_seconds)

                            logger.info(f"{self.log_prefix} wait 动作完成，继续下一次思考")

                            # 这些动作本身不产生文本回复
                            self._last_successful_reply = False
                            return {
                                "action_type": "wait",
                                "success": True,
                                "reply_text": "",
                                "command": "",
                            }

                        # listening 已合并到 wait，如果遇到则转换为 wait（向后兼容）
                        elif action_planner_info.action_type == "listening":
                            logger.debug(f"{self.log_prefix} 检测到 listening 动作，已合并到 wait，自动转换")
                            # 使用默认等待时间
                            wait_seconds = 3

                            logger.info(f"{self.log_prefix} 执行 listening（转换为 wait）动作，等待 {wait_seconds} 秒")

                            # 记录动作信息
                            await database_api.store_action_info(
                                chat_stream=self.chat_stream,
                                action_build_into_prompt=False,
                                action_prompt_display=reason or f"倾听并等待 {wait_seconds} 秒",
                                action_done=True,
                                thinking_id=thinking_id,
                                action_data={"reason": reason, "wait_seconds": wait_seconds},
                                action_name="listening",
                            )

                            # 等待指定时间
                            await asyncio.sleep(wait_seconds)

                            logger.info(f"{self.log_prefix} listening 动作完成，继续下一次思考")

                            # 这些动作本身不产生文本回复
                            self._last_successful_reply = False
                            return {
                                "action_type": "listening",
                                "success": True,
                                "reply_text": "",
                                "command": "",
                            }

                    # 其余动作：走原有插件 Action 体系
                    with Timer("动作执行", cycle_timers):
                        success, reply_text, command = await self._handle_action(
                            action_planner_info.action_type,
                            action_planner_info.reasoning or "",
                            action_planner_info.action_data or {},
                            cycle_timers,
                            thinking_id,
                            action_planner_info.action_message,
                        )
                    # 非 reply 类动作执行成功时，清空最近成功回复标记，让下一轮回到 initial Prompt
                    if success and action_planner_info.action_type != "reply":
                        self._last_successful_reply = False

                    return {
                        "action_type": action_planner_info.action_type,
                        "success": success,
                        "reply_text": reply_text,
                        "command": command,
                    }

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行动作时出错: {e}")
            logger.error(f"{self.log_prefix} 错误信息: {traceback.format_exc()}")
            return {
                "action_type": action_planner_info.action_type,
                "success": False,
                "reply_text": "",
                "loop_info": None,
                "error": str(e),
            }

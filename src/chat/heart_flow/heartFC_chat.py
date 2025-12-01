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
from src.chat.planner_actions.planner import ActionPlanner
from src.chat.planner_actions.action_modifier import ActionModifier
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.heart_flow.hfc_utils import CycleDetail
from src.express.expression_learner import expression_learner_manager
from src.chat.frequency_control.frequency_control import frequency_control_manager
from src.express.reflect_tracker import reflect_tracker_manager
from src.express.expression_reflector import expression_reflector_manager
from src.jargon import extract_and_store_jargon
from src.person_info.person_info import Person
from src.plugin_system.base.component_types import EventType, ActionInfo
from src.plugin_system.core import events_manager
from src.plugin_system.apis import generator_api, send_api, message_api, database_api
from src.chat.utils.chat_message_builder import (
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)
from src.hippo_memorizer.chat_history_summarizer import ChatHistorySummarizer

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

logger = get_logger("hfc")  # Logger Name Changed


class HeartFChatting:
    """
    管理一个连续的Focus Chat循环
    用于在特定聊天流中生成回复。
    其生命周期现在由其关联的 SubHeartflow 的 FOCUSED 状态控制。
    """

    def __init__(self, chat_id: str):
        """
        HeartFChatting 初始化函数

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
        self.action_planner = ActionPlanner(chat_id=self.stream_id, action_manager=self.action_manager)
        self.action_modifier = ActionModifier(action_manager=self.action_manager, chat_id=self.stream_id)

        # 循环控制内部状态
        self.running: bool = False
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self.history_loop: List[CycleDetail] = []
        self._cycle_counter = 0
        self._current_cycle_detail: CycleDetail = None  # type: ignore

        self.last_read_time = time.time() - 2
        self.no_reply_until_call = False

        self.is_mute = False

        self.last_active_time = time.time()  # 记录上一次非noreply时间

        self.question_probability_multiplier = 1
        self.questioned = False

        # 跟踪连续 no_reply 次数，用于动态调整阈值
        self.consecutive_no_reply_count = 0

        # 聊天内容概括器
        self.chat_history_summarizer = ChatHistorySummarizer(chat_id=self.stream_id)

    async def start(self):
        """检查是否需要启动主循环，如果未激活则启动。"""

        # 如果循环已经激活，直接返回
        if self.running:
            logger.debug(f"{self.log_prefix} HeartFChatting 已激活，无需重复启动")
            return

        try:
            # 标记为活动状态，防止重复启动
            self.running = True

            self._loop_task = asyncio.create_task(self._main_chat_loop())
            self._loop_task.add_done_callback(self._handle_loop_completion)

            # 启动聊天内容概括器的后台定期检查循环
            await self.chat_history_summarizer.start()

            logger.info(f"{self.log_prefix} HeartFChatting 启动完成")

        except Exception as e:
            # 启动失败时重置状态
            self.running = False
            self._loop_task = None
            logger.error(f"{self.log_prefix} HeartFChatting 启动失败: {e}")
            raise

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            if exception := task.exception():
                logger.error(f"{self.log_prefix} HeartFChatting: 脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 结束了聊天")

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
            if elapsed < 0.1:
                # 不显示小于0.1秒的计时器
                continue
            formatted_time = f"{elapsed:.2f}秒"
            timer_strings.append(f"{name}: {formatted_time}")

        logger.info(
            f"{self.log_prefix} 第{self._current_cycle_detail.cycle_id}次思考,"
            f"耗时: {self._current_cycle_detail.end_time - self._current_cycle_detail.start_time:.1f}秒;"  # type: ignore
            + (f"详情: {'; '.join(timer_strings)}" if timer_strings else "")
        )

    async def _loopbody(self):
        recent_messages_list = message_api.get_messages_by_time_in_chat(
            chat_id=self.stream_id,
            start_time=self.last_read_time,
            end_time=time.time(),
            limit=20,
            limit_mode="latest",
            filter_mai=True,
            filter_command=False,
            filter_no_read_command=True,
        )

        # 根据连续 no_reply 次数动态调整阈值
        # 3次 no_reply 时，阈值调高到 1.5（50%概率为1，50%概率为2）
        # 5次 no_reply 时，提高到 2（大于等于两条消息的阈值）
        if self.consecutive_no_reply_count >= 5:
            threshold = 2
        elif self.consecutive_no_reply_count >= 3:
            # 1.5 的含义：50%概率为1，50%概率为2
            threshold = 2 if random.random() < 0.5 else 1
        else:
            threshold = 1

        if len(recent_messages_list) >= threshold:
            # for message in recent_messages_list:
            # print(message.processed_plain_text)
            # !处理no_reply_until_call逻辑
            if self.no_reply_until_call:
                for message in recent_messages_list:
                    if (
                        message.is_mentioned
                        or message.is_at
                        or len(recent_messages_list) >= 8
                        or time.time() - self.last_read_time > 600
                    ):
                        self.no_reply_until_call = False
                        self.last_read_time = time.time()
                        break
                # 没有提到，继续保持沉默
                if self.no_reply_until_call:
                    # logger.info(f"{self.log_prefix} 没有提到，继续保持沉默")
                    await asyncio.sleep(1)
                    return True

            self.last_read_time = time.time()

            # !此处使at或者提及必定回复
            mentioned_message = None
            for message in recent_messages_list:
                if (message.is_mentioned or message.is_at) and global_config.chat.mentioned_bot_reply:
                    mentioned_message = message

            # logger.info(f"{self.log_prefix} 当前talk_value: {global_config.chat.get_talk_value(self.stream_id)}")

            # *控制频率用
            if mentioned_message:
                await self._observe(recent_messages_list=recent_messages_list, force_reply_message=mentioned_message)
            elif (
                random.random()
                < global_config.chat.get_talk_value(self.stream_id)
                * frequency_control_manager.get_or_create_frequency_control(self.stream_id).get_talk_frequency_adjust()
            ):
                await self._observe(recent_messages_list=recent_messages_list)
            else:
                # 没有提到，继续保持沉默，等待5秒防止频繁触发
                await asyncio.sleep(10)
                return True
        else:
            await asyncio.sleep(0.2)
            return True
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

    async def _run_planner_without_reply(
        self,
        available_actions: Dict[str, ActionInfo],
        cycle_timers: Dict[str, float],
    ) -> List[ActionPlannerInfo]:
        """执行planner，但不包含reply动作（用于并行执行场景，提及时使用简化版提示词）"""
        try:
            with Timer("规划器", cycle_timers):
                action_to_use_info = await self.action_planner.plan(
                    loop_start_time=self.last_read_time,
                    available_actions=available_actions,
                    is_mentioned=True,  # 标记为提及时，使用简化版提示词
                )
            # 过滤掉reply动作（虽然提及时不应该有reply，但为了安全还是过滤一下）
            return [action for action in action_to_use_info if action.action_type != "reply"]
        except Exception as e:
            logger.error(f"{self.log_prefix} Planner执行失败: {e}")
            traceback.print_exc()
            return []

    async def _generate_mentioned_reply(
        self,
        force_reply_message: "DatabaseMessages",
        thinking_id: str,
        cycle_timers: Dict[str, float],
        available_actions: Dict[str, ActionInfo],
    ) -> Dict[str, Any]:
        """当被提及时，独立生成回复的任务"""
        try:
            self.questioned = False
            # 重置连续 no_reply 计数
            self.consecutive_no_reply_count = 0
            reason = ""

            await database_api.store_action_info(
                chat_stream=self.chat_stream,
                action_build_into_prompt=False,
                action_prompt_display=reason,
                action_done=True,
                thinking_id=thinking_id,
                action_data={},
                action_name="reply",
                action_reasoning=reason,
            )

            with Timer("提及回复生成", cycle_timers):
                success, llm_response = await generator_api.generate_reply(
                    chat_stream=self.chat_stream,
                    reply_message=force_reply_message,
                    available_actions=available_actions,
                    chosen_actions=[],  # 独立回复，不依赖planner的动作
                    reply_reason=reason,
                    enable_tool=global_config.tool.enable_tool,
                    request_type="replyer",
                    from_plugin=False,
                    reply_time_point=self.last_read_time,
                )

            if not success or not llm_response or not llm_response.reply_set:
                logger.warning(f"{self.log_prefix} 提及回复生成失败")
                return {"action_type": "reply", "success": False, "result": "提及回复生成失败", "loop_info": None}

            response_set = llm_response.reply_set
            selected_expressions = llm_response.selected_expressions
            loop_info, reply_text, _ = await self._send_and_store_reply(
                response_set=response_set,
                action_message=force_reply_message,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                actions=[],  # 独立回复，不依赖planner的动作
                selected_expressions=selected_expressions,
            )
            self.last_active_time = time.time()
            return {
                "action_type": "reply",
                "success": True,
                "result": f"你回复内容{reply_text}",
                "loop_info": loop_info,
            }
        except Exception as e:
            logger.error(f"{self.log_prefix} 提及回复生成异常: {e}")
            traceback.print_exc()
            return {"action_type": "reply", "success": False, "result": f"提及回复生成异常: {e}", "loop_info": None}

    async def _observe(
        self,  # interest_value: float = 0.0,
        recent_messages_list: Optional[List["DatabaseMessages"]] = None,
        force_reply_message: Optional["DatabaseMessages"] = None,
    ) -> bool:  # sourcery skip: merge-else-if-into-elif, remove-redundant-if
        if recent_messages_list is None:
            recent_messages_list = []
        _reply_text = ""  # 初始化reply_text变量，避免UnboundLocalError

        # -------------------------------------------------------------------------
        # ReflectTracker Check
        # 在每次回复前检查一次上下文，看是否有反思问题得到了解答
        # -------------------------------------------------------------------------

        reflector = expression_reflector_manager.get_or_create_reflector(self.stream_id)
        await reflector.check_and_ask()
        tracker = reflect_tracker_manager.get_tracker(self.stream_id)
        if tracker:
            resolved = await tracker.trigger_tracker()
            if resolved:
                reflect_tracker_manager.remove_tracker(self.stream_id)
                logger.info(f"{self.log_prefix} ReflectTracker resolved and removed.")

        start_time = time.time()
        async with global_prompt_manager.async_message_scope(self.chat_stream.context.get_template_name()):
            asyncio.create_task(self.expression_learner.trigger_learning_for_chat())
            asyncio.create_task(
                frequency_control_manager.get_or_create_frequency_control(self.stream_id).trigger_frequency_adjust()
            )

            # 添加curious检测任务 - 检测聊天记录中的矛盾、冲突或需要提问的内容
            # asyncio.create_task(check_and_make_question(self.stream_id))
            # 添加jargon提取任务 - 提取聊天中的黑话/俚语并入库（内部自行取消息并带冷却）
            asyncio.create_task(extract_and_store_jargon(self.stream_id))
            # 添加聊天内容概括任务 - 累积、打包和压缩聊天记录
            # 注意：后台循环已在start()中启动，这里作为额外触发点，在有思考时立即处理
            # asyncio.create_task(self.chat_history_summarizer.process())

            cycle_timers, thinking_id = self.start_cycle()
            logger.info(
                f"{self.log_prefix} 开始第{self._cycle_counter}次思考(频率: {global_config.chat.get_talk_value(self.stream_id)})"
            )

            # 第一步：动作检查
            available_actions: Dict[str, ActionInfo] = {}
            try:
                await self.action_modifier.modify_actions()
                available_actions = self.action_manager.get_using_actions()
            except Exception as e:
                logger.error(f"{self.log_prefix} 动作修改失败: {e}")

            # 如果被提及，让回复生成和planner并行执行
            if force_reply_message:
                logger.info(f"{self.log_prefix} 检测到提及，回复生成与planner并行执行")

                # 并行执行planner和回复生成
                planner_task = asyncio.create_task(
                    self._run_planner_without_reply(
                        available_actions=available_actions,
                        cycle_timers=cycle_timers,
                    )
                )
                reply_task = asyncio.create_task(
                    self._generate_mentioned_reply(
                        force_reply_message=force_reply_message,
                        thinking_id=thinking_id,
                        cycle_timers=cycle_timers,
                        available_actions=available_actions,
                    )
                )

                # 等待两个任务完成
                planner_result, reply_result = await asyncio.gather(planner_task, reply_task, return_exceptions=True)

                # 处理planner结果
                if isinstance(planner_result, BaseException):
                    logger.error(f"{self.log_prefix} Planner执行异常: {planner_result}")
                    action_to_use_info = []
                else:
                    action_to_use_info = planner_result

                # 处理回复结果
                if isinstance(reply_result, BaseException):
                    logger.error(f"{self.log_prefix} 回复生成异常: {reply_result}")
                    reply_result = {
                        "action_type": "reply",
                        "success": False,
                        "result": "回复生成异常",
                        "loop_info": None,
                    }
            else:
                # 正常流程：只执行planner
                is_group_chat, chat_target_info, _ = self.action_planner.get_necessary_info()

                message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                    chat_id=self.stream_id,
                    timestamp=time.time(),
                    limit=int(global_config.chat.max_context_size * 0.6),
                    filter_no_read_command=True,
                )
                chat_content_block, message_id_list = build_readable_messages_with_id(
                    messages=message_list_before_now,
                    timestamp_mode="normal_no_YMD",
                    read_mark=self.action_planner.last_obs_time_mark,
                    truncate=True,
                    show_actions=True,
                )

                prompt_info = await self.action_planner.build_planner_prompt(
                    is_group_chat=is_group_chat,
                    chat_target_info=chat_target_info,
                    current_available_actions=available_actions,
                    chat_content_block=chat_content_block,
                    message_id_list=message_id_list,
                    interest=global_config.personality.interest,
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
                reply_result = None

            # 只在提及情况下过滤掉planner返回的reply动作（提及时已有独立回复生成）
            if force_reply_message:
                action_to_use_info = [action for action in action_to_use_info if action.action_type != "reply"]

            logger.info(
                f"{self.log_prefix} 决定执行{len(action_to_use_info)}个动作: {' '.join([a.action_type for a in action_to_use_info])}"
            )

            # 3. 并行执行所有动作（不包括reply，reply已经独立执行）
            action_tasks = [
                asyncio.create_task(
                    self._execute_action(action, action_to_use_info, thinking_id, available_actions, cycle_timers)
                )
                for action in action_to_use_info
            ]

            # 并行执行所有任务
            results = await asyncio.gather(*action_tasks, return_exceptions=True)

            # 如果有独立的回复结果，添加到结果列表中
            if reply_result:
                results = list(results) + [reply_result]

            # 处理执行结果
            reply_loop_info = None
            reply_text_from_reply = ""
            action_success = False
            action_reply_text = ""

            excute_result_str = ""
            for result in results:
                excute_result_str += f"{result['action_type']} 执行结果:{result['result']}\n"

                if isinstance(result, BaseException):
                    logger.error(f"{self.log_prefix} 动作执行异常: {result}")
                    continue

                if result["action_type"] != "reply":
                    action_success = result["success"]
                    action_reply_text = result["result"]
                elif result["action_type"] == "reply":
                    if result["success"]:
                        reply_loop_info = result["loop_info"]
                        reply_text_from_reply = result["result"]
                    else:
                        logger.warning(f"{self.log_prefix} 回复动作执行失败")

            self.action_planner.add_plan_excute_log(result=excute_result_str)

            # 构建最终的循环信息
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

            self.end_cycle(loop_info, cycle_timers)
            self.print_cycle_info(cycle_timers)

            end_time = time.time()
            if end_time - start_time < global_config.chat.planner_smooth:
                wait_time = global_config.chat.planner_smooth - (end_time - start_time)
                await asyncio.sleep(wait_time)
            else:
                await asyncio.sleep(0.1)
            return True

    async def _main_chat_loop(self):
        """主循环，持续进行计划并可能回复消息，直到被外部取消。"""
        try:
            while self.running:
                # 主循环
                success = await self._loopbody()
                await asyncio.sleep(0.1)
                if not success:
                    break
        except asyncio.CancelledError:
            # 设置了关闭标志位后被取消是正常流程
            logger.info(f"{self.log_prefix} 麦麦已关闭聊天")
        except Exception:
            logger.error(f"{self.log_prefix} 麦麦聊天意外错误，将于3s后尝试重新启动")
            print(traceback.format_exc())
            await asyncio.sleep(3)
            self._loop_task = asyncio.create_task(self._main_chat_loop())
        logger.error(f"{self.log_prefix} 结束了当前聊天循环")

    async def _handle_action(
        self,
        action: str,
        action_reasoning: str,
        action_data: dict,
        cycle_timers: Dict[str, float],
        thinking_id: str,
        action_message: Optional["DatabaseMessages"] = None,
    ) -> tuple[bool, str, str]:
        """
        处理规划动作，使用动作工厂创建相应的动作处理器

        参数:
            action: 动作类型
            action_reasoning: 决策理由
            action_data: 动作数据，包含不同动作需要的参数
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            action_message: 消息数据
        返回:
            tuple[bool, str, str]: (是否执行了动作, 思考消息ID, 命令)
        """
        try:
            # 使用工厂创建动作处理器实例
            try:
                action_handler = self.action_manager.create_action(
                    action_name=action,
                    action_data=action_data,
                    cycle_timers=cycle_timers,
                    thinking_id=thinking_id,
                    chat_stream=self.chat_stream,
                    log_prefix=self.log_prefix,
                    action_reasoning=action_reasoning,
                    action_message=action_message,
                )
            except Exception as e:
                logger.error(f"{self.log_prefix} 创建动作处理器时出错: {e}")
                traceback.print_exc()
                return False, ""

            # 处理动作并获取结果（固定记录一次动作信息）
            result = await action_handler.execute()
            success, action_text = result

            return success, action_text

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, ""

    async def _send_response(
        self,
        reply_set: "ReplySetModel",
        message_data: "DatabaseMessages",
        selected_expressions: Optional[List[int]] = None,
    ) -> str:
        new_message_count = message_api.count_new_messages(
            chat_id=self.chat_stream.stream_id, start_time=self.last_read_time, end_time=time.time()
        )

        need_reply = new_message_count >= random.randint(2, 3)

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
                # 直接当场执行no_reply逻辑
                if action_planner_info.action_type == "no_reply":
                    # 直接处理no_reply逻辑，不再通过动作系统
                    reason = action_planner_info.reasoning or "选择不回复"
                    # logger.info(f"{self.log_prefix} 选择不回复，原因: {reason}")

                    # 增加连续 no_reply 计数
                    self.consecutive_no_reply_count += 1

                    await database_api.store_action_info(
                        chat_stream=self.chat_stream,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                        action_done=True,
                        thinking_id=thinking_id,
                        action_data={},
                        action_name="no_reply",
                        action_reasoning=reason,
                    )

                    return {"action_type": "no_reply", "success": True, "result": "选择不回复", "command": ""}

                elif action_planner_info.action_type == "no_reply_until_call":
                    # 直接当场执行no_reply_until_call逻辑
                    logger.info(f"{self.log_prefix} 保持沉默，直到有人直接叫的名字")
                    reason = action_planner_info.reasoning or "选择不回复"

                    # 增加连续 no_reply 计数
                    self.consecutive_no_reply_count += 1
                    self.no_reply_until_call = True
                    await database_api.store_action_info(
                        chat_stream=self.chat_stream,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                        action_done=True,
                        thinking_id=thinking_id,
                        action_data={},
                        action_name="no_reply_until_call",
                        action_reasoning=reason,
                    )
                    return {
                        "action_type": "no_reply_until_call",
                        "success": True,
                        "result": "保持沉默，直到有人直接叫的名字",
                        "command": "",
                    }

                elif action_planner_info.action_type == "reply":
                    # 直接当场执行reply逻辑
                    self.questioned = False
                    # 刷新主动发言状态
                    # 重置连续 no_reply 计数
                    self.consecutive_no_reply_count = 0

                    reason = action_planner_info.reasoning or ""
                    # 使用 action_reasoning（planner 的整体思考理由）作为 reply_reason
                    planner_reasoning = action_planner_info.action_reasoning or reason
                    await database_api.store_action_info(
                        chat_stream=self.chat_stream,
                        action_build_into_prompt=False,
                        action_prompt_display=reason,
                        action_done=True,
                        thinking_id=thinking_id,
                        action_data={},
                        action_name="reply",
                        action_reasoning=reason,
                    )

                    success, llm_response = await generator_api.generate_reply(
                        chat_stream=self.chat_stream,
                        reply_message=action_planner_info.action_message,
                        available_actions=available_actions,
                        chosen_actions=chosen_action_plan_infos,
                        reply_reason=planner_reasoning,
                        enable_tool=global_config.tool.enable_tool,
                        request_type="replyer",
                        from_plugin=False,
                        reply_time_point=action_planner_info.action_data.get("loop_start_time", time.time()),
                    )

                    if not success or not llm_response or not llm_response.reply_set:
                        if action_planner_info.action_message:
                            logger.info(f"对 {action_planner_info.action_message.processed_plain_text} 的回复生成失败")
                        else:
                            logger.info("回复生成失败")
                        return {"action_type": "reply", "success": False, "result": "回复生成失败", "loop_info": None}

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
                    self.last_active_time = time.time()
                    return {
                        "action_type": "reply",
                        "success": True,
                        "result": f"你回复内容{reply_text}",
                        "loop_info": loop_info,
                    }

                else:
                    # 执行普通动作
                    with Timer("动作执行", cycle_timers):
                        success, result = await self._handle_action(
                            action=action_planner_info.action_type,
                            action_reasoning=action_planner_info.action_reasoning or "",
                            action_data=action_planner_info.action_data or {},
                            cycle_timers=cycle_timers,
                            thinking_id=thinking_id,
                            action_message=action_planner_info.action_message,
                        )

                    self.last_active_time = time.time()
                    return {
                        "action_type": action_planner_info.action_type,
                        "success": success,
                        "result": result,
                    }

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行动作时出错: {e}")
            logger.error(f"{self.log_prefix} 错误信息: {traceback.format_exc()}")
            return {
                "action_type": action_planner_info.action_type,
                "success": False,
                "result": "",
                "loop_info": None,
                "error": str(e),
            }

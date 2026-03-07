import json
import time
import traceback
import random
import re
from typing import Dict, Optional, Tuple, List, TYPE_CHECKING
from rich.traceback import install
from datetime import datetime
from json_repair import repair_json

from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.common.logger import get_logger
from src.chat.logger.plan_reply_logger import PlanReplyLogger
from src.common.data_models.info_data_model import ActionPlannerInfo
from src.prompt.prompt_manager import prompt_manager
from src.chat.utils.chat_message_builder import (
    build_readable_actions,
    get_actions_by_timestamp_with_chat,
    build_readable_messages_with_id,
    get_raw_msg_before_timestamp_with_chat,
)
from src.chat.utils.utils import get_chat_type_and_target_info
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.core.types import ActionActivationType, ActionInfo, ComponentType
from src.core.component_registry import component_registry

if TYPE_CHECKING:
    from src.common.data_models.info_data_model import TargetPersonInfo
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("planner")

install(extra_lines=3)


class BrainPlanner:
    def __init__(self, chat_id: str, action_manager: ActionManager):
        self.chat_id = chat_id
        self.log_prefix = f"[{_chat_manager.get_session_name(chat_id) or chat_id}]"
        self.action_manager = action_manager
        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model_set=model_config.model_task_config.planner, request_type="planner"
        )  # 用于动作规划

        self.last_obs_time_mark = 0.0

        # 计划日志记录
        self.plan_log: List[Tuple[str, float, List[ActionPlannerInfo]]] = []

    def find_message_by_id(
        self, message_id: str, message_id_list: List[Tuple[str, "DatabaseMessages"]]
    ) -> Optional["DatabaseMessages"]:
        # sourcery skip: use-next
        """
        根据message_id从message_id_list中查找对应的原始消息

        Args:
            message_id: 要查找的消息ID
            message_id_list: 消息ID列表，格式为[{'id': str, 'message': dict}, ...]

        Returns:
            找到的原始消息字典，如果未找到则返回None
        """
        for item in message_id_list:
            if item[0] == message_id:
                return item[1]
        return None

    def _parse_single_action(
        self,
        action_json: dict,
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        current_available_actions: List[Tuple[str, ActionInfo]],
    ) -> List[ActionPlannerInfo]:
        """解析单个action JSON并返回ActionPlannerInfo列表"""
        action_planner_infos = []

        try:
            action = action_json.get("action", "complete_talk")
            logger.debug(f"{self.log_prefix}解析动作JSON: action={action}, json={action_json}")
            reasoning = action_json.get("reason", "未提供原因")
            action_data = {key: value for key, value in action_json.items() if key not in ["action", "reason"]}
            # 非complete_talk动作需要target_message_id
            target_message = None

            if target_message_id := action_json.get("target_message_id"):
                # 根据target_message_id查找原始消息
                target_message = self.find_message_by_id(target_message_id, message_id_list)
                if target_message is None:
                    logger.warning(f"{self.log_prefix}无法找到target_message_id '{target_message_id}' 对应的消息")
                    # 选择最新消息作为target_message
                    target_message = message_id_list[-1][1]
            else:
                target_message = message_id_list[-1][1]
                logger.debug(f"{self.log_prefix}动作'{action}'缺少target_message_id，使用最新消息作为target_message")

            # 验证action是否可用
            available_action_names = [action_name for action_name, _ in current_available_actions]
            # 内部保留动作（不依赖插件系统）
            # 注意：listening 已合并到 wait 中，如果遇到 listening 则转换为 wait
            internal_action_names = ["complete_talk", "reply", "wait_time", "wait", "listening"]

            logger.debug(
                f"{self.log_prefix}动作验证: action={action}, internal={internal_action_names}, available={available_action_names}"
            )

            # 将 listening 转换为 wait（向后兼容）
            if action == "listening":
                logger.debug(f"{self.log_prefix}检测到 listening 动作，已合并到 wait，自动转换")
                action = "wait"

            if action not in internal_action_names and action not in available_action_names:
                logger.warning(
                    f"{self.log_prefix}LLM 返回了当前不可用或无效的动作: '{action}' (内部动作: {internal_action_names}, 可用插件动作: {available_action_names})，将强制使用 'complete_talk'"
                )
                reasoning = (
                    f"LLM 返回了当前不可用的动作 '{action}' (可用: {available_action_names})。原始理由: {reasoning}"
                )
                action = "complete_talk"
                logger.warning(f"{self.log_prefix}动作已转换为 complete_talk")

            # 创建ActionPlannerInfo对象
            # 将列表转换为字典格式
            available_actions_dict = dict(current_available_actions)
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type=action,
                    reasoning=reasoning,
                    action_data=action_data,
                    action_message=target_message,
                    available_actions=available_actions_dict,
                )
            )

        except Exception as e:
            logger.error(f"{self.log_prefix}解析单个action时出错: {e}")
            # 将列表转换为字典格式
            available_actions_dict = dict(current_available_actions)
            action_planner_infos.append(
                ActionPlannerInfo(
                    action_type="complete_talk",
                    reasoning=f"解析单个action时出错: {e}",
                    action_data={},
                    action_message=None,
                    available_actions=available_actions_dict,
                )
            )

        return action_planner_infos

    async def plan(
        self,
        available_actions: Dict[str, ActionInfo],
        loop_start_time: float = 0.0,
    ) -> List[ActionPlannerInfo]:
        # sourcery skip: use-named-expression
        """
        规划器 (Planner): 使用LLM根据上下文决定做出什么动作（ReAct模式）。
        """
        plan_start = time.perf_counter()

        # 获取聊天上下文
        message_list_before_now = get_raw_msg_before_timestamp_with_chat(
            chat_id=self.chat_id,
            timestamp=time.time(),
            limit=int(global_config.chat.max_context_size * 0.6),
            filter_intercept_message_level=1,
        )
        message_id_list: list[Tuple[str, "DatabaseMessages"]] = []
        chat_content_block, message_id_list = build_readable_messages_with_id(
            messages=message_list_before_now,
            timestamp_mode="normal_no_YMD",
            read_mark=self.last_obs_time_mark,
            truncate=True,
            show_actions=True,
        )

        message_list_before_now_short = message_list_before_now[-int(global_config.chat.max_context_size * 0.3) :]
        chat_content_block_short, message_id_list_short = build_readable_messages_with_id(
            messages=message_list_before_now_short,
            timestamp_mode="normal_no_YMD",
            truncate=False,
            show_actions=False,
        )

        self.last_obs_time_mark = time.time()

        # 获取必要信息
        is_group_chat, chat_target_info, current_available_actions = self.get_necessary_info()

        # 提及/被@ 的处理由心流或统一判定模块驱动；Planner 不再做硬编码强制回复

        # 应用激活类型过滤
        filtered_actions = self._filter_actions_by_activation_type(available_actions, chat_content_block_short)

        logger.debug(f"{self.log_prefix}过滤后有{len(filtered_actions)}个可用动作")

        prompt_build_start = time.perf_counter()
        # 构建包含所有动作的提示词：使用统一的 ReAct Prompt
        prompt_key = "brain_planner"
        # 这里不记录日志，避免重复打印，由调用方按需控制 log_prompt
        prompt, message_id_list = await self.build_planner_prompt(
            chat_target_info=chat_target_info,
            current_available_actions=filtered_actions,
            chat_content_block=chat_content_block,
            message_id_list=message_id_list,
            prompt_key=prompt_key,
        )
        prompt_build_ms = (time.perf_counter() - prompt_build_start) * 1000

        # 调用LLM获取决策
        reasoning, actions, llm_raw_output, llm_reasoning, llm_duration_ms = await self._execute_main_planner(
            prompt=prompt,
            message_id_list=message_id_list,
            filtered_actions=filtered_actions,
            available_actions=available_actions,
            loop_start_time=loop_start_time,
        )

        # 记录和展示计划日志
        logger.info(
            f"{self.log_prefix}Planner: {reasoning}。选择了{len(actions)}个动作: {' '.join([a.action_type for a in actions])}"
        )
        self.add_plan_log(reasoning, actions)

        try:
            PlanReplyLogger.log_plan(
                chat_id=self.chat_id,
                prompt=prompt,
                reasoning=reasoning,
                raw_output=llm_raw_output,
                raw_reasoning=llm_reasoning,
                actions=actions,
                timing={
                    "prompt_build_ms": round(prompt_build_ms, 2),
                    "llm_duration_ms": round(llm_duration_ms, 2) if llm_duration_ms is not None else None,
                    "total_plan_ms": round((time.perf_counter() - plan_start) * 1000, 2),
                    "loop_start_time": loop_start_time,
                },
                extra=None,
            )
        except Exception:
            logger.exception(f"{self.log_prefix}记录plan日志失败")

        return actions

    async def build_planner_prompt(
        self,
        chat_target_info: Optional["TargetPersonInfo"],
        current_available_actions: Dict[str, ActionInfo],
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        chat_content_block: str = "",
        interest: str = "",
        prompt_key: str = "brain_planner",
    ) -> tuple[str, List[Tuple[str, "DatabaseMessages"]]]:
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            # 获取最近执行过的动作
            actions_before_now = get_actions_by_timestamp_with_chat(
                chat_id=self.chat_id,
                timestamp_start=time.time() - 600,
                timestamp_end=time.time(),
                limit=6,
            )
            actions_before_now_block = build_readable_actions(actions=actions_before_now)
            if actions_before_now_block:
                actions_before_now_block = f"你刚刚选择并执行过的action是：\n{actions_before_now_block}"
            else:
                actions_before_now_block = ""

            chat_context_description: str = ""
            if chat_target_info:
                # 构建聊天上下文描述
                chat_context_description = (
                    f"你正在和 {chat_target_info.person_name or chat_target_info.user_nickname or '对方'} 聊天中"
                )

            # 构建动作选项块
            action_options_block = await self._build_action_options_block(current_available_actions)

            # 其他信息
            moderation_prompt_block = "请不要输出违法违规内容，不要输出色情，暴力，政治相关内容，如有敏感内容，请规避。"
            time_block = f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            bot_name = global_config.bot.nickname
            bot_nickname = (
                f",也可以叫你{','.join(global_config.bot.alias_names)}" if global_config.bot.alias_names else ""
            )
            name_block = f"你的名字是{bot_name}{bot_nickname}，请注意哪些是你自己的发言。"

            # 获取主规划器模板并填充
            planner_prompt_template = prompt_manager.get_prompt(prompt_key)
            planner_prompt_template.add_context("time_block", time_block)
            planner_prompt_template.add_context("chat_context_description", chat_context_description)
            planner_prompt_template.add_context("chat_content_block", chat_content_block)
            planner_prompt_template.add_context("actions_before_now_block", actions_before_now_block)
            planner_prompt_template.add_context("action_options_text", action_options_block)
            planner_prompt_template.add_context("moderation_prompt", moderation_prompt_block)
            planner_prompt_template.add_context("name_block", name_block)
            planner_prompt_template.add_context("interest", interest)
            planner_prompt_template.add_context("plan_style", global_config.experimental.private_plan_style)
            prompt = await prompt_manager.render_prompt(planner_prompt_template)

            return prompt, message_id_list
        except Exception as e:
            logger.error(f"构建 Planner 提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "构建 Planner Prompt 时出错", []

    def get_necessary_info(self) -> Tuple[bool, Optional["TargetPersonInfo"], Dict[str, ActionInfo]]:
        """
        获取 Planner 需要的必要信息
        """
        is_group_chat = True
        is_group_chat, chat_target_info = get_chat_type_and_target_info(self.chat_id)
        logger.debug(f"{self.log_prefix}获取到聊天信息 - 群聊: {is_group_chat}, 目标信息: {chat_target_info}")

        current_available_actions_dict = self.action_manager.get_using_actions()

        # 获取完整的动作信息
        all_registered_actions: Dict[str, ActionInfo] = component_registry.get_components_by_type(  # type: ignore
            ComponentType.ACTION
        )
        current_available_actions = {}
        for action_name in current_available_actions_dict:
            if action_name in all_registered_actions:
                current_available_actions[action_name] = all_registered_actions[action_name]
            else:
                logger.warning(f"{self.log_prefix}使用中的动作 {action_name} 未在已注册动作中找到")

        return is_group_chat, chat_target_info, current_available_actions

    def _filter_actions_by_activation_type(
        self, available_actions: Dict[str, ActionInfo], chat_content_block: str
    ) -> Dict[str, ActionInfo]:
        """根据激活类型过滤动作"""
        filtered_actions = {}

        for action_name, action_info in available_actions.items():
            if action_info.activation_type == ActionActivationType.NEVER:
                logger.debug(f"{self.log_prefix}动作 {action_name} 设置为 NEVER 激活类型，跳过")
                continue
            elif action_info.activation_type == ActionActivationType.ALWAYS:
                filtered_actions[action_name] = action_info
            elif action_info.activation_type == ActionActivationType.RANDOM:
                if random.random() < action_info.random_activation_probability:
                    filtered_actions[action_name] = action_info
            elif action_info.activation_type == ActionActivationType.KEYWORD:
                if action_info.activation_keywords:
                    for keyword in action_info.activation_keywords:
                        if keyword in chat_content_block:
                            filtered_actions[action_name] = action_info
                            break
            else:
                logger.warning(f"{self.log_prefix}未知的激活类型: {action_info.activation_type}，跳过处理")

        return filtered_actions

    async def _build_action_options_block(self, current_available_actions: Dict[str, ActionInfo]) -> str:
        # sourcery skip: use-join
        """构建动作选项块"""
        if not current_available_actions:
            return ""

        action_options_block = ""
        for action_name, action_info in current_available_actions.items():
            # 构建参数文本
            param_text = ""
            if action_info.action_parameters:
                param_text = "\n"
                for param_name, param_description in action_info.action_parameters.items():
                    param_text += f'    "{param_name}":"{param_description}"\n'
                param_text = param_text.rstrip("\n")

            # 构建要求文本
            require_text = ""
            for require_item in action_info.action_require:
                require_text += f"- {require_item}\n"
            require_text = require_text.rstrip("\n")

            # 获取动作提示模板并填充
            using_action_prompt_template = prompt_manager.get_prompt("brain_action")
            using_action_prompt_template.add_context("action_name", action_name)
            using_action_prompt_template.add_context("action_description", action_info.description)
            using_action_prompt_template.add_context("action_parameters", param_text)
            using_action_prompt_template.add_context("action_require", require_text)
            using_action_prompt = await prompt_manager.render_prompt(using_action_prompt_template)

            action_options_block += using_action_prompt

        return action_options_block

    async def _execute_main_planner(
        self,
        prompt: str,
        message_id_list: List[Tuple[str, "DatabaseMessages"]],
        filtered_actions: Dict[str, ActionInfo],
        available_actions: Dict[str, ActionInfo],
        loop_start_time: float,
    ) -> Tuple[str, List[ActionPlannerInfo], Optional[str], Optional[str], Optional[float]]:
        """执行主规划器"""
        llm_content = None
        actions: List[ActionPlannerInfo] = []
        extracted_reasoning = ""
        llm_reasoning = None
        llm_duration_ms = None

        try:
            # 调用LLM
            llm_start = time.perf_counter()
            llm_content, (reasoning_content, _, _) = await self.planner_llm.generate_response_async(prompt=prompt)
            llm_duration_ms = (time.perf_counter() - llm_start) * 1000
            llm_reasoning = reasoning_content

            logger.info(f"{self.log_prefix}规划器原始提示词: {prompt}")
            logger.info(f"{self.log_prefix}规划器原始响应: {llm_content}")

            if global_config.debug.show_planner_prompt:
                logger.info(f"{self.log_prefix}规划器原始提示词: {prompt}")
                logger.info(f"{self.log_prefix}规划器原始响应: {llm_content}")
                if reasoning_content:
                    logger.info(f"{self.log_prefix}规划器推理: {reasoning_content}")
            else:
                logger.debug(f"{self.log_prefix}规划器原始提示词: {prompt}")
                logger.debug(f"{self.log_prefix}规划器原始响应: {llm_content}")
                if reasoning_content:
                    logger.debug(f"{self.log_prefix}规划器推理: {reasoning_content}")

        except Exception as req_e:
            logger.error(f"{self.log_prefix}LLM 请求执行失败: {req_e}")
            extracted_reasoning = f"LLM 请求失败，模型出现问题: {req_e}"
            return (
                extracted_reasoning,
                [
                    ActionPlannerInfo(
                        action_type="complete_talk",
                        reasoning=extracted_reasoning,
                        action_data={},
                        action_message=None,
                        available_actions=available_actions,
                    )
                ],
                llm_content,
                llm_reasoning,
                llm_duration_ms,
            )

        # 解析LLM响应
        if llm_content:
            try:
                json_objects, extracted_reasoning = self._extract_json_from_markdown(llm_content)
                if json_objects:
                    logger.info(f"{self.log_prefix}从响应中提取到{len(json_objects)}个JSON对象")
                    for i, json_obj in enumerate(json_objects):
                        logger.info(f"{self.log_prefix}解析第{i + 1}个JSON对象: {json_obj}")
                    filtered_actions_list = list(filtered_actions.items())
                    for json_obj in json_objects:
                        parsed_actions = self._parse_single_action(json_obj, message_id_list, filtered_actions_list)
                        logger.info(f"{self.log_prefix}解析后的动作: {[a.action_type for a in parsed_actions]}")
                        actions.extend(parsed_actions)
                else:
                    # 尝试解析为直接的JSON
                    logger.warning(f"{self.log_prefix}LLM没有返回可用动作: {llm_content}")
                    extracted_reasoning = extracted_reasoning or "LLM没有返回可用动作"
                    actions = self._create_complete_talk(extracted_reasoning, available_actions)

            except Exception as json_e:
                logger.warning(f"{self.log_prefix}解析LLM响应JSON失败 {json_e}. LLM原始输出: '{llm_content}'")
                extracted_reasoning = f"解析LLM响应JSON失败: {json_e}"
                actions = self._create_complete_talk(extracted_reasoning, available_actions)
                traceback.print_exc()
        else:
            extracted_reasoning = "规划器没有获得LLM响应"
            actions = self._create_complete_talk(extracted_reasoning, available_actions)

        # 添加循环开始时间到所有动作
        for action in actions:
            action.action_data = action.action_data or {}
            action.action_data["loop_start_time"] = loop_start_time

        logger.debug(
            f"{self.log_prefix}规划器决定执行{len(actions)}个动作: {' '.join([a.action_type for a in actions])}"
        )

        return extracted_reasoning, actions, llm_content, llm_reasoning, llm_duration_ms

    def _create_complete_talk(
        self, reasoning: str, available_actions: Dict[str, ActionInfo]
    ) -> List[ActionPlannerInfo]:
        """创建complete_talk"""
        return [
            ActionPlannerInfo(
                action_type="complete_talk",
                reasoning=reasoning,
                action_data={},
                action_message=None,
                available_actions=available_actions,
            )
        ]

    def add_plan_log(self, reasoning: str, actions: List[ActionPlannerInfo]):
        """添加计划日志"""
        self.plan_log.append((reasoning, time.time(), actions))
        if len(self.plan_log) > 20:
            self.plan_log.pop(0)

    def _extract_json_from_markdown(self, content: str) -> Tuple[List[dict], str]:
        # sourcery skip: for-append-to-extend
        """从Markdown格式的内容中提取JSON对象和推理内容"""
        json_objects = []
        reasoning_content = ""

        # 使用正则表达式查找```json包裹的JSON内容
        json_pattern = r"```json\s*(.*?)\s*```"
        markdown_matches = re.findall(json_pattern, content, re.DOTALL)

        # 提取JSON之前的内容作为推理文本
        first_json_pos = len(content)
        if markdown_matches:
            # 找到第一个```json的位置
            first_json_pos = content.find("```json")
            if first_json_pos > 0:
                reasoning_content = content[:first_json_pos].strip()
                # 清理推理内容中的注释标记
                reasoning_content = re.sub(r"^//\s*", "", reasoning_content, flags=re.MULTILINE)
                reasoning_content = reasoning_content.strip()

        # 处理```json包裹的JSON
        for match in markdown_matches:
            try:
                # 清理可能的注释和格式问题
                json_str = re.sub(r"//.*?\n", "\n", match)  # 移除单行注释
                json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)  # 移除多行注释
                if json_str := json_str.strip():
                    # 先尝试将整个块作为一个JSON对象或数组（适用于多行JSON）
                    try:
                        json_obj = json.loads(repair_json(json_str))
                        if isinstance(json_obj, dict):
                            json_objects.append(json_obj)
                        elif isinstance(json_obj, list):
                            for item in json_obj:
                                if isinstance(item, dict):
                                    json_objects.append(item)
                    except json.JSONDecodeError:
                        # 如果整个块解析失败，尝试按行分割（适用于多个单行JSON对象）
                        lines = [line.strip() for line in json_str.split("\n") if line.strip()]
                        for line in lines:
                            try:
                                # 尝试解析每一行作为独立的JSON对象
                                json_obj = json.loads(repair_json(line))
                                if isinstance(json_obj, dict):
                                    json_objects.append(json_obj)
                                elif isinstance(json_obj, list):
                                    for item in json_obj:
                                        if isinstance(item, dict):
                                            json_objects.append(item)
                            except json.JSONDecodeError:
                                # 单行解析失败，继续下一行
                                continue
            except Exception as e:
                logger.warning(f"{self.log_prefix}解析JSON块失败: {e}, 块内容: {match[:100]}...")
                continue

        # 如果没有找到完整的```json```块，尝试查找不完整的代码块（缺少结尾```）
        if not json_objects:
            json_start_pos = content.find("```json")
            if json_start_pos != -1:
                # 找到```json之后的内容
                json_content_start = json_start_pos + 7  # ```json的长度
                # 提取从```json之后到内容结尾的所有内容
                incomplete_json_str = content[json_content_start:].strip()

                # 提取JSON之前的内容作为推理文本
                if json_start_pos > 0:
                    reasoning_content = content[:json_start_pos].strip()
                    reasoning_content = re.sub(r"^//\s*", "", reasoning_content, flags=re.MULTILINE)
                    reasoning_content = reasoning_content.strip()

                if incomplete_json_str:
                    try:
                        # 清理可能的注释和格式问题
                        json_str = re.sub(r"//.*?\n", "\n", incomplete_json_str)
                        json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)
                        json_str = json_str.strip()

                        if json_str:
                            # 尝试按行分割，每行可能是一个JSON对象
                            lines = [line.strip() for line in json_str.split("\n") if line.strip()]
                            for line in lines:
                                try:
                                    json_obj = json.loads(repair_json(line))
                                    if isinstance(json_obj, dict):
                                        json_objects.append(json_obj)
                                    elif isinstance(json_obj, list):
                                        for item in json_obj:
                                            if isinstance(item, dict):
                                                json_objects.append(item)
                                except json.JSONDecodeError:
                                    pass

                            # 如果按行解析没有成功，尝试将整个块作为一个JSON对象或数组
                            if not json_objects:
                                try:
                                    json_obj = json.loads(repair_json(json_str))
                                    if isinstance(json_obj, dict):
                                        json_objects.append(json_obj)
                                    elif isinstance(json_obj, list):
                                        for item in json_obj:
                                            if isinstance(item, dict):
                                                json_objects.append(item)
                                except Exception as e:
                                    logger.debug(f"尝试解析不完整的JSON代码块失败: {e}")
                    except Exception as e:
                        logger.debug(f"处理不完整的JSON代码块时出错: {e}")

        return json_objects, reasoning_content

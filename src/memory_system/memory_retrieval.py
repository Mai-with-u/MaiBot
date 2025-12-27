import time
import json
import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.plugin_system.apis import llm_api
from src.common.database.database_model import ThinkingBack
from src.memory_system.retrieval_tools import get_tool_registry, init_all_tools
from src.memory_system.memory_utils import parse_questions_json
from src.llm_models.payload_content.message import MessageBuilder, RoleType, Message
from src.chat.message_receive.chat_stream import get_chat_manager
from src.bw_learner.jargon_explainer import retrieve_concepts_with_jargon

logger = get_logger("memory_retrieval")

THINKING_BACK_NOT_FOUND_RETENTION_SECONDS = 36000  # 未找到答案记录保留时长
THINKING_BACK_CLEANUP_INTERVAL_SECONDS = 3000  # 清理频率
_last_not_found_cleanup_ts: float = 0.0


def _cleanup_stale_not_found_thinking_back() -> None:
    """定期清理过期的未找到答案记录"""
    global _last_not_found_cleanup_ts

    now = time.time()
    if now - _last_not_found_cleanup_ts < THINKING_BACK_CLEANUP_INTERVAL_SECONDS:
        return

    threshold_time = now - THINKING_BACK_NOT_FOUND_RETENTION_SECONDS
    try:
        deleted_rows = (
            ThinkingBack.delete()
            .where((ThinkingBack.found_answer == 0) & (ThinkingBack.update_time < threshold_time))
            .execute()
        )
        if deleted_rows:
            logger.info(f"清理过期的未找到答案thinking_back记录 {deleted_rows} 条")
        _last_not_found_cleanup_ts = now
    except Exception as e:
        logger.error(f"清理未找到答案的thinking_back记录失败: {e}")


def init_memory_retrieval_prompt():
    """初始化记忆检索相关的 prompt 模板和工具"""
    # 首先注册所有工具
    init_all_tools()

    # 第一步：问题生成prompt
    Prompt(
        """
你的名字是{bot_name}。现在是{time_now}。
群里正在进行的聊天内容：
{chat_history}

{recent_query_history}

现在，{sender}发送了内容:{target_message},你想要回复ta。
请仔细分析聊天内容，考虑以下几点：
1. 对话中是否提到了过去发生的事情、人物、事件或信息
2. 是否有需要回忆的内容（比如"之前说过"、"上次"、"以前"等）
3. 是否有需要查找历史信息的问题
4. 是否有问题可以搜集信息帮助你聊天

重要提示：
- **每次只能提出一个问题**，选择最需要查询的关键问题
- 如果"最近已查询的问题和结果"中已经包含了类似的问题并得到了答案，请避免重复生成相同或相似的问题，不需要重复查询
- 如果之前已经查询过某个问题但未找到答案，可以尝试用不同的方式提问或更具体的问题

如果你认为需要从记忆中检索信息来回答，请根据上下文提出**一个**最关键的问题来帮助你回复目标消息，放入"questions"字段

问题格式示例：
- "xxx在前几天干了什么"
- "xxx是什么，在什么时候提到过?"
- "xxxx和xxx的关系是什么"
- "xxx在某个时间点发生了什么"

问题要说明前因后果和上下文，使其全面且精准

输出格式示例：
```json
{{
  "questions": ["张三在前几天干了什么"] #问题数组（字符串数组），如果不需要检索记忆则输出空数组[]，如果需要检索则只输出包含一个问题的数组
}}
```
请只输出JSON对象，不要输出其他内容：
""",
        name="memory_retrieval_question_prompt",
    )

    # 第二步：ReAct Agent prompt（使用function calling，要求先思考再行动）
    Prompt(
        """你的名字是{bot_name}。现在是{time_now}。
你正在参与聊天，你需要搜集信息来回答问题，帮助你参与聊天。
当前需要解答的问题：{question}
已收集的信息：
{collected_info}

**工具说明：**
- 如果涉及过往事件，或者查询某个过去可能提到过的概念，或者某段时间发生的事件。可以使用聊天记录查询工具查询过往事件
- 如果涉及人物，可以使用人物信息查询工具查询人物信息
- 如果遇到不熟悉的词语、缩写、黑话或网络用语，可以使用query_words工具查询其含义
- 如果没有可靠信息，且查询时间充足，或者不确定查询类别，也可以使用lpmm知识库查询，作为辅助信息

**思考**
- 你可以对查询思路给出简短的思考：思考要简短，直接切入要点
- 先思考当前信息是否足够回答问题
- 如果信息不足，则需要使用tool查询信息，你必须给出使用什么工具进行查询
- 如果当前已收集的信息足够或信息不足确定无法找到答案，你必须调用finish_search工具结束查询
""",
        name="memory_retrieval_react_prompt_head",
    )

    # 额外，如果最后一轮迭代：ReAct Agent prompt（使用function calling，要求先思考再行动）
    Prompt(
        """你的名字是{bot_name}。现在是{time_now}。
你正在参与聊天，你需要根据搜集到的信息判断问题是否可以回答问题。

当前问题：{question}
已收集的信息：
{collected_info}

分析：
- 当前信息是否足够回答问题？
- **如果信息足够且能找到明确答案**，在思考中直接给出答案，格式为：found_answer(answer="你的答案内容")
- **如果信息不足或无法找到答案**，在思考中给出：not_enough_info(reason="信息不足或无法找到答案的原因")

**重要规则：**
- 必须严格使用检索到的信息回答问题，不要编造信息
- 答案必须精简，不要过多解释
- **只有在检索到明确、具体的答案时，才使用found_answer**
- **如果信息不足、无法确定、找不到相关信息，必须使用not_enough_info，不要使用found_answer**
- 答案必须给出，格式为 found_answer(answer="...") 或 not_enough_info(reason="...")。
""",
        name="memory_retrieval_react_final_prompt",
    )


def _log_conversation_messages(
    conversation_messages: List[Message],
    head_prompt: Optional[str] = None,
    final_status: Optional[str] = None,
) -> None:
    """输出对话消息列表的日志

    Args:
        conversation_messages: 对话消息列表
        head_prompt: 第一条系统消息（head_prompt）的内容，可选
        final_status: 最终结果状态描述（例如：找到答案/未找到答案），可选
    """
    if not global_config.debug.show_memory_prompt:
        return

    log_lines: List[str] = []

    # 如果有head_prompt，先添加为第一条消息
    if head_prompt:
        msg_info = "========================================\n[消息 1] 角色: System\n-----------------------------"
        msg_info += f"\n{head_prompt}"
        log_lines.append(msg_info)
        start_idx = 2
    else:
        start_idx = 1

    if not conversation_messages and not head_prompt:
        return

    for idx, msg in enumerate(conversation_messages, start_idx):
        role_name = msg.role.value if hasattr(msg.role, "value") else str(msg.role)

        # 构建单条消息的日志信息
        # msg_info = f"\n========================================\n[消息 {idx}] 角色: {role_name} 内容类型: {content_type}\n-----------------------------"
        msg_info = (
            f"\n========================================\n[消息 {idx}] 角色: {role_name}\n-----------------------------"
        )

        # if full_content:
        #     msg_info += f"\n{full_content}"
        if msg.content:
            msg_info += f"\n{msg.content}"

        if msg.tool_calls:
            msg_info += f"\n  工具调用: {len(msg.tool_calls)}个"
            for tool_call in msg.tool_calls:
                msg_info += f"\n    - {tool_call.func_name}: {json.dumps(tool_call.args, ensure_ascii=False)}"

        # if msg.tool_call_id:
        # msg_info += f"\n  工具调用ID: {msg.tool_call_id}"

        log_lines.append(msg_info)

    total_count = len(conversation_messages) + (1 if head_prompt else 0)
    log_text = f"消息列表 (共{total_count}条):{''.join(log_lines)}"
    if final_status:
        log_text += f"\n\n[最终结果] {final_status}"
    logger.info(log_text)


async def _react_agent_solve_question(
    question: str,
    chat_id: str,
    max_iterations: int = 5,
    timeout: float = 30.0,
    initial_info: str = "",
) -> Tuple[bool, str, List[Dict[str, Any]], bool]:
    """使用ReAct架构的Agent来解决问题

    Args:
        question: 要回答的问题
        chat_id: 聊天ID
        max_iterations: 最大迭代次数
        timeout: 超时时间（秒）
        initial_info: 初始信息，将作为collected_info的初始值

    Returns:
        Tuple[bool, str, List[Dict[str, Any]], bool]: (是否找到答案, 答案内容, 思考步骤列表, 是否超时)
    """
    start_time = time.time()
    collected_info = initial_info if initial_info else ""
    # 构造日志前缀：[聊天流名称]，用于在日志中标识聊天流
    try:
        chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id
    except Exception:
        chat_name = chat_id
    react_log_prefix = f"[{chat_name}] "
    thinking_steps = []
    is_timeout = False
    conversation_messages: List[Message] = []
    first_head_prompt: Optional[str] = None  # 保存第一次使用的head_prompt（用于日志显示）
    last_tool_name: Optional[str] = None  # 记录最后一次使用的工具名称

    # 使用 while 循环，支持额外迭代
    iteration = 0
    max_iterations_with_extra = max_iterations
    while iteration < max_iterations_with_extra:
        # 检查超时
        if time.time() - start_time > timeout:
            logger.warning(f"ReAct Agent超时，已迭代{iteration}次")
            is_timeout = True
            break

        # 获取工具注册器
        tool_registry = get_tool_registry()

        # 获取bot_name
        bot_name = global_config.bot.nickname

        # 获取当前时间
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # 计算剩余迭代次数
        current_iteration = iteration + 1
        remaining_iterations = max_iterations - current_iteration

        # 提取函数调用中参数的值，支持单引号和双引号
        def extract_quoted_content(text, func_name, param_name):
            """从文本中提取函数调用中参数的值，支持单引号和双引号

            Args:
                text: 要搜索的文本
                func_name: 函数名，如 'found_answer'
                param_name: 参数名，如 'answer'

            Returns:
                提取的参数值，如果未找到则返回None
            """
            if not text:
                return None

            # 查找函数调用位置（不区分大小写）
            func_pattern = func_name.lower()
            text_lower = text.lower()
            func_pos = text_lower.find(func_pattern)
            if func_pos == -1:
                return None

            # 查找参数名和等号
            param_pattern = f"{param_name}="
            param_pos = text_lower.find(param_pattern, func_pos)
            if param_pos == -1:
                return None

            # 跳过参数名、等号和空白
            start_pos = param_pos + len(param_pattern)
            while start_pos < len(text) and text[start_pos] in " \t\n":
                start_pos += 1

            if start_pos >= len(text):
                return None

            # 确定引号类型
            quote_char = text[start_pos]
            if quote_char not in ['"', "'"]:
                return None

            # 查找匹配的结束引号（考虑转义）
            end_pos = start_pos + 1
            while end_pos < len(text):
                if text[end_pos] == quote_char:
                    # 检查是否是转义的引号
                    if end_pos > start_pos + 1 and text[end_pos - 1] == "\\":
                        end_pos += 1
                        continue
                    # 找到匹配的引号
                    content = text[start_pos + 1 : end_pos]
                    # 处理转义字符
                    content = content.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
                    return content
                end_pos += 1

            return None

        # 正常迭代：使用head_prompt决定调用哪些工具（包含finish_search工具）
        tool_definitions = tool_registry.get_tool_definitions()
        # tool_names = [tool_def["name"] for tool_def in tool_definitions]
        # logger.debug(f"ReAct Agent 第 {iteration + 1} 次迭代，问题: {question}|可用工具: {', '.join(tool_names)} (共{len(tool_definitions)}个)")

        # head_prompt应该只构建一次，使用初始的collected_info，后续迭代都复用同一个
        if first_head_prompt is None:
            # 第一次构建，使用初始的collected_info（即initial_info）
            initial_collected_info = initial_info if initial_info else ""
            first_head_prompt = await global_prompt_manager.format_prompt(
                "memory_retrieval_react_prompt_head",
                bot_name=bot_name,
                time_now=time_now,
                question=question,
                collected_info=initial_collected_info,
                current_iteration=current_iteration,
                remaining_iterations=remaining_iterations,
                max_iterations=max_iterations,
            )

        # 后续迭代都复用第一次构建的head_prompt
        head_prompt = first_head_prompt

        def message_factory(
            _client,
            *,
            _head_prompt: str = head_prompt,
            _conversation_messages: List[Message] = conversation_messages,
        ) -> List[Message]:
            messages: List[Message] = []

            system_builder = MessageBuilder()
            system_builder.set_role(RoleType.System)
            system_builder.add_text_content(_head_prompt)
            messages.append(system_builder.build())

            messages.extend(_conversation_messages)

            return messages

        (
            success,
            response,
            reasoning_content,
            model_name,
            tool_calls,
        ) = await llm_api.generate_with_model_with_tools_by_message_factory(
            message_factory,
            model_config=model_config.model_task_config.tool_use,
            tool_options=tool_definitions,
            request_type="memory.react",
        )

        # logger.info(
        # f"ReAct Agent 第 {iteration + 1} 次迭代 模型: {model_name} ，调用工具数量: {len(tool_calls) if tool_calls else 0} ，调用工具响应: {response}"
        # )

        if not success:
            logger.error(f"ReAct Agent LLM调用失败: {response}")
            break

        # 注意：这里会检查finish_search工具调用，如果检测到finish_search工具，会根据found_answer参数决定返回答案或退出查询

        assistant_message: Optional[Message] = None
        if tool_calls:
            assistant_builder = MessageBuilder()
            assistant_builder.set_role(RoleType.Assistant)
            if response and response.strip():
                assistant_builder.add_text_content(response)
            assistant_builder.set_tool_calls(tool_calls)
            assistant_message = assistant_builder.build()
        elif response and response.strip():
            assistant_builder = MessageBuilder()
            assistant_builder.set_role(RoleType.Assistant)
            assistant_builder.add_text_content(response)
            assistant_message = assistant_builder.build()

        # 记录思考步骤
        step = {"iteration": iteration + 1, "thought": response, "actions": [], "observations": []}

        if assistant_message:
            conversation_messages.append(assistant_message)

        # 记录思考过程到collected_info中
        if reasoning_content or response:
            thought_summary = reasoning_content or (response[:200] if response else "")
            if thought_summary:
                collected_info += f"\n[思考] {thought_summary}\n"

        # 处理工具调用
        if not tool_calls:
            # 如果没有工具调用，检查响应文本中是否包含finish_search函数调用格式
            if response and response.strip():
                # 尝试从文本中解析finish_search函数调用
                def parse_finish_search_from_text(text: str):
                    """从文本中解析finish_search函数调用，返回(found_answer, answer)元组，如果未找到则返回(None, None)"""
                    if not text:
                        return None, None

                    # 查找finish_search函数调用位置（不区分大小写）
                    func_pattern = "finish_search"
                    text_lower = text.lower()
                    func_pos = text_lower.find(func_pattern)
                    if func_pos == -1:
                        return None, None

                    # 查找函数调用的开始和结束位置
                    # 从func_pos开始向后查找左括号
                    start_pos = text.find("(", func_pos)
                    if start_pos == -1:
                        return None, None

                    # 查找匹配的右括号（考虑嵌套）
                    paren_count = 0
                    end_pos = start_pos
                    for i in range(start_pos, len(text)):
                        if text[i] == "(":
                            paren_count += 1
                        elif text[i] == ")":
                            paren_count -= 1
                            if paren_count == 0:
                                end_pos = i
                                break
                    else:
                        # 没有找到匹配的右括号
                        return None, None

                    # 提取函数参数部分
                    params_text = text[start_pos + 1 : end_pos]

                    # 解析found_answer参数（布尔值，可能是true/false/True/False）
                    found_answer = None
                    found_answer_patterns = [
                        r"found_answer\s*=\s*true",
                        r"found_answer\s*=\s*True",
                        r"found_answer\s*=\s*false",
                        r"found_answer\s*=\s*False",
                    ]
                    for pattern in found_answer_patterns:
                        match = re.search(pattern, params_text, re.IGNORECASE)
                        if match:
                            found_answer = "true" in match.group(0).lower()
                            break

                    # 解析answer参数（字符串，使用extract_quoted_content）
                    answer = extract_quoted_content(text, "finish_search", "answer")

                    return found_answer, answer

                parsed_found_answer, parsed_answer = parse_finish_search_from_text(response)

                if parsed_found_answer is not None:
                    # 检测到finish_search函数调用格式
                    if parsed_found_answer:
                        # 找到了答案
                        if parsed_answer:
                            step["actions"].append(
                                {
                                    "action_type": "finish_search",
                                    "action_params": {"found_answer": True, "answer": parsed_answer},
                                }
                            )
                            step["observations"] = ["检测到finish_search文本格式调用，找到答案"]
                            thinking_steps.append(step)
                            logger.info(
                                f"{react_log_prefix}第 {iteration + 1} 次迭代 通过finish_search文本格式找到关于问题{question}的答案: {parsed_answer}"
                            )

                            _log_conversation_messages(
                                conversation_messages,
                                head_prompt=first_head_prompt,
                                final_status=f"找到答案：{parsed_answer}",
                            )

                            return True, parsed_answer, thinking_steps, False
                        else:
                            # found_answer为True但没有提供answer，视为错误，继续迭代
                            logger.warning(
                                f"{react_log_prefix}第 {iteration + 1} 次迭代 finish_search文本格式found_answer为True但未提供answer"
                            )
                    else:
                        # 未找到答案，直接退出查询
                        step["actions"].append(
                            {"action_type": "finish_search", "action_params": {"found_answer": False}}
                        )
                        step["observations"] = ["检测到finish_search文本格式调用，未找到答案"]
                        thinking_steps.append(step)
                        logger.info(
                            f"{react_log_prefix}第 {iteration + 1} 次迭代 通过finish_search文本格式判断未找到答案"
                        )

                        _log_conversation_messages(
                            conversation_messages,
                            head_prompt=first_head_prompt,
                            final_status="未找到答案：通过finish_search文本格式判断未找到答案",
                        )

                        return False, "", thinking_steps, False

                # 如果没有检测到finish_search格式，记录思考过程，继续下一轮迭代
                step["observations"] = [f"思考完成，但未调用工具。响应: {response}"]
                logger.info(
                    f"{react_log_prefix}第 {iteration + 1} 次迭代 思考完成但未调用工具: {response}"
                )
                collected_info += f"思考: {response}"
            else:
                logger.warning(f"{react_log_prefix}第 {iteration + 1} 次迭代 无工具调用且无响应")
                step["observations"] = ["无响应且无工具调用"]
            thinking_steps.append(step)
            iteration += 1  # 在continue之前增加迭代计数，避免跳过iteration += 1
            continue

        # 处理工具调用
        # 首先检查是否有finish_search工具调用，如果有则立即返回，不再处理其他工具
        finish_search_found = None
        finish_search_answer = None
        for tool_call in tool_calls:
            tool_name = tool_call.func_name
            tool_args = tool_call.args or {}

            if tool_name == "finish_search":
                finish_search_found = tool_args.get("found_answer", False)
                finish_search_answer = tool_args.get("answer", "")

                if finish_search_found:
                    # 找到了答案
                    if finish_search_answer:
                        step["actions"].append(
                            {
                                "action_type": "finish_search",
                                "action_params": {"found_answer": True, "answer": finish_search_answer},
                            }
                        )
                        step["observations"] = ["检测到finish_search工具调用，找到答案"]
                        thinking_steps.append(step)
                        logger.info(
                            f"{react_log_prefix}第 {iteration + 1} 次迭代 通过finish_search工具找到关于问题{question}的答案: {finish_search_answer}"
                        )

                        _log_conversation_messages(
                            conversation_messages,
                            head_prompt=first_head_prompt,
                            final_status=f"找到答案：{finish_search_answer}",
                        )

                        return True, finish_search_answer, thinking_steps, False
                    else:
                        # found_answer为True但没有提供answer，视为错误
                        logger.warning(
                            f"{react_log_prefix}第 {iteration + 1} 次迭代 finish_search工具found_answer为True但未提供answer"
                        )
                else:
                    # 未找到答案，直接退出查询
                    step["actions"].append({"action_type": "finish_search", "action_params": {"found_answer": False}})
                    step["observations"] = ["检测到finish_search工具调用，未找到答案"]
                    thinking_steps.append(step)
                    logger.info(
                        f"{react_log_prefix}第 {iteration + 1} 次迭代 通过finish_search工具判断未找到答案"
                    )

                    _log_conversation_messages(
                        conversation_messages,
                        head_prompt=first_head_prompt,
                        final_status="未找到答案：通过finish_search工具判断未找到答案",
                    )

                    return False, "", thinking_steps, False

        # 如果没有finish_search工具调用，继续处理其他工具
        tool_tasks = []
        for i, tool_call in enumerate(tool_calls):
            tool_name = tool_call.func_name
            tool_args = tool_call.args or {}

            logger.debug(
                f"{react_log_prefix}第 {iteration + 1} 次迭代 工具调用 {i + 1}/{len(tool_calls)}: {tool_name}({tool_args})"
            )

            # 跳过finish_search工具调用（已经在上面处理过了）
            if tool_name == "finish_search":
                continue

            # 记录最后一次使用的工具名称（用于判断是否需要额外迭代）
            last_tool_name = tool_name

            # 普通工具调用
            tool = tool_registry.get_tool(tool_name)
            if tool:
                # 准备工具参数（需要添加chat_id如果工具需要）
                import inspect

                sig = inspect.signature(tool.execute_func)
                tool_params = tool_args.copy()
                if "chat_id" in sig.parameters:
                    tool_params["chat_id"] = chat_id

                # 创建异步任务
                async def execute_single_tool(tool_instance, params, tool_name_str, iter_num):
                    try:
                        observation = await tool_instance.execute(**params)
                        param_str = ", ".join([f"{k}={v}" for k, v in params.items() if k != "chat_id"])
                        return f"查询{tool_name_str}({param_str})的结果：{observation}"
                    except Exception as e:
                        error_msg = f"工具执行失败: {str(e)}"
                        logger.error(
                            f"{react_log_prefix}第 {iter_num + 1} 次迭代 工具 {tool_name_str} {error_msg}"
                        )
                        return f"查询{tool_name_str}失败: {error_msg}"

                tool_tasks.append(execute_single_tool(tool, tool_params, tool_name, iteration))
                step["actions"].append({"action_type": tool_name, "action_params": tool_args})
            else:
                error_msg = f"未知的工具类型: {tool_name}"
                logger.warning(
                    f"{react_log_prefix}第 {iteration + 1} 次迭代 工具 {i + 1}/{len(tool_calls)} {error_msg}"
                )
                tool_tasks.append(asyncio.create_task(asyncio.sleep(0, result=f"查询{tool_name}失败: {error_msg}")))

        # 并行执行所有工具
        if tool_tasks:
            observations = await asyncio.gather(*tool_tasks, return_exceptions=True)

            # 处理执行结果
            for i, (tool_call_item, observation) in enumerate(zip(tool_calls, observations, strict=False)):
                if isinstance(observation, Exception):
                    observation = f"工具执行异常: {str(observation)}"
                    logger.error(
                        f"{react_log_prefix}第 {iteration + 1} 次迭代 工具 {i + 1} 执行异常: {observation}"
                    )

                observation_text = observation if isinstance(observation, str) else str(observation)
                stripped_observation = observation_text.strip()
                step["observations"].append(observation_text)
                collected_info += f"\n{observation_text}\n"
                if stripped_observation:
                    # 不再自动检测工具输出中的jargon，改为通过 query_words 工具主动查询
                    tool_builder = MessageBuilder()
                    tool_builder.set_role(RoleType.Tool)
                    tool_builder.add_text_content(observation_text)
                    tool_builder.add_tool_call(tool_call_item.call_id)
                    conversation_messages.append(tool_builder.build())

        thinking_steps.append(step)

        # 检查是否需要额外迭代：如果最后一次使用的工具是 search_chat_history 且达到最大迭代次数，额外增加一回合
        if iteration + 1 >= max_iterations and last_tool_name == "search_chat_history" and not is_timeout:
            max_iterations_with_extra = max_iterations + 1
            logger.info(
                f"{react_log_prefix}达到最大迭代次数（已迭代{iteration + 1}次），最后一次使用工具为 search_chat_history，额外增加一回合尝试"
            )

        iteration += 1

    # 正常迭代结束后，如果达到最大迭代次数或超时，执行最终评估
    # 最终评估单独处理，不算在迭代中
    should_do_final_evaluation = False
    if is_timeout:
        should_do_final_evaluation = True
        logger.warning(f"{react_log_prefix}超时，已迭代{iteration}次，进入最终评估")
    elif iteration >= max_iterations:
        should_do_final_evaluation = True
        logger.info(f"{react_log_prefix}达到最大迭代次数（已迭代{iteration}次），进入最终评估")

    if should_do_final_evaluation:
        # 获取必要变量用于最终评估
        tool_registry = get_tool_registry()
        bot_name = global_config.bot.nickname
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        current_iteration = iteration + 1
        remaining_iterations = 0

        # 提取函数调用中参数的值，支持单引号和双引号
        def extract_quoted_content(text, func_name, param_name):
            """从文本中提取函数调用中参数的值，支持单引号和双引号

            Args:
                text: 要搜索的文本
                func_name: 函数名，如 'found_answer'
                param_name: 参数名，如 'answer'

            Returns:
                提取的参数值，如果未找到则返回None
            """
            if not text:
                return None

            # 查找函数调用位置（不区分大小写）
            func_pattern = func_name.lower()
            text_lower = text.lower()
            func_pos = text_lower.find(func_pattern)
            if func_pos == -1:
                return None

            # 查找参数名和等号
            param_pattern = f"{param_name}="
            param_pos = text_lower.find(param_pattern, func_pos)
            if param_pos == -1:
                return None

            # 跳过参数名、等号和空白
            start_pos = param_pos + len(param_pattern)
            while start_pos < len(text) and text[start_pos] in " \t\n":
                start_pos += 1

            if start_pos >= len(text):
                return None

            # 确定引号类型
            quote_char = text[start_pos]
            if quote_char not in ['"', "'"]:
                return None

            # 查找匹配的结束引号（考虑转义）
            end_pos = start_pos + 1
            while end_pos < len(text):
                if text[end_pos] == quote_char:
                    # 检查是否是转义的引号
                    if end_pos > start_pos + 1 and text[end_pos - 1] == "\\":
                        end_pos += 1
                        continue
                    # 找到匹配的引号
                    content = text[start_pos + 1 : end_pos]
                    # 处理转义字符
                    content = content.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
                    return content
                end_pos += 1

            return None

        # 执行最终评估
        evaluation_prompt = await global_prompt_manager.format_prompt(
            "memory_retrieval_react_final_prompt",
            bot_name=bot_name,
            time_now=time_now,
            question=question,
            collected_info=collected_info if collected_info else "暂无信息",
            current_iteration=current_iteration,
            remaining_iterations=remaining_iterations,
            max_iterations=max_iterations,
        )

        (
            eval_success,
            eval_response,
            eval_reasoning_content,
            eval_model_name,
            eval_tool_calls,
        ) = await llm_api.generate_with_model_with_tools(
            evaluation_prompt,
            model_config=model_config.model_task_config.tool_use,
            tool_options=[],  # 最终评估阶段不提供工具
            request_type="memory.react.final",
        )

        if not eval_success:
            logger.error(f"ReAct Agent 最终评估阶段 LLM调用失败: {eval_response}")
            _log_conversation_messages(
                conversation_messages,
                head_prompt=first_head_prompt,
                final_status="未找到答案：最终评估阶段LLM调用失败",
            )
            return False, "最终评估阶段LLM调用失败", thinking_steps, is_timeout

        if global_config.debug.show_memory_prompt:
            logger.info(f"{react_log_prefix}最终评估Prompt: {evaluation_prompt}")
            logger.info(f"{react_log_prefix}最终评估响应: {eval_response}")

        # 从最终评估响应中提取found_answer或not_enough_info
        found_answer_content = None
        not_enough_info_reason = None

        if eval_response:
            found_answer_content = extract_quoted_content(eval_response, "found_answer", "answer")
            if not found_answer_content:
                not_enough_info_reason = extract_quoted_content(eval_response, "not_enough_info", "reason")

        # 如果找到答案，返回（找到答案时，无论是否超时，都视为成功完成）
        if found_answer_content:
            eval_step = {
                "iteration": current_iteration,
                "thought": f"[最终评估] {eval_response}",
                "actions": [{"action_type": "found_answer", "action_params": {"answer": found_answer_content}}],
                "observations": ["最终评估阶段检测到found_answer"],
            }
            thinking_steps.append(eval_step)
            logger.info(f"ReAct Agent 最终评估阶段找到关于问题{question}的答案: {found_answer_content}")

            _log_conversation_messages(
                conversation_messages,
                head_prompt=first_head_prompt,
                final_status=f"找到答案：{found_answer_content}",
            )

            return True, found_answer_content, thinking_steps, False

        # 如果评估为not_enough_info，返回空字符串（不返回任何信息）
        if not_enough_info_reason:
            eval_step = {
                "iteration": current_iteration,
                "thought": f"[最终评估] {eval_response}",
                "actions": [{"action_type": "not_enough_info", "action_params": {"reason": not_enough_info_reason}}],
                "observations": ["最终评估阶段检测到not_enough_info"],
            }
            thinking_steps.append(eval_step)
            logger.info(f"ReAct Agent 最终评估阶段判断信息不足: {not_enough_info_reason}")

            _log_conversation_messages(
                conversation_messages,
                head_prompt=first_head_prompt,
                final_status=f"未找到答案：{not_enough_info_reason}",
            )

            return False, "", thinking_steps, is_timeout

        # 如果没有明确判断，视为not_enough_info，返回空字符串（不返回任何信息）
        eval_step = {
            "iteration": current_iteration,
            "thought": f"[最终评估] {eval_response}",
            "actions": [
                {"action_type": "not_enough_info", "action_params": {"reason": "已到达最大迭代次数，无法找到答案"}}
            ],
            "observations": ["已到达最大迭代次数，无法找到答案"],
        }
        thinking_steps.append(eval_step)
        logger.info("ReAct Agent 已到达最大迭代次数，无法找到答案")

        _log_conversation_messages(
            conversation_messages,
            head_prompt=first_head_prompt,
            final_status="未找到答案：已到达最大迭代次数，无法找到答案",
        )

        return False, "", thinking_steps, is_timeout

    # 如果正常迭代过程中提前找到答案返回，不会到达这里
    # 如果正常迭代结束但没有触发最终评估（理论上不应该发生），直接返回
    logger.warning("ReAct Agent正常迭代结束，但未触发最终评估")
    _log_conversation_messages(
        conversation_messages,
        head_prompt=first_head_prompt,
        final_status="未找到答案：正常迭代结束",
    )

    return False, "", thinking_steps, is_timeout


def _get_recent_query_history(chat_id: str, time_window_seconds: float = 600.0) -> str:
    """获取最近一段时间内的查询历史（用于避免重复查询）

    Args:
        chat_id: 聊天ID
        time_window_seconds: 时间窗口（秒），默认10分钟

    Returns:
        str: 格式化的查询历史字符串
    """
    try:
        current_time = time.time()
        start_time = current_time - time_window_seconds

        # 查询最近时间窗口内的记录，按更新时间倒序
        records = (
            ThinkingBack.select()
            .where((ThinkingBack.chat_id == chat_id) & (ThinkingBack.update_time >= start_time))
            .order_by(ThinkingBack.update_time.desc())
            .limit(5)  # 最多返回5条最近的记录
        )

        if not records.exists():
            return ""

        history_lines = []
        history_lines.append("最近已查询的问题和结果：")

        for record in records:
            status = "✓ 已找到答案" if record.found_answer else "✗ 未找到答案"
            answer_preview = ""
            # 只有找到答案时才显示答案内容
            if record.found_answer and record.answer:
                # 截取答案前100字符
                answer_preview = record.answer[:100]
                if len(record.answer) > 100:
                    answer_preview += "..."

            history_lines.append(f"- 问题：{record.question}")
            history_lines.append(f"  状态：{status}")
            if answer_preview:
                history_lines.append(f"  答案：{answer_preview}")
            history_lines.append("")  # 空行分隔

        return "\n".join(history_lines)

    except Exception as e:
        logger.error(f"获取查询历史失败: {e}")
        return ""


def _get_recent_found_answers(chat_id: str, time_window_seconds: float = 600.0) -> List[str]:
    """获取最近一段时间内已找到答案的查询记录（用于返回给 replyer）

    Args:
        chat_id: 聊天ID
        time_window_seconds: 时间窗口（秒），默认10分钟

    Returns:
        List[str]: 格式化的答案列表，每个元素格式为 "问题：xxx\n答案：xxx"
    """
    try:
        current_time = time.time()
        start_time = current_time - time_window_seconds

        # 查询最近时间窗口内已找到答案的记录，按更新时间倒序
        records = (
            ThinkingBack.select()
            .where(
                (ThinkingBack.chat_id == chat_id)
                & (ThinkingBack.update_time >= start_time)
                & (ThinkingBack.found_answer == 1)
                & (ThinkingBack.answer.is_null(False))
                & (ThinkingBack.answer != "")
            )
            .order_by(ThinkingBack.update_time.desc())
            .limit(3)  # 最多返回5条最近的记录
        )

        if not records.exists():
            return []

        found_answers = []
        for record in records:
            if record.answer:
                found_answers.append(f"问题：{record.question}\n答案：{record.answer}")

        return found_answers

    except Exception as e:
        logger.error(f"获取最近已找到答案的记录失败: {e}")
        return []


def _store_thinking_back(
    chat_id: str, question: str, context: str, found_answer: bool, answer: str, thinking_steps: List[Dict[str, Any]]
) -> None:
    """存储或更新思考过程到数据库（如果已存在则更新，否则创建）

    Args:
        chat_id: 聊天ID
        question: 问题
        context: 上下文信息
        found_answer: 是否找到答案
        answer: 答案内容
        thinking_steps: 思考步骤列表
    """
    try:
        now = time.time()

        # 先查询是否已存在相同chat_id和问题的记录
        existing = (
            ThinkingBack.select()
            .where((ThinkingBack.chat_id == chat_id) & (ThinkingBack.question == question))
            .order_by(ThinkingBack.update_time.desc())
            .limit(1)
        )

        if existing.exists():
            # 更新现有记录
            record = existing.get()
            record.context = context
            record.found_answer = found_answer
            record.answer = answer
            record.thinking_steps = json.dumps(thinking_steps, ensure_ascii=False)
            record.update_time = now
            record.save()
            logger.info(f"已更新思考过程到数据库，问题: {question[:50]}...")
        else:
            # 创建新记录
            ThinkingBack.create(
                chat_id=chat_id,
                question=question,
                context=context,
                found_answer=found_answer,
                answer=answer,
                thinking_steps=json.dumps(thinking_steps, ensure_ascii=False),
                create_time=now,
                update_time=now,
            )
            # logger.info(f"已创建思考过程到数据库，问题: {question[:50]}...")
    except Exception as e:
        logger.error(f"存储思考过程失败: {e}")


async def _process_single_question(
    question: str,
    chat_id: str,
    context: str,
    initial_info: str = "",
    max_iterations: Optional[int] = None,
) -> Optional[str]:
    """处理单个问题的查询

    Args:
        question: 要查询的问题
        chat_id: 聊天ID
        context: 上下文信息
        initial_info: 初始信息，将传递给ReAct Agent
        max_iterations: 最大迭代次数

    Returns:
        Optional[str]: 如果找到答案，返回格式化的结果字符串，否则返回None
    """
    # 如果question为空或None，直接返回None，不进行查询
    if not question or not question.strip():
        logger.debug("问题为空，跳过查询")
        return None
    
    # logger.info(f"开始处理问题: {question}")

    _cleanup_stale_not_found_thinking_back()

    question_initial_info = initial_info or ""

    # 直接使用ReAct Agent查询（不再从thinking_back获取缓存）
    # logger.info(f"使用ReAct Agent查询，问题: {question[:50]}...")

    # 如果未指定max_iterations，使用配置的默认值
    if max_iterations is None:
        max_iterations = global_config.memory.max_agent_iterations

    found_answer, answer, thinking_steps, is_timeout = await _react_agent_solve_question(
        question=question,
        chat_id=chat_id,
        max_iterations=max_iterations,
        timeout=global_config.memory.agent_timeout_seconds,
        initial_info=question_initial_info,
    )

    # 存储查询历史到数据库（超时时不存储）
    if not is_timeout:
        _store_thinking_back(
            chat_id=chat_id,
            question=question,
            context=context,
            found_answer=found_answer,
            answer=answer,
            thinking_steps=thinking_steps,
        )
    else:
        logger.info(f"ReAct Agent超时，不存储到数据库，问题: {question[:50]}...")

    if found_answer and answer:
        return f"问题：{question}\n答案：{answer}"

    return None


async def build_memory_retrieval_prompt(
    message: str,
    sender: str,
    target: str,
    chat_stream,
    think_level: int = 1,
    unknown_words: Optional[List[str]] = None,
    question: Optional[str] = None,
) -> str:
    """构建记忆检索提示
    使用两段式查询：第一步生成问题，第二步使用ReAct Agent查询答案

    Args:
        message: 聊天历史记录
        sender: 发送者名称
        target: 目标消息内容
        chat_stream: 聊天流对象
        think_level: 思考深度等级
        unknown_words: Planner 提供的未知词语列表，优先使用此列表而不是从聊天记录匹配
        question: Planner 提供的问题，当 planner_question 配置开启时，直接使用此问题进行检索

    Returns:
        str: 记忆检索结果字符串
    """
    start_time = time.time()

    # 构造日志前缀：[聊天流名称]，用于在日志中标识聊天流（优先群名称/用户昵称）
    try:
        group_info = chat_stream.group_info
        user_info = chat_stream.user_info
        # 群聊优先使用群名称
        if group_info is not None and getattr(group_info, "group_name", None):
            stream_name = group_info.group_name.strip() or str(group_info.group_id)
        # 私聊使用用户昵称
        elif user_info is not None and getattr(user_info, "user_nickname", None):
            stream_name = user_info.user_nickname.strip() or str(user_info.user_id)
        # 兜底使用 stream_id
        else:
            stream_name = chat_stream.stream_id
    except Exception:
        stream_name = chat_stream.stream_id
    log_prefix = f"[{stream_name}] " if stream_name else ""

    logger.info(f"{log_prefix}检测是否需要回忆，元消息：{message[:30]}...，消息长度: {len(message)}")
    try:
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        bot_name = global_config.bot.nickname
        chat_id = chat_stream.stream_id

        # 获取最近查询历史（最近10分钟内的查询，用于避免重复查询）
        recent_query_history = _get_recent_query_history(chat_id, time_window_seconds=600.0)
        if not recent_query_history:
            recent_query_history = "最近没有查询记录。"

        # 第一步：生成问题或使用 Planner 提供的问题
        single_question: Optional[str] = None
        
        # 如果 planner_question 配置开启，只使用 Planner 提供的问题，不使用旧模式
        if global_config.memory.planner_question:
            if question and isinstance(question, str) and question.strip():
                # 清理和验证 question
                single_question = question.strip()
                logger.info(f"{log_prefix}使用 Planner 提供的 question: {single_question}")
            else:
                # planner_question 开启但没有提供 question，跳过记忆检索
                logger.debug(f"{log_prefix}planner_question 已开启但未提供 question，跳过记忆检索")
                end_time = time.time()
                logger.info(f"{log_prefix}无当次查询，不返回任何结果，耗时: {(end_time - start_time):.3f}秒")
                return ""
        else:
            # planner_question 关闭，使用旧模式：LLM 生成问题
            question_prompt = await global_prompt_manager.format_prompt(
                "memory_retrieval_question_prompt",
                bot_name=bot_name,
                time_now=time_now,
                chat_history=message,
                recent_query_history=recent_query_history,
                sender=sender,
                target_message=target,
            )

            success, response, reasoning_content, model_name = await llm_api.generate_with_model(
                question_prompt,
                model_config=model_config.model_task_config.tool_use,
                request_type="memory.question",
            )

            if global_config.debug.show_memory_prompt:
                logger.info(f"{log_prefix}记忆检索问题生成提示词: {question_prompt}")
            # logger.info(f"记忆检索问题生成响应: {response}")

            if not success:
                logger.error(f"{log_prefix}LLM生成问题失败: {response}")
                return ""

            # 解析概念列表和问题列表，只取第一个问题
            _, questions = parse_questions_json(response)
            if questions and len(questions) > 0:
                single_question = questions[0].strip()
                logger.info(f"{log_prefix}解析到问题: {single_question}")

        # 初始阶段：使用 Planner 提供的 unknown_words 进行检索（如果提供）
        initial_info = ""
        if unknown_words and len(unknown_words) > 0:
            # 清理和去重 unknown_words
            cleaned_concepts = []
            for word in unknown_words:
                if isinstance(word, str):
                    cleaned = word.strip()
                    if cleaned:
                        cleaned_concepts.append(cleaned)
            if cleaned_concepts:
                # 对匹配到的概念进行jargon检索，作为初始信息
                concept_info = await retrieve_concepts_with_jargon(cleaned_concepts, chat_id)
                if concept_info:
                    initial_info += concept_info
                    logger.info(
                        f"{log_prefix}使用 Planner 提供的 unknown_words，共 {len(cleaned_concepts)} 个概念，检索结果: {concept_info[:100]}..."
                    )
                else:
                    logger.debug(f"{log_prefix}unknown_words 检索未找到任何结果")

        if not single_question:
            logger.debug(f"{log_prefix}模型认为不需要检索记忆或解析失败，不返回任何查询结果")
            end_time = time.time()
            logger.info(f"{log_prefix}无当次查询，不返回任何结果，耗时: {(end_time - start_time):.3f}秒")
            return ""

        # 第二步：处理问题（使用配置的最大迭代次数和超时时间）
        base_max_iterations = global_config.memory.max_agent_iterations
        # 根据think_level调整迭代次数：think_level=1时不变，think_level=0时减半
        if think_level == 0:
            max_iterations = max(1, base_max_iterations // 2)  # 至少为1
        else:
            max_iterations = base_max_iterations
        timeout_seconds = global_config.memory.agent_timeout_seconds
        logger.debug(
            f"{log_prefix}问题: {single_question}，think_level={think_level}，设置最大迭代次数: {max_iterations}（基础值: {base_max_iterations}），超时时间: {timeout_seconds}秒"
        )

        # 处理单个问题
        try:
            result = await _process_single_question(
                question=single_question,
                chat_id=chat_id,
                context=message,
                initial_info=initial_info,
                max_iterations=max_iterations,
            )
        except Exception as e:
            logger.error(f"{log_prefix}处理问题 '{single_question}' 时发生异常: {e}")
            result = None

        # 获取最近10分钟内已找到答案的缓存记录
        cached_answers = _get_recent_found_answers(chat_id, time_window_seconds=600.0)

        # 合并当前查询结果和缓存答案（去重：如果当前查询的问题在缓存中已存在，优先使用当前结果）
        all_results = []

        # 先添加当前查询的结果
        current_question = None
        if result:
            all_results.append(result)
            # 提取问题（格式为 "问题：xxx\n答案：xxx"）
            if result.startswith("问题："):
                question_end = result.find("\n答案：")
                if question_end != -1:
                    current_question = result[4:question_end]

        # 添加缓存答案（排除当前查询的问题）
        for cached_answer in cached_answers:
            if cached_answer.startswith("问题："):
                question_end = cached_answer.find("\n答案：")
                if question_end != -1:
                    cached_question = cached_answer[4:question_end]
                    if cached_question != current_question:
                        all_results.append(cached_answer)

        end_time = time.time()

        if all_results:
            retrieved_memory = "\n\n".join(all_results)
            current_count = 1 if result else 0
            cached_count = len(all_results) - current_count
            logger.info(
                f"{log_prefix}记忆检索成功，耗时: {(end_time - start_time):.3f}秒，"
                f"当前查询 {current_count} 条记忆，缓存 {cached_count} 条记忆，共 {len(all_results)} 条记忆"
            )
            return f"你回忆起了以下信息：\n{retrieved_memory}\n如果与回复内容相关，可以参考这些回忆的信息。\n"
        else:
            logger.debug(f"{log_prefix}问题未找到答案，且无缓存答案")
            return ""

    except Exception as e:
        logger.error(f"{log_prefix}记忆检索时发生异常: {str(e)}")
        return ""

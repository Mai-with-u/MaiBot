import time
import json
import re
import random
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.plugin_system.apis import llm_api
from src.common.database.database_model import ThinkingBack
from json_repair import repair_json
from src.memory_system.retrieval_tools import get_tool_registry, init_all_tools
from src.memory_system.retrieval_tools.query_lpmm_knowledge import query_lpmm_knowledge
from src.llm_models.payload_content.message import MessageBuilder, RoleType, Message

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
5. 对话中是否包含黑话、俚语、缩写等可能需要查询的概念

重要提示：
- **每次只能提出一个问题**，选择最需要查询的关键问题
- 如果"最近已查询的问题和结果"中已经包含了类似的问题并得到了答案，请避免重复生成相同或相似的问题，不需要重复查询
- 如果之前已经查询过某个问题但未找到答案，可以尝试用不同的方式提问或更具体的问题

如果你认为需要从记忆中检索信息来回答，请：
1. 识别对话中可能需要查询的概念（黑话/俚语/缩写/专有名词等关键词），放入"concepts"字段
2. 根据上下文提出**一个**最关键的问题来帮助你回复目标消息，放入"questions"字段

问题格式示例：
- "xxx在前几天干了什么"
- "xxx是什么"
- "xxxx和xxx的关系是什么"
- "xxx在某个时间点发生了什么"

输出格式示例（需要检索时）：
```json
{{
  "concepts": ["AAA", "BBB", "CCC"], #需要检索的概念列表（字符串数组），如果不需要检索概念则输出空数组[]
  "questions": ["张三在前几天干了什么"] #问题数组（字符串数组），如果不需要检索记忆则输出空数组[]，如果需要检索则只输出包含一个问题的数组
}}
```

输出格式示例（不需要检索时）：
```json
{{
  "concepts": [],
  "questions": []
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

**重要限制：**
- 最大查询轮数：{max_iterations}轮（当前第{current_iteration}轮，剩余{remaining_iterations}轮）
- 必须尽快得出答案，避免不必要的查询
- 思考要简短，直接切入要点
- 必须严格使用检索到的信息回答问题，不要编造信息

当前问题：{question}
已收集的信息：
{collected_info}

**执行步骤：**
**第一步：思考（Think）**
在思考中分析：
- 当前信息是否足够回答问题？
- **如果信息足够且能找到明确答案**，在思考中直接给出答案，格式为：found_answer(answer="你的答案内容")
- **如果需要尝试搜集更多信息，进一步调用工具，进入第二步行动环节
- **如果已有信息不足或无法找到答案**，在思考中给出：not_enough_info(reason="信息不足或无法找到答案的原因")

**第二步：行动（Action）**
- 如果涉及过往事件，可以使用聊天记录查询工具查询过往事件
- 如果涉及概念，可以用jargon查询，或根据关键词检索聊天记录
- 如果涉及人物，可以使用人物信息查询工具查询人物信息
- 如果不确定查询类别，也可以使用lpmm知识库查询
- 如果信息不足且需要继续查询，说明最需要查询什么，并输出为纯文本说明，然后调用相应工具查询（可并行调用多个工具）

**重要规则：**
- **只有在检索到明确、有关的信息并得出答案时，才使用found_answer**
- **如果信息不足、无法确定、找不到相关信息，必须使用not_enough_info，不要使用found_answer**
- 答案必须在思考中给出，格式为 found_answer(answer="...") 或 not_enough_info(reason="...")
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

**执行步骤：**
分析：
- 当前信息是否足够回答问题？
- **如果信息足够且能找到明确答案**，在思考中直接给出答案，格式为：found_answer(answer="你的答案内容")
- **如果信息不足或无法找到答案**，在思考中给出：not_enough_info(reason="信息不足或无法找到答案的原因")

**重要规则：**
- 你已经经过几轮查询，尝试了信息搜集，现在你需要总结信息，选择回答问题或判断问题无法回答
- 必须严格使用检索到的信息回答问题，不要编造信息
- 答案必须精简，不要过多解释
- **只有在检索到明确、具体的答案时，才使用found_answer**
- **如果信息不足、无法确定、找不到相关信息，必须使用not_enough_info，不要使用found_answer**
- 答案必须给出，格式为 found_answer(answer="...") 或 not_enough_info(reason="...")。
""",
        name="memory_retrieval_react_final_prompt",
    )


def _parse_react_response(response: str) -> Optional[Dict[str, Any]]:
    """解析ReAct Agent的响应

    Args:
        response: LLM返回的响应

    Returns:
        Dict[str, Any]: 解析后的动作信息，如果解析失败返回None
        格式: {"thought": str, "actions": List[Dict[str, Any]]}
        每个action格式: {"action_type": str, "action_params": dict}
    """
    try:
        # 尝试提取JSON（可能包含在```json代码块中）
        json_pattern = r"```json\s*(.*?)\s*```"
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            json_str = matches[0]
        else:
            # 尝试直接解析整个响应
            json_str = response.strip()

        # 修复可能的JSON错误
        repaired_json = repair_json(json_str)

        # 解析JSON
        action_info = json.loads(repaired_json)

        if not isinstance(action_info, dict):
            logger.warning(f"解析的JSON不是对象格式: {action_info}")
            return None

        # 确保actions字段存在且为列表
        if "actions" not in action_info:
            logger.warning(f"响应中缺少actions字段: {action_info}")
            return None

        if not isinstance(action_info["actions"], list):
            logger.warning(f"actions字段不是数组格式: {action_info['actions']}")
            return None

        # 确保actions不为空
        if len(action_info["actions"]) == 0:
            logger.warning("actions数组为空")
            return None

        return action_info

    except Exception as e:
        logger.error(f"解析ReAct响应失败: {e}, 响应内容: {response[:200]}...")
        return None


async def _retrieve_concepts_with_jargon(concepts: List[str], chat_id: str) -> str:
    """对概念列表进行jargon检索

    Args:
        concepts: 概念列表
        chat_id: 聊天ID

    Returns:
        str: 检索结果字符串
    """
    if not concepts:
        return ""

    from src.jargon.jargon_miner import search_jargon

    results = []
    for concept in concepts:
        concept = concept.strip()
        if not concept:
            continue

        # 先尝试精确匹配
        jargon_results = search_jargon(keyword=concept, chat_id=chat_id, limit=10, case_sensitive=False, fuzzy=False)

        is_fuzzy_match = False

        # 如果精确匹配未找到，尝试模糊搜索
        if not jargon_results:
            jargon_results = search_jargon(keyword=concept, chat_id=chat_id, limit=10, case_sensitive=False, fuzzy=True)
            is_fuzzy_match = True

        if jargon_results:
            # 找到结果
            if is_fuzzy_match:
                # 模糊匹配
                output_parts = [f"未精确匹配到'{concept}'"]
                for result in jargon_results:
                    found_content = result.get("content", "").strip()
                    meaning = result.get("meaning", "").strip()
                    if found_content and meaning:
                        output_parts.append(f"找到 '{found_content}' 的含义为：{meaning}")
                results.append("，".join(output_parts))
                logger.info(f"在jargon库中找到匹配（模糊搜索）: {concept}，找到{len(jargon_results)}条结果")
            else:
                # 精确匹配
                output_parts = []
                for result in jargon_results:
                    meaning = result.get("meaning", "").strip()
                    if meaning:
                        output_parts.append(f"'{concept}' 为黑话或者网络简写，含义为：{meaning}")
                results.append("；".join(output_parts) if len(output_parts) > 1 else output_parts[0])
                logger.info(f"在jargon库中找到匹配（精确匹配）: {concept}，找到{len(jargon_results)}条结果")
        else:
            # 未找到，不返回占位信息，只记录日志
            logger.info(f"在jargon库中未找到匹配: {concept}")

    if results:
        return "【概念检索结果】\n" + "\n".join(results) + "\n"
    return ""


async def _react_agent_solve_question(
    question: str, chat_id: str, max_iterations: int = 5, timeout: float = 30.0, initial_info: str = ""
) -> Tuple[bool, str, List[Dict[str, Any]], bool]:
    """使用ReAct架构的Agent来解决问题

    Args:
        question: 要回答的问题
        chat_id: 聊天ID
        max_iterations: 最大迭代次数
        timeout: 超时时间（秒）
        initial_info: 初始信息（如概念检索结果），将作为collected_info的初始值

    Returns:
        Tuple[bool, str, List[Dict[str, Any]], bool]: (是否找到答案, 答案内容, 思考步骤列表, 是否超时)
    """
    start_time = time.time()
    collected_info = initial_info if initial_info else ""
    thinking_steps = []
    is_timeout = False
    conversation_messages: List[Message] = []

    for iteration in range(max_iterations):
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
        is_final_iteration = current_iteration >= max_iterations

        if is_final_iteration:
            # 最后一次迭代，使用最终prompt
            tool_definitions = []
            logger.info(
                f"ReAct Agent 第 {iteration + 1} 次迭代，问题: {question}|可用工具数量: 0（最后一次迭代，不提供工具调用）"
            )

            prompt = await global_prompt_manager.format_prompt(
                "memory_retrieval_react_final_prompt",
                bot_name=bot_name,
                time_now=time_now,
                question=question,
                collected_info=collected_info if collected_info else "暂无信息",
                current_iteration=current_iteration,
                remaining_iterations=remaining_iterations,
                max_iterations=max_iterations,
            )

            if global_config.debug.show_memory_prompt:
                logger.info(f"ReAct Agent 第 {iteration + 1} 次Prompt: {prompt}")
            success, response, reasoning_content, model_name, tool_calls = await llm_api.generate_with_model_with_tools(
                prompt,
                model_config=model_config.model_task_config.tool_use,
                tool_options=tool_definitions,
                request_type="memory.react",
            )
        else:
            # 非最终迭代，使用head_prompt
            tool_definitions = tool_registry.get_tool_definitions()
            logger.info(
                f"ReAct Agent 第 {iteration + 1} 次迭代，问题: {question}|可用工具数量: {len(tool_definitions)}"
            )

            head_prompt = await global_prompt_manager.format_prompt(
                "memory_retrieval_react_prompt_head",
                bot_name=bot_name,
                time_now=time_now,
                question=question,
                collected_info=collected_info if collected_info else "",
                current_iteration=current_iteration,
                remaining_iterations=remaining_iterations,
                max_iterations=max_iterations,
            )

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

                if global_config.debug.show_memory_prompt:
                    # 优化日志展示 - 合并所有消息到一条日志
                    log_lines = []
                    for idx, msg in enumerate(messages, 1):
                        role_name = msg.role.value if hasattr(msg.role, "value") else str(msg.role)

                        # 处理内容 - 显示完整内容，不截断
                        if isinstance(msg.content, str):
                            full_content = msg.content
                            content_type = "文本"
                        elif isinstance(msg.content, list):
                            text_parts = [item for item in msg.content if isinstance(item, str)]
                            image_count = len([item for item in msg.content if isinstance(item, tuple)])
                            full_content = "".join(text_parts) if text_parts else ""
                            content_type = f"混合({len(text_parts)}段文本, {image_count}张图片)"
                        else:
                            full_content = str(msg.content)
                            content_type = "未知"

                        # 构建单条消息的日志信息
                        msg_info = f"\n[消息 {idx}] 角色: {role_name} 内容类型: {content_type}\n========================================"

                        if full_content:
                            msg_info += f"\n{full_content}"

                        if msg.tool_calls:
                            msg_info += f"\n  工具调用: {len(msg.tool_calls)}个"
                            for tool_call in msg.tool_calls:
                                msg_info += f"\n    - {tool_call}"

                        if msg.tool_call_id:
                            msg_info += f"\n  工具调用ID: {msg.tool_call_id}"

                        log_lines.append(msg_info)

                    # 合并所有消息为一条日志输出
                    logger.info(f"消息列表 (共{len(messages)}条):{''.join(log_lines)}")

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

        logger.info(
            f"ReAct Agent 第 {iteration + 1} 次迭代 模型: {model_name} ，调用工具数量: {len(tool_calls) if tool_calls else 0} ，调用工具响应: {response}"
        )

        if not success:
            logger.error(f"ReAct Agent LLM调用失败: {response}")
            break

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

        # 优先从思考内容中提取found_answer或not_enough_info
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

        # 从LLM的直接输出内容中提取found_answer或not_enough_info
        found_answer_content = None
        not_enough_info_reason = None

        # 只检查response（LLM的直接输出内容），不检查reasoning_content
        if response:
            found_answer_content = extract_quoted_content(response, "found_answer", "answer")
            if not found_answer_content:
                not_enough_info_reason = extract_quoted_content(response, "not_enough_info", "reason")

        # 如果从输出内容中找到了答案，直接返回
        if found_answer_content:
            step["actions"].append({"action_type": "found_answer", "action_params": {"answer": found_answer_content}})
            step["observations"] = ["从LLM输出内容中检测到found_answer"]
            thinking_steps.append(step)
            logger.info(
                f"ReAct Agent 第 {iteration + 1} 次迭代 从LLM输出内容中检测到found_answer: {found_answer_content[:100]}..."
            )
            return True, found_answer_content, thinking_steps, False

        if not_enough_info_reason:
            step["actions"].append(
                {"action_type": "not_enough_info", "action_params": {"reason": not_enough_info_reason}}
            )
            step["observations"] = ["从LLM输出内容中检测到not_enough_info"]
            thinking_steps.append(step)
            logger.info(
                f"ReAct Agent 第 {iteration + 1} 次迭代 从LLM输出内容中检测到not_enough_info: {not_enough_info_reason[:100]}..."
            )
            return False, not_enough_info_reason, thinking_steps, False

        if is_final_iteration:
            step["actions"].append(
                {"action_type": "not_enough_info", "action_params": {"reason": "已到达最后一次迭代，无法找到答案"}}
            )
            step["observations"] = ["已到达最后一次迭代，无法找到答案"]
            thinking_steps.append(step)
            logger.info(f"ReAct Agent 第 {iteration + 1} 次迭代 已到达最后一次迭代，无法找到答案")
            return False, "已到达最后一次迭代，无法找到答案", thinking_steps, False

        if assistant_message:
            conversation_messages.append(assistant_message)

        # 记录思考过程到collected_info中
        if reasoning_content or response:
            thought_summary = reasoning_content or (response[:200] if response else "")
            if thought_summary:
                collected_info += f"\n[思考] {thought_summary}\n"

        # 处理工具调用
        if not tool_calls:
            # 没有工具调用，说明LLM在思考中已经给出了答案（已在前面检查），或者需要继续查询
            # 如果思考中没有答案，说明需要继续查询或等待下一轮
            if response and response.strip():
                # 如果响应不为空，记录思考过程，继续下一轮迭代
                step["observations"] = [f"思考完成，但未调用工具。响应: {response}"]
                logger.info(f"ReAct Agent 第 {iteration + 1} 次迭代 思考完成但未调用工具: {response[:100]}...")
                # 继续下一轮迭代，让LLM有机会在思考中给出found_answer或继续查询
                collected_info += f"思考: {response}"
                thinking_steps.append(step)
                continue
            else:
                logger.warning(f"ReAct Agent 第 {iteration + 1} 次迭代 无工具调用且无响应")
                step["observations"] = ["无响应且无工具调用"]
                thinking_steps.append(step)
                break

        # 处理工具调用
        tool_tasks = []

        for i, tool_call in enumerate(tool_calls):
            tool_name = tool_call.func_name
            tool_args = tool_call.args or {}

            logger.info(
                f"ReAct Agent 第 {iteration + 1} 次迭代 工具调用 {i + 1}/{len(tool_calls)}: {tool_name}({tool_args})"
            )

            # 普通工具调用
            tool = tool_registry.get_tool(tool_name)
            if tool:
                # 准备工具参数（需要添加chat_id如果工具需要）
                tool_params = tool_args.copy()

                # 如果工具函数签名需要chat_id，添加它
                import inspect

                sig = inspect.signature(tool.execute_func)
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
                        logger.error(f"ReAct Agent 第 {iter_num + 1} 次迭代 工具 {tool_name_str} {error_msg}")
                        return f"查询{tool_name_str}失败: {error_msg}"

                tool_tasks.append(execute_single_tool(tool, tool_params, tool_name, iteration))
                step["actions"].append({"action_type": tool_name, "action_params": tool_args})
            else:
                error_msg = f"未知的工具类型: {tool_name}"
                logger.warning(f"ReAct Agent 第 {iteration + 1} 次迭代 工具 {i + 1}/{len(tool_calls)} {error_msg}")
                tool_tasks.append(asyncio.create_task(asyncio.sleep(0, result=f"查询{tool_name}失败: {error_msg}")))

        # 并行执行所有工具
        if tool_tasks:
            observations = await asyncio.gather(*tool_tasks, return_exceptions=True)

            # 处理执行结果
            for i, (tool_call_item, observation) in enumerate(zip(tool_calls, observations, strict=False)):
                if isinstance(observation, Exception):
                    observation = f"工具执行异常: {str(observation)}"
                    logger.error(f"ReAct Agent 第 {iteration + 1} 次迭代 工具 {i + 1} 执行异常: {observation}")

                observation_text = observation if isinstance(observation, str) else str(observation)
                step["observations"].append(observation_text)
                collected_info += f"\n{observation_text}\n"
                if observation_text.strip():
                    tool_builder = MessageBuilder()
                    tool_builder.set_role(RoleType.Tool)
                    tool_builder.add_text_content(observation_text)
                    tool_builder.add_tool_call(tool_call_item.call_id)
                    conversation_messages.append(tool_builder.build())
                # logger.info(f"ReAct Agent 第 {iteration + 1} 次迭代 工具 {i+1} 执行结果: {observation_text}")

        thinking_steps.append(step)

    # 达到最大迭代次数或超时，但Agent没有明确返回found_answer
    # 迭代超时应该直接视为not_enough_info，而不是使用已有信息
    # 只有Agent明确返回found_answer时，才认为找到了答案
    if collected_info:
        logger.warning(
            f"ReAct Agent达到最大迭代次数或超时，但未明确返回found_answer。已收集信息: {collected_info[:100]}..."
        )
    if is_timeout:
        logger.warning("ReAct Agent超时，直接视为not_enough_info")
    else:
        logger.warning("ReAct Agent达到最大迭代次数，直接视为not_enough_info")
    return False, "未找到相关信息", thinking_steps, is_timeout


def _get_recent_query_history(chat_id: str, time_window_seconds: float = 300.0) -> str:
    """获取最近一段时间内的查询历史

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


def _get_cached_memories(chat_id: str, time_window_seconds: float = 300.0) -> List[str]:
    """获取最近一段时间内缓存的记忆（只返回找到答案的记录）

    Args:
        chat_id: 聊天ID
        time_window_seconds: 时间窗口（秒），默认300秒（5分钟）

    Returns:
        List[str]: 格式化的记忆列表，每个元素格式为 "问题：xxx\n答案：xxx"
    """
    try:
        current_time = time.time()
        start_time = current_time - time_window_seconds

        # 查询最近时间窗口内找到答案的记录，按更新时间倒序
        records = (
            ThinkingBack.select()
            .where(
                (ThinkingBack.chat_id == chat_id)
                & (ThinkingBack.update_time >= start_time)
                & (ThinkingBack.found_answer == 1)
            )
            .order_by(ThinkingBack.update_time.desc())
            .limit(5)  # 最多返回5条最近的记录
        )

        if not records.exists():
            return []

        cached_memories = []
        for record in records:
            if record.answer:
                cached_memories.append(f"问题：{record.question}\n答案：{record.answer}")

        return cached_memories

    except Exception as e:
        logger.error(f"获取缓存记忆失败: {e}")
        return []


def _query_thinking_back(chat_id: str, question: str) -> Optional[Tuple[bool, str]]:
    """从thinking_back数据库中查询是否有现成的答案

    Args:
        chat_id: 聊天ID
        question: 问题

    Returns:
        Optional[Tuple[bool, str]]: 如果找到记录，返回(found_answer, answer)，否则返回None
            found_answer: 是否找到答案（True表示found_answer=1，False表示found_answer=0）
            answer: 答案内容
    """
    try:
        # 查询相同chat_id和问题的所有记录（包括found_answer为0和1的）
        # 按更新时间倒序，获取最新的记录
        records = (
            ThinkingBack.select()
            .where((ThinkingBack.chat_id == chat_id) & (ThinkingBack.question == question))
            .order_by(ThinkingBack.update_time.desc())
            .limit(1)
        )

        if records.exists():
            record = records.get()
            found_answer = bool(record.found_answer)
            answer = record.answer or ""
            logger.info(f"在thinking_back中找到记录，问题: {question[:50]}...，found_answer: {found_answer}")
            return found_answer, answer

        return None

    except Exception as e:
        logger.error(f"查询thinking_back失败: {e}")
        return None


async def _analyze_question_answer(question: str, answer: str, chat_id: str) -> None:
    """异步分析问题和答案的类别，并存储到相应系统

    Args:
        question: 问题
        answer: 答案
        chat_id: 聊天ID
    """
    try:
        # 使用LLM分析类别
        analysis_prompt = f"""请分析以下问题和答案的类别：

问题：{question}
答案：{answer}

类别说明：
1. 人物信息：有关某个用户的个体信息（如某人的喜好、习惯、经历等）
2. 黑话：对特定概念、缩写词、谐音词、自创词的解释（如"yyds"、"社死"等）
3. 其他：除此之外的其他内容

请输出JSON格式：
{{
    "category": "人物信息" | "黑话" | "其他",
    "jargon_keyword": "如果是黑话，提取关键词（如'yyds'），否则为空字符串",
    "person_name": "如果是人物信息，提取人物名称，否则为空字符串",
    "memory_content": "如果是人物信息，提取要存储的记忆内容（简短概括），否则为空字符串"
}}

只输出JSON，不要输出其他内容："""

        success, response, _, _ = await llm_api.generate_with_model(
            analysis_prompt,
            model_config=model_config.model_task_config.utils,
            request_type="memory.analyze_qa",
        )

        if not success:
            logger.error(f"分析问题和答案失败: {response}")
            return

        # 解析JSON响应
        try:
            json_pattern = r"```json\s*(.*?)\s*```"
            matches = re.findall(json_pattern, response, re.DOTALL)

            if matches:
                json_str = matches[0]
            else:
                json_str = response.strip()

            repaired_json = repair_json(json_str)
            analysis_result = json.loads(repaired_json)

            category = analysis_result.get("category", "").strip()

            if category == "黑话":
                # 处理黑话
                jargon_keyword = analysis_result.get("jargon_keyword", "").strip()
                if jargon_keyword:
                    from src.jargon.jargon_miner import store_jargon_from_answer

                    await store_jargon_from_answer(jargon_keyword, answer, chat_id)
                else:
                    logger.warning(f"分析为黑话但未提取到关键词，问题: {question[:50]}...")

            elif category == "人物信息":
                # 处理人物信息
                # person_name = analysis_result.get("person_name", "").strip()
                # memory_content = analysis_result.get("memory_content", "").strip()
                # if person_name and memory_content:
                #     from src.person_info.person_info import store_person_memory_from_answer
                #     await store_person_memory_from_answer(person_name, memory_content, chat_id)
                # else:
                #     logger.warning(f"分析为人物信息但未提取到人物名称或记忆内容，问题: {question[:50]}...")
                pass  # 功能暂时禁用

            else:
                logger.info(f"问题和答案类别为'其他'，不进行存储，问题: {question[:50]}...")

        except Exception as e:
            logger.error(f"解析分析结果失败: {e}, 响应: {response[:200]}...")

    except Exception as e:
        logger.error(f"分析问题和答案时发生异常: {e}")


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
            logger.info(f"已创建思考过程到数据库，问题: {question[:50]}...")
    except Exception as e:
        logger.error(f"存储思考过程失败: {e}")


async def _process_single_question(question: str, chat_id: str, context: str, initial_info: str = "") -> Optional[str]:
    """处理单个问题的查询（包含缓存检查逻辑）

    Args:
        question: 要查询的问题
        chat_id: 聊天ID
        context: 上下文信息
        initial_info: 初始信息（如概念检索结果），将传递给ReAct Agent

    Returns:
        Optional[str]: 如果找到答案，返回格式化的结果字符串，否则返回None
    """
    logger.info(f"开始处理问题: {question}")

    _cleanup_stale_not_found_thinking_back()

    question_initial_info = initial_info or ""

    # 预先进行一次LPMM知识库查询，作为后续ReAct Agent的辅助信息
    if global_config.lpmm_knowledge.enable:
        try:
            lpmm_result = await query_lpmm_knowledge(question, limit=2)
            if lpmm_result and lpmm_result.startswith("你从LPMM知识库中找到"):
                if question_initial_info:
                    question_initial_info += "\n"
                question_initial_info += f"【LPMM知识库预查询】\n{lpmm_result}"
                logger.info(f"LPMM预查询命中，问题: {question[:50]}...")
            else:
                logger.info(f"LPMM预查询未命中或未找到信息，问题: {question[:50]}...")
        except Exception as e:
            logger.error(f"LPMM预查询失败，问题: {question[:50]}... 错误: {e}")

    # 先检查thinking_back数据库中是否有现成答案
    cached_result = _query_thinking_back(chat_id, question)
    should_requery = False

    if cached_result:
        cached_found_answer, cached_answer = cached_result

        if cached_found_answer:  # found_answer == 1 (True)
            # found_answer == 1：20%概率重新查询
            if random.random() < 0.5:
                should_requery = True
                logger.info(f"found_answer=1，触发20%概率重新查询，问题: {question[:50]}...")

            if not should_requery and cached_answer:
                logger.info(f"从thinking_back缓存中获取答案，问题: {question[:50]}...")
                return f"问题：{question}\n答案：{cached_answer}"
            elif not cached_answer:
                should_requery = True
                logger.info(f"found_answer=1 但缓存答案为空，重新查询，问题: {question[:50]}...")
        else:
            # found_answer == 0：不使用缓存，直接重新查询
            should_requery = True
            logger.info(f"thinking_back存在但未找到答案，忽略缓存重新查询，问题: {question[:50]}...")

    # 如果没有缓存答案或需要重新查询，使用ReAct Agent查询
    if not cached_result or should_requery:
        if should_requery:
            logger.info(f"概率触发重新查询，使用ReAct Agent查询，问题: {question[:50]}...")
        else:
            logger.info(f"未找到缓存答案，使用ReAct Agent查询，问题: {question[:50]}...")

        found_answer, answer, thinking_steps, is_timeout = await _react_agent_solve_question(
            question=question,
            chat_id=chat_id,
            max_iterations=global_config.memory.max_agent_iterations,
            timeout=120.0,
            initial_info=question_initial_info,
        )

        # 存储到数据库（超时时不存储）
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
            # 创建异步任务分析问题和答案
            asyncio.create_task(_analyze_question_answer(question, answer, chat_id))
            return f"问题：{question}\n答案：{answer}"

    return None


async def build_memory_retrieval_prompt(
    message: str,
    sender: str,
    target: str,
    chat_stream,
    tool_executor,
) -> str:
    """构建记忆检索提示
    使用两段式查询：第一步生成问题，第二步使用ReAct Agent查询答案

    Args:
        message: 聊天历史记录
        sender: 发送者名称
        target: 目标消息内容
        chat_stream: 聊天流对象
        tool_executor: 工具执行器（保留参数以兼容接口）

    Returns:
        str: 记忆检索结果字符串
    """
    start_time = time.time()

    logger.info(f"检测是否需要回忆，元消息：{message[:30]}...，消息长度: {len(message)}")
    try:
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        bot_name = global_config.bot.nickname
        chat_id = chat_stream.stream_id

        # 获取最近查询历史（最近1小时内的查询）
        recent_query_history = _get_recent_query_history(chat_id, time_window_seconds=300.0)
        if not recent_query_history:
            recent_query_history = "最近没有查询记录。"

        # 第一步：生成问题
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
            logger.info(f"记忆检索问题生成提示词: {question_prompt}")
        logger.info(f"记忆检索问题生成响应: {response}")

        if not success:
            logger.error(f"LLM生成问题失败: {response}")
            return ""

        # 解析概念列表和问题列表
        concepts, questions = _parse_questions_json(response)
        logger.info(f"解析到 {len(concepts)} 个概念: {concepts}")
        logger.info(f"解析到 {len(questions)} 个问题: {questions}")

        # 对概念进行jargon检索，作为初始信息
        initial_info = ""
        if concepts:
            logger.info(f"开始对 {len(concepts)} 个概念进行jargon检索")
            concept_info = await _retrieve_concepts_with_jargon(concepts, chat_id)
            if concept_info:
                initial_info += concept_info
                logger.info(f"概念检索完成，结果: {concept_info[:200]}...")
            else:
                logger.info("概念检索未找到任何结果")

        # 获取缓存的记忆（与question时使用相同的时间窗口和数量限制）
        cached_memories = _get_cached_memories(chat_id, time_window_seconds=300.0)

        if not questions:
            logger.debug("模型认为不需要检索记忆或解析失败")
            # 即使没有当次查询，也返回缓存的记忆和概念检索结果
            all_results = []
            if initial_info:
                all_results.append(initial_info.strip())
            if cached_memories:
                all_results.extend(cached_memories)

            if all_results:
                retrieved_memory = "\n\n".join(all_results)
                end_time = time.time()
                logger.info(f"无当次查询，返回缓存记忆和概念检索结果，耗时: {(end_time - start_time):.3f}秒")
                return f"你回忆起了以下信息：\n{retrieved_memory}\n如果与回复内容相关，可以参考这些回忆的信息。\n"
            else:
                return ""

        logger.info(f"解析到 {len(questions)} 个问题: {questions}")

        # 第二步：并行处理所有问题（使用配置的最大迭代次数/120秒超时）
        max_iterations = global_config.memory.max_agent_iterations
        logger.info(f"问题数量: {len(questions)}，设置最大迭代次数: {max_iterations}，超时时间: 120秒")

        # 并行处理所有问题，将概念检索结果作为初始信息传递
        question_tasks = [
            _process_single_question(question=question, chat_id=chat_id, context=message, initial_info=initial_info)
            for question in questions
        ]

        # 并行执行所有查询任务
        results = await asyncio.gather(*question_tasks, return_exceptions=True)

        # 收集所有有效结果
        all_results = []
        current_questions = set()  # 用于去重，避免缓存和当次查询重复
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"处理问题 '{questions[i]}' 时发生异常: {result}")
            elif result is not None:
                all_results.append(result)
                # 提取问题用于去重
                if result.startswith("问题："):
                    question = result.split("\n")[0].replace("问题：", "").strip()
                    current_questions.add(question)

        # 将缓存的记忆添加到结果中（排除当次查询已包含的问题，避免重复）
        for cached_memory in cached_memories:
            if cached_memory.startswith("问题："):
                question = cached_memory.split("\n")[0].replace("问题：", "").strip()
                # 只有当次查询中没有相同问题时，才添加缓存记忆
                if question not in current_questions:
                    all_results.append(cached_memory)
                    logger.debug(f"添加缓存记忆: {question[:50]}...")

        end_time = time.time()

        if all_results:
            retrieved_memory = "\n\n".join(all_results)
            logger.info(
                f"记忆检索成功，耗时: {(end_time - start_time):.3f}秒，包含 {len(all_results)} 条记忆（含缓存）"
            )
            return f"你回忆起了以下信息：\n{retrieved_memory}\n如果与回复内容相关，可以参考这些回忆的信息。\n"
        else:
            logger.debug("所有问题均未找到答案，且无缓存记忆")
            return ""

    except Exception as e:
        logger.error(f"记忆检索时发生异常: {str(e)}")
        return ""


def _parse_questions_json(response: str) -> Tuple[List[str], List[str]]:
    """解析问题JSON，返回概念列表和问题列表

    Args:
        response: LLM返回的响应

    Returns:
        Tuple[List[str], List[str]]: (概念列表, 问题列表)
    """
    try:
        # 尝试提取JSON（可能包含在```json代码块中）
        json_pattern = r"```json\s*(.*?)\s*```"
        matches = re.findall(json_pattern, response, re.DOTALL)

        if matches:
            json_str = matches[0]
        else:
            # 尝试直接解析整个响应
            json_str = response.strip()

        # 修复可能的JSON错误
        repaired_json = repair_json(json_str)

        # 解析JSON
        parsed = json.loads(repaired_json)

        # 只支持新格式：包含concepts和questions的对象
        if not isinstance(parsed, dict):
            logger.warning(f"解析的JSON不是对象格式: {parsed}")
            return [], []

        concepts_raw = parsed.get("concepts", [])
        questions_raw = parsed.get("questions", [])

        # 确保是列表
        if not isinstance(concepts_raw, list):
            concepts_raw = []
        if not isinstance(questions_raw, list):
            questions_raw = []

        # 确保所有元素都是字符串
        concepts = [c for c in concepts_raw if isinstance(c, str) and c.strip()]
        questions = [q for q in questions_raw if isinstance(q, str) and q.strip()]

        return concepts, questions

    except Exception as e:
        logger.error(f"解析问题JSON失败: {e}, 响应内容: {response[:200]}...")
        return [], []

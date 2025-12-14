import asyncio
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from peewee import fn

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.common.database.database_model import ChatHistory
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.llm_models.payload_content.message import MessageBuilder, RoleType, Message
from src.plugin_system.apis import llm_api
from src.dream.dream_generator import generate_dream_summary

# dream 工具工厂函数
from src.dream.tools.search_chat_history_tool import make_search_chat_history
from src.dream.tools.get_chat_history_detail_tool import make_get_chat_history_detail
from src.dream.tools.delete_chat_history_tool import make_delete_chat_history
from src.dream.tools.create_chat_history_tool import make_create_chat_history
from src.dream.tools.update_chat_history_tool import make_update_chat_history
from src.dream.tools.finish_maintenance_tool import make_finish_maintenance
from src.dream.tools.search_jargon_tool import make_search_jargon
from src.dream.tools.delete_jargon_tool import make_delete_jargon
from src.dream.tools.update_jargon_tool import make_update_jargon

logger = get_logger("dream_agent")


def init_dream_prompts() -> None:
    """初始化 dream agent 的提示词"""
    Prompt(
        """
你的名字是{bot_name}，你现在处于"梦境维护模式（dream agent）"。
你可以自由地在 ChatHistory 库中探索、整理、创建和删改记录，以帮助自己在未来更好地回忆和理解对话历史。

本轮要维护的聊天ID：{chat_id}
本轮随机选中的起始记忆 ID：{start_memory_id}
请优先以这条起始记忆为切入点，先理解它的内容与上下文，再决定如何在其附近进行创建新概括、重写或删除等整理操作；如果起始记忆为空，则由你自行选择合适的切入点。

你可以使用的工具包括：
**ChatHistory 维护工具：**
- search_chat_history：根据关键词或参与人搜索该 chat_id 下的历史记忆概括列表
- get_chat_history_detail：查看某条概括的详细内容
- create_chat_history：根据整理后的理解创建一条新的 ChatHistory 概括记录（主题、概括、关键词、关键信息等）
- update_chat_history：在不改变事实的前提下重写或精炼主题、概括、关键词、关键信息
- delete_chat_history：删除明显冗余、噪声、错误或无意义的记录，或者非常有时效性的信息，或者无太多有用信息的日常互动。
你也可以先用 create_chat_history 创建一条新的综合概括，再对旧的冗余记录执行多次 delete_chat_history 来完成“合并”效果。

**Jargon（黑话）维护工具（只读，禁止修改）：**
- search_jargon：根据一个或多个关键词搜索Jargon 记录，通常是含义不明确的词条或者特殊的缩写

**通用工具：**
- finish_maintenance：当你认为当前维护工作已经完成，没有更多需要整理的内容时，调用此工具来结束本次运行

**工作目标**：
- 发现冗余、重复或高度相似的记录，并进行合并或删除；
- 发现主题/概括过于含糊、啰嗦或缺少关键信息的记录，进行重写和精简；
- summary要尽可能保持有用的信息；
- 尽量保持信息的真实与可用性，不要凭空捏造事实。

**合并准则**
- 你可以新建一个记录，然后删除旧记录来实现合并。
- 如果两个或多个记录的主题相似，内容是对主题不同方面的信息或讨论，且信息量较少，则可以合并为一条记录。
- 如果两个记录冲突，可以根据逻辑保留一个或者进行整合，也可以采取更新的记录，删除旧的记录

**轮次信息**：
- 本次维护最多执行 {max_iterations} 轮
- 每轮开始时，系统会告知你当前是第几轮，还剩多少轮
- 如果提前完成维护工作，可以调用 finish_maintenance 工具主动结束

**每一轮的执行方式（必须遵守）：**
- 第一步：先用一小段中文自然语言，写出你的「思考」和本轮计划（例如要查什么、准备怎么合并/修改）；
- 第二步：在这段思考之后，再通过工具调用来执行你的计划（可以调用 0~N 个工具）；
- 第三步：收到工具结果后，在下一轮继续先写出新的思考，再视情况继续调用工具。

请不要在没有先写出思考的情况下直接调用工具。
只输出你的思考内容或工具调用结果，由系统负责真正执行工具调用。
""",
        name="dream_react_head_prompt",
    )


class DreamTool:
    """dream 模块内部使用的简易工具封装"""

    def __init__(self, name: str, description: str, parameters: List[Tuple], execute_func):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute_func = execute_func

    def get_tool_definition(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, **kwargs) -> str:
        return await self.execute_func(**kwargs)


class DreamToolRegistry:
    def __init__(self) -> None:
        self.tools: Dict[str, DreamTool] = {}

    def register_tool(self, tool: DreamTool) -> None:
        """
        注册或更新 dream 工具。
        注意：dream agent 每个 chat_id 会重新初始化工具，这里允许覆盖已有同名工具。
        """
        self.tools[tool.name] = tool
        logger.info(f"注册/更新 dream 工具: {tool.name}")

    def get_tool(self, name: str) -> Optional[DreamTool]:
        return self.tools.get(name)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [tool.get_tool_definition() for tool in self.tools.values()]


_dream_tool_registry = DreamToolRegistry()


def get_dream_tool_registry() -> DreamToolRegistry:
    return _dream_tool_registry


def init_dream_tools(chat_id: str) -> None:
    """注册 dream agent 可用的 ChatHistory / Jargon 相关工具（限定在当前 chat_id 作用域内）"""
    from src.llm_models.payload_content.tool_option import ToolParamType

    # 通过工厂函数生成绑定当前 chat_id 的工具实现
    search_chat_history = make_search_chat_history(chat_id)
    get_chat_history_detail = make_get_chat_history_detail(chat_id)
    delete_chat_history = make_delete_chat_history(chat_id)
    create_chat_history = make_create_chat_history(chat_id)
    update_chat_history = make_update_chat_history(chat_id)
    finish_maintenance = make_finish_maintenance(chat_id)

    search_jargon = make_search_jargon(chat_id)
    delete_jargon = make_delete_jargon(chat_id)
    update_jargon = make_update_jargon(chat_id)

    _dream_tool_registry.register_tool(
        DreamTool(
            "search_chat_history",
            "根据关键词或参与人查询当前 chat_id 下的 ChatHistory 概览，便于快速定位相关记忆。",
            [
                (
                    "keyword",
                    ToolParamType.STRING,
                    "关键词（可选，支持多个关键词，可用空格、逗号等分隔）。",
                    False,
                    None,
                ),
                ("participant", ToolParamType.STRING, "参与人昵称（可选）。", False, None),
            ],
            search_chat_history,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "get_chat_history_detail",
            "根据 memory_id 获取单条 ChatHistory 的详细内容，包含主题、概括、关键词、关键信息等字段（不包含原文）。",
            [
                ("memory_id", ToolParamType.INTEGER, "ChatHistory 主键 ID。", True, None),
            ],
            get_chat_history_detail,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "delete_chat_history",
            "根据 memory_id 删除一条 ChatHistory 记录（请谨慎使用）。",
            [
                ("memory_id", ToolParamType.INTEGER, "需要删除的 ChatHistory 主键 ID。", True, None),
            ],
            delete_chat_history,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "update_chat_history",
            "按字段更新 ChatHistory 记录，可用于清理、重写或补充信息。",
            [
                ("memory_id", ToolParamType.INTEGER, "需要更新的 ChatHistory 主键 ID。", True, None),
                ("theme", ToolParamType.STRING, "新的主题标题，如果不需要修改可不填。", False, None),
                ("summary", ToolParamType.STRING, "新的概括内容，如果不需要修改可不填。", False, None),
                ("keywords", ToolParamType.STRING, "新的关键词 JSON 字符串，如 ['关键词1','关键词2']。", False, None),
                ("key_point", ToolParamType.STRING, "新的关键信息 JSON 字符串，如 ['要点1','要点2']。", False, None),
            ],
            update_chat_history,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "create_chat_history",
            "根据整理后的理解创建一条新的 ChatHistory 概括记录（主题、概括、关键词、关键信息等）。",
            [
                ("theme", ToolParamType.STRING, "新的主题标题（必填）。", True, None),
                ("summary", ToolParamType.STRING, "新的概括内容（必填）。", True, None),
                (
                    "keywords",
                    ToolParamType.STRING,
                    "新的关键词 JSON 字符串，如 ['关键词1','关键词2']（必填）。",
                    True,
                    None,
                ),
                (
                    "key_point",
                    ToolParamType.STRING,
                    "新的关键信息 JSON 字符串，如 ['要点1','要点2']（必填）。",
                    True,
                    None,
                ),
                ("start_time", ToolParamType.STRING, "起始时间戳（秒，Unix 时间，必填）。", True, None),
                ("end_time", ToolParamType.STRING, "结束时间戳（秒，Unix 时间，必填）。", True, None),
            ],
            create_chat_history,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "finish_maintenance",
            "结束本次 dream 维护任务。当你认为当前 chat_id 下的维护工作已经完成，没有更多需要整理、合并或修改的内容时，调用此工具来主动结束本次运行。",
            [
                (
                    "reason",
                    ToolParamType.STRING,
                    "结束维护的原因说明（可选），例如 '已完成所有记录的整理' 或 '当前记录质量良好，无需进一步维护'。",
                    False,
                    None,
                ),
            ],
            finish_maintenance,
        )
    )

    # ==================== Jargon 维护工具 ====================
    # 注册 Jargon 工具
    _dream_tool_registry.register_tool(
        DreamTool(
            "search_jargon",
            "根据一个或多个关键词搜索当前 chat_id 相关的 Jargon 记录概览（只包含 is_jargon=True，含全局 Jargon），便于快速理解黑话库。",
            [
                ("keyword", ToolParamType.STRING, "按一个或多个关键词搜索内容/含义/推断结果（必填）。", True, None),
            ],
            search_jargon,
        )
    )


async def run_dream_agent_once(
    chat_id: str,
    max_iterations: Optional[int] = None,
    start_memory_id: Optional[int] = None,
) -> None:
    """
    运行一次 dream agent，对指定 chat_id 的 ChatHistory 进行最多 max_iterations 轮的整理。
    如果 max_iterations 为 None，则使用配置文件中的默认值。
    """
    if max_iterations is None:
        max_iterations = global_config.dream.max_iterations

    start_ts = time.time()
    logger.info(f"[dream] 开始对 chat_id={chat_id} 进行 dream 维护，最多迭代 {max_iterations} 轮")

    # 初始化工具（作用域限定在当前 chat_id）
    init_dream_tools(chat_id)

    tool_registry = get_dream_tool_registry()
    tool_defs = tool_registry.get_tool_definitions()

    bot_name = global_config.bot.nickname
    time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    head_prompt = await global_prompt_manager.format_prompt(
        "dream_react_head_prompt",
        bot_name=bot_name,
        time_now=time_now,
        chat_id=chat_id,
        start_memory_id=start_memory_id if start_memory_id is not None else "无（本轮由你自由选择切入点）",
        max_iterations=max_iterations,
    )

    conversation_messages: List[Message] = []

    # 如果提供了起始记忆 ID，则在对话正式开始前，先把这条记忆的详细信息放入上下文，
    # 避免 LLM 还需要额外调用一次 get_chat_history_detail 才能看到起始记忆内容。
    if start_memory_id is not None:
        try:
            record = ChatHistory.get_or_none(ChatHistory.id == start_memory_id)
            if record:
                start_time_str = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.start_time))
                    if record.start_time
                    else "未知"
                )
                end_time_str = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.end_time)) if record.end_time else "未知"
                )
                detail_text = (
                    f"ID={record.id}\n"
                    f"chat_id={record.chat_id}\n"
                    f"时间范围={start_time_str} 至 {end_time_str}\n"
                    f"主题={record.theme or '无'}\n"
                    f"关键词={record.keywords or '无'}\n"
                    f"参与者={record.participants or '无'}\n"
                    f"概括={record.summary or '无'}\n"
                    f"关键信息={record.key_point or '无'}"
                )

                logger.debug(
                    f"[dream] 预加载起始记忆详情 memory_id={start_memory_id}，"
                    f"预览: {detail_text[:200].replace(chr(10), ' ')}"
                )

                start_detail_builder = MessageBuilder()
                start_detail_builder.set_role(RoleType.User)
                start_detail_builder.add_text_content(
                    "【起始记忆详情】以下是本轮随机/指定的起始记忆的详细信息，供你在整理时优先参考：\n\n" + detail_text
                )
                conversation_messages.append(start_detail_builder.build())
            else:
                logger.warning(
                    f"[dream] 提供的 start_memory_id={start_memory_id} 未找到对应 ChatHistory 记录，"
                    "将不预加载起始记忆详情。"
                )
        except Exception as e:
            logger.error(f"[dream] 预加载起始记忆详情失败 start_memory_id={start_memory_id}: {e}")

    # 注意：message_factory 必须是同步函数，返回消息列表（不能是 async/coroutine）
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

    for iteration in range(1, max_iterations + 1):
        # 在每轮开始时，添加轮次信息到对话中
        remaining_rounds = max_iterations - iteration + 1
        round_info_builder = MessageBuilder()
        round_info_builder.set_role(RoleType.User)
        round_info_builder.add_text_content(
            f"【轮次信息】当前是第 {iteration}/{max_iterations} 轮，还剩 {remaining_rounds} 轮。"
        )
        conversation_messages.append(round_info_builder.build())

        # 调用 LLM 让其决定是否要使用工具
        (
            success,
            response,
            reasoning_content,
            model_name,
            tool_calls,
        ) = await llm_api.generate_with_model_with_tools_by_message_factory(
            message_factory,
            model_config=model_config.model_task_config.tool_use,
            tool_options=tool_defs,
            request_type="dream.react",
        )

        if not success:
            logger.error(f"[dream] 第 {iteration} 轮 LLM 调用失败: {response}")
            break

        # 先输出「思考」内容，再输出工具调用信息（思考文本较长，仅在 debug 下输出）
        thought_log = reasoning_content or (response[:300] if response else "")
        if thought_log:
            logger.debug(f"[dream] 第 {iteration} 轮思考内容: {thought_log}")

        logger.info(
            f"[dream] 第 {iteration} 轮响应，模型={model_name}，工具调用数={len(tool_calls) if tool_calls else 0}"
        )

        assistant_msg: Optional[Message] = None
        if tool_calls:
            builder = MessageBuilder()
            builder.set_role(RoleType.Assistant)
            if response and response.strip():
                builder.add_text_content(response)
            builder.set_tool_calls(tool_calls)
            assistant_msg = builder.build()
        elif response and response.strip():
            builder = MessageBuilder()
            builder.set_role(RoleType.Assistant)
            builder.add_text_content(response)
            assistant_msg = builder.build()

        if assistant_msg:
            conversation_messages.append(assistant_msg)

        # 如果本轮没有工具调用，仅作为思考记录，继续下一轮
        if not tool_calls:
            logger.debug(f"[dream] 第 {iteration} 轮未调用任何工具，仅记录思考。")
            continue

        # 执行所有工具调用
        tasks = []
        finish_maintenance_called = False
        for tc in tool_calls:
            tool = tool_registry.get_tool(tc.func_name)
            if not tool:
                logger.warning(f"[dream] 未知工具：{tc.func_name}")
                continue

            # 检测是否调用了 finish_maintenance 工具
            if tc.func_name == "finish_maintenance":
                finish_maintenance_called = True

            params = tc.args or {}

            async def _run_single(t: DreamTool, p: Dict[str, Any], call_id: str, it: int):
                try:
                    result = await t.execute(**p)
                    logger.debug(f"[dream] 第 {it} 轮 工具 {t.name} 执行完成")
                    return call_id, result
                except Exception as e:
                    logger.error(f"[dream] 工具 {t.name} 执行失败: {e}")
                    return call_id, f"工具 {t.name} 执行失败: {e}"

            tasks.append(_run_single(tool, params, tc.call_id, iteration))

        if not tasks:
            continue

        tool_results = await asyncio.gather(*tasks, return_exceptions=False)

        # 将工具结果作为 Tool 消息追加
        for call_id, obs in tool_results:
            tool_builder = MessageBuilder()
            tool_builder.set_role(RoleType.Tool)
            tool_builder.add_text_content(str(obs))
            tool_builder.add_tool_call(call_id)
            conversation_messages.append(tool_builder.build())

        # 如果调用了 finish_maintenance 工具，提前结束本次运行
        if finish_maintenance_called:
            logger.info(f"[dream] 第 {iteration} 轮检测到 finish_maintenance 工具调用，提前结束本次维护。")
            break

    cost = time.time() - start_ts
    logger.info(f"[dream] 对 chat_id={chat_id} 的 dream 维护结束，共迭代 {iteration} 轮，耗时 {cost:.1f} 秒")

    # 生成梦境总结
    await generate_dream_summary(chat_id, conversation_messages, iteration, cost)


def _pick_random_chat_id() -> Optional[str]:
    """从 ChatHistory 中随机选择一个 chat_id，用于 dream agent 本次维护

    规则：
    - 只在 chat_id 所属的 ChatHistory 记录数 >= 10 时才会参与随机选择；
    - 记录数不足 10 的 chat_id 将被跳过，不会触发做梦 react。
    """
    try:
        # 统计每个 chat_id 的记录数，只保留记录数 >= 10 的 chat_id
        rows = (
            ChatHistory.select(ChatHistory.chat_id, fn.COUNT(ChatHistory.id).alias("cnt"))
            .group_by(ChatHistory.chat_id)
            .having(fn.COUNT(ChatHistory.id) >= 10)
            .order_by(ChatHistory.chat_id)
            .limit(200)
        )
        eligible_ids = [r.chat_id for r in rows]
        if not eligible_ids:
            logger.warning("[dream] ChatHistory 中暂无满足条件（记录数 >= 10）的 chat_id，本轮 dream 任务跳过。")
            return None
        chosen = random.choice(eligible_ids)
        logger.info(f"[dream] 从 {len(eligible_ids)} 个满足条件的 chat_id 中随机选择：{chosen}")
        return chosen
    except Exception as e:
        logger.error(f"[dream] 随机选择 chat_id 失败: {e}")
        return None


def _pick_random_memory_for_chat(chat_id: str) -> Optional[int]:
    """
    在给定 chat_id 下随机选择一条 ChatHistory 记录，作为本轮整理的起始记忆。
    """
    try:
        rows = (
            ChatHistory.select(ChatHistory.id)
            .where(ChatHistory.chat_id == chat_id)
            .order_by(ChatHistory.start_time.asc())
            .limit(200)
        )
        ids = [r.id for r in rows]
        if not ids:
            logger.warning(f"[dream] chat_id={chat_id} 下暂无 ChatHistory 记录，无法选择起始记忆。")
            return None
        return random.choice(ids)
    except Exception as e:
        logger.error(f"[dream] 在 chat_id={chat_id} 下随机选择起始记忆失败: {e}")
        return None


async def run_dream_cycle_once() -> None:
    """
    单次 dream 周期：
    - 随机选择一个 chat_id
    - 在该 chat_id 下随机选择一条 ChatHistory 作为起始记忆
    - 以这条起始记忆为切入点，对该 chat_id 运行一次 dream agent（最多 15 轮）
    """
    chat_id = _pick_random_chat_id()
    if not chat_id:
        return

    start_memory_id = _pick_random_memory_for_chat(chat_id)
    await run_dream_agent_once(
        chat_id=chat_id,
        max_iterations=None,  # 使用配置文件中的默认值
        start_memory_id=start_memory_id,
    )


async def start_dream_scheduler(
    first_delay_seconds: Optional[int] = None,
    interval_seconds: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    dream 调度器：
    - 程序启动后先等待 first_delay_seconds（如果为 None，则使用配置文件中的值，默认 60s）
    - 然后每隔 interval_seconds（如果为 None，则使用配置文件中的值，默认 30 分钟）运行一次 dream agent 周期
    - 如果提供 stop_event，则在 stop_event 被 set() 后优雅退出循环
    """
    if first_delay_seconds is None:
        first_delay_seconds = global_config.dream.first_delay_seconds

    if interval_seconds is None:
        interval_seconds = global_config.dream.interval_minutes * 60

    logger.info(
        f"[dream] dream 调度器启动：首次延迟 {first_delay_seconds}s，之后每隔 {interval_seconds}s ({interval_seconds // 60} 分钟) 运行一次 dream agent"
    )

    try:
        await asyncio.sleep(first_delay_seconds)
        while True:
            if stop_event is not None and stop_event.is_set():
                logger.info("[dream] 收到停止事件，结束 dream 调度器循环。")
                break

            start_ts = time.time()
            # 检查当前时间是否在允许做梦的时间段内
            if not global_config.dream.is_in_dream_time():
                logger.debug("[dream] 当前时间不在允许做梦的时间段内，跳过本次执行")
            else:
                try:
                    await run_dream_cycle_once()
                except Exception as e:
                    logger.error(f"[dream] 单次 dream 周期执行异常: {e}")

            elapsed = time.time() - start_ts
            # 保证两次执行之间至少间隔 interval_seconds
            to_sleep = max(0.0, interval_seconds - elapsed)
            await asyncio.sleep(to_sleep)
    except asyncio.CancelledError:
        logger.info("[dream] dream 调度器任务被取消，准备退出。")
        raise


# 初始化提示词
init_dream_prompts()

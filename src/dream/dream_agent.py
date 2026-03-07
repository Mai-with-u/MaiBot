import asyncio
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func as fn

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.common.database.database_model import ChatHistory
from src.prompt.prompt_manager import prompt_manager
from src.llm_models.payload_content.message import MessageBuilder, RoleType, Message
from src.services import llm_service as llm_api
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
    _delete_jargon = make_delete_jargon(chat_id)
    _update_jargon = make_update_jargon(chat_id)

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
            ],
            update_chat_history,
        )
    )

    _dream_tool_registry.register_tool(
        DreamTool(
            "create_chat_history",
            "根据整理后的理解创建一条新的 ChatHistory 概括记录（主题、概括、关键词等）。",
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
                ("original_text", ToolParamType.STRING, "对话原文内容（必填）。", True, None),
                (
                    "participants",
                    ToolParamType.STRING,
                    "参与人的 JSON 字符串，如 ['用户1','用户2']（必填）。",
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

    head_prompt_template = prompt_manager.get_prompt("dream_react_head")
    head_prompt_template.add_context("bot_name", global_config.bot.nickname)
    head_prompt_template.add_context("time_now", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    head_prompt_template.add_context("chat_id", chat_id)
    head_prompt_template.add_context(
        "start_memory_id",
        str(start_memory_id) if start_memory_id is not None else "无（本轮由你自由选择切入点）",
    )
    head_prompt_template.add_context("max_iterations", str(max_iterations))

    head_prompt = await prompt_manager.render_prompt(head_prompt_template)

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
                    f"概括={record.summary or '无'}"
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
            if not TempMethodsDream.is_in_dream_time():
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


class TempMethodsDream:
    @staticmethod
    def is_in_dream_time() -> bool:
        if not global_config.dream.dream_time_ranges:
            return True
        now_min = time.localtime()
        now_total_min = now_min.tm_hour * 60 + now_min.tm_min
        for time_range in global_config.dream.dream_time_ranges:
            if parsed := TempMethodsDream._parse_range(time_range):
                start_min, end_min = parsed
                if TempMethodsDream._in_range(now_total_min, start_min, end_min):
                    return True
        return False

    @staticmethod
    def _in_range(now_min, start_min, end_min) -> bool:
        if start_min <= end_min:
            return start_min <= now_min <= end_min
        return now_min >= start_min or now_min <= end_min

    @staticmethod
    def _parse_range(range_str: str) -> Optional[Tuple[int, int]]:
        try:
            start_str, end_str = [s.strip() for s in range_str.split("-")]
            sh, sm = [int(x) for x in start_str.split(":")]
            eh, em = [int(x) for x in end_str.split(":")]
            return sh * 60 + sm, eh * 60 + em
        except Exception:
            return None

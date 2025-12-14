import random
from typing import List, Optional

from src.common.logger import get_logger
from src.config.config import model_config
from src.chat.utils.prompt_builder import Prompt
from src.llm_models.payload_content.message import RoleType, Message
from src.llm_models.utils_model import LLMRequest

logger = get_logger("dream_generator")

# 初始化 utils 模型用于生成梦境总结
_dream_summary_model: Optional[LLMRequest] = None

# 梦境风格列表（21种）
DREAM_STYLES = [
    "保持诗意和想象力，自由编写",
    "诗意朦胧，如薄雾笼罩的清晨",
    "奇幻冒险，充满未知与探索",
    "温暖怀旧，带着时光的痕迹",
    "神秘悬疑，暗藏深意",
    "浪漫唯美，如诗如画",
    "科幻未来，科技与想象交织",
    "自然清新，如山林间的微风",
    "深沉哲思，引人深思",
    "轻松幽默，充满趣味",
    "悲伤忧郁，带着淡淡哀愁",
    "激昂热烈，充满活力",
    "宁静平和，如湖面般平静",
    "荒诞离奇，打破常规",
    "细腻温柔，如春风拂面",
    "壮阔宏大，气势磅礴",
    "简约纯粹，返璞归真",
    "复杂多变，层次丰富",
    "梦幻迷离，虚实难辨",
    "现实写意，贴近生活",
    "抽象概念，超越具象",
]


def get_random_dream_styles(count: int = 2) -> List[str]:
    """从梦境风格列表中随机选择指定数量的风格"""
    return random.sample(DREAM_STYLES, min(count, len(DREAM_STYLES)))


def get_dream_summary_model() -> LLMRequest:
    """获取用于生成梦境总结的 utils 模型实例"""
    global _dream_summary_model
    if _dream_summary_model is None:
        _dream_summary_model = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="dream.summary",
        )
    return _dream_summary_model


def init_dream_summary_prompt() -> None:
    """初始化梦境总结的提示词"""
    Prompt(
        """
你刚刚完成了一次对聊天记录的记忆整理工作。以下是整理过程的摘要：
整理过程：
{conversation_text}

请将这次整理涉及的相关信息改写为一个富有诗意和想象力的"梦境"，请你仅使用具体的记忆的内容，而不是整理过程编写。
要求：
1. 使用第一人称视角
2. 叙述直白，不要复杂修辞，口语化
3. 长度控制在200-800字
4. 用中文输出
梦境风格：
{dream_styles}
请直接输出梦境内容，不要添加其他说明：
""",
        name="dream_summary_prompt",
    )


async def generate_dream_summary(
    chat_id: str,
    conversation_messages: List[Message],
    total_iterations: int,
    time_cost: float,
) -> None:
    """生成梦境总结并输出到日志"""
    try:
        import json
        from src.chat.utils.prompt_builder import global_prompt_manager

        # 第一步：建立工具调用结果映射 (call_id -> result)
        tool_results_map: dict[str, str] = {}
        for msg in conversation_messages:
            if msg.role == RoleType.Tool and msg.tool_call_id:
                content = ""
                if msg.content:
                    if isinstance(msg.content, list) and msg.content:
                        content = msg.content[0].text if hasattr(msg.content[0], "text") else str(msg.content[0])
                    else:
                        content = str(msg.content)
                tool_results_map[msg.tool_call_id] = content

        # 第二步：详细记录所有工具调用操作和结果到日志
        tool_call_count = 0
        logger.info(f"[dream][工具调用详情] 开始记录 chat_id={chat_id} 的所有工具调用操作：")

        for msg in conversation_messages:
            if msg.role == RoleType.Assistant and msg.tool_calls:
                tool_call_count += 1
                # 提取思考内容
                thought_content = ""
                if msg.content:
                    if isinstance(msg.content, list) and msg.content:
                        thought_content = (
                            msg.content[0].text if hasattr(msg.content[0], "text") else str(msg.content[0])
                        )
                    else:
                        thought_content = str(msg.content)

                logger.info(f"[dream][工具调用详情] === 第 {tool_call_count} 组工具调用 ===")
                if thought_content:
                    logger.info(
                        f"[dream][工具调用详情] 思考内容：{thought_content[:500]}{'...' if len(thought_content) > 500 else ''}"
                    )

                # 记录每个工具调用的详细信息
                for idx, tool_call in enumerate(msg.tool_calls, 1):
                    tool_name = tool_call.func_name
                    tool_args = tool_call.args or {}
                    tool_call_id = tool_call.call_id
                    tool_result = tool_results_map.get(tool_call_id, "未找到执行结果")

                    # 格式化参数
                    try:
                        args_str = json.dumps(tool_args, ensure_ascii=False, indent=2) if tool_args else "无参数"
                    except Exception:
                        args_str = str(tool_args)

                    logger.info(f"[dream][工具调用详情] --- 工具 {idx}: {tool_name} ---")
                    logger.info(f"[dream][工具调用详情] 调用参数：\n{args_str}")
                    logger.info(f"[dream][工具调用详情] 执行结果：\n{tool_result}")
                    logger.info(f"[dream][工具调用详情] {'-' * 60}")

        logger.info(f"[dream][工具调用详情] 共记录了 {tool_call_count} 组工具调用操作")

        # 第三步：构建对话历史摘要（用于生成梦境）
        conversation_summary = []
        for msg in conversation_messages:
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            content = ""
            if msg.content:
                content = msg.content[0].text if isinstance(msg.content, list) and msg.content else str(msg.content)

            if role == "user" and "轮次信息" in content:
                # 跳过轮次信息消息
                continue

            if role == "assistant":
                # 只保留思考内容，简化工具调用信息
                if content:
                    # 截取前500字符，避免过长
                    content_preview = content[:500] + ("..." if len(content) > 500 else "")
                    conversation_summary.append(f"[{role}] {content_preview}")
            elif role == "tool":
                # 工具结果，只保留关键信息
                if content:
                    # 截取前300字符
                    content_preview = content[:300] + ("..." if len(content) > 300 else "")
                    conversation_summary.append(f"[工具执行] {content_preview}")

        conversation_text = "\n".join(conversation_summary[-20:])  # 只保留最后20条消息

        # 随机选择2个梦境风格
        selected_styles = get_random_dream_styles(2)
        dream_styles_text = "\n".join([f"{i + 1}. {style}" for i, style in enumerate(selected_styles)])

        # 使用 Prompt 管理器格式化梦境生成 prompt
        dream_prompt = await global_prompt_manager.format_prompt(
            "dream_summary_prompt",
            chat_id=chat_id,
            total_iterations=total_iterations,
            time_cost=time_cost,
            conversation_text=conversation_text,
            dream_styles=dream_styles_text,
        )

        # 调用 utils 模型生成梦境
        summary_model = get_dream_summary_model()
        dream_content, (reasoning, model_name, _) = await summary_model.generate_response_async(
            dream_prompt,
            max_tokens=512,
            temperature=0.8,
        )

        if dream_content:
            logger.info(f"[dream][梦境总结] 对 chat_id={chat_id} 的整理过程梦境：\n{dream_content}")
        else:
            logger.warning("[dream][梦境总结] 未能生成梦境总结")

    except Exception as e:
        logger.error(f"[dream][梦境总结] 生成梦境总结失败: {e}", exc_info=True)


init_dream_summary_prompt()

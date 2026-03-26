"""
found_answer工具 - 用于在记忆检索过程中结束查询
"""

from src.common.logger import get_logger
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def found_answer(answer: str = "") -> str:
    """结束查询

    Args:
        answer: 如果找到了答案，提供答案内容；如果未找到答案，可以为空或不提供此参数

    Returns:
        str: 确认信息
    """
    if answer and answer.strip():
        logger.info(f"找到答案: {answer}")
        return f"已确认找到答案: {answer}"
    else:
        logger.info("未找到答案，结束查询")
        return "未找到答案，查询结束"


def register_tool():
    """注册found_answer工具"""
    register_memory_retrieval_tool(
        name="found_answer",
        description="当你决定结束查询时，调用此工具。如果找到了明确答案，在answer参数中提供答案内容；如果未找到答案，可以不提供answer参数或提供空字符串。只有在检索到明确、具体的答案时才提供answer，不要编造信息。",
        parameters=[
            {
                "name": "answer",
                "type": "string",
                "description": "如果找到了答案，提供找到的答案内容，必须基于已收集的信息，不要编造；如果未找到答案，可以不提供此参数或提供空字符串",
                "required": False,
            },
        ],
        execute_func=found_answer,
    )

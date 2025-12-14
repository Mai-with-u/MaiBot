"""
finish_search工具 - 用于在记忆检索过程中结束查询
"""

from src.common.logger import get_logger
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def finish_search(found_answer: bool, answer: str = "") -> str:
    """结束查询

    Args:
        found_answer: 是否找到了答案
        answer: 如果找到了答案，提供答案内容；如果未找到，可以为空

    Returns:
        str: 确认信息
    """
    if found_answer:
        logger.info(f"找到答案: {answer}")
        return f"已确认找到答案: {answer}"
    else:
        logger.info("未找到答案，结束查询")
        return "未找到答案，查询结束"


def register_tool():
    """注册finish_search工具"""
    register_memory_retrieval_tool(
        name="finish_search",
        description="当你决定结束查询时，调用此工具。如果找到了明确答案，设置found_answer为true并在answer中提供答案；如果未找到答案，设置found_answer为false。只有在检索到明确、具体的答案时才设置found_answer为true，不要编造信息。",
        parameters=[
            {
                "name": "found_answer",
                "type": "boolean",
                "description": "是否找到了答案",
                "required": True,
            },
            {
                "name": "answer",
                "type": "string",
                "description": "如果found_answer为true，提供找到的答案内容，必须基于已收集的信息，不要编造；如果found_answer为false，可以为空",
                "required": False,
            },
        ],
        execute_func=finish_search,
    )

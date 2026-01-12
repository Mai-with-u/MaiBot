"""
return_information工具 - 用于在记忆检索过程中返回总结信息并结束查询
"""

from src.common.logger import get_logger
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def return_information(information: str) -> str:
    """返回总结信息并结束查询

    Args:
        information: 基于已收集信息总结出的相关信息，用于帮助回复。如果收集的信息对当前聊天没有帮助，可以返回空字符串。

    Returns:
        str: 确认信息
    """
    if information and information.strip():
        logger.info(f"返回总结信息: {information}")
        return f"已确认返回信息: {information}"
    else:
        logger.info("未收集到相关信息，结束查询")
        return "未收集到相关信息，查询结束"


def register_tool():
    """注册return_information工具"""
    register_memory_retrieval_tool(
        name="return_information",
        description="当你决定结束查询时，调用此工具。基于已收集的信息，总结出一段相关信息用于帮助回复。如果收集的信息对当前聊天有帮助，在information参数中提供总结信息；如果信息无关或没有帮助，可以提供空字符串。",
        parameters=[
            {
                "name": "information",
                "type": "string",
                "description": "基于已收集信息总结出的相关信息，用于帮助回复。必须基于已收集的信息，不要编造。如果信息对当前聊天没有帮助，可以返回空字符串。",
                "required": True,
            },
        ],
        execute_func=return_information,
    )

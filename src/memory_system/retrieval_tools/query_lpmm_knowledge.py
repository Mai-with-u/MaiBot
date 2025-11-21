"""
通过LPMM知识库查询信息 - 工具实现
"""

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.knowledge import get_qa_manager
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def query_lpmm_knowledge(query: str, limit: int = 5) -> str:
    """在LPMM知识库中查询相关信息

    Args:
        query: 查询关键词

    Returns:
        str: 查询结果
    """
    try:
        content = str(query).strip()
        if not content:
            return "查询关键词为空"

        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 5
        limit_value = max(1, limit_value)

        if not global_config.lpmm_knowledge.enable:
            logger.debug("LPMM知识库未启用")
            return "LPMM知识库未启用"

        qa_manager = get_qa_manager()
        if qa_manager is None:
            logger.debug("LPMM知识库未初始化，跳过查询")
            return "LPMM知识库未初始化"

        knowledge_info = await qa_manager.get_knowledge(content, limit=limit_value)
        logger.debug(f"LPMM知识库查询结果: {knowledge_info}")

        if knowledge_info:
            return f"你从LPMM知识库中找到以下信息：\n{knowledge_info}"

        return f"在LPMM知识库中未找到与“{content}”相关的信息"

    except Exception as e:
        logger.error(f"LPMM知识库查询失败: {e}")
        return f"LPMM知识库查询失败：{str(e)}"


def register_tool():
    """注册LPMM知识库查询工具"""
    register_memory_retrieval_tool(
        name="lpmm_search_knowledge",
        description="从LPMM知识库中搜索相关信息，适用于需要知识支持的场景。",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "需要查询的关键词或问题",
                "required": True,
            },
            {
                "name": "limit",
                "type": "integer",
                "description": "希望返回的相关知识条数，默认为5",
                "required": False,
            },
        ],
        execute_func=query_lpmm_knowledge,
    )

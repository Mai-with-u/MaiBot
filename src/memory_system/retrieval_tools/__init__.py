"""
记忆检索工具模块
提供统一的工具注册和管理系统
"""

from .tool_registry import (
    MemoryRetrievalTool,
    MemoryRetrievalToolRegistry,
    register_memory_retrieval_tool,
    get_tool_registry,
)

# 导入所有工具的注册函数
from .query_jargon import register_tool as register_query_jargon
from .query_chat_history import register_tool as register_query_chat_history
from .query_lpmm_knowledge import register_tool as register_lpmm_knowledge
from .query_person_info import register_tool as register_query_person_info
from src.config.config import global_config


def init_all_tools():
    """初始化并注册所有记忆检索工具"""
    register_query_jargon()
    register_query_chat_history()
    register_query_person_info()

    if global_config.lpmm_knowledge.lpmm_mode == "agent":
        register_lpmm_knowledge()


__all__ = [
    "MemoryRetrievalTool",
    "MemoryRetrievalToolRegistry",
    "register_memory_retrieval_tool",
    "get_tool_registry",
    "init_all_tools",
]

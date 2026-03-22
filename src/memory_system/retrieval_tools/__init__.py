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


def init_all_tools():
    """初始化并注册所有记忆检索工具"""
    # 延迟导入，避免在仅使用部分工具或单元测试阶段触发不必要的依赖链。
    from .query_long_term_memory import register_tool as register_long_term_memory
    from .query_words import register_tool as register_query_words
    from .return_information import register_tool as register_return_information

    register_query_words()
    register_return_information()
    register_long_term_memory()


__all__ = [
    "MemoryRetrievalTool",
    "MemoryRetrievalToolRegistry",
    "register_memory_retrieval_tool",
    "get_tool_registry",
    "init_all_tools",
]

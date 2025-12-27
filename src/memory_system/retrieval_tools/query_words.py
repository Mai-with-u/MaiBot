"""
查询黑话/概念含义 - 工具实现
用于在记忆检索过程中主动查询未知词语或黑话的含义
"""

from typing import List, Optional
from src.common.logger import get_logger
from src.bw_learner.jargon_explainer import retrieve_concepts_with_jargon
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def query_words(chat_id: str, words: str) -> str:
    """查询词语或黑话的含义

    Args:
        chat_id: 聊天ID
        words: 要查询的词语，可以是单个词语或多个词语（用逗号、空格等分隔）

    Returns:
        str: 查询结果，包含词语的含义解释
    """
    try:
        if not words or not words.strip():
            return "未提供要查询的词语"

        # 解析词语列表（支持逗号、空格等分隔符）
        words_list = []
        for separator in [",", "，", " ", "\n", "\t"]:
            if separator in words:
                words_list = [w.strip() for w in words.split(separator) if w.strip()]
                break
        
        # 如果没有找到分隔符，整个字符串作为一个词语
        if not words_list:
            words_list = [words.strip()]

        # 去重
        unique_words = []
        seen = set()
        for word in words_list:
            if word and word not in seen:
                unique_words.append(word)
                seen.add(word)

        if not unique_words:
            return "未提供有效的词语"

        logger.info(f"查询词语含义: {unique_words}")

        # 调用检索函数
        result = await retrieve_concepts_with_jargon(unique_words, chat_id)

        if result:
            return result
        else:
            return f"未找到词语 '{', '.join(unique_words)}' 的含义或黑话解释"

    except Exception as e:
        logger.error(f"查询词语含义失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    register_memory_retrieval_tool(
        name="query_words",
        description="查询词语或黑话的含义。当遇到不熟悉的词语、缩写、黑话或网络用语时，可以使用此工具查询其含义。支持查询单个或多个词语（用逗号、空格等分隔）。",
        parameters=[
            {
                "name": "words",
                "type": "string",
                "description": "要查询的词语，可以是单个词语或多个词语（用逗号、空格等分隔，如：'YYDS' 或 'YYDS,内卷,996'）",
                "required": True,
            },
        ],
        execute_func=query_words,
    )


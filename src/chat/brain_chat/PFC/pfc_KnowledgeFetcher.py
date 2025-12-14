from typing import List, Tuple
from src.common.logger import get_module_logger
from src.plugins.memory_system.Hippocampus import HippocampusManager
from ..models.utils_model import LLMRequest
from ...config.config import global_config
from ..chat.message import Message
from ..knowledge.knowledge_lib import qa_manager
from ..utils.chat_message_builder import build_readable_messages

logger = get_module_logger("knowledge_fetcher")


class KnowledgeFetcher:
    """知识调取器"""

    def __init__(self, private_name: str):
        self.llm = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=1000,
            request_type="knowledge_fetch",
        )
        self.private_name = private_name

    def _lpmm_get_knowledge(self, query: str) -> str:
        """获取相关知识

        Args:
            query: 查询内容

        Returns:
            str: 构造好的,带相关度的知识
        """

        logger.debug(f"[私聊][{self.private_name}]正在从LPMM知识库中获取知识")
        try:
            knowledge_info = qa_manager.get_knowledge(query)
            logger.debug(f"[私聊][{self.private_name}]LPMM知识库查询结果: {knowledge_info:150}")
            return knowledge_info
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]LPMM知识库搜索工具执行失败: {str(e)}")
            return "未找到匹配的知识"

    async def fetch(self, query: str, chat_history: List[Message]) -> Tuple[str, str]:
        """获取相关知识

        Args:
            query: 查询内容
            chat_history: 聊天历史

        Returns:
            Tuple[str, str]: (获取的知识, 知识来源)
        """
        # 构建查询上下文
        chat_history_text = await build_readable_messages(
            chat_history,
            replace_bot_name=True,
            merge_messages=False,
            timestamp_mode="relative",
            read_mark=0.0,
        )

        # 从记忆中获取相关知识
        related_memory = await HippocampusManager.get_instance().get_memory_from_text(
            text=f"{query}\n{chat_history_text}",
            max_memory_num=3,
            max_memory_length=2,
            max_depth=3,
            fast_retrieval=False,
        )
        knowledge_text = ""
        sources_text = "无记忆匹配"  # 默认值
        if related_memory:
            sources = []
            for memory in related_memory:
                knowledge_text += memory[1] + "\n"
                sources.append(f"记忆片段{memory[0]}")
            knowledge_text = knowledge_text.strip()
            sources_text = "，".join(sources)

        knowledge_text += "\n现在有以下**知识**可供参考：\n "
        knowledge_text += self._lpmm_get_knowledge(query)
        knowledge_text += "\n请记住这些**知识**，并根据**知识**回答问题。\n"

        return knowledge_text or "未找到相关知识", sources_text or "无记忆匹配"

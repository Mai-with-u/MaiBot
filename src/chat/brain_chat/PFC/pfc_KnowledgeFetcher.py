from typing import Any, Dict, List, Tuple

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.common.logger import get_logger

# NOTE: HippocampusManager doesn't exist in v0.12.2 - memory system was redesigned
# from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest
from src.person_info.person_info import resolve_person_id_for_memory
from src.services.memory_service import memory_service

logger = get_logger("knowledge_fetcher")


class KnowledgeFetcher:
    """知识调取器"""

    def __init__(self, private_name: str, stream_id: str):
        self.llm = LLMRequest(model_set=model_config.model_task_config.utils)
        self.private_name = private_name
        self.stream_id = stream_id

    def _resolve_private_memory_context(self) -> Dict[str, str]:
        session = _chat_manager.get_session_by_session_id(self.stream_id)
        if session is None:
            return {"chat_id": self.stream_id}

        group_id = str(getattr(session, "group_id", "") or "").strip()
        user_id = str(getattr(session, "user_id", "") or "").strip()
        platform = str(getattr(session, "platform", "") or "").strip()

        person_id = ""
        if not group_id:
            try:
                person_id = resolve_person_id_for_memory(
                    person_name=self.private_name,
                    platform=platform,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.debug(f"[私聊][{self.private_name}]解析人物ID失败: {exc}")

        return {
            "chat_id": self.stream_id,
            "person_id": person_id,
            "user_id": user_id,
            "group_id": group_id,
        }

    async def _memory_get_knowledge(self, query: str) -> str:
        """获取相关知识

        Args:
            query: 查询内容

        Returns:
            str: 构造好的,带相关度的知识
        """

        logger.debug(f"[私聊][{self.private_name}]正在从长期记忆中获取知识")
        try:
            context = self._resolve_private_memory_context()
            search_kwargs = {
                "limit": 5,
                "mode": "search",
                "chat_id": context.get("chat_id", ""),
                "person_id": context.get("person_id", ""),
                "user_id": context.get("user_id", ""),
                "group_id": context.get("group_id", ""),
                "respect_filter": True,
            }
            result = await memory_service.search(query, **search_kwargs)
            if not result.success:
                logger.warning(
                    f"[私聊][{self.private_name}]长期记忆查询失败: {result.error or '未知错误'}"
                )
                return f"长期记忆检索失败：{result.error or '未知错误'}"
            if not result.filtered and not result.hits and search_kwargs["person_id"]:
                fallback_kwargs = dict(search_kwargs)
                fallback_kwargs["person_id"] = ""
                logger.debug(f"[私聊][{self.private_name}]人物过滤未命中，退回仅按会话检索长期记忆")
                result = await memory_service.search(query, **fallback_kwargs)
                if not result.success:
                    logger.warning(
                        f"[私聊][{self.private_name}]长期记忆回退查询失败: {result.error or '未知错误'}"
                    )
                    return f"长期记忆检索失败：{result.error or '未知错误'}"
            knowledge_info = result.to_text(limit=5)
            if result.filtered:
                logger.debug(f"[私聊][{self.private_name}]长期记忆查询被聊天过滤策略跳过")
            else:
                logger.debug(f"[私聊][{self.private_name}]长期记忆查询结果: {knowledge_info[:150]}")
            return knowledge_info or "未找到匹配的知识"
        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]长期记忆搜索工具执行失败: {str(e)}")
            return "未找到匹配的知识"

    async def fetch(self, query: str, chat_history: List[Dict[str, Any]]) -> Tuple[str, str]:
        """获取相关知识

        Args:
            query: 查询内容
            chat_history: 聊天历史 (PFC dict format)

        Returns:
            Tuple[str, str]: (获取的知识, 知识来源)
        """
        _ = chat_history

        # NOTE: Hippocampus memory system was redesigned in v0.12.2
        # The old get_memory_from_text API no longer exists
        # For now, we'll skip the memory retrieval part and only use LPMM knowledge
        # TODO: Integrate with new memory system if needed
        knowledge_text = ""
        sources_text = "无记忆匹配"  # 默认值

        # # 从记忆中获取相关知识 (DISABLED - old Hippocampus API)
        # related_memory = await HippocampusManager.get_instance().get_memory_from_text(
        #     text=f"{query}\n{chat_history_text}",
        #     max_memory_num=3,
        #     max_memory_length=2,
        #     max_depth=3,
        #     fast_retrieval=False,
        # )
        # if related_memory:
        #     sources = []
        #     for memory in related_memory:
        #         knowledge_text += memory[1] + "\n"
        #         sources.append(f"记忆片段{memory[0]}")
        #     knowledge_text = knowledge_text.strip()
        #     sources_text = "，".join(sources)

        knowledge_text += "\n现在有以下**知识**可供参考：\n "
        knowledge_text += await self._memory_get_knowledge(query)
        knowledge_text += "\n请记住这些**知识**，并根据**知识**回答问题。\n"

        return knowledge_text or "未找到相关知识", sources_text or "无记忆匹配"

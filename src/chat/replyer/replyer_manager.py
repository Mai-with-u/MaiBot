from typing import Any, Dict, Optional

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager as _chat_manager
from src.common.logger import get_logger

from .maisaka_generator import MaisakaReplyGenerator

logger = get_logger("ReplyerManager")


class ReplyerManager:
    """统一管理不同类型的回复生成器。"""

    def __init__(self) -> None:
        self._repliers: Dict[str, Any] = {}

    def get_replyer(
        self,
        chat_stream: Optional[BotChatSession] = None,
        chat_id: Optional[str] = None,
        request_type: str = "replyer",
        replyer_type: str = "default",
    ) -> Optional[MaisakaReplyGenerator]:
        """按会话和 replyer 类型获取实例。"""
        stream_id = chat_stream.session_id if chat_stream else chat_id
        if not stream_id:
            logger.warning("[ReplyerManager] 缺少 stream_id，无法获取 replyer")
            return None

        cache_key = f"{replyer_type}:{stream_id}"
        if cache_key in self._repliers:
            return self._repliers[cache_key]

        target_stream = chat_stream or _chat_manager.get_session_by_session_id(stream_id)
        if not target_stream:
            logger.warning(f"[ReplyerManager] 未找到会话，stream_id={stream_id}")
            return None

        logger.info(
            f"[ReplyerManager] 开始创建 replyer: cache_key={cache_key}, "
            f"replyer_type={replyer_type}, is_group_session={target_stream.is_group_session}"
        )

        try:
            if replyer_type == "maisaka":
                replyer = MaisakaReplyGenerator(
                    chat_stream=target_stream,
                    request_type=request_type,
                )
            else:
                logger.warning(f"[ReplyerManager] 不支持的 replyer_type={replyer_type}")
                return None
        except Exception:
            logger.exception(f"[ReplyerManager] 创建 replyer 失败: cache_key={cache_key}")
            raise

        self._repliers[cache_key] = replyer
        logger.debug(f"Replyer 创建完成: cache_key={cache_key}")
        return replyer


replyer_manager = ReplyerManager()

from typing import Any, Callable, Optional

from src.chat.message_receive.chat_manager import BotChatSession
from src.common.prompt_i18n import load_prompt
from src.services.llm_service import LLMServiceClient

from .maisaka_generator_base import BaseMaisakaReplyGenerator


class MaisakaReplyGenerator(BaseMaisakaReplyGenerator):
    """Maisaka replyer。"""

    def __init__(
        self,
        chat_stream: Optional[BotChatSession] = None,
        request_type: str = "maisaka.replyer",
        llm_client_cls: Optional[Any] = None,
        load_prompt_func: Optional[Callable[..., str]] = None,
    ) -> None:
        super().__init__(
            chat_stream=chat_stream,
            request_type=request_type,
            llm_client_cls=llm_client_cls or LLMServiceClient,
            load_prompt_func=load_prompt_func or load_prompt,
        )

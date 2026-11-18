"""reply 的内置语音附件。"""

from typing import Any, Dict, List, Literal, Optional, get_args
import asyncio

from src.cli.maisaka_cli_sender import CLI_PLATFORM_NAME
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent, VoiceComponent
from src.config.config import model_config
from src.services.tts_service import synthesize_reply_speech

from .context import PostProcessedReplyMessage

VoiceMode = Literal["voice_only", "with_text"]


def voice_attachment_schema(platform: Optional[str]) -> Optional[Dict[str, Any]]:
    if platform == CLI_PLATFORM_NAME or not model_config.model_task_config.tts.model_list:
        return None
    return {
        "type": "string",
        "enum": list(get_args(VoiceMode)),
        "description": "需要用语音回复时选择 voice_only 朗读整段正文；需要同时发送文字时选择 with_text。平时省略。",
    }


async def attach_reply_voice(
    items: List[PostProcessedReplyMessage], text: str, mode: VoiceMode,
) -> List[PostProcessedReplyMessage]:
    """整组正文合成一次，保留原有的附件顺序及引用。"""
    components = [part for item in items for part in item.sequence.components]
    if any(isinstance(part, VoiceComponent) for part in components):
        raise ValueError("本次回复已包含语音附件")
    if not any(isinstance(part, TextComponent) and part.text.strip() for part in components):
        raise ValueError("没有可朗读的回复正文")
    audio = await synthesize_reply_speech(text)
    voice = await asyncio.to_thread(VoiceComponent, binary_hash="", content=text, binary_data=audio)
    if mode == "with_text":
        return [*items, PostProcessedReplyMessage(MessageSequence([voice]))]

    result: List[PostProcessedReplyMessage] = []
    inserted = False
    for item in items:
        parts = []
        for part in item.sequence.components:
            if not isinstance(part, TextComponent):
                parts.append(part)
            elif not inserted and part.text.strip():
                parts.append(voice)
                inserted = True
        if parts:
            result.append(PostProcessedReplyMessage(MessageSequence(parts), item.quote_previous))
    return result

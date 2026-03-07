from rich.traceback import install
from typing import Optional

import base64

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest


install(extra_lines=3)

logger = get_logger("chat_voice")

# TODO: 在LLMRequest重构后修改这里
asr_model = LLMRequest(model_set=model_config.model_task_config.voice, request_type="audio")


async def get_voice_text(voice_bytes: bytes) -> Optional[str]:
    """
    获取音频文件转录文本

    Args:
        voice_bytes (bytes): 语音消息的字节数据
    Returns:
        return (Optional[str]): 转录后的文本描述，如果转录失败或未启用语音识别功能，则返回 None
    """
    if not global_config.voice.enable_asr:
        logger.warning("语音识别未启用，无法处理语音消息")
        return None
    try:
        voice_base64 = base64.b64encode(voice_bytes).decode("utf-8")
        text = await asr_model.generate_response_for_voice(voice_base64)
        if not text:
            logger.warning("语音转文字结果为空")

        # logger.debug(f"转录结果是是{text}")

        return text
    except Exception as e:
        logger.error(f"语音转文字失败: {str(e)}")
        return None

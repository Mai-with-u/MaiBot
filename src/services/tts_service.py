"""使用当前语音合成分工朗读回复正文。"""

import asyncio

from src.config.config import config_manager
from src.llm_models.model_client.base_client import client_registry


async def synthesize_reply_speech(text: str) -> bytes:
    config = config_manager.get_model_config()
    task = config.model_task_config.tts
    if len(task.model_list) != 1:
        raise ValueError("请在语音合成分工中选用一个模型")
    name = task.model_list[0]
    model = next((item for item in config.models if item.name == name), None)
    if model is None or model.model_type != "tts":
        raise ValueError(f"语音合成分工中的 {name} 不是支持的语音合成模型")
    provider = next((item for item in config.api_providers if item.name == model.api_provider), None)
    if provider is None:
        raise ValueError(f"找不到语音合成模型的提供商：{model.api_provider}")
    client = await client_registry.get_client_class_instance_async(provider)
    try:
        audio = await asyncio.wait_for(
            client.synthesize_speech(model.model_identifier, text, model.extra_params.get("voice_id")),
            timeout=task.hard_timeout,
        )
        return audio.data
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"语音合成超过分工设置的 {task.hard_timeout:g} 秒") from exc

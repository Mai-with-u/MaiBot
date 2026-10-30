"""人设能力，沿用 Host 的 manifest 授权检查。"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict

from src.common.logger import get_logger
from src.config.personality_service import current_profile, profile_data, switch_profile

logger = get_logger("plugin_runtime.personality")


class SwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, pattern=r"\S")


async def handle(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from src.config.config import global_config

    if capability == "personality.switch":
        request = SwitchRequest.model_validate(args)
        result = await switch_profile(request.name)
        logger.info(f"插件 {plugin_id} 切换全局人设: {request.name}")
    elif capability == "personality.list":
        if args:
            raise ValueError("personality.list 不接受参数")
        result = {"profiles": [profile_data(profile) for profile in global_config.personality.profiles]}
    elif capability == "personality.get_current":
        if args:
            raise ValueError("personality.get_current 不接受参数")
        result = current_profile(global_config.personality, global_config.chat.reply_style)
    else:
        raise ValueError(f"不支持的人设能力: {capability}")
    return {"success": True, "result": result}

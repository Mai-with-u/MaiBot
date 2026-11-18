"""Bot 内部统一聊天的 Host 能力：插件界面（如桌宠）代主人与麦麦对话。授权由统一 CapabilityService 校验。"""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from src.core.internal_chat import INTERNAL_CHAT_PLATFORM, INTERNAL_CHAT_USER_ID


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=2000)


async def identity(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """返回内部统一聊天的平台与主人用户 ID，供插件识别这条聊天流及麦麦发给主人的消息。"""
    del plugin_id, capability, args
    return {"success": True, "result": {"platform": INTERNAL_CHAT_PLATFORM, "user_id": INTERNAL_CHAT_USER_ID}}


async def send(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """以主人身份向内部统一聊天投递一句话；消息记录插件来源，不具备受保护命令权限。"""
    del capability
    # 聊天模块依赖聊天核心，延迟导入避免 Host 注册能力时形成循环导入。
    from src.webui.routers.chat.service import relay_plugin_chat_message

    request = SendRequest.model_validate(args)
    message_id = await relay_plugin_chat_message(plugin_id, request.text)
    return {"success": True, "result": {"message_id": message_id}}

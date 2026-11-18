"""内置桌宠控制能力；不允许插件执行窗口脚本或改写主人聊天记录。"""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from src.common.runtime_loop import run_on_main_loop
from src.desktop_pet.service import desktop_pet_service


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SayRequest(EmptyRequest):
    text: str = Field(min_length=1, max_length=2000)


class ThinkingRequest(EmptyRequest):
    active: StrictBool


ANIMATION_ACTIONS = ("get_skin", "list_animations", "play_animation", "play_event", "stop_animation", "animation_status")


class AnimationRequest(EmptyRequest):
    name: str = Field(min_length=1, max_length=64)
    repeat: StrictInt = Field(default=1, ge=1, le=30)


class EventRequest(EmptyRequest):
    event: str = Field(pattern="^(click|happy|error|greeting)$")


class PlaybackRequest(EmptyRequest):
    playback_id: str = Field(min_length=1, max_length=64)


async def animation(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    action = capability.removeprefix("desktop_pet.")
    if action in {"get_skin", "list_animations"}:
        request = EmptyRequest.model_validate(args)
    elif action == "play_animation":
        request = AnimationRequest.model_validate(args)
    elif action == "play_event":
        request = EventRequest.model_validate(args)
    elif action in {"stop_animation", "animation_status"}:
        request = PlaybackRequest.model_validate(args)
    else:
        raise ValueError("未知动画能力")
    result = await run_on_main_loop(desktop_pet_service.animation(plugin_id, action, request.model_dump()))
    return {"success": True, "result": result}


async def control(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    del plugin_id
    action = capability.removeprefix("desktop_pet.")
    if action == "say":
        request = SayRequest.model_validate(args)
    elif action == "set_thinking":
        request = ThinkingRequest.model_validate(args)
    elif action in {"status", "show", "hide", "refresh_buttons"}:
        request = EmptyRequest.model_validate(args)
    else:
        raise ValueError(f"未知桌宠能力: {capability}")
    result = await run_on_main_loop(desktop_pet_service.control(action, **request.model_dump()))
    return {"success": True, "result": result}

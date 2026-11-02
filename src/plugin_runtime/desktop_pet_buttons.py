"""桌宠按钮的跨进程协议与插件回调校验。"""

from typing import Any, Dict, List, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

import inspect


class DesktopPetButton(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=32)
    icon: Literal["puzzle", "volume", "settings", "refresh", "star", "heart", "book", "bell"] = "puzzle"
    order: int = Field(default=0, strict=True, ge=-10000, le=10000)


class DesktopPetButtonState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: StrictBool = True
    disabled: StrictBool = False
    text: str = Field(default="", max_length=200)


class DesktopPetButtonSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buttons: List[DesktopPetButton] = Field(default_factory=list, max_length=10)
    states: Dict[str, DesktopPetButtonState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ids(self) -> "DesktopPetButtonSnapshot":
        ids = [button.id for button in self.buttons]
        if len(ids) != len(set(ids)):
            raise ValueError("桌宠按钮 ID 重复")
        if set(self.states) != set(ids):
            raise ValueError("必须为每个桌宠按钮提供状态，且不能包含未声明的按钮")
        return self


class DesktopPetButtonProvider(Protocol):
    desktop_pet_buttons: List[Any]

    async def get_desktop_pet_button_states(self) -> Dict[str, Any]: ...
    async def on_desktop_pet_button_click(self, button_id: str) -> None: ...


def read_button_declarations(instance: object) -> List[DesktopPetButton]:
    # 支持旧版 SDK 插件用字典显式声明这一可选协议，也支持新版 SDK 的类型模型。
    if not hasattr(instance, "desktop_pet_buttons"):
        return []
    raw = cast(DesktopPetButtonProvider, instance).desktop_pet_buttons
    if not isinstance(raw, list) or len(raw) > 10:
        raise ValueError("每个插件最多声明 10 个桌宠按钮，且必须使用列表")
    buttons = [DesktopPetButton.model_validate(item.model_dump() if isinstance(item, BaseModel) else item) for item in raw]
    if len({button.id for button in buttons}) != len(buttons):
        raise ValueError("桌宠按钮 ID 重复")
    if buttons:
        provider = cast(DesktopPetButtonProvider, instance)
        if not inspect.iscoroutinefunction(provider.get_desktop_pet_button_states):
            raise ValueError("get_desktop_pet_button_states 必须为 async 方法")
        if not inspect.iscoroutinefunction(provider.on_desktop_pet_button_click):
            raise ValueError("on_desktop_pet_button_click 必须为 async 方法")
    return buttons


async def read_button_snapshot(provider: DesktopPetButtonProvider) -> DesktopPetButtonSnapshot:
    if not inspect.iscoroutinefunction(provider.get_desktop_pet_button_states):
        raise ValueError("get_desktop_pet_button_states 必须为 async 方法")
    buttons = read_button_declarations(provider)
    states = await provider.get_desktop_pet_button_states()
    if not isinstance(states, dict):
        raise ValueError("桌宠按钮状态必须是字典")
    return DesktopPetButtonSnapshot.model_validate({
        "buttons": [button.model_dump() for button in buttons],
        "states": {key: value.model_dump() if isinstance(value, BaseModel) else value for key, value in states.items()},
    })


async def invoke_button(provider: DesktopPetButtonProvider, snapshot: DesktopPetButtonSnapshot, button_id: str) -> None:
    if button_id not in snapshot.states:
        raise ValueError("未声明的桌宠按钮")
    state = snapshot.states[button_id]
    if not state.visible or state.disabled:
        raise ValueError("当前桌宠按钮不可用")
    if not inspect.iscoroutinefunction(provider.on_desktop_pet_button_click):
        raise ValueError("on_desktop_pet_button_click 必须为 async 方法")
    await provider.on_desktop_pet_button_click(button_id)

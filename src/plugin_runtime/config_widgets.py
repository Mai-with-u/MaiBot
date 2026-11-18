"""配置页运行时控件协议；所有值仅在内存中传输，不参与 TOML 配置。"""

from typing import Any, Dict, List, Literal, Optional, Protocol

import inspect

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigWidget(BaseModel):
    """静态控件声明。id 在插件内唯一；action 只能调用声明的按钮。"""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    kind: Literal["qrcode", "status", "action"]
    label: str = Field(min_length=1, max_length=200)
    confirmation: Optional[str] = Field(default=None, max_length=500)


class ConfigWidgetState(BaseModel):
    """控件最新状态。二维码使用 PNG/JPEG data URL，expires_at 为 Unix 秒。"""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=2000)
    tone: Literal["neutral", "success", "warning", "error"] = "neutral"
    image: Optional[str] = Field(
        default=None, max_length=1_000_000, pattern=r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$"
    )
    expires_at: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    visible: bool = True
    disabled: bool = False


class ConfigWidgetSnapshot(BaseModel):
    """完整快照，不做增量合并，避免保留过期二维码。"""

    model_config = ConfigDict(extra="forbid")
    widgets: List[ConfigWidget] = Field(default_factory=list, max_length=50)
    states: Dict[str, ConfigWidgetState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ids(self) -> "ConfigWidgetSnapshot":
        ids = [widget.id for widget in self.widgets]
        if len(ids) != len(set(ids)):
            raise ValueError("运行时控件 ID 重复")
        if set(self.states) != set(ids):
            raise ValueError("必须为每个运行时控件提供状态，且不能包含未声明的控件")
        return self


class ConfigWidgetProvider(Protocol):
    async def get_config_widget_state(self) -> Dict[str, Any]: ...
    async def on_config_widget_action(self, action_id: str) -> None: ...


async def read_widget_snapshot(provider: ConfigWidgetProvider, widgets: List[Dict[str, Any]]) -> ConfigWidgetSnapshot:
    """校验跨 SDK 版本传来的模型和状态，不读取或修改插件配置。"""
    if not inspect.iscoroutinefunction(provider.get_config_widget_state):
        raise ValueError("get_config_widget_state 必须为 async 方法")
    states = await provider.get_config_widget_state()
    return ConfigWidgetSnapshot.model_validate(
        {
            "widgets": widgets,
            "states": {
                key: value.model_dump() if isinstance(value, BaseModel) else value for key, value in states.items()
            },
        }
    )


async def invoke_widget_action(provider: ConfigWidgetProvider, snapshot: ConfigWidgetSnapshot, action_id: str) -> None:
    """仅执行已声明、可见且未禁用的按钮；拒绝任意方法名调用。"""
    widget = next((item for item in snapshot.widgets if item.id == action_id), None)
    if widget is None or widget.kind != "action":
        raise ValueError("未声明的配置页操作")
    state = snapshot.states[action_id]
    if not state.visible or state.disabled:
        raise ValueError("当前操作不可用")
    if not inspect.iscoroutinefunction(provider.on_config_widget_action):
        raise ValueError("on_config_widget_action 必须为 async 方法")
    await provider.on_config_widget_action(action_id)

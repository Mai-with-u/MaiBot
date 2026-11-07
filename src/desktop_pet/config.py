"""内置桌宠配置。

WebUI 的排布完全由字段元数据声明：
- ``x-row`` + ``x-row-columns``：相邻同名字段排成一行的多列；
- ``x-column-group`` + ``x-column``：相邻字段按列标题分成并排的几列；
- ``x-depends-on``：依赖同级布尔开关，开关关闭时该字段只读；
- ``x-layout``/``x-input-width``：标签在左、控件在右，并限制控件宽度。
"""

from typing import Literal

from src.config.config_base import ConfigBase, Field


def _switch(label: str, **extra: object) -> dict[str, object]:
    return {"x-widget": "switch", "x-layout": "inline-right", "label": {"zh_CN": label}, **extra}


def _number(label: str, **extra: object) -> dict[str, object]:
    return {
        "x-widget": "input",
        "x-layout": "inline-right",
        "x-input-width": "min(100%, 7.5rem)",
        "label": {"zh_CN": label},
        **extra,
    }


_CORE_ROW = {"x-row": "desktop-pet-core", "x-row-columns": 4}
_COLUMNS = {"x-column-group": "desktop-pet-columns"}
_SKIN_COLUMN = {**_COLUMNS, "x-column": "皮肤"}
_WAKE_COLUMN = {**_COLUMNS, "x-column": "主动思考"}
_SCREEN_COLUMN = {**_COLUMNS, "x-column": "读取桌面"}


class DesktopPetConfig(ConfigBase):
    __ui_label__ = "桌宠"
    __ui_order__ = 45

    enabled: bool = Field(default=False, json_schema_extra=_switch("启用桌宠", **_CORE_ROW))
    """启用本机桌宠或接受远端桌宠连接，和网页聊天室共用聊天与昵称；支持热重载。"""

    mode: Literal["local", "remote"] = Field(
        default="local",
        json_schema_extra={
            "x-widget": "select",
            "x-layout": "inline-right",
            "x-input-width": "min(100%, 9rem)",
            "x-option-labels": {"local": "本机", "remote": "远端连接"},
            "label": {"zh_CN": "运行模式"},
            **_CORE_ROW,
        },
    )
    """local 在本机 Windows 启动窗口；remote 等待独立 Windows 客户端连接，服务器可使用 Linux。一次连接一台桌宠。"""

    chat_tools_enabled: bool = Field(default=False, json_schema_extra=_switch("聊天流工具", **_CORE_ROW))
    """允许桌宠与网页共用的主人聊天查询其他聊天流并唤起传话；不影响图片发送。"""

    show_current_task: bool = Field(default=False, json_schema_extra=_switch("显示桌宠手中事", **_CORE_ROW))
    """在桌宠底部显示当前动作的像素字幕；需单独配置 fast_model，未配置时不可启用。"""

    skin_id: str = Field(default="builtin", json_schema_extra={"label": {"zh_CN": "当前皮肤"}, **_SKIN_COLUMN})
    """选择已安装的皮肤 ID；支持热切换，builtin 为内置麦麦。"""

    proactive_wake_enabled: bool = Field(default=False, json_schema_extra=_switch("定期主动思考", **_WAKE_COLUMN))
    """桌宠运行期间定期唤醒主人聊天流，自主探索并选择是否交流；会调用模型，默认关闭。"""

    proactive_wake_interval_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        json_schema_extra=_number("主动思考间隔（分钟）", **_WAKE_COLUMN, **{"x-depends-on": "proactive_wake_enabled"}),
    )
    """每次间隔后触发一次，范围 1～1440 分钟；重启或修改桌宠配置后重新计时，不补跑离线周期。"""

    screen_read_enabled: bool = Field(default=False, json_schema_extra=_switch("允许读取桌面", **_SCREEN_COLUMN))
    """允许主人聊天工具和桌宠按钮截图本机主屏并交给聊天模型理解；截图可能包含屏幕上的私人信息。默认关闭，不授予鼠标键盘控制。"""

    screen_read_interval_seconds: int = Field(
        default=10,
        ge=2,
        le=3600,
        json_schema_extra=_number("屏幕时间线投递间隔（秒）", **_SCREEN_COLUMN, **{"x-depends-on": "screen_read_enabled"}),
    )
    """按此间隔将有变化的关键帧拼图交给模型，最多 6 帧；无明显变化不重复投递。停止或重启后需手动重新开启。"""

    screen_sample_interval_seconds: float = Field(
        default=2,
        ge=0.5,
        le=30,
        json_schema_extra=_number("屏幕差异采样间隔（秒）", **_SCREEN_COLUMN, **{"x-depends-on": "screen_read_enabled"}),
    )
    """截图和比较画面的频率，独立于模型调用；有效采样间隔不超过时间线投递间隔。"""

    screen_change_threshold: float = Field(
        default=0.015,
        ge=0.001,
        le=1,
        json_schema_extra=_number("屏幕变化比例阈值", **_SCREEN_COLUMN, **{"x-depends-on": "screen_read_enabled"}),
    )
    """缩略图中明显变化的像素比例达到阈值才保留，0.015 表示 1.5%；越低越敏感，小面积更新可能被过滤。"""

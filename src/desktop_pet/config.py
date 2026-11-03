"""内置桌宠配置。"""

from src.config.config_base import ConfigBase, Field


class DesktopPetConfig(ConfigBase):
    __ui_label__ = "桌宠"
    __ui_order__ = 45

    enabled: bool = Field(default=False, json_schema_extra={"label": {"zh_CN": "启用桌宠"}})
    """在本机 Windows 桌面显示麦麦，和网页聊天室共用聊天与昵称；支持热重载。"""

    chat_tools_enabled: bool = Field(default=False, json_schema_extra={"label": {"zh_CN": "聊天流工具"}})
    """允许桌宠与网页共用的主人聊天查询其他聊天流并唤起传话；不影响图片发送。"""

    skin_id: str = Field(default="builtin", json_schema_extra={"label": {"zh_CN": "当前皮肤"}})
    """选择已安装的皮肤 ID；支持热切换，builtin 为内置麦麦。"""

    proactive_wake_enabled: bool = Field(
        default=False, json_schema_extra={"label": {"zh_CN": "定期主动思考"}},
    )
    """桌宠运行期间定期唤醒主人聊天流，自主探索并选择是否交流；会调用模型，默认关闭。"""

    proactive_wake_interval_minutes: int = Field(
        default=30, ge=1, le=1440, json_schema_extra={"label": {"zh_CN": "主动思考间隔（分钟）"}},
    )
    """每次间隔后触发一次，范围 1～1440 分钟；重启或修改桌宠配置后重新计时，不补跑离线周期。"""

    screen_read_enabled: bool = Field(
        default=False, json_schema_extra={"label": {"zh_CN": "允许读取桌面"}},
    )
    """允许主人聊天工具和桌宠按钮截图本机主屏并交给聊天模型理解；截图可能包含屏幕上的私人信息。默认关闭，不授予鼠标键盘控制。"""

    screen_read_interval_seconds: int = Field(
        default=10, ge=2, le=3600, json_schema_extra={"label": {"zh_CN": "屏幕时间线投递间隔（秒）"}},
    )
    """按此间隔将有变化的关键帧拼图交给模型，最多 6 帧；无明显变化不重复投递。停止或重启后需手动重新开启。"""

    screen_sample_interval_seconds: float = Field(
        default=2, ge=0.5, le=30, json_schema_extra={"label": {"zh_CN": "屏幕差异采样间隔（秒）"}},
    )
    """截图和比较画面的频率，独立于模型调用；有效采样间隔不超过时间线投递间隔。"""

    screen_change_threshold: float = Field(
        default=0.015, ge=0.001, le=1, json_schema_extra={"label": {"zh_CN": "屏幕变化比例阈值"}},
    )
    """缩略图中明显变化的像素比例达到阈值才保留，0.015 表示 1.5%；越低越敏感，小面积更新可能被过滤。"""

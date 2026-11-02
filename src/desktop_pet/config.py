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

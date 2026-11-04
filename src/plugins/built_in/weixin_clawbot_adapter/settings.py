from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSettings(PluginConfigBase):
    enabled: bool = Field(default=False, description="启用微信连接；启用后在「微信账号管理」中添加账号扫码")
    config_version: str = "0.2.0"


class WeixinSettings(PluginConfigBase):
    allowed_users: List[str] = Field(
        default_factory=list, description="所有账号共享的 iLink 用户 ID 过滤列表；一般留空，允许所有收到的用户"
    )
    retry_seconds: int = Field(default=5, ge=1, le=60, description="连接异常重试间隔（秒）")
    max_image_mb: int = Field(default=5, ge=1, le=8, description="单张图片大小上限（MB）")


class Settings(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    weixin: WeixinSettings = Field(default_factory=WeixinSettings)

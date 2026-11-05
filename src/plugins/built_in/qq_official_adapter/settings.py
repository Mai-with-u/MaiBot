from __future__ import annotations

from typing import ClassVar, List

from maibot_sdk import Field, PluginConfigBase


class PluginSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(default=False, description="启用 QQ 官方机器人连接")
    config_version: str = Field(default="0.4.9", description="配置版本")


class QQOfficialSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "QQ 开放平台"
    __ui_order__: ClassVar[int] = 1

    app_id: str = Field(default="", description="QQ 开放平台机器人的 AppID", json_schema_extra={"label": "AppID"})
    app_secret: str = Field(
        default="",
        description="QQ 开放平台机器人的 AppSecret（ClientSecret）",
        repr=False,
        json_schema_extra={"label": "AppSecret", "x-widget": "password"},
    )
    api_base_url: str = Field(default="https://api.bot.qq.com", description="QQ 开放平台 API 地址")
    unified_account_id: str = Field(
        default="",
        description="统一账号 ID（如 NapCat 机器人 QQ 号）；非空时聊天流按此 ID 归属，与其它 qq 适配器共享上下文",
        json_schema_extra={"label": "关联QQ号"},
    )
    assign_admin_ids: List[str] = Field(
        default_factory=list,
        description="允许执行 /assign_group_id 与 /assign_id 的用户 ID（统一 ID 或 OpenID）；留空不限制",
        json_schema_extra={"label": "QQ Bot管理员账号"},
    )
    silent_user_sync: bool = Field(
        default=True,
        description="在已绑定群中与 SnowLuma 静默匹配 QQ 号、昵称和群名片；需填写关联QQ号，三条不同正文一致后生效",
        json_schema_extra={"label": "静默同步群成员身份"},
    )
    snowluma_identity_only: bool = Field(
        default=False,
        description="官方连接就绪时持续续期，让关联 SL/NapCat 账号仅采集身份，暂停其消息和通知入站投递并撤销出站路由，避免双重发送；15 秒未续期自动恢复收发",
        json_schema_extra={"label": "SL 仅作身份查询（默认关闭）"},
    )


class MuteSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "禁言工具"
    __ui_order__: ClassVar[int] = 2

    enabled: bool = Field(default=True, description="启用 mute 禁言工具，默认开启，关闭时不向模型提供该工具")
    allowed_groups: List[str] = Field(
        default_factory=list,
        description="允许使用禁言的群（统一群号或 group_openid）；留空不限制",
    )
    admin_users: List[str] = Field(
        default_factory=list,
        description="禁言保护名单（统一 QQ 号或 OpenID），名单内用户不会被禁言",
    )
    min_duration: int = Field(default=60, ge=1, description="最短禁言时长（秒）")
    max_duration: int = Field(default=2592000, ge=1, le=2592000, description="最长禁言时长（秒），官方上限 30 天")


class RecallSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "撤回工具"
    __ui_order__: ClassVar[int] = 3

    enabled: bool = Field(default=True, description="启用 recall_message 撤回工具，默认开启，关闭时不向模型提供该工具")
    allowed_groups: List[str] = Field(
        default_factory=list,
        description="允许使用撤回的群（统一群号或 group_openid）；留空不限制，单聊不受此限制",
    )


class AdvancedSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "高级设置"
    __ui_order__: ClassVar[int] = 4

    reconnect_delay_sec: float = Field(
        default=5.0,
        ge=1.0,
        le=300.0,
        description="断线后等待多久重新连接，单位为秒",
        json_schema_extra={"label": "断线重连间隔（秒）"},
    )


class AdapterSettings(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    qq_official: QQOfficialSettings = Field(default_factory=QQOfficialSettings)
    mute: MuteSettings = Field(default_factory=MuteSettings)
    recall: RecallSettings = Field(default_factory=RecallSettings)
    advanced: AdvancedSettings = Field(default_factory=AdvancedSettings)

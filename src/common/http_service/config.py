from typing import List

from src.config.config_base import ConfigBase, Field


class HTTPConfig(ConfigBase):
    """后端 HTTP 监听配置，文件服务和 WebUI 共用。"""

    __ui_label__ = "HTTP 服务"
    __ui_parent__ = "webui"
    __ui_advanced__ = True
    __ui_order__ = 109

    host: List[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1"], min_length=1,
        json_schema_extra={
            "label": {"zh_CN": "监听地址", "en_US": "Listen addresses", "ja_JP": "待受アドレス"},
            "x-widget": "tags",
        },
    )
    """后端监听地址列表，可同时绑定 IPv4 和 IPv6。修改后需重启主程序。"""

    port: int = Field(
        default=8001, ge=1, le=65535,
        json_schema_extra={
            "label": {"zh_CN": "监听端口", "en_US": "Listen port", "ja_JP": "待受ポート"},
            "x-widget": "input",
        },
    )
    """文件传输与 WebUI 共用的 HTTP 端口。修改后需重启主程序；7999 仍仅用于前端开发。"""

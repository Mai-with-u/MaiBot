from typing import List

from src.config.config_base import ConfigBase, Field


class FilesConfig(ConfigBase):
    __ui_label__ = "文件"

    max_file_mib: int = Field(default=50, ge=1, le=2048)
    """单文件大小上限（MiB），按实际接收字节数校验。"""
    max_storage_gib: int = Field(default=10, ge=1)
    """实例文件存储配额（GiB），包含下载、原件与正在上传的预留空间。"""
    max_text_mib: int = Field(default=8, ge=1, le=50)
    """文本解析大小上限（MiB），更大的文件仍可收发与下载。"""
    cache_days: int = Field(default=30, ge=1)
    """平台下载缓存最后访问后保留天数。本地上传和生成的原件不会自动删除。"""
    max_concurrent_transfers: int = Field(default=4, ge=1, le=32)
    """同时上传、下载的数量上限，配额不足时拒绝新增。"""
    transfer_timeout: int = Field(default=120, ge=10, le=3600)
    """单次文件传输的超时秒数。"""
    trusted_download_origins: List[str] = Field(default_factory=list)
    """允许下载的内网适配器来源，填写精确的 http(s)://主机:端口；默认仅允许公网 HTTP(S) 地址。不要填写用户可控制的服务。"""


def files_enabled() -> bool:
    """动态读取总开关，配置热重载后所有入口使用同一个值。"""
    from src.config.config import global_config

    return global_config.capabilities.files

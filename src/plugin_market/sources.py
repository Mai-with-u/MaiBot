"""插件信息源和单个下载源设置；网络错误直接暴露，不自动换源。"""

from os import getenv
from pathlib import Path
from pydantic import BaseModel, model_validator
from threading import RLock
from typing import Any, Dict, Literal, Optional
import asyncio
import json

from src.common.atomic_file import quarantine_file, write_private
from src.common.git_source import GitSource
from src.common.logger import get_logger
from src.common.http_client import get_shared_ssl_context

from .cache import market_cache

SETTINGS_FILE = Path("data/plugin_market.json")
# 官方服务只负责统计与互动（点赞、评分、下载计数）；插件清单和版本索引来自 CNB / GitHub 的 plugin-repo。
MARKET_BASE_URL = getenv("MAIBOT_PLUGIN_MARKET_BASE_URL", "http://hyybuth.xyz:10059").rstrip("/")
CATALOG_TTL = 30 * 60
MARKET_TTL = 5 * 60
REQUEST_TIMEOUT = 8
logger = get_logger("plugin_market")
_settings_lock = RLock()


class PluginSourceSettings(BaseModel):
    information_source: Literal["cnb", "github"] = "cnb"
    download_source: Literal["cnb", "github", "custom"] = "cnb"
    custom_download_prefix: Optional[str] = None

    @model_validator(mode="after")
    def validate_custom_source(self) -> "PluginSourceSettings":
        if self.download_source == "custom" and not self.custom_download_prefix:
            raise ValueError("自定义下载源必须填写 Git 克隆前缀")
        return self


def load_source_settings() -> PluginSourceSettings:
    with _settings_lock:
        if not SETTINGS_FILE.exists():
            return PluginSourceSettings()
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            # 旧开关只表达是否使用 Git 索引；已移除官方信息源，旧设置一律迁移为 CNB 信息源。
            if isinstance(data, dict) and "use_github" in data:
                if not isinstance(data["use_github"], bool):
                    raise ValueError("插件市场 use_github 设置必须为布尔值")
                data = {"information_source": "cnb"}
            if isinstance(data, dict) and data.get("information_source") == "official":
                data = {**data, "information_source": "cnb"}
            return PluginSourceSettings.model_validate(data)
        except ValueError:
            backup = quarantine_file(SETTINGS_FILE)
            logger.warning(f"插件市场源设置损坏，已保留原文件并恢复默认源：{backup}", exc_info=True)
            return PluginSourceSettings()


def save_source_settings(settings: PluginSourceSettings) -> None:
    if settings.custom_download_prefix:
        settings = settings.model_copy(update={
            "custom_download_prefix": GitSource.select("custom", settings.custom_download_prefix).clone_prefix
        })
    with _settings_lock:
        write_private(SETTINGS_FILE, settings.model_dump_json())


def download_source() -> GitSource:
    """下载源与信息源分别配置，通过同一个组件解析。"""
    settings = load_source_settings()
    return GitSource.select(settings.download_source, settings.custom_download_prefix or "")


async def fetch_information_file(
    file_path: str, source: Optional[str] = None, force_refresh: bool = False,
) -> Dict[str, Any]:
    return await asyncio.to_thread(_fetch_information_file, file_path, source, force_refresh)


def _fetch_information_file(file_path: str, source: Optional[str], force_refresh: bool) -> Dict[str, Any]:
    selected = source if source is not None else load_source_settings().information_source
    url = GitSource.select(selected).raw_url("Mai-with-u", "plugin-repo", "main", file_path)

    def load() -> str:
        return GitSource.read_text(url, REQUEST_TIMEOUT, get_shared_ssl_context())

    ttl = MARKET_TTL if file_path == "plugin_versions.json" else CATALOG_TTL
    data, cache = market_cache.get(url, load, ttl, force_refresh)
    logger.debug(f"插件市场索引: source={selected} cache={cache} url={url}")
    return {"success": True, "data": data, "mirror_used": selected, "attempts": 1, "url": url, "cache": cache}

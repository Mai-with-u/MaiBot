"""插件信息源和单个下载源设置；网络错误直接暴露，不自动换源。"""

from os import getenv
from pathlib import Path
from pydantic import BaseModel, model_validator
from threading import RLock
from time import monotonic
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote, urlencode
import asyncio
import json
import os
import tempfile

from src.common.git_source import GitSource
from src.common.logger import get_logger
from src.common.http_client import get_shared_ssl_context

from .cache import market_cache

SETTINGS_FILE = Path("data/plugin_market.json")
MARKET_BASE_URL = getenv("MAIBOT_PLUGIN_MARKET_BASE_URL", "http://hyybuth.xyz:10059").rstrip("/")
CATALOG_TTL = 30 * 60
MARKET_TTL = 5 * 60
REQUEST_TIMEOUT = 8
logger = get_logger("plugin_market")
_settings_lock = RLock()


class PluginSourceSettings(BaseModel):
    information_source: Literal["official", "cnb", "github"] = "official"
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
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        # 旧开关只表达是否使用 Git 索引，开启时迁移为固定的 CNB 信息源。
        if "use_github" in data:
            if not isinstance(data["use_github"], bool):
                raise ValueError("插件市场 use_github 设置必须为布尔值")
            data = {"information_source": "cnb" if data["use_github"] else "official"}
        return PluginSourceSettings.model_validate(data)


def save_source_settings(settings: PluginSourceSettings) -> None:
    from src.common.network_security import validate_public_url

    if settings.custom_download_prefix:
        settings = settings.model_copy(update={
            "custom_download_prefix": validate_public_url(settings.custom_download_prefix, allow_fake_ip=True).rstrip("/")
        })
    with _settings_lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".plugin-market-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(settings.model_dump(), stream)
            os.replace(name, SETTINGS_FILE)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def download_source() -> GitSource:
    """下载源与信息源分别配置，通过同一个组件解析。"""
    settings = load_source_settings()
    return GitSource.select(settings.download_source, settings.custom_download_prefix or "")


async def fetch_information_file(
    file_path: str, source: Optional[str] = None, force_refresh: bool = False,
) -> Dict[str, Any]:
    return await asyncio.to_thread(_fetch_information_file, file_path, source, force_refresh)


def _fetch_information_file(file_path: str, source: Optional[str], force_refresh: bool) -> Dict[str, Any]:
    from src.common.network_security import validate_public_url

    selected = source if source is not None else load_source_settings().information_source
    url = GitSource.select(selected).raw_url("Mai-with-u", "plugin-repo", "main", file_path)

    def load() -> str:
        validate_public_url(url, allow_fake_ip=True)
        return GitSource.read_text(url, REQUEST_TIMEOUT, get_shared_ssl_context())

    ttl = MARKET_TTL if file_path == "plugin_versions.json" else CATALOG_TTL
    data, cache = market_cache.get(url, load, ttl, force_refresh)
    logger.debug(f"插件市场索引: source={selected} cache={cache} url={url}")
    return {"success": True, "data": data, "mirror_used": selected, "attempts": 1, "url": url, "cache": cache}


async def request_market(path: str, params: Dict[str, Any], force_refresh: bool = False) -> Dict[str, Any]:
    url = f"{MARKET_BASE_URL}/api/v2/market/{path}?{urlencode(params)}"

    def load() -> Dict[str, Any]:
        return GitSource.read_json(url, REQUEST_TIMEOUT, get_shared_ssl_context())

    def read() -> Dict[str, Any]:
        started = monotonic()
        try:
            data, cache = market_cache.get(url, load, MARKET_TTL, force_refresh)
        except (OSError, ValueError):
            logger.warning(f"插件市场请求失败: url={url} elapsed={monotonic() - started:.2f}s")
            raise
        logger.debug(f"插件市场请求: url={url} cache={cache} elapsed={monotonic() - started:.2f}s")
        return {**data, "cache": cache, "source_url": url}

    return await asyncio.to_thread(read)


async def request_market_detail(
    plugin_id: str, maibot_version: str, force_refresh: bool = False,
) -> Dict[str, Any]:
    return await request_market(
        f"plugins/{quote(plugin_id, safe='')}", {"maibot_version": maibot_version}, force_refresh,
    )

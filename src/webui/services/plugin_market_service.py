"""插件中心 v2 请求与数据源设置；网络错误直接暴露，不自动切回 GitHub。"""

from os import getenv
from pathlib import Path
from threading import RLock
from typing import Any, Dict
from urllib.parse import quote
import json
import os
import tempfile

import httpx

from src.webui.utils.http_client import get_shared_ssl_context

SETTINGS_FILE = Path("data/plugin_market.json")
MARKET_BASE_URL = getenv("MAIBOT_PLUGIN_MARKET_BASE_URL", "http://hyybuth.xyz:10059").rstrip("/")
_settings_lock = RLock()


def use_github_market_data() -> bool:
    with _settings_lock:
        if not SETTINGS_FILE.exists():
            return False
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        value = data["use_github"]
        if not isinstance(value, bool):
            raise ValueError("插件市场 use_github 设置必须为布尔值")
        return value


def save_market_source(use_github: bool) -> None:
    with _settings_lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".plugin-market-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"use_github": use_github}, stream)
            os.replace(name, SETTINGS_FILE)
        finally:
            if os.path.exists(name):
                os.unlink(name)


async def request_market(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(verify=get_shared_ssl_context(), timeout=20) as client:
        response = await client.get(f"{MARKET_BASE_URL}/api/v2/market/{path}", params=params)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("插件中心返回了无效的数据结构")
    return data


async def request_market_detail(plugin_id: str, maibot_version: str) -> Dict[str, Any]:
    return await request_market(f"plugins/{quote(plugin_id, safe='')}", {"maibot_version": maibot_version})

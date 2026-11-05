"""README 和更新日志的公共读取逻辑，前端不再自行拼接仓库或读取本地路径。"""

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlsplit
import asyncio
import json
import re

from src.common.git_source import GitSource
from src.common.http_client import get_shared_ssl_context
from src.common.network_security import validate_public_url
from src.common.paths import get_instance_root

from .cache import market_cache
from .sources import download_source, load_source_settings

DocumentKind = Literal["readme", "changelog"]
PluginResolver = Callable[[str, bool], Awaitable[Tuple[Dict[str, Any], str]]]


def _find_local_plugin(reference: str) -> Optional[Tuple[Path, Dict[str, Any]]]:
    root = (get_instance_root() / "plugins").resolve()
    if not root.is_dir():
        return None
    matches = []
    for folder in root.iterdir():
        if folder.name.startswith((".", "__")) or not folder.is_dir() or not folder.resolve().is_relative_to(root):
            continue
        manifest_path = folder / "_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("manifest_version", 1) < 2:
            continue
        if reference.casefold() in {folder.name.casefold(), manifest["id"].casefold(), manifest["name"].casefold()}:
            matches.append((folder, manifest))
    if len(matches) > 1:
        raise ValueError("多个本地插件匹配该名称，请指定准确 ID")
    return matches[0] if matches else None


def read_local_document(folder: Path, manifest: Dict[str, Any], kind: DocumentKind) -> Optional[str]:
    declared = manifest.get("changelog") if kind == "changelog" else None
    if declared and ("\n" in declared or declared.startswith(("#", "- "))):
        return declared
    if declared and not declared.startswith(("https://", "http://")):
        path = folder / declared
    else:
        filename = "readme.md" if kind == "readme" else "changelog.md"
        path = next((p for p in folder.iterdir() if p.name.casefold() == filename), None)
    if path is None or not path.is_file():
        return None
    # 所有本地文档（包括 manifest 声明路径与符号链接）都必须留在插件目录内。
    path.resolve().relative_to(folder.resolve())
    return path.read_text(encoding="utf-8")


def _remote_document(
    plugin: Dict[str, Any], kind: DocumentKind, source: str, branch: str, force_refresh: bool,
) -> Dict[str, Any]:
    declared = (plugin.get("changelog") or plugin["manifest"].get("changelog")) if kind == "changelog" else None
    if declared and ("\n" in declared or declared.startswith(("#", "- "))):
        return {"content": declared, "source": "manifest", "source_type": "inline"}
    if kind == "changelog" and not declared:
        return {"content": "", "source": "manifest", "source_type": "missing"}
    if declared and declared.startswith(("https://", "http://")):
        url = declared
    else:
        parsed = urlsplit(plugin["repository_url"])
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not re.fullmatch(r"/[\w.-]+/[\w.-]+/?", parsed.path):
            raise ValueError("插件仓库必须是 https://github.com/owner/repo 地址")
        owner, repo = parsed.path.strip("/").removesuffix(".git").split("/")
        # README 跟随下载源，并保留所选发布版本的提交，避免展示其他版本的文档。
        # 更新日志仍按信息源读取，外部声明的 URL 则直接使用原地址。
        git = download_source() if kind == "readme" else GitSource.select("github" if source == "official" else source)
        url = git.raw_url(owner, repo, branch, declared if kind == "changelog" else "README.md")

    def load() -> str:
        validate_public_url(url, allow_fake_ip=True)
        return GitSource.read_text(url, 8, get_shared_ssl_context())

    try:
        content, cache = market_cache.get(url, load, 1800, force_refresh)
    except HTTPError as exc:
        if exc.code == 404:
            return {"content": "", "source": url, "source_type": "missing"}
        raise
    return {"content": content, "source": url, "source_type": "remote", "cache": cache}


async def get_document(
    plugin_id: str, kind: DocumentKind, resolver: PluginResolver, max_chars: int = 200000,
    prefer_local: bool = True, branch: str = "main", force_refresh: bool = False,
) -> Dict[str, Any]:
    if not plugin_id.strip() or not branch.strip() or not 500 <= max_chars <= 200000:
        raise ValueError("plugin_id 和 branch 不能为空，max_chars 必须在 500 到 200000 之间")
    local = await asyncio.to_thread(_find_local_plugin, plugin_id.strip()) if prefer_local else None
    if local is not None:
        folder, manifest = local
        plugin = {"id": manifest["id"], "name": manifest["name"], "manifest": manifest,
                  "repository_url": manifest.get("urls", {}).get("repository", "")}
        if prefer_local:
            content = await asyncio.to_thread(read_local_document, folder, manifest, kind)
            if content is not None:
                return {"success": True, "plugin": {"id": plugin["id"], "name": plugin["name"]},
                        "content": content[:max_chars], "chars": len(content), "truncated": len(content) > max_chars,
                        "source": str(folder), "source_type": "local"}
        source = (await asyncio.to_thread(load_source_settings)).information_source
    else:
        plugin, source = await resolver(plugin_id, force_refresh)
    result = await asyncio.to_thread(_remote_document, plugin, kind, source, branch, force_refresh)
    content = result["content"]
    return {"success": True, **result, "plugin": {"id": plugin["id"], "name": plugin["name"]},
            "content": content[:max_chars], "chars": len(content), "truncated": len(content) > max_chars}

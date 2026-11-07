"""答疑 Agent 的插件检索、安装准备和配置工具，以及本次请求内的操作记录。"""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List
from urllib.parse import quote
from uuid import uuid4
import asyncio
import httpx

from src.llm_models.payload_content.tool_option import ToolDefinitionInput
from src.plugin_market import service

from .ai_search_models import AISearchInstallRequest, AISearchPlugin, AISearchSource, PluginReference
from .ai_search_plugin_config import (
    PluginConfigPatch,
    PluginConfigStore,
    build_plugin_config_tools,
    list_installed_plugins,
)


PLUGIN_TOOL_NAMES = {
    "lookup_plugins",
    "search_plugins",
    "read_plugin_detail",
    "read_plugin_readme",
    "prepare_plugin_install",
    "list_installed_plugins",
    "read_plugin_config",
    "update_plugin_config",
}
_LOOKUP_LIMIT = 2
_LOOKUP_README_MAX_CHARS = 4000
_LOOKUP_RELEASE_LIMIT = 4


class PluginInstall(PluginReference):
    version: str = Field(
        default="", max_length=100, description="指定兼容发布版本；留空使用推荐版本，分支模式仅支持 latest"
    )


class PluginSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=200, description="功能、名称、作者或分类关键词；空值浏览市场")
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    limit: int = Field(default=6, ge=1, le=10, description="每页返回数量，最多 10 个")
    compatible_only: bool = Field(default=False, description="仅搜索当前 Host 和 SDK 环境兼容的插件")


class PluginLookup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=200, description="插件名称或功能关键词")
    include_readme: bool = Field(default=False, description="同时读取首个合适候选的 README 摘录")


class PluginReadme(PluginReference):
    branch: str = Field(
        default="main", min_length=1, max_length=200, description="远程 README 分支或提交；仅查询指定版本"
    )
    prefer_local: bool = Field(default=True, description="优先读取本地已安装插件的 README")
    max_chars: int = Field(default=8000, ge=500, le=20000, description="最多返回的正文字符数")


def build_market_tools() -> List[ToolDefinitionInput]:
    """安装工具只生成待确认请求，实际安装只能由用户在界面点击触发。"""

    return [
        {
            "name": "lookup_plugins",
            "description": "按名称或功能查找插件，并行核对前两个匹配项的兼容性和推荐版本。询问用法时可附带读取一个 README 摘录。",
            "parameters": PluginLookup.model_json_schema(),
        },
        {
            "name": "search_plugins",
            "description": "按功能搜索或浏览 MaiBot 插件市场，返回真实插件 ID、摘要及来源。具体兼容性须查询详情。",
            "parameters": PluginSearch.model_json_schema(),
        },
        {
            "name": "read_plugin_detail",
            "description": "读取插件详情、依赖、Host/SDK 要求、各发布版本兼容原因及推荐版本；兼容性以服务返回值为准。",
            "parameters": PluginReference.model_json_schema(),
        },
        {
            "name": "read_plugin_readme",
            "description": "读取插件安装、配置和使用说明。默认优先读取本地已安装版本；远程只读指定分支，不切换分支。",
            "parameters": PluginReadme.model_json_schema(),
        },
        {
            "name": "prepare_plugin_install",
            "description": "用户要求安装时生成安装确认卡片，核对真实插件与兼容版本；此工具不会安装。必须等待用户手动点击确认，不能通过工具或用户文字替代按钮确认。",
            "parameters": PluginInstall.model_json_schema(),
        },
    ] + build_plugin_config_tools()


def _requirements(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """保留每个版本各自的依赖与兼容要求，避免将最新版要求套用到推荐的旧版本。"""

    return {key: manifest[key] for key in ("host_application", "sdk", "dependencies") if key in manifest}


class AISearchMarketStore:
    """每次答疑单独持有查询记录，供最终插件 ID 和引用来源校验。"""

    def __init__(self) -> None:
        self.plugins: Dict[str, AISearchPlugin] = {}
        self.sources: Dict[str, AISearchSource] = {}
        self.used = False
        self.install_requests: Dict[str, AISearchInstallRequest] = {}
        self.config = PluginConfigStore()

    def select_plugins(self, plugin_ids: List[str]) -> List[AISearchPlugin]:
        """卡片只允许引用本次查询中的真实插件，忽略模型捏造的 ID 并保持排序。"""

        return [self.plugins[pid] for pid in dict.fromkeys(plugin_ids) if pid in self.plugins]

    def _record_plugin(self, plugin: Dict[str, Any]) -> AISearchPlugin:
        card = self.plugins.get(plugin["id"])
        if card is None:
            card = AISearchPlugin(
                id=plugin["id"],
                name=plugin["name"],
                description=plugin.get("description", ""),
                version=plugin.get("version", ""),
            )
            self.plugins[card.id] = card
        else:
            card.name = plugin["name"]
            if "description" in plugin:
                card.description = plugin["description"]
            if "version" in plugin:
                card.version = plugin["version"]
        return card

    def _record_source(self, plugin: Dict[str, Any], kind: str, url: str | None = None) -> str:
        source_id = f"plugin:{kind}:{plugin['id']}"
        # 本地 README 的文件路径不作为链接暴露，统一导航到对应的商店详情。
        self.sources[source_id] = AISearchSource(
            title=f"{plugin['name']} · {'README' if kind == 'readme' else '市场'}",
            url=url if url is not None else f"/plugins?pluginId={quote(plugin['id'], safe='')}",
        )
        return source_id

    async def _lookup_plugins(self, request: PluginLookup) -> Dict[str, Any]:
        """一次完成短名单查找、兼容性核验和可选文档读取。"""

        search = await self._search_plugins(PluginSearch(query=request.query, limit=_LOOKUP_LIMIT))

        async def summarize(candidate: Dict[str, Any]) -> Dict[str, Any]:
            try:
                detail = await self._read_plugin_detail(candidate["id"])
            except (OSError, ValueError, httpx.HTTPError) as exc:
                # 详情取不到时保留市场摘要，不能把未核验的插件说成不兼容。
                return {**candidate, "compatibility": "unknown", "recommended_version": None, "detail_error": str(exc)}
            releases = detail["releases"]
            versions = releases["versions"]
            shown_versions = versions[:_LOOKUP_RELEASE_LIMIT]
            recommended = releases["recommended_version"]
            # 推荐版可能比最新的几版更旧，摘要仍需保留它的真实版本资料。
            shown_versions.extend(
                version for version in versions[_LOOKUP_RELEASE_LIMIT:] if version["version"] == recommended
            )
            return {
                **detail["plugin"],
                "source_id": detail["source_id"],
                "requirements": detail["requirements"],
                "versions": shown_versions,
                "versions_truncated": len(versions) > len(shown_versions),
                "sync_error": releases["sync_error"],
            }

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(summarize(candidate)) for candidate in search["plugins"]]
        plugins = [task.result() for task in tasks]
        result = {**search, "plugins": plugins}
        if not request.include_readme:
            return result

        selected = next((plugin for plugin in plugins if plugin["compatibility"] == "compatible"), None)
        if selected is None:
            selected = next((plugin for plugin in plugins if plugin["compatibility"] == "unknown"), None)
        if selected is None:
            return result

        # 已核验的发布版使用真实标签，分支模式或未知版本明确读取 main。
        recommended = selected["recommended_version"]
        ref = "main"
        if recommended is not None:
            ref = next(version["tag"] for version in selected["versions"] if version["version"] == recommended)
        try:
            result["readme"] = await self._read_plugin_readme(
                PluginReadme(plugin_id=selected["id"], branch=ref, max_chars=_LOOKUP_README_MAX_CHARS)
            )
        except (OSError, ValueError, httpx.HTTPError) as exc:
            result["readme_error"] = str(exc)
        return result

    async def execute(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.used = True
        if name == "lookup_plugins":
            return await self._lookup_plugins(PluginLookup.model_validate(arguments))
        if name == "list_installed_plugins":
            if arguments:
                raise ValueError("list_installed_plugins 不接受参数")
            return await list_installed_plugins()
        if name == "read_plugin_config":
            return await self._read_plugin_config(PluginReference.model_validate(arguments).plugin_id)
        if name == "update_plugin_config":
            return await self.config.update(PluginConfigPatch.model_validate(arguments))
        if name == "prepare_plugin_install":
            return await self._prepare_install(PluginInstall.model_validate(arguments))
        if name == "search_plugins":
            return await self._search_plugins(PluginSearch.model_validate(arguments))
        if name == "read_plugin_detail":
            return await self._read_plugin_detail(PluginReference.model_validate(arguments).plugin_id)
        if name == "read_plugin_readme":
            return await self._read_plugin_readme(PluginReadme.model_validate(arguments))
        raise ValueError(f"未知插件工具: {name}")

    async def _read_plugin_config(self, plugin_id: str) -> Dict[str, Any]:
        try:
            return await self.config.read(plugin_id)
        except HTTPException:
            # 配置解析可以在未加载时成功；仅在解析被拒绝时补充已记录的加载诊断。
            installed = await list_installed_plugins()
            plugin = next((item for item in installed["plugins"] if item["id"] == plugin_id), None)
            if plugin is None or plugin["load_status"] not in {"failed", "timed_out"}:
                raise
            return {"plugin": plugin, "message": "当前无法读取插件配置，请根据 load_status 和 load_error 说明加载情况"}

    async def _prepare_install(self, request: PluginInstall) -> Dict[str, Any]:
        result = await service.get_detail(request.plugin_id)
        plugin, releases = result["plugin"], result["releases"]
        installed = await list_installed_plugins()
        if any(item["id"] == plugin["id"] for item in installed["plugins"]):
            raise ValueError("插件已经安装，请直接读取并修改插件配置")
        if releases["sync_error"]:
            raise ValueError(f"插件版本同步失败: {releases['sync_error']}")
        if releases["mode"] == "branch":
            if not releases["branch_compatible"] or request.version not in {"", "latest"}:
                raise ValueError("分支不兼容或指定了不存在的发布版本")
            version = "latest"
        else:
            version = request.version or releases["recommended_version"]
            release = next((item for item in releases["versions"] if item["version"] == version), None)
            if release is None or not release["compatible"]:
                raise ValueError("没有指定的兼容发布版本")
        key = f"{plugin['id']}:{version}"
        proposal = self.install_requests.get(key)
        if proposal is None:
            proposal = AISearchInstallRequest(
                id=uuid4().hex,
                plugin_id=plugin["id"],
                name=plugin["name"],
                version=version,
                repository_url=plugin["repository_url"],
                mode=releases["mode"],
            )
            self.install_requests[key] = proposal
        self._record_plugin(plugin)
        return {
            "plugin": {"id": plugin["id"], "name": plugin["name"]},
            "install_request": proposal.model_dump(),
            "message": "等待用户在界面手动确认，尚未安装",
        }

    async def _search_plugins(self, request: PluginSearch) -> Dict[str, Any]:
        result = await service.search(service.MarketSearch(**request.model_dump()))
        plugins = []
        for plugin in result["plugins"]:
            card = self._record_plugin(plugin)
            plugins.append({**card.model_dump(), "source_id": self._record_source(plugin, "detail")})
        return {
            "query": request.query,
            "plugins": plugins,
            "matched": result["matched"],
            "page": result["page"],
            "pagination": result["pagination"],
            "source": result["source"],
            "source_url": result["source_url"],
            "cache": result["cache"],
        }

    async def _read_plugin_detail(self, plugin_id: str) -> Dict[str, Any]:
        result = await service.get_detail(plugin_id)
        plugin, releases = result["plugin"], result["releases"]
        card = self._record_plugin(plugin)
        card.recommended_version = releases["recommended_version"]
        if releases["mode"] == "branch":
            compatible = releases["branch_compatible"]
            reasons = releases["branch_reasons"]
        else:
            compatible = bool(card.recommended_version)
            reasons = (
                list(dict.fromkeys(reason for version in releases["versions"] for reason in version["reasons"]))
                if not compatible
                else []
            )
            if releases["sync_error"]:
                reasons.append(releases["sync_error"])
        card.compatibility = "compatible" if compatible else "incompatible"
        card.reasons = reasons
        # 保留依赖与兼容要求，去掉版本列表中重复的完整清单以控制模型上下文。
        manifest = plugin["manifest"]
        return {
            "plugin": card.model_dump(),
            "source_id": self._record_source(plugin, "detail"),
            "requirements": _requirements(manifest),
            "releases": {
                **releases,
                "versions": [
                    {
                        **{key: value for key, value in version.items() if key != "manifest"},
                        "requirements": _requirements(version["manifest"]),
                    }
                    for version in releases["versions"]
                ],
            },
            "source": result["source"],
            "source_url": result["source_url"],
            "cache": result["cache"],
        }

    async def _read_plugin_readme(self, request: PluginReadme) -> Dict[str, Any]:
        result = await service.get_readme(**request.model_dump())
        if not result["content"].strip():
            raise ValueError(f"插件 {result['plugin']['id']} 未找到 README 正文")
        plugin = result["plugin"]
        self._record_plugin(plugin)
        # 不将本机绝对路径送入模型；正文来源仍明确标注本地/远程与截断状态。
        return {
            "plugin": plugin,
            "content": result["content"],
            "chars": result["chars"],
            "truncated": result["truncated"],
            "source_type": result["source_type"],
            "source_id": self._record_source(
                plugin,
                "readme",
                result["source"] if result["source_type"] == "remote" else None,
            ),
            "source": result["source"] if result["source_type"] != "local" else "local",
            "branch": request.branch if result["source_type"] != "local" else None,
        }

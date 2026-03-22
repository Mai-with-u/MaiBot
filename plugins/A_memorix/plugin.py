"""A_Memorix SDK plugin entry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from maibot_sdk import MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel


def _tool_param(name: str, param_type: ToolParamType, description: str, required: bool) -> ToolParameterInfo:
    return ToolParameterInfo(name=name, param_type=param_type, description=description, required=required)


_ADMIN_TOOL_PARAMS = [
    _tool_param("action", ToolParamType.STRING, "管理动作", True),
    _tool_param("target", ToolParamType.STRING, "可选目标标识", False),
]


class AMemorixPlugin(MaiBotPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._plugin_root = Path(__file__).resolve().parent
        self._plugin_config: Dict[str, Any] = {}
        self._kernel: Optional[SDKMemoryKernel] = None

    def set_plugin_config(self, config: Dict[str, Any]) -> None:
        self._plugin_config = config or {}
        if self._kernel is not None:
            self._kernel.close()
            self._kernel = None

    async def on_load(self):
        await self._get_kernel()

    async def on_unload(self):
        if self._kernel is not None:
            shutdown = getattr(self._kernel, "shutdown", None)
            if callable(shutdown):
                await shutdown()
            else:
                self._kernel.close()
            self._kernel = None

    async def _get_kernel(self) -> SDKMemoryKernel:
        if self._kernel is None:
            self._kernel = SDKMemoryKernel(plugin_root=self._plugin_root, config=self._plugin_config)
            await self._kernel.initialize()
        return self._kernel

    async def _dispatch_admin_tool(self, method_name: str, action: str, **kwargs):
        kernel = await self._get_kernel()
        handler = getattr(kernel, method_name)
        return await handler(action=action, **kwargs)

    @Tool(
        "search_memory",
        description="搜索长期记忆",
        parameters=[
            _tool_param("query", ToolParamType.STRING, "查询文本", False),
            _tool_param("limit", ToolParamType.INTEGER, "返回条数", False),
            _tool_param("mode", ToolParamType.STRING, "search/time/hybrid/episode/aggregate", False),
            _tool_param("chat_id", ToolParamType.STRING, "聊天流 ID", False),
            _tool_param("person_id", ToolParamType.STRING, "人物 ID", False),
            _tool_param("time_start", ToolParamType.FLOAT, "起始时间戳", False),
            _tool_param("time_end", ToolParamType.FLOAT, "结束时间戳", False),
            _tool_param("respect_filter", ToolParamType.BOOLEAN, "是否应用聊天过滤配置", False),
        ],
    )
    async def handle_search_memory(
        self,
        query: str = "",
        limit: int = 5,
        mode: str = "search",
        chat_id: str = "",
        person_id: str = "",
        time_start: str | float | None = None,
        time_end: str | float | None = None,
        respect_filter: bool = True,
        **kwargs,
    ):
        kernel = await self._get_kernel()
        return await kernel.search_memory(
            KernelSearchRequest(
                query=query,
                limit=limit,
                mode=mode,
                chat_id=chat_id,
                person_id=person_id,
                time_start=time_start,
                time_end=time_end,
                respect_filter=respect_filter,
                user_id=str(kwargs.get("user_id", "") or "").strip(),
                group_id=str(kwargs.get("group_id", "") or "").strip(),
            )
        )

    @Tool(
        "ingest_summary",
        description="写入聊天摘要到长期记忆",
        parameters=[
            _tool_param("external_id", ToolParamType.STRING, "外部幂等 ID", True),
            _tool_param("chat_id", ToolParamType.STRING, "聊天流 ID", True),
            _tool_param("text", ToolParamType.STRING, "摘要文本", True),
            _tool_param("time_start", ToolParamType.FLOAT, "起始时间戳", False),
            _tool_param("time_end", ToolParamType.FLOAT, "结束时间戳", False),
            _tool_param("respect_filter", ToolParamType.BOOLEAN, "是否应用聊天过滤配置", False),
        ],
    )
    async def handle_ingest_summary(
        self,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[List[str]] = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        **kwargs,
    ):
        kernel = await self._get_kernel()
        return await kernel.ingest_summary(
            external_id=external_id,
            chat_id=chat_id,
            text=text,
            participants=participants,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=metadata,
            respect_filter=respect_filter,
            user_id=str(kwargs.get("user_id", "") or "").strip(),
            group_id=str(kwargs.get("group_id", "") or "").strip(),
        )

    @Tool(
        "ingest_text",
        description="写入普通长期记忆文本",
        parameters=[
            _tool_param("external_id", ToolParamType.STRING, "外部幂等 ID", True),
            _tool_param("source_type", ToolParamType.STRING, "来源类型", True),
            _tool_param("text", ToolParamType.STRING, "原始文本", True),
            _tool_param("chat_id", ToolParamType.STRING, "聊天流 ID", False),
            _tool_param("timestamp", ToolParamType.FLOAT, "时间戳", False),
            _tool_param("respect_filter", ToolParamType.BOOLEAN, "是否应用聊天过滤配置", False),
        ],
    )
    async def handle_ingest_text(
        self,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[List[str]] = None,
        participants: Optional[List[str]] = None,
        timestamp: float | None = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        **kwargs,
    ):
        relations = kwargs.get("relations")
        entities = kwargs.get("entities")
        kernel = await self._get_kernel()
        return await kernel.ingest_text(
            external_id=external_id,
            source_type=source_type,
            text=text,
            chat_id=chat_id,
            person_ids=person_ids,
            participants=participants,
            timestamp=timestamp,
            time_start=time_start,
            time_end=time_end,
            tags=tags,
            metadata=metadata,
            entities=entities,
            relations=relations,
            respect_filter=respect_filter,
            user_id=str(kwargs.get("user_id", "") or "").strip(),
            group_id=str(kwargs.get("group_id", "") or "").strip(),
        )

    @Tool(
        "get_person_profile",
        description="获取人物画像",
        parameters=[
            _tool_param("person_id", ToolParamType.STRING, "人物 ID", True),
            _tool_param("chat_id", ToolParamType.STRING, "聊天流 ID", False),
            _tool_param("limit", ToolParamType.INTEGER, "证据条数", False),
        ],
    )
    async def handle_get_person_profile(self, person_id: str, chat_id: str = "", limit: int = 10, **kwargs):
        _ = kwargs
        kernel = await self._get_kernel()
        return await kernel.get_person_profile(person_id=person_id, chat_id=chat_id, limit=limit)

    @Tool(
        "maintain_memory",
        description="维护长期记忆关系状态",
        parameters=[
            _tool_param("action", ToolParamType.STRING, "reinforce/protect/restore/freeze/recycle_bin", True),
            _tool_param("target", ToolParamType.STRING, "目标哈希或查询文本", False),
            _tool_param("hours", ToolParamType.FLOAT, "保护时长（小时）", False),
            _tool_param("limit", ToolParamType.INTEGER, "查询条数（用于 recycle_bin）", False),
        ],
    )
    async def handle_maintain_memory(
        self,
        action: str,
        target: str = "",
        hours: float | None = None,
        reason: str = "",
        limit: int = 50,
        **kwargs,
    ):
        _ = kwargs
        kernel = await self._get_kernel()
        return await kernel.maintain_memory(action=action, target=target, hours=hours, reason=reason, limit=limit)

    @Tool("memory_stats", description="获取长期记忆统计", parameters=[])
    async def handle_memory_stats(self, **kwargs):
        _ = kwargs
        kernel = await self._get_kernel()
        return kernel.memory_stats()

    @Tool("memory_graph_admin", description="长期记忆图谱管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_graph_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_graph_admin", action=action, **kwargs)

    @Tool("memory_source_admin", description="长期记忆来源管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_source_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_source_admin", action=action, **kwargs)

    @Tool("memory_episode_admin", description="Episode 管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_episode_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_episode_admin", action=action, **kwargs)

    @Tool("memory_profile_admin", description="人物画像管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_profile_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_profile_admin", action=action, **kwargs)

    @Tool("memory_runtime_admin", description="长期记忆运行时管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_runtime_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_runtime_admin", action=action, **kwargs)

    @Tool("memory_import_admin", description="长期记忆导入管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_import_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_import_admin", action=action, **kwargs)

    @Tool("memory_tuning_admin", description="长期记忆调优管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_tuning_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_tuning_admin", action=action, **kwargs)

    @Tool("memory_v5_admin", description="长期记忆 V5 管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_v5_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_v5_admin", action=action, **kwargs)

    @Tool("memory_delete_admin", description="长期记忆删除管理接口", parameters=_ADMIN_TOOL_PARAMS)
    async def handle_memory_delete_admin(self, action: str, **kwargs):
        return await self._dispatch_admin_tool("memory_delete_admin", action=action, **kwargs)


def create_plugin():
    return AMemorixPlugin()

"""联网服务配置，作为 model_config.toml 的独立 web 配置节。"""

from typing import Any, Dict, List, Literal
from urllib.parse import urlsplit

from src.config.config_base import ConfigBase, Field


class WebServiceConfig(ConfigBase):
    adapter: Literal["deepseek", "tavily", "stepsearch_mcp"] = "deepseek"
    """服务协议类型，不需要配置模型列表。"""

    base_url: str = ""
    """HTTP API 根地址；留空使用适配器的官方地址。"""

    api_key: str = Field(default="", repr=False)
    """HTTP API 密钥。MCP 服务的鉴权由已有 MCP 连接管理。"""

    server_ref: str = ""
    """StepSearch 对应的 bot_config.toml 中 mcp.servers 的名称。"""

    timeout: float = Field(default=60.0, gt=0, le=300)
    """单次联网调用的总时限，单位秒。"""

    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    """Tavily 搜索深度，仅对 tavily 生效。"""

    extract_depth: Literal["basic", "advanced"] = "basic"
    """Tavily 网页提取深度，仅对 tavily 生效。"""

    category: Literal["", "programming", "research", "gov", "business"] = ""
    """StepSearch 搜索类别；留空不传递类别。"""

    def model_post_init(self, context: Any = None) -> None:
        if self.base_url:
            parts = urlsplit(self.base_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError("联网服务 base_url 必须是无用户名和密码的 HTTP(S) 地址")
            if parts.query or parts.fragment:
                raise ValueError("联网服务 base_url 不能包含查询参数或片段")
        if self.adapter == "stepsearch_mcp" and (self.api_key or self.base_url):
            raise ValueError("StepSearch MCP 请使用 server_ref，地址和密钥在 MCP 连接中配置")
        super().model_post_init(context)


class WebConfig(ConfigBase):
    preview_chars: int = Field(default=4000, ge=500, le=12000)
    """单次返回的内容预览字数，完整内容通过 read_tool_result 读取。"""

    services: Dict[str, WebServiceConfig] = Field(default_factory=dict)
    """联网服务定义；仅添加服务不会启用工具，需要在功能分配中选择。"""

    def model_post_init(self, context: Any = None) -> None:
        for name in self.services:
            if not name.strip() or name != name.strip():
                raise ValueError("联网服务名称不能为空或包含首尾空格")
        super().model_post_init(context)


class WebSearchTaskConfig(ConfigBase):
    model_list: List[str] = Field(default_factory=list, max_length=1)
    """选择一个 web.services 中的联网服务名称；留空不开放统一联网工具。"""


def resolve_web_search_config(config: WebConfig, task: WebSearchTaskConfig) -> "WebRuntimeConfig":
    """只有功能分配能启用统一联网工具，服务定义本身不授予使用权限。"""
    selected = task.model_list[0] if task.model_list else ""
    if selected and selected not in config.services:
        raise ValueError(f"web_search 引用了不存在的联网服务：{selected}，请先取消该功能分配")
    return WebRuntimeConfig(
        enabled=bool(selected), search=selected, fetch=selected,
        preview_chars=config.preview_chars, services=config.services,
    )


class WebRuntimeConfig(WebConfig):
    """由功能分配生成的调用快照，不作为持久化配置的启用入口。"""

    enabled: bool = False
    """启用统一 web_search / web_fetch 工具；默认关闭，不影响原始 MCP 工具。"""

    search: str = ""
    """搜索使用的 services 条目名；留空不提供统一搜索工具。"""

    fetch: str = ""
    """网页读取使用的 services 条目名；留空不提供统一读取工具。"""

    def model_post_init(self, context: Any = None) -> None:
        for operation in ("search", "fetch"):
            selected = self.search if operation == "search" else self.fetch
            if selected and selected not in self.services:
                raise ValueError(f"web.{operation} 引用了不存在的服务：{selected}")
            if self.enabled and selected:
                service = self.services[selected]
                if service.adapter == "stepsearch_mcp" and not service.server_ref.strip():
                    raise ValueError(f"联网服务 {selected} 缺少 server_ref")
                if service.adapter != "stepsearch_mcp" and not service.api_key.strip():
                    raise ValueError(f"联网服务 {selected} 缺少 api_key")
        if self.enabled and not (self.search or self.fetch):
            raise ValueError("启用联网功能时至少需要选择一个 search 或 fetch 服务")
        super().model_post_init(context)

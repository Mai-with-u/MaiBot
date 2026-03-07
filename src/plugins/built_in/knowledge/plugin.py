"""LPMM 知识库搜索插件 — 新 SDK 版本

提供 LLM 可调用的知识库搜索工具。
"""

from maibot_sdk import MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType


class KnowledgePlugin(MaiBotPlugin):
    """LPMM 知识库插件"""

    @Tool(
        "lpmm_search_knowledge",
        description="从知识库中搜索相关信息，如果你需要知识，就使用这个工具",
        parameters=[
            ToolParameterInfo(name="query", param_type=ToolParamType.STRING, description="搜索查询关键词", required=True),
            ToolParameterInfo(name="limit", param_type=ToolParamType.INTEGER, description="希望返回的相关知识条数，默认5", required=False, default=5),
        ],
    )
    async def handle_lpmm_search_knowledge(self, query: str = "", limit: int = 5, **kwargs):
        """执行知识库搜索"""
        if not query:
            return {"type": "info", "id": "", "content": "未提供搜索关键词"}

        try:
            limit_value = max(1, int(limit))
        except (TypeError, ValueError):
            limit_value = 5

        result = await self.ctx.call_capability("knowledge.search", query=query, limit=limit_value)
        if result and result.get("success"):
            content = result.get("content", f"你不太了解有关{query}的知识")
            return {"type": "lpmm_knowledge", "id": query, "content": content}
        return {"type": "info", "id": query, "content": f"知识库搜索失败: {result}"}


def create_plugin():
    return KnowledgePlugin()

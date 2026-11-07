"""插件联网能力，复用统一联网服务的配置、参数校验和错误语义。"""

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict
import json

from src.core.tooling import ToolInvocation
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.web_search.config import WebRuntimeConfig, WebSearchConfigError, resolve_web_search_config
from src.web_search.models import WebSearchError
from src.web_search.service import WebSearchService

MAX_RESULT_CHARS = 500000


def get_config() -> WebRuntimeConfig:
    from src.config.config import model_config

    return resolve_web_search_config(model_config.web, model_config.model_task_config.web_search)


def get_service() -> WebSearchService:
    from src.mcp_module.service import get_mcp_service

    return WebSearchService(get_mcp_service())


async def call(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """返回完整结构化结果；授权由 CapabilityService 处理，不创建聊天或暂存结果。"""
    tool_name = {"web.search": "web_search", "web.fetch": "web_fetch"}[capability]
    # 固定本次调用的配置，避免热重载在请求中途替换服务或凭据。
    config = get_config().model_copy(deep=True)
    invocation = ToolInvocation(tool_name=tool_name, arguments=args, metadata={"plugin_id": plugin_id})
    try:
        result = await get_service().execute(config, invocation)
    except WebSearchConfigError as exc:
        raise RPCError(ErrorCode.E_WEB_SERVICE_CONFIG_INVALID, str(exc)) from exc
    payload = asdict(result)
    # 不静默截断正文；限制序列化结果，给 RPC 帧和协议包装留出空间。
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_RESULT_CHARS:
        raise WebSearchError("联网结果超过 500000 字符的传输限制，请缩小查询范围")
    return {
        "success": True,
        "result": {
            **payload,
            "service": config.search if capability == "web.search" else config.fetch,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        },
    }

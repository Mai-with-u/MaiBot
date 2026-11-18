"""有大小限制的异步 JSON 请求，不重试、不切换供应商。"""

from typing import Any, Dict, Optional
import asyncio
import json

import httpx

from src.common.http_client import get_shared_ssl_context_async

from ..config import WebServiceConfig
from ..models import WebSearchError

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class HTTPAdapter:
    def __init__(self, config: WebServiceConfig, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.config = config
        self.transport = transport

    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.api_key.strip():
            raise WebSearchError("联网服务缺少 API Key")
        # 每次调用拥有自己的客户端，避免 bot 与 WebUI 的事件循环共享连接池。
        try:
            async with httpx.AsyncClient(
                verify=await get_shared_ssl_context_async(),
                timeout=self.config.timeout, transport=self.transport, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=self.auth_headers(),
                ) as response:
                    if not response.is_success:
                        raise WebSearchError(f"联网服务返回 HTTP {response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise WebSearchError("联网服务响应超过 8 MiB 限制")
        except httpx.TimeoutException:
            raise WebSearchError("联网服务请求超时") from None
        except httpx.HTTPError as exc:
            # 异常字符串可能带有完整 URL 或代理凭据，只向调用方暴露错误类型。
            raise WebSearchError(f"联网服务连接失败：{type(exc).__name__}") from None
        try:
            result = await asyncio.to_thread(json.loads, data)
        except (ValueError, UnicodeError):
            raise WebSearchError("联网服务返回了无效 JSON") from None
        if not isinstance(result, dict):
            raise WebSearchError("联网服务响应必须是 JSON 对象")
        if "error" in result:
            raise WebSearchError("联网服务返回业务错误")
        return result

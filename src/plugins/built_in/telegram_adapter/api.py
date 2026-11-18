"""异步 Telegram Bot API 客户端；错误信息不包含 Token 或代理凭据。"""

from ssl import SSLError
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote, urlsplit

import asyncio
import json
import re

import httpx


class TelegramAPIError(RuntimeError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(f"Telegram API {code}: {description}")
        self.code = code


class TelegramTransportError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, base_url: str, proxy: str, poll_timeout: int, max_bytes: int) -> None:
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("Telegram Token 格式无效")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("Bot API 地址必须是无凭据、无查询参数的 HTTP(S) 地址")
        if proxy and urlsplit(proxy).scheme not in {"http", "https", "socks5", "socks5h"}:
            raise ValueError("代理仅支持 HTTP(S)/SOCKS5")
        self.token = token
        self.base = base_url.rstrip("/")
        self.max_bytes = max_bytes
        self.http = httpx.AsyncClient(
            proxy=proxy or None,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(max(poll_timeout + 15, 60), connect=15),
        )

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<token>")[:300]

    async def close(self) -> None:
        await self.http.aclose()

    async def call(
        self,
        method: str,
        body: Optional[Dict[str, Any]] = None,
        upload: Optional[Union[Tuple[str, bytes, str, str], List[Tuple[str, bytes, str, str]]]] = None,
    ) -> Any:
        if not re.fullmatch(r"[A-Za-z]+", method):
            raise ValueError("无效的 Telegram API 方法名")
        url = f"{self.base}/bot{self.token}/{method}"
        payload = body or {}
        # 仅在服务端明确拒绝执行（429）时重试；网络超时无法确认发送结果，不能盲目重发。
        for attempt in range(3):
            try:
                if upload is None:
                    response = await self.http.post(url, json=payload)
                else:
                    uploads = upload if isinstance(upload, list) else [upload]
                    fields = {
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list, bool))
                        else str(value)
                        for key, value in payload.items()
                    }
                    files = {field: (filename, binary, mime) for field, binary, filename, mime in uploads}
                    response = await self.http.post(url, data=fields, files=files)
            except (httpx.RequestError, SSLError) as exc:
                # 底层 TLS 读写异常可能未经 HTTPX 包装；统一交给接收循环重连，不在此重发请求。
                raise TelegramTransportError(f"Telegram 网络请求失败（{type(exc).__name__}）") from None
            try:
                result = response.json()
            except ValueError:
                raise TelegramAPIError(response.status_code, "响应不是 JSON") from None
            if not isinstance(result, dict):
                raise TelegramAPIError(response.status_code, "响应格式无效")
            if response.is_success and result.get("ok") is True:
                return result["result"]
            code = int(result.get("error_code", response.status_code))
            delay = result.get("parameters", {}).get("retry_after")
            if code == 429 and isinstance(delay, int) and 0 < delay <= 300 and attempt < 2:
                await asyncio.sleep(delay)
                continue
            raise TelegramAPIError(code, self._redact(str(result.get("description", "请求失败"))))
        raise RuntimeError("Telegram API 重试次数耗尽")

    async def download(self, file_id: str) -> bytes:
        file = await self.call("getFile", {"file_id": file_id})
        if int(file.get("file_size", 0)) > self.max_bytes:
            raise ValueError("Telegram 媒体超过配置的下载上限")
        path = file.get("file_path")
        # 本地 Bot API 必须以远端 HTTP 文件路径运行；不读取服务器返回的任意本地文件。
        if (
            not isinstance(path, str)
            or not path
            or path.startswith(("/", "\\"))
            or any(piece == ".." for piece in path.split("/"))
        ):
            raise ValueError("Telegram 未返回有效的相对文件路径；本地 Bot API 的 --local 模式暂不支持")
        url = f"{self.base}/file/bot{self.token}/{quote(path, safe='/')}"
        try:
            async with self.http.stream("GET", url) as response:
                if not response.is_success:
                    raise TelegramAPIError(response.status_code, "媒体下载失败")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > self.max_bytes:
                        raise ValueError("Telegram 媒体超过配置的下载上限")
                    chunks.extend(chunk)
        except (httpx.RequestError, SSLError) as exc:
            # 流式读取中的 TLS 错误也属于连接故障，不能当成坏消息确认更新。
            raise TelegramTransportError(f"Telegram 媒体下载失败（{type(exc).__name__}）") from None
        if not chunks:
            raise ValueError("Telegram 下载的媒体为空")
        return bytes(chunks)

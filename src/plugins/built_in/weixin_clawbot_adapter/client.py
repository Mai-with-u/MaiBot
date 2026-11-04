"""Small independent iLink protocol client (no AstrBot runtime dependency)."""

from typing import Any
from urllib.parse import urlencode, urlsplit

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

import asyncio
import base64
import hashlib
import secrets

import aiohttp

BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


class APIError(RuntimeError):
    def __init__(self, code: int, endpoint: str) -> None:
        self.code = code
        # Do not include response bodies: they can contain account credentials.
        super().__init__(f"微信接口 {endpoint} 返回错误码 {code}")


def trusted_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not host.endswith(".weixin.qq.com")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("微信返回了非受信任的服务地址")
    return url.rstrip("/")


def decode_key(value: str) -> bytes:
    raw = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    if len(raw) == 32:
        raw = bytes.fromhex(raw.decode("ascii"))
    if len(raw) != 16:
        raise ValueError("微信图片密钥格式无效")
    return raw


class Client:
    def __init__(self, base_url: str = BASE_URL, token: str = "", max_bytes: int = 5 * 1024**2) -> None:
        self.base_url = trusted_url(base_url)
        self.token = token
        self.max_bytes = max_bytes
        self.session: aiohttp.ClientSession | None = None

    async def http(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        return self.session

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def call(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
        login: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
        }
        if login:
            headers["iLink-App-ClientVersion"] = "1"
        else:
            if not self.token:
                raise RuntimeError("微信尚未登录")
            headers["Authorization"] = f"Bearer {self.token}"
        body = None if payload is None else {"base_info": {"channel_version": "maibot"}, **payload}
        session = await self.http()
        async with session.request(
            "GET" if login else "POST",
            f"{self.base_url}/ilink/bot/{endpoint}",
            params=params,
            json=body,
            headers=headers,
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise APIError(response.status, endpoint)
            data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise ValueError("微信接口响应必须为对象")
        code = int(data.get("errcode") or data.get("ret") or 0)
        if code:
            raise APIError(code, endpoint)
        return data

    async def updates(self, cursor: str) -> dict[str, Any]:
        try:
            return await self.call("getupdates", {"get_updates_buf": cursor})
        except asyncio.TimeoutError:
            # An idle long poll is not a lost connection or a reason to clear login state.
            return {"msgs": [], "get_updates_buf": cursor}

    async def send(self, user: str, context: str, items: list[dict[str, Any]], client_id: str) -> None:
        if not context:
            raise ValueError("缺少会话凭证，请先在微信给机器人发送一条消息")
        await self.call(
            "sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": user,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context,
                    "item_list": items,
                }
            },
        )

    async def download_image(self, item: dict[str, Any]) -> bytes:
        media = item.get("media") or {}
        query = media.get("encrypt_query_param")
        if not query:
            raise ValueError("微信图片缺少下载参数")
        url = CDN_URL + "/download?" + urlencode({"encrypted_query_param": query})
        session = await self.http()
        chunks = bytearray()
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise APIError(response.status, "cdn/download")
            async for chunk in response.content.iter_chunked(65536):
                chunks.extend(chunk)
                if len(chunks) > self.max_bytes + 16:
                    raise ValueError("图片超过大小限制")
        data = bytes(chunks)
        key = (
            bytes.fromhex(item["aeskey"])
            if item.get("aeskey")
            else (decode_key(media["aes_key"]) if media.get("aes_key") else None)
        )
        if key is not None:
            data = await asyncio.to_thread(lambda: unpad(AES.new(key, AES.MODE_ECB).decrypt(data), 16))
        if len(data) > self.max_bytes:
            raise ValueError("图片超过大小限制")
        return data

    async def upload_image(self, user: str, data: bytes) -> dict[str, Any]:
        if not data or len(data) > self.max_bytes:
            raise ValueError("图片为空或超过大小限制")
        key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
        encrypted = await asyncio.to_thread(lambda: AES.new(key, AES.MODE_ECB).encrypt(pad(data, 16)))
        result = await self.call(
            "getuploadurl",
            {
                "filekey": filekey,
                "media_type": 1,
                "to_user_id": user,
                "rawsize": len(data),
                "rawfilemd5": hashlib.md5(data).hexdigest(),
                "filesize": len(encrypted),
                "no_need_thumb": True,
                "aeskey": key.hex(),
            },
        )
        if result.get("upload_full_url"):
            url = trusted_url(result["upload_full_url"])
        elif result.get("upload_param"):
            url = (
                CDN_URL + "/upload?" + urlencode({"encrypted_query_param": result["upload_param"], "filekey": filekey})
            )
        else:
            raise ValueError("微信未返回图片上传地址")
        session = await self.http()
        async with session.post(
            url, data=encrypted, headers={"Content-Type": "application/octet-stream"}, allow_redirects=False
        ) as response:
            if response.status != 200:
                raise APIError(response.status, "cdn/upload")
            query = response.headers.get("x-encrypted-param")
            if not query:
                raise ValueError("微信图片上传响应缺少下载参数")
        return {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": query,
                    "aes_key": base64.b64encode(key.hex().encode()).decode(),
                    "encrypt_type": 1,
                },
                "mid_size": len(encrypted),
            },
        }

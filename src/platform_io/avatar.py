"""独立于消息载荷的头像服务：适配器查询、图片缓存和过期刷新。"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple
from urllib.parse import urlsplit
from uuid import uuid4
import asyncio
import json
import time

from httpx import AsyncClient
from PIL import Image

from src.common.http_client import get_shared_ssl_context_async
from src.common.paths import get_instance_root

AvatarTargetType = Literal["user", "group"]
AvatarStatus = Literal["available", "unsupported", "missing"]
AVATAR_CACHE_ROOT = get_instance_root() / "data" / "avatar"
MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_CACHE_TTL = 86400
AVATAR_NEGATIVE_TTL = 300
SUPPORTED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}


@dataclass(frozen=True)
class AvatarResult:
    """头像状态；图片路径只供 Host 使用，不进入 SDK 或消息载荷。"""

    status: AvatarStatus
    expires_at: float
    url: Optional[str] = None
    path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "url": self.url, "expires_at": self.expires_at}


def validate_avatar_target(platform: str, target_id: str, target_type: str) -> Tuple[str, str]:
    platform = platform.strip().lower()
    target_id = target_id.strip()
    if not platform or not target_id or len(platform) > 128 or len(target_id) > 512:
        raise ValueError("头像平台和目标 ID 必须非空，且不能超过长度限制")
    if target_type not in {"user", "group"}:
        raise ValueError("头像 target_type 必须为 user 或 group")
    return platform, target_id


class AvatarService:
    """同一个目标的并发请求合并；所有入口在主事件循环调用本服务。"""

    def __init__(self) -> None:
        self._pending: Dict[str, asyncio.Task[AvatarResult]] = {}

    async def get_avatar(
        self,
        platform: str,
        target_id: str,
        target_type: AvatarTargetType = "user",
        *,
        account_id: str = "",
        scope: str = "",
        force_refresh: bool = False,
    ) -> AvatarResult:
        platform, target_id = validate_avatar_target(platform, target_id, target_type)
        account_id = account_id.strip()
        scope = scope.strip()
        # 使用完整路由和目标类型作键，避免多账号、群/用户以及特殊字符互相污染缓存。
        key = sha256(json.dumps([platform, account_id, scope, target_type, target_id]).encode()).hexdigest()
        if not force_refresh:
            cached = await asyncio.to_thread(self._read_cache, key)
            if cached is not None:
                return cached
        pending = self._pending.get(key)
        if pending is None:
            pending = asyncio.create_task(self._resolve(key, platform, target_id, target_type, account_id, scope))
            self._pending[key] = pending
            pending.add_done_callback(lambda task: self._finish(key, task))
        return await asyncio.shield(pending)

    def _finish(self, key: str, task: asyncio.Task[AvatarResult]) -> None:
        self._pending.pop(key, None)
        # 请求取消后后台刷新仍会完成，取出异常以免产生未处理任务警告。
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _read_cache(key: str) -> Optional[AvatarResult]:
        metadata_path = AVATAR_CACHE_ROOT / "remote" / f"{key}.json"
        if not metadata_path.is_file():
            return None
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if data["expires_at"] <= time.time():
            return None
        status = data["status"]
        if status not in {"available", "unsupported", "missing"}:
            raise ValueError("头像缓存状态不合法")
        path = None
        if status == "available":
            suffix = data["suffix"]
            if suffix not in SUPPORTED_FORMATS.values():
                raise ValueError("头像缓存图片格式不合法")
            path = metadata_path.with_suffix(suffix)
            if not path.is_file():
                return None
        return AvatarResult(status=status, url=data["url"], expires_at=data["expires_at"], path=path)

    @staticmethod
    def _write_cache(key: str, result: AvatarResult, image_bytes: Optional[bytes]) -> None:
        cache_dir = AVATAR_CACHE_ROOT / "remote"
        cache_dir.mkdir(parents=True, exist_ok=True)
        if result.path is not None and image_bytes is not None:
            temporary_path = cache_dir / f"{key}.{uuid4().hex}.tmp"
            try:
                temporary_path.write_bytes(image_bytes)
                temporary_path.replace(result.path)
            finally:
                temporary_path.unlink(missing_ok=True)
        metadata_path = cache_dir / f"{key}.json"
        temporary_path = cache_dir / f"{key}.{uuid4().hex}.tmp"
        try:
            data = result.to_dict()
            data["suffix"] = result.path.suffix if result.path is not None else None
            temporary_path.write_text(json.dumps(data), encoding="utf-8")
            temporary_path.replace(metadata_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        # 格式变化或头像消失时，删除上一版图片，避免残留文件被误用。
        for suffix in SUPPORTED_FORMATS.values():
            old_path = metadata_path.with_suffix(suffix)
            if old_path != result.path:
                old_path.unlink(missing_ok=True)

    @staticmethod
    async def _download(url: str) -> bytes:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("适配器头像 URL 必须为不含凭据的 HTTP(S) 地址")
        image_bytes = bytearray()
        async with AsyncClient(verify=await get_shared_ssl_context_async(), timeout=10, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"User-Agent": "MaiBot-Avatar/1.0"}) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    image_bytes.extend(chunk)
                    if len(image_bytes) > MAX_AVATAR_BYTES:
                        raise ValueError("头像图片不能超过 5 MB")
        if not image_bytes:
            raise ValueError("头像图片为空")
        return bytes(image_bytes)

    @staticmethod
    def _image_suffix(image_bytes: bytes) -> str:
        with Image.open(BytesIO(image_bytes)) as image:
            suffix = SUPPORTED_FORMATS.get(image.format)
            if suffix is None:
                raise ValueError("头像仅支持 JPG、PNG、WebP、GIF 或 BMP")
            image.verify()
        return suffix

    async def _resolve(
        self,
        key: str,
        platform: str,
        target_id: str,
        target_type: AvatarTargetType,
        account_id: str,
        scope: str,
    ) -> AvatarResult:
        from src.plugin_runtime.avatar_provider import query_adapter_avatar

        source = await query_adapter_avatar(platform, target_id, target_type, account_id=account_id, scope=scope)
        status = source.get("status")
        if status not in {"available", "unsupported", "missing"}:
            raise ValueError("适配器必须返回 available、unsupported 或 missing 头像状态")
        image_bytes = None
        path = None
        url = None
        ttl = AVATAR_NEGATIVE_TTL
        if status == "available":
            url = source.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError("可用头像必须提供 URL")
            source_ttl = source.get("expires_in", AVATAR_CACHE_TTL)
            if isinstance(source_ttl, bool) or not isinstance(source_ttl, (int, float)) or not source_ttl > 0:
                raise ValueError("头像 expires_in 必须大于 0")
            ttl = min(source_ttl, AVATAR_CACHE_TTL)
            image_bytes = await self._download(url)
            suffix = await asyncio.to_thread(self._image_suffix, image_bytes)
            path = AVATAR_CACHE_ROOT / "remote" / f"{key}{suffix}"
        result = AvatarResult(status=status, url=url, path=path, expires_at=time.time() + ttl)
        await asyncio.to_thread(self._write_cache, key, result, image_bytes)
        return result


avatar_service = AvatarService()

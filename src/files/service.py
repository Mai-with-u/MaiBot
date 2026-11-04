"""独立于 Agent 和电脑的附件服务；所有归属来自真实聊天流。"""

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, TypeVar
from urllib.parse import urlsplit

from aiohttp import ClientError

import asyncio
import hashlib
import json
import secrets
import threading
import time

from src.common.runtime_context import runtime_context

from .config import files_enabled
from .network import download_chunks
from .models import FileAsset
from .store import FileStore

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession
    from src.chat.message_receive.message import SessionMessage

T = TypeVar("T")


async def disk_call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    # 取消协程时先等待正在写盘的线程结束，再允许清理同一附件。
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class FileService:
    def __init__(self, store: FileStore, *, enabled_provider: Callable[[], bool],
                 default_base_url: str = "http://127.0.0.1:8001") -> None:
        self.store = store
        self._enabled_provider = enabled_provider
        self.default_base_url = default_base_url
        self._lock = threading.Lock()
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self._active_readers = 0

    @property
    def enabled(self) -> bool:
        return self._enabled_provider()

    def require_enabled(self) -> None:
        if not self.enabled:
            raise PermissionError("文件功能已关闭，请在能力设置中开启“文件功能”")

    def acquire_read(self) -> bool:
        """使用实时并发上限；调低时允许已有读取结束，期间不再接纳超额读取。"""
        with self._lock:
            if self._active_readers >= self.store.config.max_concurrent_transfers:
                return False
            self._active_readers += 1
            return True

    def release_read(self) -> None:
        with self._lock:
            if self._active_readers <= 0:
                raise RuntimeError("文件读取名额重复释放")
            self._active_readers -= 1

    @staticmethod
    def require_session(session_id: str) -> "BotChatSession":
        from src.chat.message_receive.chat_manager import chat_manager

        session = chat_manager.get_session_by_session_id(session_id)
        if session is None:
            raise ValueError("聊天流不存在")
        return session

    def ticket(self, purpose: str, *, lifetime: int = 300, **claims) -> str:
        self.require_enabled()
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._tickets = {key: value for key, value in self._tickets.items() if value["expires"] > now}
            if len(self._tickets) >= 1024:
                raise RuntimeError("文件传输凭证数量已满")
            self._tickets[token] = {**claims, "purpose": purpose, "expires": now + lifetime}
        return token

    def claims(self, token: str, purpose: str) -> Dict[str, Any]:
        with self._lock:
            claims = self._tickets.get(token)
            if claims is None or claims["expires"] <= time.time() or claims["purpose"] != purpose:
                raise PermissionError("文件凭证无效或已过期")
            return dict(claims)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tickets.pop(token, None)

    async def cancel_upload_ticket(self, token: str, scope: str, plugin_id: str) -> None:
        with self._lock:
            claims = self._tickets.get(token)
            if claims is None:
                # HTTP 已消费的凭证由接收端负责完成或清理。
                return
            if claims["purpose"] != "upload" or claims["scope"] != scope or claims["plugin_id"] != plugin_id:
                raise PermissionError("上传凭证不属于当前调用")
            self._tickets.pop(token)
        asset = await disk_call(self.store.get, claims["file_ref"], None, admin=True)
        await disk_call(self.store.fail, asset.file_ref, asset.transfer_id, "插件已取消上传")

    def download_url(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> str:
        self.require_enabled()
        self.store.lease(file_ref, session_id, admin=admin)
        token = self.ticket("download", file_ref=file_ref)
        return self.transfer_url(token)

    def transfer_url(self, token: str) -> str:
        # 对外地址由宿主统一下发，实例文件配额与下载白名单仍各自独立。
        base = self.default_base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("宿主 public_base_url 必须是无凭证、无查询参数的 HTTP(S) 地址")
        # 公开地址属于所有 Bot 共用的宿主，文件凭证必须显式路由到所属 Bot。
        return f"{base}/api/files/bots/{runtime_context().bot_id}/transfers/{token}"

    async def resolve_url(self, source: Dict[str, Any]) -> str:
        from src.platform_io import get_platform_io_manager
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        driver = get_platform_io_manager().driver_registry.get(source["driver_id"])
        if driver is None or driver.descriptor.plugin_id != source["plugin_id"]:
            raise RuntimeError("接收此文件的适配器不可用")
        descriptor = driver.descriptor
        if ((descriptor.account_id or "") != source["account_id"] or
                (descriptor.scope or "") != source["scope"] or descriptor.platform != source["platform"]):
            raise PermissionError("文件来源账号、平台或作用域与适配器不一致")
        for supervisor in get_plugin_runtime_manager().supervisors:
            entry = supervisor.api_registry.get_api(source["plugin_id"], "adapter.files.resolve", version="1")
            if entry is None:
                continue
            response = await supervisor.invoke_api(plugin_id=source["plugin_id"], component_name=entry.handler_name,
                                                   args={"source": source}, timeout_ms=30000)
            if response.error or not response.payload.get("success"):
                raise RuntimeError("适配器解析文件失败")
            result = response.payload["result"]
            if not isinstance(result, dict) or not isinstance(result.get("url"), str):
                raise RuntimeError("适配器没有返回有效下载地址")
            return result["url"]
        raise RuntimeError("适配器尚未实现文件解析能力")

    async def ensure(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> FileAsset:
        self.require_enabled()
        asset = await disk_call(self.store.get, file_ref, session_id, admin=admin)
        if asset.state == "ready":
            await disk_call(self.store.touch, file_ref, session_id, admin=admin)
            return asset
        asset = await disk_call(self.store.begin_download, file_ref, session_id, admin=admin)
        if asset.state == "ready":
            return asset
        transfer = asset.transfer_id
        try:
            async with asyncio.timeout(self.store.config.transfer_timeout):
                url = await self.resolve_url(json.loads(asset.source))
                async for chunk in download_chunks(url, self.store.config.trusted_download_origins,
                                                   self.store.config.transfer_timeout):
                    self.require_enabled()
                    await disk_call(self.store.append, file_ref, transfer, chunk)
                return await disk_call(self.store.finish, file_ref, transfer)
        except BaseException as exc:
            # URL、凭证和平台内部错误不写入公开的附件索引。
            await disk_call(self.store.fail, file_ref, transfer, f"下载失败: {type(exc).__name__}")
            if isinstance(exc, (ClientError, TimeoutError)):
                raise RuntimeError(f"附件下载失败: {type(exc).__name__}，可稍后重新请求") from exc
            raise

    async def create(self, session_id: str, name: str, content: bytes, mime_type: str = "application/octet-stream") -> FileAsset:
        self.require_enabled()
        self.require_session(session_id)
        if len(content) > self.store.config.max_file_mib * 1024 ** 2:
            raise ValueError("文件超过单文件上限")
        asset = await disk_call(self.store.begin_upload, session_id, name, mime_type)
        transfer = asset.transfer_id
        try:
            await disk_call(self.store.append, asset.file_ref, transfer, content)
            return await disk_call(self.store.finish, asset.file_ref, transfer)
        except BaseException as exc:
            await disk_call(self.store.fail, asset.file_ref, transfer, type(exc).__name__)
            raise

    def read_text(self, file_ref: str, session_id: Optional[str], *, admin: bool = False,
                  offset: int = 0, limit: int = 12000, encoding: str = "utf-8-sig") -> Dict[str, Any]:
        self.require_enabled()
        if not self.acquire_read():
            raise RuntimeError("文件读取并发数已满")
        try:
            return self._read_text(file_ref, session_id, admin=admin, offset=offset, limit=limit, encoding=encoding)
        finally:
            self.release_read()

    def _read_text(self, file_ref: str, session_id: Optional[str], *, admin: bool,
                   offset: int, limit: int, encoding: str) -> Dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 32000:
            raise ValueError("文本分页参数无效")
        if encoding not in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            raise ValueError("不支持的文本编码")
        asset = self.store.get(file_ref, session_id, admin=admin)
        if asset.size > self.store.config.max_text_mib * 1024 ** 2:
            raise ValueError(f"文本解析超过 {self.store.config.max_text_mib} MiB 上限，仍可下载原文件")
        if Path(asset.name).suffix.lower() not in (".txt", ".md", ".markdown", ".csv", ".json"):
            raise ValueError("第一期仅解析 TXT、Markdown、CSV、JSON 文本")
        try:
            data = self.store.read_bytes(file_ref, session_id, admin=admin)
            text = data.decode(encoding)
            if "\x00" in text:
                raise ValueError("文件包含二进制内容")
            self.store.record_parse(file_ref, encoding)
            return {"file_ref": file_ref, "text": text[offset:offset + limit], "offset": offset,
                    "next_offset": min(offset + limit, len(text)), "total_characters": len(text),
                    "untrusted_content": True}
        except (UnicodeError, ValueError) as exc:
            self.store.record_parse(file_ref, encoding, str(exc))
            raise

    async def send(self, file_ref: str, session_id: str, *, admin: bool = False) -> Dict[str, Any]:
        from src.services.send_service import custom_to_stream_with_message

        self.require_session(session_id)
        asset = await self.ensure(file_ref, session_id, admin=admin)
        if admin:
            await disk_call(self.store.grant, file_ref, session_id)
        message = await custom_to_stream_with_message("file", self.store.public(asset), session_id,
                                                      sync_to_maisaka_history=True)
        # 发送超时可能已经到达平台，不能盲目重试而产生重复附件。
        return {"file_ref": file_ref, "status": "sent" if message is not None else "unknown",
                "message_id": message.message_id if message is not None else "",
                "retry_automatically": False}

    async def ingest(self, message: "SessionMessage") -> None:
        if not self.enabled:
            return

        from src.common.data_models.message_component_data_model import FileComponent

        self.require_session(message.session_id)
        for component in message.raw_message.components:
            if not isinstance(component, FileComponent):
                continue
            previous_text = component.to_plain_text()
            if component.file_ref:
                asset = await disk_call(self.store.get, component.file_ref, message.session_id)
            else:
                source = dict(message.file_source or {})
                if not source.get("plugin_id") or not component.file_id:
                    continue
                original_files = source.pop("files")
                if (component.file_id, component.busid) not in original_files:
                    raise PermissionError("附件定位信息已被入站 Hook 修改")
                group = message.message_info.group_info
                if source["group_id"] != (group.group_id if group else "") or source["user_id"] != message.message_info.user_info.user_id:
                    raise PermissionError("附件来源聊天已被入站 Hook 修改")
                source.update(file_id=component.file_id, busid=component.busid)
                key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
                asset = await disk_call(self.store.register_remote, message.session_id, message.message_id,
                                        component.name, int(component.size or 0), component.mime_type, source, key)
            component.file_ref, component.name, component.size = asset.file_ref, asset.name, str(asset.size)
            component.url, component.base64_data = "", ""
            if previous_text in message.processed_plain_text:
                message.processed_plain_text = message.processed_plain_text.replace(previous_text, component.to_plain_text(), 1)
            else:
                message.processed_plain_text += "\n" + component.to_plain_text()


@lru_cache(maxsize=1)
def get_file_service() -> FileService:
    from src.common.database.database import engine, initialize_database
    from src.common.paths import get_instance_root
    from src.config.config import global_config

    initialize_database()
    return FileService(FileStore(get_instance_root() / "data" / "files", engine, global_config.files,
                                 config_provider=lambda: global_config.files),
                       default_base_url=runtime_context().public_base_url, enabled_provider=files_enabled)


async def periodic_file_cleanup() -> None:
    from src.common.logger import get_logger

    logger = get_logger("files.cleanup")
    while True:
        await asyncio.sleep(60)
        try:
            await disk_call(get_file_service().store.cleanup)
        except Exception:
            logger.exception("文件清理失败，保留文件并在下一次巡检重试")

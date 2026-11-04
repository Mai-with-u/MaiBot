"""QQ 官方扫码绑定协议。

依据 @tencent-connect/qqbot-connector 1.2.0 的公开协议独立实现：
create_bind_task → poll_bind_result → AES-256-GCM 解密。
参考 https://github.com/tencent-connect/openclaw-qqbot/blob/main/src/setup/login.ts
"""

from __future__ import annotations

from aiohttp import ClientSession, ClientTimeout
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from io import BytesIO
from pathlib import Path
from qrcode.image.pil import PilImage
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlencode

import asyncio
import base64
import os
import tempfile
import time

import qrcode
import tomlkit


def render_qr(url: str) -> str:
    """本地生成二维码，不将授权链接发送到第三方图片服务。"""
    image = qrcode.make(url, image_factory=PilImage, border=4)
    output = BytesIO()
    image.save(output)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def decrypt_secret(encrypted: str, key: bytes) -> str:
    """官方密文为 12 字节 IV + ciphertext + 16 字节 GCM tag。"""
    data = base64.b64decode(encrypted, validate=True)
    if len(key) != 32 or len(data) <= 28:
        raise ValueError("扫码凭据密文格式无效")
    secret = AESGCM(key).decrypt(data[:12], data[12:], None).decode("utf-8")
    if not secret.strip():
        raise ValueError("扫码返回了空凭据")
    return secret


def save_credentials(path: Path, app_id: str, secret: str) -> Dict[str, Any]:
    """保留现有配置与注释，原子保存凭据；失败时不替换原配置。"""
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document.setdefault("qq_official", tomlkit.table())
    document.setdefault("plugin", tomlkit.table())
    document["qq_official"]["app_id"] = app_id
    document["qq_official"]["app_secret"] = secret
    document["plugin"]["enabled"] = True
    fd, temporary = tempfile.mkstemp(prefix=".qq-login-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(tomlkit.dumps(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return document.unwrap()


class QRLogin:
    """仅在服务端保留临时 AES 密钥和授权凭据，公开快照不包含它们。"""

    def __init__(self, apply: Callable[[str, str], Awaitable[None]]) -> None:
        self._apply = apply
        self._task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self.state = "idle"
        self.message = "点击扫码登录，使用手机 QQ 授权机器人。"
        self.image = ""
        self.app_id = ""
        self._deadline = 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self.state, "message": self.message, "image": self.image, "app_id": self.app_id,
            "expires_at": time.time() + max(0, self._deadline - time.monotonic()) if self.image else None,
        }

    async def start(self) -> Dict[str, Any]:
        async with self._lock:
            await self._cancel()
            self.state, self.message = "creating", "正在生成二维码…"
            self.image, self.app_id = "", ""
            self._task = asyncio.create_task(self._run(), name="qq-official-qr-login")
            return self.snapshot()

    async def cancel(self) -> Dict[str, Any]:
        async with self._lock:
            await self._cancel()
            return self.snapshot()

    async def _cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            # 一旦进入原子保存阶段，等待写入及连接切换完成，避免取消后仍写入凭据。
            if self.state != "saving":
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                self.state, self.message = "cancelled", "已取消扫码登录。"
        self._task = None
        self.image = ""

    async def _post(self, session: ClientSession, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with session.post(f"https://q.qq.com/lite/{endpoint}", json=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError(f"QQ 扫码接口 HTTP {response.status}")
            body = await response.json()
        if not isinstance(body, dict) or body.get("retcode") != 0:
            # 不回显远端消息或响应，防止凭据进入页面、日志。
            raise RuntimeError("QQ 扫码接口返回失败")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ValueError("QQ 扫码接口返回格式无效")
        return data

    async def _run(self) -> None:
        stage = "创建绑定任务"
        try:
            key = os.urandom(32)
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                data = await self._post(session, "create_bind_task", {"key": base64.b64encode(key).decode("ascii")})
                task_id = data.get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    raise ValueError("QQ 未返回绑定任务 ID")
                url = "https://q.qq.com/qqbot/openclaw/connect.html?" + urlencode({"task_id": task_id, "source": "maibot", "_wv": "2"})
                self._deadline = time.monotonic() + 300
                self.image = await asyncio.to_thread(render_qr, url)
                self.state, self.message = "waiting", "请使用手机 QQ 扫码并完成授权。"
                stage = "查询扫码结果"
                while time.monotonic() < self._deadline:
                    result = await self._post(session, "poll_bind_result", {"task_id": task_id})
                    status = result.get("status")
                    if status == 2:
                        app_id = str(result.get("bot_appid", ""))
                        encrypted = result.get("bot_encrypt_secret")
                        if not app_id.isascii() or not app_id.isdigit() or not isinstance(encrypted, str):
                            raise ValueError("QQ 返回的机器人凭据不完整")
                        stage = "解密授权凭据"
                        secret = decrypt_secret(encrypted, key)
                        self.state, self.message, self.image = "saving", "授权成功，正在保存并连接…", ""
                        stage = "保存凭据并启动连接"
                        await self._apply(app_id, secret)
                        self.app_id = app_id
                        self.state, self.message = "success", "凭据已保存，已启动连接。"
                        return
                    if status == 3:
                        break
                    if status not in (0, 1):
                        raise ValueError("QQ 返回了未知绑定状态")
                    await asyncio.sleep(2)
                self.state, self.message = "expired", "二维码已过期，请重新扫码。"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = "error"
            # 错误类型提供排查线索，异常内容可能包含响应或敏感配置，禁止直接回传。
            detail = str(exc) if type(exc) is RuntimeError and str(exc).startswith("QQ 扫码接口") else type(exc).__name__
            self.message = f"{stage}失败（{detail}）。请检查网络或配置文件写入权限后重试。"
        finally:
            self.image = ""

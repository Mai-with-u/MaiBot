from contextlib import suppress
from typing import TYPE_CHECKING, Any, Dict, Optional

import asyncio
import base64
import io
import secrets

import aiohttp
import qrcode

from .client import APIError, Client, trusted_url
from .codec import PLATFORM, inbound, message_id, outbound
from .storage import Store

if TYPE_CHECKING:
    from .plugin import WeixinClawbotPlugin
    from .settings import Settings


def qr_image(content: str) -> str:
    output = io.BytesIO()
    qrcode.make(content).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


class AccountConnection:
    """每个账号独占轮询、HTTP 会话和发送锁，互不覆盖路由与登录态。"""

    def __init__(self, owner: "WeixinClawbotPlugin", slot: int, store: Store, gateway_name: str) -> None:
        self.owner = owner
        self.slot = slot
        self.store = store
        self.gateway_name = gateway_name
        self.client: Optional[Client] = None
        self.worker: Optional[asyncio.Task[None]] = None
        self.action_task: Optional[asyncio.Task[None]] = None
        self.send_lock = asyncio.Lock()
        self.ready = False
        self.status = "等待扫码"
        self.qr: Optional[str] = None
        self.expired = False

    @property
    def config(self) -> "Settings":
        return self.owner.config

    @property
    def ctx(self):
        return self.owner.ctx

    def storage(self) -> Store:
        return self.store

    def nickname(self, user: str) -> str:
        state = self.store.state
        # 旧登录态没有扫码用户字段时，仅在已知对端唯一时使用账号备注。
        bound_user = state["user_id"]
        if not bound_user and len(state["contexts"]) == 1:
            bound_user = next(iter(state["contexts"]))
        return state["remark"] if user == bound_user and state["remark"] else user

    def start(self, login: bool = False) -> None:
        self.expired = False
        if not self.config.plugin.enabled:
            self.status = "未启用：请先打开插件配置中的启用开关"
        elif login or self.store.state["token"]:
            self.worker = asyncio.create_task(self.run(login), name=f"weixin-clawbot-{self.slot}")
        else:
            self.status = "等待扫码或重新登录"

    async def set_ready(self, ready: bool) -> None:
        accepted = await self.ctx.gateway.update_state(
            gateway_name=self.gateway_name,
            ready=ready,
            platform=PLATFORM,
            account_id=self.store.state["account_id"],
            metadata={"protocol": "weixin_ilink", "account_name": self.store.state["remark"]},
        )
        self.ready = ready and accepted
        if ready and not accepted:
            raise RuntimeError("麦麦未接受微信网关状态")

    async def stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
            self.worker = None
        # In-flight sends finish before closing their HTTP session or clearing state.
        async with self.send_lock:
            await self.set_ready(False)
            if self.client is not None:
                await self.client.close()
                self.client = None
        self.qr = None

    async def login(self, client: Client) -> bool:
        for _ in range(3):
            result = await client.call("get_bot_qrcode", params={"bot_type": "3"}, login=True)
            code, content = result.get("qrcode"), result.get("qrcode_img_content")
            if not isinstance(code, str) or not isinstance(content, str) or not content:
                raise ValueError("微信未返回登录二维码")
            self.qr = await asyncio.to_thread(qr_image, content)
            self.status = "等待微信扫码确认"
            while True:
                try:
                    status = await client.call("get_qrcode_status", params={"qrcode": code}, login=True)
                except asyncio.TimeoutError:
                    continue
                state = status.get("status")
                if state == "confirmed":
                    token, account = status.get("bot_token"), status.get("ilink_bot_id")
                    if not isinstance(token, str) or not token or not isinstance(account, str) or not account:
                        raise ValueError("微信登录成功但缺少账号凭证")
                    base = trusted_url(status.get("baseurl") or client.base_url)
                    previous = self.storage().state["account_id"]
                    if previous and previous != account:
                        raise ValueError("扫码账号与原绑定不同，请添加新账号")
                    if any(c is not self and c.storage().state["account_id"] == account
                           for c in self.owner.connections.values()):
                        raise ValueError("该微信账号已绑定，请在原账号卡片重新扫码")
                    self.storage().state.update(
                        token=token, account_id=account, base_url=base,
                        user_id=str(status.get("ilink_user_id") or ""),
                    )
                    await self.storage().save()
                    client.token, client.base_url = token, base
                    self.qr = None
                    return True
                if state == "expired":
                    self.qr = None
                    break
                if state in {"cancel", "canceled", "denied"}:
                    self.status = "微信登录已取消，请重新扫码"
                    self.qr = None
                    return False
                await asyncio.sleep(1)
        self.qr = None
        self.status = "二维码已过期，请点击刷新二维码"
        return False

    async def run(self, login: bool) -> None:
        state = self.storage().state
        client = Client(state["base_url"], state["token"], self.config.weixin.max_image_mb * 1024**2)
        self.client = client
        try:
            if login and not await self.login(client):
                return
            self.status = "正在连接微信"
            while True:
                try:
                    if self.expired:
                        raise APIError(-14, "session")
                    data = await client.updates(self.storage().state["cursor"])
                    if self.expired:
                        raise APIError(-14, "session")
                    await self.set_ready(True)
                    self.status = "已连接：微信私聊"
                    await self.process_batch(data, client)
                except APIError as exc:
                    await self.set_ready(False)
                    if exc.code in {-14, 401}:
                        async with self.send_lock:
                            await self.storage().invalidate()
                            client.token = ""
                        self.status = "微信登录已失效，请重新扫码"
                        return
                    self.status = f"微信接口错误（{exc.code}），等待重试"
                    self.ctx.logger.warning("微信轮询接口错误，code=%s", exc.code)
                    await asyncio.sleep(self.config.weixin.retry_seconds)
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                    await self.set_ready(False)
                    self.status = "连接或消息处理失败，等待重试；详见日志"
                    self.ctx.logger.warning("微信接收失败，类型=%s", type(exc).__name__)
                    await asyncio.sleep(self.config.weixin.retry_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.qr = None
            self.status = str(exc) if isinstance(exc, ValueError) else "连接启动失败，请重新扫码"
            self.ctx.logger.error("微信启动失败，类型=%s", type(exc).__name__)
        finally:
            try:
                await self.set_ready(False)
            finally:
                # stop() closes the session after outstanding sends finish; an exited worker
                # also releases it here under the same lock, even if Host is disconnected.
                async with self.send_lock:
                    await client.close()
                    if self.client is client:
                        self.client = None

    async def process_batch(self, data: Dict[str, Any], client: Client) -> None:
        msgs = data.get("msgs") or []
        if not isinstance(msgs, list):
            raise ValueError("微信消息列表格式无效")
        for msg in msgs:
            if not isinstance(msg, dict) or not msg.get("from_user_id"):
                continue
            if msg.get("message_type", 1) != 1:
                continue
            user = str(msg["from_user_id"])
            allowed = self.config.weixin.allowed_users
            if allowed and user not in allowed:
                continue
            state = self.storage().state
            key = message_id(state["account_id"], msg)
            # Persist refreshed context even if the message was already processed.
            if msg.get("context_token"):
                state["contexts"][user] = str(msg["context_token"])
                await self.storage().save()
            if key in state["seen"]:
                continue
            segments = []
            for item in msg.get("item_list") or []:
                kind = item.get("type")
                if kind == 1:
                    segments.append({"type": "text", "data": str(item.get("text_item", {}).get("text", ""))})
                elif kind == 2:
                    try:
                        binary = await client.download_image(item.get("image_item") or {})
                    except ValueError:
                        segments.append({"type": "text", "data": "[图片无法读取或超过适配器大小限制]"})
                    else:
                        segments.append(
                            {"type": "image", "data": "[图片]", "binary_data_base64": base64.b64encode(binary).decode()}
                        )
                elif kind == 3:
                    text = item.get("voice_item", {}).get("text") or "[语音未提供转写，暂无法识别]"
                    segments.append({"type": "text", "data": str(text)})
                else:
                    label = {4: "文件", 5: "视频"}.get(kind, "消息类型")
                    segments.append({"type": "text", "data": f"[适配器暂不支持读取此{label}]"})
            if segments:
                try:
                    message = await asyncio.to_thread(inbound, state["account_id"], msg, segments, self.nickname(user))
                except ValueError:
                    # Do not let one oversized multi-image message permanently block polling.
                    self.ctx.logger.warning("微信消息超过网关容量或时间戳无效：%s", key)
                    safe_msg = {**msg, "create_time_ms": None, "create_time": None}
                    message = inbound(
                        state["account_id"],
                        safe_msg,
                        [
                            {"type": "text", "data": "[消息过大或格式无效，适配器未读取内容，请拆分后重新发送]"},
                        ],
                        self.nickname(user),
                    )
                accepted = await self.ctx.gateway.route_message(
                    gateway_name=self.gateway_name,
                    message=message,
                    external_message_id=key,
                    dedupe_key=key,
                )
                if not accepted:
                    # Host uses False for policy filtering AND duplicates, not just
                    # errors. Replaying False forever would block the whole account.
                    self.ctx.logger.info("麦麦过滤或去重了微信入站消息：%s", key)
            state["seen"] = (state["seen"] + [key])[-2000:]
            await self.storage().save()
        # RPC exceptions leave the cursor unchanged; explicit filtering is acknowledged.
        if "get_updates_buf" in data:
            self.storage().state["cursor"] = str(data["get_updates_buf"])
            await self.storage().save()

    async def send_to_platform(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        async with self.send_lock:
            if not self.ready or self.client is None or self.expired:
                raise RuntimeError("微信网关尚未就绪")
            target, parts = await asyncio.to_thread(outbound, message, self.client.max_bytes)
            if self.config.weixin.allowed_users and target not in self.config.weixin.allowed_users:
                raise ValueError("目标用户不在允许列表")
            state = self.storage().state
            if message.get("account_id") and message["account_id"] != state["account_id"]:
                raise ValueError("出站消息不属于当前微信账号")
            context = state["contexts"].get(target)
            if not context:
                raise ValueError("没有该用户的会话凭证，请先从微信发送消息")
            sent = []
            try:
                for kind, content in parts:
                    item = (
                        {"type": 1, "text_item": {"text": content}}
                        if kind == "text"
                        else await self.client.upload_image(target, content)
                    )
                    identifier = secrets.token_hex(16)
                    await self.client.send(target, context, [item], identifier)
                    sent.append(identifier)
            except APIError as exc:
                if exc.code in {-14, 401}:
                    self.expired = True
                    self.ready = False
                    self.status = "微信登录已失效，请重新扫码"
                raise RuntimeError(f"微信发送失败（错误码 {exc.code}），已发送 {len(sent)} 段；未自动重试") from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise RuntimeError(f"微信发送结果不确定，已确认 {len(sent)} 段；为避免重复不自动重试") from exc
            return {"success": True, "external_message_id": sent[-1]}

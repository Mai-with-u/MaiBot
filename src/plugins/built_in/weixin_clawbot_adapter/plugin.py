from contextlib import suppress
from typing import Any, Dict

from maibot_sdk import API, ConfigWidget, ConfigWidgetState, MaiBotPlugin, MessageGateway

import asyncio

from .codec import PLATFORM
from .connection import AccountConnection
from .settings import Settings
from .storage import Store

GATEWAY = "weixin_clawbot_gateway"
# Host 每个网关组件只能声明一个账号；预注册独立槽位，新增绑定无需重载。
MAX_ACCOUNTS = 16


def gateway_name(slot: int) -> str:
    return GATEWAY if slot == 0 else f"{GATEWAY}_{slot}"


class WeixinClawbotPlugin(MaiBotPlugin):
    config_model = Settings
    config_widgets = [ConfigWidget(id="status", kind="status", label="微信账号")]

    def __init__(self) -> None:
        super().__init__()
        self.connections: Dict[int, AccountConnection] = {}
        self.lifecycle_lock = asyncio.Lock()
        self.closing = False

    def make_connection(self, slot: int) -> AccountConnection:
        # 首个槽位沿用旧文件和网关名，保留已有登录态、聊天流与策略归属。
        filename = "account.json" if slot == 0 else f"account-{slot}.json"
        return AccountConnection(self, slot, Store(self.ctx.paths.data_dir / filename), gateway_name(slot))

    async def on_load(self) -> None:
        for slot in range(MAX_ACCOUNTS):
            c = self.make_connection(slot)
            await c.store.load()
            state = c.store.state
            if state["account_id"] or state["token"] or state["remark"]:
                self.connections[slot] = c
                c.start()

    async def on_unload(self) -> None:
        self.closing = True
        tasks = [c.action_task for c in self.connections.values() if c.action_task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        async with self.lifecycle_lock:
            await asyncio.gather(*(c.stop() for c in self.connections.values()))

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        if scope == "self":
            async with self.lifecycle_lock:
                await asyncio.gather(*(c.stop() for c in self.connections.values()))
                self.set_plugin_config(config_data)
                for c in self.connections.values():
                    c.start()

    async def get_config_widget_state(self) -> Dict[str, ConfigWidgetState]:
        online = sum(c.ready for c in self.connections.values())
        return {"status": ConfigWidgetState(
            text=f"已连接 {online} / {len(self.connections)} 个账号。请打开插件扩展中的「微信账号管理」扫码、设置聊天显示名称或移除绑定。",
            tone="success" if online else "neutral",
        )}

    def connection(self, slot: int) -> AccountConnection:
        if self.closing:
            raise RuntimeError("插件正在停止")
        if slot not in self.connections:
            raise ValueError("账号不存在，请刷新页面")
        return self.connections[slot]

    @staticmethod
    def validate_remark(remark: str) -> str:
        remark = remark.strip()
        if not remark or len(remark) > 80:
            raise ValueError("备注名不能为空，且最多 80 个字符")
        return remark

    @API("accounts", description="读取微信账号列表与扫码状态", version="1")
    async def accounts(self) -> Dict[str, Any]:
        # 只返回显示字段，不向页面发送 token、context_token 或同步游标。
        return {
            "enabled": self.config.plugin.enabled,
            "limit": MAX_ACCOUNTS,
            "accounts": [{
                "slot": c.slot,
                "remark": c.store.state["remark"] or f"微信账号 {c.slot + 1}",
                "account_id": c.store.state["account_id"],
                "status": c.status,
                "ready": c.ready,
                "qr": c.qr,
                "busy": c.action_task is not None and not c.action_task.done(),
            } for c in self.connections.values()],
        }

    @API("add_account", description="添加微信账号并开始扫码", version="1")
    async def add_account(self, remark: str) -> Dict[str, Any]:
        remark = self.validate_remark(remark)
        if self.closing or not self.config.plugin.enabled:
            raise ValueError("请先启用微信适配器")
        slot = next((i for i in range(MAX_ACCOUNTS) if i not in self.connections), None)
        if slot is None:
            raise ValueError(f"最多同时绑定 {MAX_ACCOUNTS} 个账号")
        c = self.make_connection(slot)
        c.store.state["remark"] = remark
        self.connections[slot] = c
        self.schedule(c, "login")
        return {"slot": slot}

    @API("rename_account", description="设置微信账号备注名", version="1")
    async def rename_account(self, slot: int, remark: str) -> Dict[str, Any]:
        remark = self.validate_remark(remark)
        async with self.lifecycle_lock:
            c = self.connection(slot)
            c.store.state["remark"] = remark
            await c.store.save()
        return {"success": True}

    def schedule(self, c: AccountConnection, action: str) -> None:
        if c.action_task is not None and not c.action_task.done():
            raise RuntimeError("该账号操作仍在进行，请稍后再试")
        # RPC 立即返回，等待在途发送、保存与重连都在后台完成。
        c.status = "正在移除账号" if action == "remove" else "正在获取二维码"
        c.action_task = asyncio.create_task(self.change_account(c, action))

    @API("login_account", description="重新扫码绑定原微信账号", version="1")
    async def login_account(self, slot: int) -> Dict[str, Any]:
        if not self.config.plugin.enabled:
            raise ValueError("请先启用微信适配器")
        self.schedule(self.connection(slot), "login")
        return {"success": True}

    @API("remove_account", description="移除单个微信账号的本地登录态", version="1")
    async def remove_account(self, slot: int) -> Dict[str, Any]:
        self.schedule(self.connection(slot), "remove")
        return {"success": True}

    async def change_account(self, c: AccountConnection, action: str) -> None:
        try:
            async with self.lifecycle_lock:
                await c.stop()
                if action == "remove":
                    await c.store.clear()
                    del self.connections[c.slot]
                else:
                    await c.store.save()
                    c.start(login=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            c.status = "账号操作失败，请查看日志后重试"
            self.ctx.logger.exception("微信账号操作失败，slot=%s", c.slot)

    async def send_for_slot(self, slot: int, message: Dict[str, Any]) -> Dict[str, Any]:
        c = self.connection(slot)
        if message.get("account_id") != c.store.state["account_id"]:
            raise ValueError("出站消息与微信账号归属不一致")
        return await c.send_to_platform(message)


def make_gateway(slot: int):
    async def send(self: WeixinClawbotPlugin, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return await self.send_for_slot(slot, message)

    return MessageGateway(
        route_type="duplex", name=gateway_name(slot), platform=PLATFORM,
        protocol="weixin_ilink", timeout_ms=180000,
    )(send)


# 动态构造带标准 SDK 装饰器的方法，由 Runner 正常收集和注册。
WeixinClawbotPlugin = type("WeixinClawbotPlugin", (WeixinClawbotPlugin,), {
    f"send_account_{slot}": make_gateway(slot) for slot in range(MAX_ACCOUNTS)
})


def create_plugin() -> WeixinClawbotPlugin:
    return WeixinClawbotPlugin()

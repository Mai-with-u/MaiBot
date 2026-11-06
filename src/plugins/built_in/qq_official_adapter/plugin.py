from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta, timezone
from hashlib import md5, sha1, sha256
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple, Type
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncio
import base64
import json
import re
import time

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout, ClientWebSocketResponse, WSMsgType
from maibot_sdk import API, ConfigWidget, ConfigWidgetState, MaiBotPlugin, MessageGateway, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from src.common.paths import resolve_plugin_config_path

from .codec import (
    GROUP_EVENTS,
    PLATFORM,
    QQ_FACE_TAG_PATTERN,
    QQ_MENTION_TOKEN_PATTERN,
    SUPPORTED_EVENTS,
    decode_message,
    encode_message,
    extract_command_text,
    extract_message_scene_fields,
    summarize_payload,
)
from .settings import AdapterSettings
from .identity_sync import PROFILE_TTL, IdentityScope, SilentIdentitySync, save_profiles
from .qr_login import QRLogin, save_credentials

GATEWAY_NAME = "qq_official_gateway"
MESSAGE_INTENTS = 1 << 25
REPLY_LIMITS = {"group": (300, 5), "private": (3600, 4)}
# 官方引用索引（REFIDX）没有与被动回复一致的有效期，按 24 小时保留并限制条目数，防内存无限增长。
REF_IDX_TTL_SEC = 24 * 3600
MAX_REF_IDX_ENTRIES = 2000
MAX_PENDING_DISPATCHES = 32
MAX_INBOUND_ATTACHMENTS = 10
MAX_INBOUND_MEDIA_BYTES = 20 * 1024 * 1024
# 官方预上传要求的 md5_10m：文件前 10002432 字节的 MD5。
MD5_10M_BYTES = 10002432
TRUSTED_QQ_MEDIA_HOSTS = frozenset({"grouppro.grouppro.qq.com"})
TRUSTED_QQ_MEDIA_HOST_SUFFIXES = ("nt.qq.com", "nt.qq.com.cn", "ugcimg.cn")
ID_MAP_FILE_NAME = "id_map.json"
ASSIGN_GROUP_COMMAND = "/assign_group_id"
ASSIGN_USER_COMMAND = "/assign_id"
MUTE_TOOL_NAME = "mute"
RECALL_TOOL_NAME = "recall_message"
ASSIGN_COMMANDS = frozenset({ASSIGN_GROUP_COMMAND, ASSIGN_USER_COMMAND})
ASSIGN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class QQOfficialAdapterPlugin(MaiBotPlugin):
    """使用 QQ 开放平台的 WebSocket 事件和 OpenAPI 接入 MaiBot。"""

    config_model: ClassVar[Optional[Type[PluginConfigBase]]] = AdapterSettings

    config_widgets: ClassVar[List[ConfigWidget]] = [
        ConfigWidget(id="connection", kind="status", label="连接状态"),
        ConfigWidget(id="login", kind="qrcode", label="QQ 扫码授权"),
        ConfigWidget(id="login_start", kind="action", label="生成 / 刷新二维码",
                     confirmation="扫码授权成功后将替换 AppID / AppSecret 并重新连接，是否继续？"),
        ConfigWidget(id="login_cancel", kind="action", label="取消扫码"),
        ConfigWidget(id="reconnect", kind="action", label="重新连接"),
    ]

    async def get_config_widget_state(self) -> Dict[str, ConfigWidgetState]:
        login = self._qr_login.snapshot()
        active = login["state"] in {"creating", "waiting", "saving"}
        configured = bool(self.config.qq_official.app_id.strip() and self.config.qq_official.app_secret.strip())
        connection = (f"已连接：{self._bot_name or self._account_id}" if self._ready
                      else "连接中 / 等待重连" if self._worker is not None and not self._worker.done()
                      else "尚未连接" if configured else "请扫码授权或填写 AppID / AppSecret")
        return {
            "connection": ConfigWidgetState(text=connection, tone="success" if self._ready else "neutral"),
            "login": ConfigWidgetState(text=login["message"], image=login["image"] or None,
                                       expires_at=login["expires_at"],
                                       visible=active or not self._ready or login["state"] in {"error", "expired"},
                                       tone="error" if login["state"] == "error" else "neutral"),
            "login_start": ConfigWidgetState(disabled=login["state"] in {"creating", "saving"}),
            "login_cancel": ConfigWidgetState(visible=active, disabled=login["state"] == "saving"),
            "reconnect": ConfigWidgetState(disabled=not configured or active),
        }

    async def on_config_widget_action(self, action_id: str) -> None:
        if action_id == "login_start":
            await self.webui_login_start()
        elif action_id == "login_cancel":
            await self.webui_login_cancel()
        elif action_id == "reconnect":
            async with self._connection_lock:
                await self._stop()
                if self.config.plugin.enabled:
                    self._start()
        else:
            raise ValueError(f"未知配置页操作: {action_id}")

    def __init__(self) -> None:
        super().__init__()
        self._worker: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._session: Optional[ClientSession] = None
        self._ws: Optional[ClientWebSocketResponse] = None
        self._access_token = ""
        self._token_expires_at = 0.0
        self._session_id = ""
        self._seq: Optional[int] = None
        self._account_id = ""
        self._bot_name = ""
        self._ready = False
        self._resuming = False
        self._heartbeat_ack = True
        self._replies: Dict[Tuple[str, str, str], Tuple[float, int]] = {}
        # 平台消息 id（ROBOT1.0_...）→ 官方引用索引（REFIDX），入站取 message_scene.ext.msg_idx，
        # 自发消息取发送响应 ext_info.ref_idx；用于出站引用，也供入站按账号、场景、目标反查。
        self._ref_idx: Dict[str, Tuple[float, str, Optional[Tuple[str, str, str]]]] = {}
        self._reply_lock = asyncio.Lock()
        self._dispatch_tasks: Set[asyncio.Task[None]] = set()
        self._id_map: Dict[str, Any] = {"group": {}, "user": {}}
        self._unified_account_id = ""
        self._assign_admin_ids: List[str] = []
        self._connection_lock = asyncio.Lock()
        self._qr_login = QRLogin(self._apply_qr_credentials)
        self._gateway_state_lock = asyncio.Lock()
        self._inbound_ready = asyncio.Event()
        self._identity_sync = SilentIdentitySync()
        self._identity_task: Optional[asyncio.Task[None]] = None
        self._identity_dirty = False
        self._identity_only_task: Optional[asyncio.Task[None]] = None
        self._identity_only_owner = uuid4().hex
        self._identity_only_target = ""

    async def on_load(self) -> None:
        # 页面在连接关闭或凭据尚未填写时也需要展示已有绑定。
        await asyncio.to_thread(self._load_id_map)
        try:
            await asyncio.to_thread(self._identity_sync.load, self.ctx.paths.data_dir / "auto_user_profiles.json")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.ctx.logger.error("QQ 自动身份映射加载失败: %s", exc)
        await self._sync_tool_states()
        if self.config.plugin.enabled:
            self._start()

    async def on_unload(self) -> None:
        await self._qr_login.cancel()
        await self._stop()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        if scope != "self":
            return
        async with self._connection_lock:
            self.set_plugin_config(config_data)
            await self._sync_tool_states()
            await self._stop()
            if self.config.plugin.enabled:
                self._start()

    async def _apply_qr_credentials(self, app_id: str, secret: str) -> None:
        """保存至插件配置并立即应用，重启后仍使用同一机器人凭据。"""
        async with self._connection_lock:
            config_data = await asyncio.to_thread(save_credentials, resolve_plugin_config_path(Path(__file__).parent), app_id, secret)
            self.set_plugin_config(config_data)
            await self._stop()
            self._start()

    @API("webui_login_status", description="查询 QQ 扫码授权及连接状态", version="1", public=True)
    async def webui_login_status(self) -> Dict[str, Any]:
        return {
            **self._qr_login.snapshot(),
            "connected": self._ready,
            "enabled": self.config.plugin.enabled,
            "configured_app_id": self.config.qq_official.app_id,
            "bot_name": self._bot_name,
        }

    @API("webui_login_start", description="发起 QQ 官方机器人扫码登录", version="1", public=True)
    async def webui_login_start(self) -> Dict[str, Any]:
        return await self._qr_login.start()

    @API("webui_login_cancel", description="取消当前 QQ 扫码登录", version="1", public=True)
    async def webui_login_cancel(self) -> Dict[str, Any]:
        return await self._qr_login.cancel()

    async def _sync_tool_states(self) -> None:
        """按 [mute].enabled、[recall].enabled 同步可选 LLM 工具的启停状态。"""
        tool_states = (
            (MUTE_TOOL_NAME, "禁言", self.config.mute.enabled),
            (RECALL_TOOL_NAME, "撤回", self.config.recall.enabled),
        )
        for tool_name, label, enabled in tool_states:
            component_name = f"{self.ctx.plugin_id}.{tool_name}"
            try:
                if enabled:
                    result = await self.ctx.component.enable_component(component_name, "TOOL")
                else:
                    result = await self.ctx.component.disable_component(component_name, "TOOL")
            except Exception as exc:
                self.ctx.logger.warning("同步%s工具启停状态失败: %s", label, exc)
                continue
            if isinstance(result, Mapping) and not result.get("success", False):
                self.ctx.logger.warning("同步%s工具启停状态失败: %s", label, result.get("error") or result)
                continue
            self.ctx.logger.info("%s工具已%s", label, "启用" if enabled else "停用")

    def _start(self) -> None:
        settings = self.config.qq_official
        if not settings.app_id.strip() or not settings.app_secret.strip():
            self.ctx.logger.error("QQ 官方适配器缺少凭据，请在插件配置 [qq_official] 中填写 app_id 和 app_secret")
            return
        self._unified_account_id = settings.unified_account_id.strip()
        self._assign_admin_ids = [str(item).strip() for item in settings.assign_admin_ids if str(item).strip()]
        self._worker = asyncio.create_task(self._run(), name="qq-official-adapter")
        if self.config.qq_official.silent_user_sync:
            self._identity_task = asyncio.create_task(self._identity_loop(), name="qq-official-identity-sync")
        if self.config.qq_official.snowluma_identity_only:
            self._identity_only_task = asyncio.create_task(self._identity_only_loop(), name="qq-official-identity-only")

    async def _stop(self) -> None:
        identity_only_task = self._identity_only_task
        self._identity_only_task = None
        if identity_only_task is not None:
            identity_only_task.cancel()
            with suppress(asyncio.CancelledError):
                await identity_only_task
        await self._release_identity_only()
        identity_task = self._identity_task
        self._identity_task = None
        if identity_task is not None:
            identity_task.cancel()
            with suppress(asyncio.CancelledError):
                await identity_task
        self._identity_sync.clear_pending()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
        # 只有主动停止（卸载、禁用、切换配置）才取消已接收任务，普通断线保留它们。
        # 先取消投递任务释放状态锁，再等待网关的离线清理。
        tasks = tuple(self._dispatch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._dispatch_tasks.clear()
        if worker is not None:
            with suppress(asyncio.CancelledError):
                await worker
        await self._disconnect()
        self._replies.clear()
        self._ref_idx.clear()
        self._session_id = ""
        self._seq = None
        self._account_id = ""
        self._bot_name = ""
        self._access_token = ""
        self._token_expires_at = 0.0

    def _id_map_path(self) -> Path:
        return self.ctx.paths.data_dir / ID_MAP_FILE_NAME

    def _identity_namespace(self) -> Tuple[str, str]:
        settings = self.config.qq_official
        return settings.app_id.strip(), settings.unified_account_id.strip()

    def _identity_scope(self, group_openid: str, user_openid: str) -> IdentityScope:
        return (*self._identity_namespace(), group_openid, self._id_map["group"].get(group_openid, ""), user_openid)

    def _group_user_map(self, group_openid: str) -> Dict[str, str]:
        """自动推断只用于本群的消息转换，不加入管理员鉴权使用的人工映射。"""
        users: Dict[str, str] = {}
        if self.config.qq_official.silent_user_sync and group_openid in self._id_map["group"]:
            prefix = self._identity_scope(group_openid, "")[:4]
            for scope in self._identity_sync.profiles:
                if scope[:4] == prefix and (profile := self._identity_sync.get_profile(scope)) is not None:
                    users[scope[4]] = profile.user_id
        users.update(self._id_map["user"])
        return users

    async def _sync_identities_once(self) -> None:
        namespace = self._identity_namespace()
        if not namespace[0] or not namespace[1].isdecimal() or not self._ready:
            return
        bound_groups = set(self._id_map["group"].values())
        groups = [group for group in self._identity_sync.groups(namespace) if group in bound_groups]
        # 每轮限定 128 群；顺序分批，避免活跃群之外的请求被永久饿死。
        for offset in range(0, len(groups), 128):
            snapshot = await asyncio.wait_for(self.ctx.api.call(
                "maibot-team.snowluma-adapter.identity_observations", version="1",
                account_id=namespace[1], group_ids=groups[offset:offset + 128],
            ), timeout=5.0)
            if not isinstance(snapshot, dict) or snapshot.get("protocol") != 1:
                raise ValueError("SnowLuma 未提供身份指纹协议 v1，请更新适配器")
            previous_conflicts = {scope for scope, candidate in self._identity_sync.candidates.items() if candidate.conflict}
            changes = self._identity_sync.reconcile(namespace, snapshot["observations"])
            for scope, candidate in self._identity_sync.candidates.items():
                if candidate.conflict and scope not in previous_conflicts:
                    self.ctx.logger.warning("QQ 静默身份匹配存在冲突，暂停自动绑定: group=%s openid=%s", scope[3], scope[4])
            if changes:
                self._identity_dirty = True
                self.ctx.logger.debug("QQ 静默身份同步更新 %s 条记录", len(changes))
        if self._identity_dirty:
            # 停用时等待正在进行的原子写入结束，避免取消 to_thread 后遗留并发写入。
            saving = asyncio.create_task(asyncio.to_thread(
                save_profiles, self.ctx.paths.data_dir / "auto_user_profiles.json", self._identity_sync.dump()
            ))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                self._identity_dirty = False
                raise
            self._identity_dirty = False

    async def _identity_loop(self) -> None:
        failed = False
        while True:
            await asyncio.sleep(2.0 if not failed else 30.0)
            try:
                await self._sync_identities_once()
            except Exception as exc:
                # 协作适配器可独立停用或升级；不阻塞消息收发，首次失败完整记录原因。
                if not failed:
                    self.ctx.logger.warning("QQ 静默身份同步暂不可用: %s", exc, exc_info=True)
                failed = True
            else:
                failed = False

    async def _release_identity_only(self) -> None:
        target = self._identity_only_target
        self._identity_only_target = ""
        if not target:
            return
        try:
            await asyncio.wait_for(self.ctx.api.call(
                "maibot-team.snowluma-adapter.identity_only_lease", version="1",
                account_id=target, owner=self._identity_only_owner, enabled=False,
            ), timeout=2.0)
        except Exception as exc:
            self.ctx.logger.warning("SL 身份查询模式释放失败，将在最后续期后 15 秒自动恢复: %s", exc)

    async def _renew_identity_only(self, *, connecting: bool = False) -> None:
        target = self.config.qq_official.unified_account_id.strip()
        # 必须是已就绪且 WebSocket 仍打开的官方连接，保存开关本身不会改变 SL 状态。
        if (not self.config.qq_official.snowluma_identity_only or not self.config.plugin.enabled
                or not target.isdecimal()):
            await self._release_identity_only()
            return
        if self._identity_only_target and self._identity_only_target != target:
            await self._release_identity_only()
        # 短暂重连只停止续期，不主动释放，避免 SL 在两次官方连接之间立即恢复发送。
        # 长时间离线仍由 SL 的 15 秒租约自行恢复；READY 握手允许在发布路由前续租。
        if (not self._ready and not connecting) or self._ws is None or self._ws.closed:
            return
        # 发起请求前记录目标；即使响应丢失或被取消，停用时也能尝试释放已经生效的租约。
        self._identity_only_target = target
        result = await asyncio.wait_for(self.ctx.api.call(
            "maibot-team.snowluma-adapter.identity_only_lease", version="1",
            account_id=target, owner=self._identity_only_owner, enabled=True,
        ), timeout=2.0)
        if not isinstance(result, dict) or result.get("active") is not True:
            raise RuntimeError("SL 身份查询模式未激活，请确认关联账号在线并已更新 SL 适配器")

    async def _identity_only_loop(self) -> None:
        failed = False
        while True:
            try:
                await self._renew_identity_only()
            except Exception as exc:
                if not failed:
                    self.ctx.logger.warning("SL 身份查询模式续期失败，将在最后续期后 15 秒自动恢复: %s", exc, exc_info=True)
                failed = True
            else:
                failed = False
            await asyncio.sleep(5.0)

    @API("webui_group_mappings", description="查看已有的 QQ 群 OpenID 与群号映射", version="1", public=True)
    async def webui_group_mappings(self) -> Dict[str, Any]:
        """返回当前生效的群绑定快照，不包含用户映射。"""
        groups = [
            {"group_openid": openid, "group_id": group_id}
            for openid, group_id in sorted(self._id_map["group"].items(), key=lambda item: (item[1], item[0]))
        ]
        return {"total": len(groups), "groups": groups}

    @API("webui_mappings", description="查看 QQ 群和用户的 OpenID 绑定", version="1", public=True)
    async def webui_mappings(self) -> Dict[str, Any]:
        """合并人工映射与当前账号的群内自动身份，保留冲突记录供页面核对。"""
        result = {
            kind: [
                {"openid": openid, "id": value, "source": "manual", "nickname": "", "cardname": "",
                 "group_id": value if kind == "group" else "", "group_openid": "",
                 "conflict": False, "status": "已绑定"}
                for openid, value in sorted(self._id_map[kind].items(), key=lambda item: (item[1], item[0]))
            ]
            for kind in ("group", "user")
        }
        namespace = self._identity_namespace()
        for scope, profile in self._identity_sync.profiles.items():
            if (scope[:2] != namespace or self._id_map["group"].get(scope[2]) != scope[3]
                    or time.time() - profile.updated_at >= PROFILE_TTL):
                continue
            candidate = self._identity_sync.candidates.get(scope)
            conflict = profile.conflict or (candidate is not None and candidate.conflict)
            manual_id = self._id_map["user"].get(scope[4])
            if manual_id is not None and manual_id != profile.user_id:
                conflict, status = True, "与手动绑定冲突，手动生效"
            elif conflict:
                status = "冲突，自动绑定已暂停"
            elif not self.config.qq_official.silent_user_sync:
                status = "自动绑定已停用"
            else:
                status = "与手动绑定一致" if manual_id is not None else "已确认"
            result["user"].append({
                "openid": scope[4], "id": profile.user_id, "source": "auto",
                "nickname": profile.nickname, "cardname": profile.cardname,
                "group_id": scope[3], "group_openid": scope[2], "conflict": conflict, "status": status,
            })
        result["user"].sort(key=lambda row: (row["id"], row["source"], row["group_id"], row["openid"]))
        return result

    @API("webui_mapping_avatar", description="获取已绑定群或用户的头像", version="1", public=True)
    async def webui_mapping_avatar(
        self, kind: str, openid: str, source: str = "manual", group_openid: str = ""
    ) -> Dict[str, str]:
        """隔离页面不能访问外网，将已绑定对象的小尺寸头像转为内嵌图片。"""
        if kind not in {"group", "user"} or source not in {"manual", "auto"}:
            raise ValueError("映射类型或来源无效")
        target_id: Optional[str] = None
        if source == "manual":
            target_id = self._id_map[kind].get(openid)
        elif kind == "user" and group_openid in self._id_map["group"]:
            profile = self._identity_sync.profiles.get(self._identity_scope(group_openid, openid))
            if profile is not None and time.time() - profile.updated_at < PROFILE_TTL:
                target_id = profile.user_id
        if target_id is None:
            raise ValueError("群或用户映射不存在")
        resource = await self.api_get_avatar(PLATFORM, target_id, kind)
        if resource["status"] != "available":
            return {"image": "", "message": "绑定 ID 不是数字，无法获取 QQ 头像"}
        url = resource["url"].replace("/640", "/100").replace("s=640", "s=100")
        async with ClientSession(timeout=ClientTimeout(total=10)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError(f"QQ 头像请求失败: HTTP {response.status}")
                mime = response.content_type
                if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
                    raise ValueError("QQ 头像返回了不支持的图片格式")
                data = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    data.extend(chunk)
                    if len(data) > 256 * 1024:
                        raise ValueError("QQ 头像超过 256 KiB")
        return {"image": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}

    def _load_id_map(self) -> None:
        """加载统一 ID 映射；文件缺失或损坏时按空映射启动，绑定命令会重建文件。"""
        path = self._id_map_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.ctx.logger.error("QQ 统一 ID 映射文件读取失败，按空映射启动: %s", exc)
            return
        if not isinstance(raw, Mapping):
            self.ctx.logger.error("QQ 统一 ID 映射文件格式无效，按空映射启动")
            return
        id_map: Dict[str, Any] = {"group": {}, "user": {}}
        for kind in ("group", "user"):
            entries = raw.get(kind)
            if isinstance(entries, Mapping):
                id_map[kind] = {
                    str(key): str(value) for key, value in entries.items() if str(key).strip() and str(value).strip()
                }
        self._id_map = id_map

    def _save_id_map(self) -> None:
        try:
            path = self._id_map_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._id_map, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.ctx.logger.error("QQ 统一 ID 映射保存失败: %s", exc)

    @staticmethod
    def _author_openid(event: str, author: Mapping[str, Any]) -> str:
        """提取发送者的原始 OpenID（群聊 member_openid 优先，单聊 user_openid 优先）。"""
        keys = (
            ("member_openid", "openid", "id", "user_openid")
            if event in GROUP_EVENTS
            else ("user_openid", "openid", "id")
        )
        return next((candidate for key in keys if (candidate := str(author.get(key) or "").strip())), "")

    def _assign_allowed(self, sender_openid: str) -> bool:
        """配置了管理员名单时，仅允许名单内（统一 ID 或 OpenID）的发送者执行绑定命令。"""
        if not self._assign_admin_ids:
            return True
        sender_ids = {sender_openid, self._id_map["user"].get(sender_openid, "")} - {""}
        return bool(sender_ids & set(self._assign_admin_ids))

    async def _handle_assign_command(self, command: str, event: str, data: Mapping[str, Any]) -> None:
        """处理绑定命令：群/用户 OpenID 绑定到统一 ID；统一账号 ID 只从配置文件读取。

        命令消息不进入聊天流，绑定结果以被动回复回执。
        """
        author = data.get("author")
        author = author if isinstance(author, Mapping) else {}
        sender_openid = self._author_openid(event, author)
        tokens = QQ_MENTION_TOKEN_PATTERN.sub("", command).split()
        value = tokens[1] if len(tokens) > 1 else ""
        if not self._assign_allowed(sender_openid):
            await self._reply_to_command(event, data, "仅配置的管理员可执行该命令")
            return
        if command.split(" ", 1)[0] == ASSIGN_GROUP_COMMAND:
            kind = "group"
            openid = str(data.get("group_openid") or "").strip()
            subject = "当前群"
            if event not in GROUP_EVENTS:
                await self._reply_to_command(event, data, f"{ASSIGN_GROUP_COMMAND} 仅支持群聊使用")
                return
        else:
            kind = "user"
            mention = QQ_MENTION_TOKEN_PATTERN.search(command)
            if mention is not None and mention.group(1) != self._account_id:
                openid = mention.group(1)
                subject = "被 @ 的用户"
            else:
                openid = sender_openid
                subject = "发送者"
        if not openid or not ASSIGN_VALUE_PATTERN.fullmatch(value):
            usage = {
                "group": f"用法：{ASSIGN_GROUP_COMMAND} <群号>",
                "user": f"用法：{ASSIGN_USER_COMMAND} <QQ号>（可 @ 对方为其绑定）",
            }
            await self._reply_to_command(event, data, usage[kind])
            return
        self._id_map[kind][openid] = value
        self._save_id_map()
        self.ctx.logger.info("QQ 统一 ID 绑定更新: %s %s -> %s", kind, openid, value)
        await self._reply_to_command(event, data, f"已将{subject}的 ID 绑定为 {value}，后续消息按该 ID 归属")

    async def _reply_to_command(self, event: str, data: Mapping[str, Any], text: str) -> None:
        """以被动回复回执管理命令结果（引用命令消息的 msg_id，不占主动消息额度）。"""
        if event in GROUP_EVENTS:
            openid = str(data.get("group_openid") or "").strip()
            path = f"/v2/groups/{quote(openid, safe='')}/messages"
        else:
            author = data.get("author")
            openid = self._author_openid(event, author if isinstance(author, Mapping) else {})
            path = f"/v2/users/{quote(openid, safe='')}/messages"
        if not openid:
            self.ctx.logger.warning("QQ 管理命令回执缺少目标 OpenID")
            return
        command_msg_idx = extract_message_scene_fields(data)[0]
        try:
            session = self._require_session()
            token = await self._authorize()
            payload: Dict[str, Any] = {
                "content": text,
                "msg_type": 0,
                "msg_id": str(data.get("id") or ""),
                "msg_seq": 1,
            }
            if command_msg_idx:
                payload["message_reference"] = {"message_id": command_msg_idx}
            async with session.post(
                self._api_url(path),
                headers={"Authorization": f"QQBot {token}"},
                json=payload,
            ) as response:
                if response.status >= 400:
                    self.ctx.logger.warning("QQ 管理命令回执失败: HTTP %s", response.status)
        except Exception as exc:
            self.ctx.logger.warning("QQ 管理命令回执失败: %s", exc)

    async def _run(self) -> None:
        async with ClientSession(timeout=ClientTimeout(total=20)) as session:
            self._session = session
            try:
                while True:
                    retry = True
                    delay = self.config.advanced.reconnect_delay_sec
                    try:
                        close_code = await self._connect()
                        retry = self._handle_gateway_close(close_code)
                        if close_code == 4008:
                            delay = max(delay, 60.0)
                    except asyncio.CancelledError:
                        raise
                    except ClientResponseError as exc:
                        if exc.status == 401:
                            self._invalidate_token()
                        self.ctx.logger.warning("QQ 官方网关 HTTP 请求失败，将重试: %s", exc)
                    except Exception as exc:
                        self.ctx.logger.warning("QQ 官方网关连接失败，将重试: %s", exc)
                    finally:
                        await self._disconnect()
                    if not retry:
                        break
                    await asyncio.sleep(delay)
            finally:
                self._session = None

    def _api_url(self, path: str) -> str:
        return self.config.qq_official.api_base_url.rstrip("/") + path

    async def _authorize(self) -> str:
        session = self._require_session()
        if self._access_token and time.time() < self._token_expires_at - 50:
            return self._access_token
        async with session.post(
            self._api_url("/app/getAppAccessToken"),
            json={
                "appId": self.config.qq_official.app_id.strip(),
                "clientSecret": self.config.qq_official.app_secret.strip(),
            },
        ) as response:
            payload = await response.json()
            if response.status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
                raise RuntimeError(
                    f"获取 QQ 访问凭证失败: HTTP {response.status}, code={payload.get('code') if isinstance(payload, dict) else 'unknown'}"
                )
        self._access_token = str(payload["access_token"])
        self._token_expires_at = time.time() + int(payload["expires_in"])
        return self._access_token

    def _require_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("QQ 官方适配器 HTTP 会话未就绪")
        return self._session

    def _invalidate_token(self) -> None:
        self._access_token = ""
        self._token_expires_at = 0.0

    def _reset_gateway_session(self) -> None:
        """重置恢复游标；账号身份保留给断线前已接收的任务。"""
        self._session_id = ""
        self._seq = None
        self._resuming = False

    def _handle_gateway_close(self, code: Optional[int]) -> bool:
        """根据官方关闭码处理凭据、恢复游标及不可重试错误。"""
        if code in {4914, 4915}:
            self.ctx.logger.error("QQ 官方网关不可恢复地断开: code=%s，请检查机器人上线状态及平台权限", code)
            return False
        if code == 4004:
            self._invalidate_token()
        elif code in {4006, 4007, 4009} or (code is not None and 4900 <= code <= 4913):
            self._reset_gateway_session()
            self._invalidate_token()
        self.ctx.logger.warning("QQ 官方网关已断开: code=%s，将%s", code, "等待至少 60 秒重连" if code == 4008 else "重连")
        return True

    async def _connect(self) -> Optional[int]:
        session = self._require_session()
        token = await self._authorize()
        auth = {"Authorization": f"QQBot {token}"}
        async with session.get(self._api_url("/gateway"), headers=auth) as response:
            response.raise_for_status()
            gateway = await response.json()
        url = str(gateway.get("url") or "")
        if not url.startswith("wss://"):
            raise ValueError("QQ 网关没有返回有效的 wss 地址")
        async with session.ws_connect(url, heartbeat=None) as ws:
            self._ws = ws
            async for frame in ws:
                if frame.type == WSMsgType.TEXT:
                    await self._handle_gateway_payload(frame.json())
                elif frame.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
            # aiohttp 的迭代器可能直接结束而不产出 CLOSE 帧，需从连接读取最终关闭码。
            return ws.close_code

    async def _handle_gateway_payload(self, payload: Mapping[str, Any]) -> None:
        op = payload.get("op")
        data = payload.get("d")
        if op == 10:
            if not isinstance(data, Mapping) or not isinstance(data.get("heartbeat_interval"), (int, float)):
                raise ValueError("QQ 网关 Hello 缺少心跳间隔")
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            interval = float(data["heartbeat_interval"]) / 1000
            if interval <= 0:
                raise ValueError("QQ 网关心跳间隔无效")
            self._heartbeat_ack = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat(interval), name="qq-official-heartbeat")
            if self._session_id and self._seq is not None:
                self._resuming = True
                await self._send_ws(
                    {
                        "op": 6,
                        "d": {"token": f"QQBot {self._access_token}", "session_id": self._session_id, "seq": self._seq},
                    }
                )
            else:
                await self._send_ws(
                    {
                        "op": 2,
                        "d": {"token": f"QQBot {self._access_token}", "intents": MESSAGE_INTENTS, "shard": [0, 1]},
                    }
                )
        elif op == 0:
            if isinstance(payload.get("s"), int):
                self._seq = payload["s"]
            event = str(payload.get("t") or "")
            if event == "READY":
                if not isinstance(data, Mapping) or not isinstance(data.get("user"), Mapping):
                    raise ValueError("QQ 网关 READY 结构无效")
                self._session_id = str(data.get("session_id") or "")
                self._account_id = str(data["user"].get("id") or "")
                self._bot_name = str(data["user"].get("username") or "")
                if not self._session_id or not self._account_id:
                    raise ValueError("QQ 网关 READY 缺少账号或会话 ID")
                self._resuming = False
                await self._set_ready(True)
            elif event == "RESUMED":
                if not self._account_id:
                    raise ValueError("QQ 网关恢复成功但缺少账号身份")
                self._resuming = False
                await self._set_ready(True)
            elif event in SUPPORTED_EVENTS:
                # Resume 的补发事件先于 RESUMED 到达，首条补发事件即表明连接已恢复。
                if self._resuming:
                    if not self._account_id:
                        raise ValueError("QQ 网关补发消息时缺少账号身份")
                    await self._set_ready(True)
                    self._resuming = False
                if isinstance(data, Mapping) and self._ready:
                    if len(self._dispatch_tasks) >= MAX_PENDING_DISPATCHES:
                        self.ctx.logger.warning("QQ 官方消息处理队列已满，丢弃事件: %s", data.get("id"))
                    else:
                        task = asyncio.create_task(
                            self._receive_message(event, data, account_id=self._account_id, bot_name=self._bot_name),
                            name="qq-official-event",
                        )
                        self._dispatch_tasks.add(task)
                        task.add_done_callback(self._on_dispatch_done)
        elif op == 1:
            await self._send_ws({"op": 1, "d": self._seq})
        elif op == 11:
            self._heartbeat_ack = True
        elif op in {7, 9}:
            if op == 9 and data is not True:
                # INVALID_SESSION 的 d=true 允许恢复；不可恢复时才重新鉴权和 Identify。
                self._reset_gateway_session()
                self._invalidate_token()
            if self._ws is not None:
                await self._ws.close()

    def _on_dispatch_done(self, task: asyncio.Task[None]) -> None:
        self._dispatch_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self.ctx.logger.error("QQ 官方消息路由失败", exc_info=(type(error), error, error.__traceback__))

    async def _heartbeat(self, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._heartbeat_ack:
                    self.ctx.logger.warning("QQ 网关心跳未确认，重新建立连接")
                    if self._ws is not None:
                        await self._ws.close()
                    return
                self._heartbeat_ack = False
                await self._send_ws({"op": 1, "d": self._seq})
        except Exception:
            self.ctx.logger.exception("QQ 网关心跳发送失败，重新建立连接")
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()

    async def _send_ws(self, payload: Mapping[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("QQ WebSocket 未连接")
        await self._ws.send_json(dict(payload))

    async def _receive_message(self, event: str, data: Mapping[str, Any], *, account_id: str, bot_name: str) -> None:
        try:
            # 管理命令（/assign_group_id、/assign_id）在解码前拦截，不进入聊天流。
            command = extract_command_text(data)
            if command.split(" ", 1)[0] in ASSIGN_COMMANDS:
                await self._handle_assign_command(command, event, data)
                return
            segments, labels, is_picture, is_emoji = await self._build_attachments(data)
            group_openid = str(data.get("group_openid") or "") if event in GROUP_EVENTS else ""
            effective_id_map = {**self._id_map, "user": self._group_user_map(group_openid)}
            message, scene, target = decode_message(
                event,
                data,
                account_id,
                attachment_segments=segments,
                attachment_labels=labels,
                is_picture=is_picture,
                is_emoji=is_emoji,
                bot_name=bot_name,
                id_map=effective_id_map,
                unified_account_id=self._unified_account_id,
            )
            if group_openid and self.config.qq_official.silent_user_sync:
                user_openid = message["message_info"]["additional_config"]["qq_official_user_openid"]
                scope = self._identity_scope(group_openid, user_openid)
                # 群号必须先人工绑定，且已指定用于协作的 SnowLuma 账号。
                if scope[3].isdecimal() and scope[1].isdecimal():
                    self._identity_sync.observe(scope, message)
                    profile = self._identity_sync.get_profile(scope)
                    user_info = message["message_info"]["user_info"]
                    if profile is not None and user_info["user_id"] == profile.user_id:
                        if profile.nickname:
                            user_info["user_nickname"] = profile.nickname
                        user_info["user_cardname"] = profile.cardname or None
        except ValueError as exc:
            self.ctx.logger.info("忽略 QQ 官方消息: %s | payload=%s", exc, summarize_payload(data))
            return
        message_id = message["message_id"]
        key = (scene, target, message_id)
        inbound_config = message.get("message_info", {}).get("additional_config", {})
        inbound_msg_idx = str(inbound_config.get("qq_official_msg_idx") or "").strip() if isinstance(inbound_config, Mapping) else ""
        inbound_ref_idx = str(inbound_config.get("qq_official_ref_msg_idx") or "").strip() if isinstance(inbound_config, Mapping) else ""
        reference_scope = (account_id, scene, target)
        # 在保存本条消息索引前解析引用，避免异常的自引用覆盖原消息映射。
        if inbound_ref_idx:
            quoted_id = self._lookup_message_id(inbound_ref_idx, reference_scope)
            if quoted_id:
                message["raw_message"].insert(0, {"type": "reply", "data": {"target_message_id": quoted_id}})
            else:
                self.ctx.logger.debug("QQ 官方入站引用未命中内存缓存: message_id=%s", message_id)
        async with self._reply_lock:
            now = time.time()
            self._replies = {
                reply_key: reply
                for reply_key, reply in self._replies.items()
                if now - reply[0] < REPLY_LIMITS[reply_key[0]][0]
            }
            inserted = key not in self._replies
            self._replies.setdefault(key, (float(message["timestamp"]), 0))
            if inbound_msg_idx:
                self._store_ref_idx(
                    message_id, inbound_msg_idx, received_at=float(message["timestamp"]), scope=reference_scope
                )
        try:
            # Host 拒收未就绪网关的消息。等待恢复，并与离线声明互斥，避免检查后断线的竞态。
            while True:
                await self._inbound_ready.wait()
                async with self._gateway_state_lock:
                    if not self._ready:
                        continue
                    accepted = await self.ctx.gateway.route_message(
                        gateway_name=GATEWAY_NAME,
                        message=message,
                        external_message_id=message_id,
                        dedupe_key=message_id,
                    )
                    break
        except BaseException:
            if inserted:
                async with self._reply_lock:
                    self._replies.pop(key, None)
            raise
        if not accepted and inserted:
            async with self._reply_lock:
                self._replies.pop(key, None)

    async def _build_attachments(
        self, payload: Mapping[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[str], bool, bool]:
        """下载并转换 QQ 附件；下载失败降级为文本标签，返回段、标签与图片/表情标志。"""
        attachments = payload.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            return [], [], False, False
        if len(attachments) > MAX_INBOUND_ATTACHMENTS:
            self.ctx.logger.warning(
                "QQ 入站附件超过 %d 个限制，仅处理前 %d 个", MAX_INBOUND_ATTACHMENTS, MAX_INBOUND_ATTACHMENTS
            )
            attachments = attachments[:MAX_INBOUND_ATTACHMENTS]

        content_hint = str(payload.get("content") or "").strip()
        has_single_inline_emoji = len(attachments) == 1 and bool(QQ_FACE_TAG_PATTERN.search(content_hint))
        segments: List[Dict[str, Any]] = []
        labels: List[str] = []
        is_picture = False
        is_emoji = False
        for attachment in attachments:
            if not isinstance(attachment, Mapping):
                continue
            content_type = str(attachment.get("content_type") or attachment.get("type") or "").strip().lower()
            attachment_type = str(attachment.get("type") or "").strip().lower()
            filename = str(attachment.get("filename") or "").strip()
            url = str(attachment.get("voice_wav_url") or attachment.get("url") or "").strip()
            is_emoji_attachment = (
                attachment_type in {"emoji", "face", "sticker"}
                or bool(attachment.get("is_emoji"))
                or has_single_inline_emoji
                or content_hint in {"[表情]", "[动画表情]"}
            )

            if content_type.startswith("image/") or attachment_type in {"image", "emoji", "face", "sticker"}:
                is_picture = True
                is_emoji = is_emoji or is_emoji_attachment
                binary = await self._download_attachment(url) if url else b""
                if binary:
                    segments.append(
                        {
                            "type": "emoji" if is_emoji_attachment else "image",
                            "data": "",
                            "binary_data_base64": base64.b64encode(binary).decode("ascii"),
                            "hash": sha256(binary).hexdigest(),
                        }
                    )
                else:
                    segments.append({"type": "text", "data": "[表情]" if is_emoji_attachment else "[图片]"})
                labels.append("[表情]" if is_emoji_attachment else "[图片]")
                continue

            if content_type == "voice" or content_type.startswith("audio/") or attachment_type == "voice":
                voice_text = str(attachment.get("asr_refer_text") or "[语音]").strip()
                binary = await self._download_attachment(url) if url else b""
                if binary:
                    segments.append(
                        {
                            "type": "voice",
                            "data": voice_text,
                            "binary_data_base64": base64.b64encode(binary).decode("ascii"),
                            "hash": sha256(binary).hexdigest(),
                        }
                    )
                else:
                    segments.append({"type": "file", "data": self._file_payload(attachment, content_type, url)})
                labels.append(voice_text)
                continue

            if url or filename:
                segments.append({"type": "file", "data": self._file_payload(attachment, content_type, url)})
                labels.append("[视频]" if content_type.startswith("video/") else f"[文件：{filename or '未命名'}]")
        return segments, labels, is_picture, is_emoji

    @staticmethod
    def _file_payload(attachment: Mapping[str, Any], content_type: str, url: str) -> Dict[str, Any]:
        """构造 MaiBot FileComponent 所需的稳定字段。"""
        return {
            "name": str(attachment.get("filename") or "").strip(),
            "size": str(attachment.get("size") or "").strip(),
            "url": url,
            "file_id": str(attachment.get("file_uuid") or "").strip(),
            "mime_type": content_type,
        }

    async def _download_attachment(self, url: str) -> bytes:
        """安全下载 QQ 入站附件；失败返回空字节，由调用方降级为文本标签。"""
        try:
            self._validate_media_url(url)
            session = self._require_session()
            async with session.get(url, allow_redirects=False) as response:
                if 300 <= response.status < 400:
                    raise RuntimeError(f"附件地址返回重定向: HTTP {response.status}")
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
                if response.content_length and response.content_length > MAX_INBOUND_MEDIA_BYTES:
                    raise RuntimeError(f"附件超过 {MAX_INBOUND_MEDIA_BYTES // 1024 // 1024} MiB 限制")
                chunks: List[bytes] = []
                total_size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total_size += len(chunk)
                    if total_size > MAX_INBOUND_MEDIA_BYTES:
                        raise RuntimeError(f"附件超过 {MAX_INBOUND_MEDIA_BYTES // 1024 // 1024} MiB 限制")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (ClientError, RuntimeError, TimeoutError, ValueError) as exc:
            self.ctx.logger.warning("QQ 入站附件下载失败: host=%s error=%s", self._safe_url_host(url), exc)
            return b""

    @staticmethod
    def _validate_media_url(url: str) -> None:
        """只允许 QQ 官方 HTTPS 媒体域名。"""
        if len(url) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in url):
            raise RuntimeError("QQ 附件 URL 过长或包含控制字符")
        try:
            parsed_url = urlsplit(url)
            parsed_port = parsed_url.port
        except ValueError as exc:
            raise RuntimeError("QQ 附件 URL 格式无效") from exc
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise RuntimeError("QQ 附件 URL 必须是有效的 HTTPS 地址")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise RuntimeError("QQ 附件 URL 不能包含用户凭据")
        if parsed_port not in {None, 443}:
            raise RuntimeError("QQ 附件 URL 端口无效")
        normalized_hostname = parsed_url.hostname.lower().rstrip(".")
        is_trusted_suffix = any(
            normalized_hostname == suffix or normalized_hostname.endswith(f".{suffix}")
            for suffix in TRUSTED_QQ_MEDIA_HOST_SUFFIXES
        )
        if normalized_hostname not in TRUSTED_QQ_MEDIA_HOSTS and not is_trusted_suffix:
            raise RuntimeError("QQ 附件 URL 不属于受信任的 QQ 媒体域名")

    @staticmethod
    def _safe_url_host(url: str) -> str:
        """安全提取用于日志的主机名，不回显查询参数。"""
        try:
            return urlsplit(url).hostname or "<无效>"
        except ValueError:
            return "<无效>"

    async def _set_ready(self, ready: bool, *, force: bool = False) -> None:
        async with self._gateway_state_lock:
            if self._ready == ready and not force:
                return
            self._ready = False
            self._inbound_ready.clear()
            if ready and self.config.qq_official.snowluma_identity_only:
                # 先确认 SL 已暂停，再开放官方路由，消除重连后等待后台续租的双发送窗口。
                await self._renew_identity_only(connecting=True)
            accepted = await self.ctx.gateway.update_state(
                gateway_name=GATEWAY_NAME,
                ready=ready,
                platform=PLATFORM,
                # 统一账号模式下路由身份跟随统一账号，否则入站 RouteKey 与注册身份不一致会被接收路由表丢弃
                account_id=self._unified_account_id or self._account_id,
                metadata={"connection": "qq_official", "protocol": "qq_official"},
            )
            self._ready = ready and accepted
            if self._ready:
                self._inbound_ready.set()
            if ready and not accepted:
                raise RuntimeError("MaiBot 未接受 QQ 官方消息网关路由")

    async def _disconnect(self) -> None:
        self._resuming = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.ctx.logger.exception("QQ 网关心跳任务退出异常")
            self._heartbeat_task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        await self._set_ready(False)

    def _resolve_group_openid(self, group_id: str) -> str:
        """统一群号反查回 group_openid；未绑定时按原值调用（可能本就是 OpenID）。"""
        for openid, unified in self._id_map.get("group", {}).items():
            if unified == group_id:
                return openid
        return group_id

    def _resolve_member_openid(self, user_id: str, additional_config: Any) -> str:
        """解析目标用户的 OpenID：消息原始路由信息优先，其次统一 ID 反查。"""
        if isinstance(additional_config, Mapping):
            if openid := str(additional_config.get("qq_official_user_openid") or "").strip():
                return openid
        return self._resolve_user_openid(user_id)

    def _resolve_user_openid(self, user_id: str) -> str:
        """统一用户 ID 反查回 OpenID。"""
        for openid, unified in self._id_map.get("user", {}).items():
            if unified == user_id:
                return openid
        # 未绑定的用户 user_id 本身就是 OpenID；纯数字是无法反查的 QQ 号。
        return "" if user_id.isdigit() else user_id

    @API("adapter.avatar.get", description="获取独立用户或群头像资源", version="1", public=True)
    async def api_get_avatar(
        self,
        platform: str,
        target_id: str,
        target_type: str = "user",
        account_id: str = "",
        scope: str = "",
    ) -> Dict[str, Any]:
        """统一头像协议：只返回资源 URL，图片由 Host 按有效期独立缓存。"""
        if platform != PLATFORM:
            return {"status": "unsupported"}
        if target_type not in {"user", "group"}:
            raise ValueError("target_type 必须为 user 或 group")
        normalized_target_id = target_id.strip()
        if not normalized_target_id:
            raise ValueError("target_id 必须非空")

        # OpenID 不能用于 QQ 头像 URL；已绑定时使用对应的真实 QQ 号或群号。
        normalized_target_id = self._id_map[target_type].get(normalized_target_id, normalized_target_id).strip()
        if re.fullmatch(r"[0-9]+", normalized_target_id) is None:
            return {"status": "unsupported"}
        normalized_id = int(normalized_target_id)
        if normalized_id <= 0:
            raise ValueError("target_id 必须为正整数")
        url = (
            f"https://p.qlogo.cn/gh/{normalized_id}/{normalized_id}/640"
            if target_type == "group"
            else f"https://q1.qlogo.cn/g?b=qq&nk={normalized_id}&s=640"
        )
        return {"status": "available", "url": url, "expires_in": 86400}

    @Tool(
        MUTE_TOOL_NAME,
        platforms=PLATFORM,
        description=(
            "在 QQ 群聊中根据消息 ID 禁言该消息的发送者。"
            "适用于刷屏、违规发言等情况。群主与管理员平台不允许禁言；"
            "目标用户需能解析出 OpenID（其消息经本适配器收到，或已通过 /assign_id 绑定）。"
        ),
        parameters=[
            ToolParameterInfo(
                name="msg_id",
                param_type=ToolParamType.STRING,
                description="要禁言的目标所发送的消息 ID。",
                required=True,
            ),
            ToolParameterInfo(
                name="duration",
                param_type=ToolParamType.INTEGER,
                description="禁言时长，单位秒，正整数。",
                required=True,
            ),
            ToolParameterInfo(
                name="reason",
                param_type=ToolParamType.STRING,
                description="禁言原因，可选。",
                required=False,
            ),
        ],
        enabled=False,
        visibility="visible",
    )
    async def tool_mute(
        self,
        msg_id: Any = "",
        duration: Any = "",
        reason: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """根据消息 ID 禁言发送者（QQ 官方 restrict_chat_setting 接口）；群与用户均从目标消息解析。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if not self._ready:
            return {"success": False, "error": "QQ 官方网关未连接"}
        mute_settings = self.config.mute

        normalized_msg_id = str(msg_id or "").strip()
        if not normalized_msg_id:
            return {"success": False, "error": "缺少目标消息 ID"}
        try:
            normalized_duration = int(str(duration).strip())
        except (TypeError, ValueError):
            return {"success": False, "error": "禁言时长必须是正整数（秒）"}
        if normalized_duration <= 0:
            return {"success": False, "error": "禁言时长必须是正整数（秒）"}
        normalized_duration = max(mute_settings.min_duration, min(normalized_duration, mute_settings.max_duration))

        # 通过消息 ID 定位发送者，复用宿主消息查询能力。
        query_result = await self.ctx.message.get_by_id(
            normalized_msg_id,
            stream_id=stream_id,
            include_binary_data=False,
        )
        message_info = query_result.get("message_info", {}) if isinstance(query_result, Mapping) else {}
        user_info = message_info.get("user_info", {}) if isinstance(message_info, Mapping) else {}
        additional_config = message_info.get("additional_config") if isinstance(message_info, Mapping) else None
        target_user_id = str(user_info.get("user_id") or "").strip()
        if not target_user_id:
            return {"success": False, "error": f"未找到消息 {normalized_msg_id} 或其缺少发送者信息"}
        target_display_name = (
            str(user_info.get("user_cardname") or "").strip()
            or str(user_info.get("user_nickname") or "").strip()
            or target_user_id
        )

        group_info = message_info.get("group_info") if isinstance(message_info, Mapping) else None
        normalized_group_id = str(group_info.get("group_id") or "").strip() if isinstance(group_info, Mapping) else ""
        if not normalized_group_id:
            return {"success": False, "error": "禁言仅支持群聊，目标消息不属于群聊"}
        group_openid = (
            str(additional_config.get("qq_official_group_openid") or "").strip()
            if isinstance(additional_config, Mapping)
            else ""
        ) or self._resolve_group_openid(normalized_group_id)
        allowed_groups = [str(item).strip() for item in mute_settings.allowed_groups if str(item).strip()]
        if allowed_groups and normalized_group_id not in allowed_groups and group_openid not in allowed_groups:
            return {"success": False, "error": f"群 {normalized_group_id} 不在禁言白名单内"}

        # 保护名单：配置内用户一律不禁言（统一 QQ 号或 OpenID 均可命中）。
        member_openid = self._resolve_member_openid(target_user_id, additional_config)
        admin_users = [str(item).strip() for item in mute_settings.admin_users if str(item).strip()]
        if target_user_id in admin_users or (member_openid and member_openid in admin_users):
            return {"success": False, "error": f"用户 {target_display_name} 在禁言保护名单中，无法被禁言"}
        if not member_openid:
            return {
                "success": False,
                "error": f"无法解析 {target_display_name} 的 OpenID，需其消息经本适配器接收或先 /assign_id 绑定",
            }

        # 官方接口按 RFC3339 到期时间设置禁言，最长 30 天。
        expire_at = (datetime.now(timezone.utc) + timedelta(seconds=normalized_duration)).isoformat().replace(
            "+00:00", "Z"
        )
        try:
            session = self._require_session()
            token = await self._authorize()
            async with session.post(
                self._api_url(f"/v2/groups/{quote(group_openid, safe='')}/restrict_chat_setting"),
                headers={"Authorization": f"QQBot {token}"},
                json={
                    "members": [
                        {"op": "add", "member_openid": member_openid, "mute_expire_at": expire_at},
                    ]
                },
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    return {"success": False, "error": f"QQ 禁言失败: HTTP {response.status} {body[:200]}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        normalized_reason = str(reason or "").strip()
        self.ctx.logger.info(
            "QQ 官方禁言: group=%s user=%s(%s) duration=%ss reason=%s",
            normalized_group_id,
            target_display_name,
            member_openid,
            normalized_duration,
            normalized_reason or "<none>",
        )
        return {
            "success": True,
            "content": f"已禁言 {target_display_name}，时长 {normalized_duration} 秒。",
            "group_id": normalized_group_id,
            "user_id": target_user_id,
            "duration": normalized_duration,
            "reason": normalized_reason,
        }

    @Tool(
        RECALL_TOOL_NAME,
        platforms=PLATFORM,
        description=(
            "撤回一条 QQ 消息。发送超过 2 分钟的消息不可撤回；"
            "群聊中机器人是群管理员时可撤回自己和普通群成员的消息，否则只能撤回自己发送的消息；"
            "单聊只能撤回机器人自己发送的消息。msg_id 使用聊天记录中的消息 ID。"
        ),
        parameters=[
            ToolParameterInfo(
                name="msg_id",
                param_type=ToolParamType.STRING,
                description="要撤回的消息 ID，来自聊天记录中的消息 ID。",
                required=True,
            ),
        ],
        enabled=False,
        visibility="visible",
    )
    async def tool_recall_message(self, msg_id: Any = "", **kwargs: Any) -> Dict[str, Any]:
        """撤回指定消息（QQ 官方 DELETE /v2/groups|users/{openid}/messages/{message_id} 接口）。

        聊天记录中的消息 ID 即平台真实 ID：入站消息透传官方 d.id，机器人自发消息由发送回执回填。
        会话目标（群/用户）从消息的路由信息解析。
        """
        stream_id = str(kwargs.get("stream_id") or "")
        if not self._ready:
            return {"success": False, "error": "QQ 官方网关未连接"}
        normalized_msg_id = str(msg_id or "").strip()
        if not normalized_msg_id:
            return {"success": False, "error": "缺少目标消息 ID"}

        query_result = await self.ctx.message.get_by_id(
            normalized_msg_id,
            stream_id=stream_id,
            include_binary_data=False,
        )
        message_info = query_result.get("message_info") if isinstance(query_result, Mapping) else None
        if not isinstance(message_info, Mapping):
            return {"success": False, "error": f"未找到消息 {normalized_msg_id}"}
        additional_config = message_info.get("additional_config")
        additional_config = additional_config if isinstance(additional_config, Mapping) else {}
        group_info = message_info.get("group_info")
        group_id = (str(group_info.get("group_id") or "").strip() if isinstance(group_info, Mapping) else "") or str(
            additional_config.get("platform_io_target_group_id") or ""
        ).strip()

        if group_id:
            openid = str(additional_config.get("qq_official_group_openid") or "").strip() or self._resolve_group_openid(
                group_id
            )
            allowed_groups = [str(item).strip() for item in self.config.recall.allowed_groups if str(item).strip()]
            if allowed_groups and group_id not in allowed_groups and openid not in allowed_groups:
                return {"success": False, "error": f"群 {group_id} 不在撤回白名单内"}
            path = f"/v2/groups/{quote(openid, safe='')}/messages/{quote(normalized_msg_id, safe='')}"
            scope_text = f"群 {group_id}"
        else:
            # 入站单聊消息带发送者 OpenID；机器人自发消息只有目标用户的统一 ID，需反查。
            user_id = str(additional_config.get("platform_io_target_user_id") or "").strip()
            openid = str(additional_config.get("qq_official_user_openid") or "").strip() or self._resolve_user_openid(
                user_id
            )
            path = f"/v2/users/{quote(openid, safe='')}/messages/{quote(normalized_msg_id, safe='')}"
            scope_text = "单聊"
        # 纯数字是未绑定 OpenID 的群号/QQ 号，通常意味着该消息不是经本适配器收发的。
        if not openid or openid.isdigit():
            return {"success": False, "error": f"无法解析消息 {normalized_msg_id} 所在会话的 OpenID"}

        try:
            session = self._require_session()
            token = await self._authorize()
            async with session.delete(self._api_url(path), headers={"Authorization": f"QQBot {token}"}) as response:
                if response.status >= 400:
                    body = await response.text()
                    return {"success": False, "error": f"QQ 撤回失败: HTTP {response.status} {body[:200]}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        self.ctx.logger.info("QQ 官方撤回: %s msg_id=%s", scope_text, normalized_msg_id)
        return {
            "success": True,
            "content": f"已撤回消息 {normalized_msg_id}（{scope_text}）。",
            "msg_id": normalized_msg_id,
            "group_id": group_id,
        }

    @MessageGateway(
        name=GATEWAY_NAME,
        route_type="duplex",
        platform=PLATFORM,
        protocol="qq_official",
        description="QQ 官方机器人群聊与单聊消息网关（文本、@、图片、表情、语音）",
    )
    async def handle_qq_official_gateway(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del route, metadata, kwargs
        if not self._ready:
            return {"success": False, "error": "QQ 官方消息网关未就绪"}
        sent_ids: List[str] = []
        try:
            # 统一 ID 反查回 OpenID 后才能调用官方 API
            reverse_map = {kind: {v: k for k, v in mapping.items()} for kind, mapping in self._id_map.items()}
            group_info = message.get("message_info", {}).get("group_info") or {}
            group_id = str(group_info.get("group_id") or "")
            group_openid = reverse_map["group"].get(group_id, group_id)
            if group_openid:
                reverse_map["user"] = {value: key for key, value in self._group_user_map(group_openid).items()}
            # 媒体 Base64 解码和内容哈希放在线程池，避免大段语音占住网关事件循环。
            scene, target, parts, reply_target_id = await asyncio.to_thread(
                encode_message, message, reverse_map=reverse_map
            )
            # 被动回复窗口按入站记录的统一 ID 匹配（encode 返回的 target 已反查为 OpenID）。
            route_target = self._id_map.get("group" if scene == "group" else "user", {}).get(target, target)
            reference_scope = (self._account_id, scene, route_target)
            reply_to = str(message.get("reply_to") or "").strip()
            # 只有标准 reply 段代表宿主决定显示引用；reply_to 是回复上下文及被动发送凭证，
            # 未选中引用时也可能存在，不能据此绕过主程序的引用频率控制。
            quote_target = reply_target_id
            base_path = f"/v2/{'groups' if scene == 'group' else 'users'}/{quote(target, safe='')}"
            # 官方一条消息只承载一种内容，文本、图片与语音按原顺序逐条发送。
            for index, part in enumerate(parts):
                if part["kind"] == "media":
                    file_info = await self._upload_media(
                        base_path, part["binary"], part["file_name"], file_type=part["file_type"]
                    )
                    body: Dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
                else:
                    body = part["body"]
                # 引用样式只挂在第一条分片；message_reference 与被动凭证 msg_id/msg_seq 互不替代。
                # 但 msg_type=2 的 markdown（含 @ 段）携带引用会在 Mac QQ / 部分手机 QQ
                # 重复渲染正文（卡片 + 纯文本），因此 markdown 不挂引用，文本/图片照常。
                supports_reference = body.get("msg_type") != 2
                if index == 0 and supports_reference:
                    reference_fields = self._build_message_reference(quote_target)
                else:
                    reference_fields = {}
                if index == 0 and quote_target and not supports_reference:
                    self.ctx.logger.debug(
                        "Markdown 消息不携带 message_reference 以避免客户端重复渲染: target=%s", quote_target
                    )
                elif index == 0 and quote_target and not reference_fields:
                    self.ctx.logger.debug(
                        "QQ 官方出站缺少引用索引，消息不携带 message_reference: target=%s", quote_target
                    )
                passive_fields = await self._claim_passive_fields(scene, route_target, reply_to)
                result = await self._api_post(
                    f"{base_path}/messages", {**body, **reference_fields, **passive_fields}
                )
                if not result.get("id"):
                    raise RuntimeError("QQ 发送失败: 响应缺少消息 ID")
                self._store_send_ref_idx(result, scope=reference_scope)
                if reference_fields:
                    self.ctx.logger.info(
                        "QQ 官方消息已携带引用: message_reference=%s",
                        reference_fields["message_reference"]["message_id"],
                    )
                sent_ids.append(str(result["id"]))
            return {"success": True, "external_message_id": sent_ids[0]}
        except Exception as exc:
            if sent_ids:
                exc = RuntimeError(f"已发送 {len(sent_ids)} 条后失败: {exc}")
            self.ctx.logger.warning("QQ 官方消息发送失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def _store_ref_idx(
        self,
        message_id: str,
        ref_idx: str,
        received_at: Optional[float] = None,
        *,
        scope: Optional[Tuple[str, str, str]] = None,
    ) -> None:
        """保存平台消息 id 与官方引用索引（REFIDX）的映射，单事件循环内同步调用。"""
        message_id = str(message_id or "").strip()
        ref_idx = str(ref_idx or "").strip()
        if not message_id or not ref_idx:
            return
        now = time.time()
        stored_at = received_at if received_at is not None else now
        if self._ref_idx:
            self._ref_idx = {
                mid: item for mid, item in self._ref_idx.items() if now - item[0] < REF_IDX_TTL_SEC
            }
        self._ref_idx[message_id] = (stored_at, ref_idx, scope)
        overflow = len(self._ref_idx) - MAX_REF_IDX_ENTRIES
        if overflow > 0:
            for old_id in sorted(self._ref_idx, key=lambda mid: self._ref_idx[mid][0])[:overflow]:
                self._ref_idx.pop(old_id, None)

    def _lookup_ref_idx(self, message_id: str) -> str:
        """按平台消息 id 查官方引用索引；过期或不存在时返回空串（调用方按无引用降级）。"""
        message_id = str(message_id or "").strip()
        if not message_id:
            return ""
        item = self._ref_idx.get(message_id)
        if item is None:
            return ""
        stored_at, ref_idx, _ = item
        if time.time() - stored_at >= REF_IDX_TTL_SEC:
            self._ref_idx.pop(message_id, None)
            return ""
        return ref_idx

    def _lookup_message_id(self, ref_idx: str, scope: Tuple[str, str, str]) -> str:
        """在同一账号和会话内反查消息 ID；复用最多 2000 条的缓存，不另建反向表。"""
        now = time.time()
        # 反查时也清理过期条目；容量淘汰、停用清空直接沿用同一份缓存的生命周期。
        self._ref_idx = {
            mid: item for mid, item in self._ref_idx.items() if now - item[0] < REF_IDX_TTL_SEC
        }
        return next(
            (
                mid
                for mid, (_, cached_idx, cached_scope) in self._ref_idx.items()
                if cached_idx == ref_idx and cached_scope == scope
            ),
            "",
        )

    def _build_message_reference(self, target_message_id: str) -> Dict[str, Any]:
        """把被回复消息 id 转成官方 message_reference；没有可用 REFIDX 时返回空字典。"""
        ref_idx = self._lookup_ref_idx(str(target_message_id or "").strip())
        if not ref_idx:
            return {}
        return {"message_reference": {"message_id": ref_idx}}

    def _store_send_ref_idx(
        self, result: Mapping[str, Any], *, scope: Optional[Tuple[str, str, str]] = None
    ) -> None:
        """保存自发消息回执 ext_info.ref_idx，使后续引用机器人消息也能渲染引用。"""
        message_id = str(result.get("id") or "").strip()
        ext_info = result.get("ext_info")
        ref_idx = str(ext_info.get("ref_idx") or "").strip() if isinstance(ext_info, Mapping) else ""
        if message_id and ref_idx:
            self._store_ref_idx(message_id, ref_idx, scope=scope)

    async def _claim_passive_fields(self, scene: str, route_target: str, reply_to: str) -> Dict[str, Any]:
        """为一次发送占用被动回复序号；无入站上下文或被动窗口已失效时返回空字典。

        省略 msg_id/msg_seq 时由平台按主动消息额度受理，发送是否成功以平台响应为准。
        """
        async with self._reply_lock:
            if reply_to:
                key: Optional[Tuple[str, str, str]] = (scene, route_target, reply_to)
            else:
                candidates = (key for key in self._replies if key[:2] == (scene, route_target))
                key = max(candidates, key=lambda candidate: self._replies[candidate][0], default=None)
            reference = self._replies.get(key) if key is not None else None
            if key is None or reference is None:
                return {}
            received_at, used = reference
            ttl, limit = REPLY_LIMITS[scene]
            if time.time() - received_at >= ttl or used >= limit:
                return {}
            # HTTP 超时无法证明 QQ 未接受消息，发起发送后不再复用该序号。
            sequence = used + 1
            self._replies[key] = (received_at, sequence)
            return {"msg_id": key[2], "msg_seq": sequence}

    async def _api_post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """调用官方 OpenAPI（POST），HTTP 错误时带上平台错误码与说明抛出。"""
        session = self._require_session()
        token = await self._authorize()
        async with session.post(
            self._api_url(path),
            headers={"Authorization": f"QQBot {token}"},
            json=dict(payload),
        ) as response:
            text = await response.text()
            try:
                result = json.loads(text) if text.strip() else {}
            except ValueError:
                result = None
            if response.status >= 400 or not isinstance(result, Mapping):
                detail = (
                    f"code={result.get('code')} message={result.get('message')}"
                    if isinstance(result, Mapping)
                    else text[:200]
                )
                raise RuntimeError(f"QQ 接口 {path.rsplit('/', 1)[-1]} 失败: HTTP {response.status} {detail}")
        return result

    async def _upload_media(self, base_path: str, binary: bytes, file_name: str, *, file_type: int) -> str:
        """按官方分片上传流程上传图片或语音，返回用于 msg_type=7 的 file_info。

        流程：upload_prepare 取预签名分片 URL → 逐片 PUT 并 upload_part_finish → 携带 upload_id 调用 files 合并。
        """
        def prepare_metadata() -> Dict[str, Any]:
            # 完整媒体的多次哈希可能较慢，不在事件循环内计算。
            return {
                "file_type": file_type,
                "file_size": str(len(binary)),
                "file_name": file_name,
                "md5": md5(binary).hexdigest(),
                "sha1": sha1(binary).hexdigest(),
                "md5_10m": md5(binary[:MD5_10M_BYTES]).hexdigest(),
            }

        prepare = await self._api_post(
            f"{base_path}/upload_prepare",
            await asyncio.to_thread(prepare_metadata),
        )
        upload_id = str(prepare.get("upload_id") or "")
        block_size = int(prepare.get("block_size") or 0)
        upload_parts = prepare.get("parts")
        if not upload_id or block_size <= 0 or not isinstance(upload_parts, list) or not upload_parts:
            raise RuntimeError("QQ 媒体预上传响应缺少 upload_id、block_size 或 parts")
        # 文档写分片序号从 0 开始，实测（2026-09-30）返回从 1 开始，按实际最小序号计算偏移。
        index_base = min(int(upload_part["index"]) for upload_part in upload_parts)
        if index_base not in (0, 1):
            raise RuntimeError(f"QQ 媒体预上传返回的分片序号起点无效: {index_base}")
        session = self._require_session()
        for upload_part in upload_parts:
            index = int(upload_part["index"])
            presigned_url = str(upload_part.get("presigned_url") or "")
            if not presigned_url.startswith("https://"):
                raise RuntimeError(f"QQ 媒体分片 {index} 的预签名地址无效")
            offset = (index - index_base) * block_size
            chunk = binary[offset : offset + block_size]
            if not chunk:
                raise RuntimeError(f"QQ 媒体分片 {index} 超出文件范围")
            # 预签名 URL 自带鉴权，不能携带 QQBot Authorization 头。
            async with session.put(presigned_url, data=chunk) as response:
                if response.status >= 400:
                    raise RuntimeError(f"QQ 媒体分片 {index} 上传失败: HTTP {response.status}")
            chunk_md5 = await asyncio.to_thread(md5, chunk)
            await self._api_post(
                f"{base_path}/upload_part_finish",
                {
                    "upload_id": upload_id,
                    "part_index": index,
                    "block_size": str(len(chunk)),
                    "md5": chunk_md5.hexdigest(),
                },
            )
        result = await self._api_post(
            f"{base_path}/files",
            {"file_type": file_type, "srv_send_msg": False, "file_name": file_name, "upload_id": upload_id},
        )
        file_info = str(result.get("file_info") or "")
        if not file_info:
            raise RuntimeError("QQ 媒体上传响应缺少 file_info")
        return file_info


def create_plugin() -> QQOfficialAdapterPlugin:
    return QQOfficialAdapterPlugin()

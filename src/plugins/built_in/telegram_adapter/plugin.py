from contextlib import suppress
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from maibot_sdk import API, ConfigWidget, ConfigWidgetState, HookHandler, MaiBotPlugin, MessageGateway, PluginConfigBase, Tool

import asyncio
import hashlib
import msgpack
import traceback

from .api import TelegramAPIError, TelegramClient, TelegramTransportError
from .avatar import AVATAR_TTL, AvatarRelay, avatar_mime
from .codec import (
    PLATFORM,
    binary_segment,
    decode_message,
    decode_reaction,
    encode_segments,
    external_id,
    outbound_target,
    outbound_thread,
    parse_group_id,
    parse_message_id,
    reply_parameters,
)
from .media import prepare_image, sticker_to_image, voice_to_opus, voice_to_wav
from .settings import AdapterSettings
from .storage import AdapterStore

GATEWAY_NAME = "telegram_gateway"
MAX_GATEWAY_MESSAGE_BYTES = 16 * 1024 * 1024 - 64 * 1024


def validate_message_size(message: Dict[str, Any]) -> None:
    # 宿主 IPC 每帧上限 16 MB，预留信封空间，避免大媒体让传输连接直接断开。
    size = len(msgpack.packb(message, use_bin_type=True))
    if size > MAX_GATEWAY_MESSAGE_BYTES:
        raise ValueError(
            f"消息 {message.get('message_id', '<未知>')} 转换后超过 MaiBot 网关的 16 MB 帧限制: {size} bytes"
        )


class TelegramAdapterPlugin(MaiBotPlugin):
    config_model: ClassVar[Optional[Type[PluginConfigBase]]] = AdapterSettings

    config_widgets: ClassVar[List[ConfigWidget]] = [
        ConfigWidget(id="connection", kind="status", label="Telegram 连接状态"),
        ConfigWidget(id="reconnect", kind="action", label="重新连接"),
    ]

    async def get_config_widget_state(self) -> Dict[str, ConfigWidgetState]:
        configured = bool(self.config.telegram.bot_token.strip())
        text = (f"已连接：@{self._username}" if self._ready
                else "连接中 / 等待重连" if self._worker is not None and not self._worker.done()
                else "尚未连接" if configured else "请先填写 Bot Token；Telegram Bot API 无需扫码登录")
        return {
            "connection": ConfigWidgetState(text=text, tone="success" if self._ready else "neutral"),
            "reconnect": ConfigWidgetState(disabled=not configured),
        }

    async def on_config_widget_action(self, action_id: str) -> None:
        if action_id != "reconnect":
            raise ValueError(f"未知配置页操作: {action_id}")
        async with self._lifecycle_lock:
            await self._stop()
            if self.config.plugin.enabled:
                self._start()

    def __init__(self) -> None:
        super().__init__()
        self._worker: Optional[asyncio.Task[None]] = None
        self._client: Optional[TelegramClient] = None
        self._store: Optional[AdapterStore] = None
        self._account_id = ""
        self._username = ""
        self._ready = False
        self._lifecycle_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._avatar_lock = asyncio.Lock()
        self._avatar_relay = AvatarRelay()
        self._typing_tasks: Dict[Tuple[int, Optional[int]], asyncio.Task[None]] = {}

    async def on_load(self) -> None:
        self._store = AdapterStore(self.ctx.paths.data_dir / "telegram.sqlite3")
        await self._store.initialize()
        if self.config.plugin.enabled:
            self._start()

    async def on_unload(self) -> None:
        async with self._lifecycle_lock:
            await self._stop()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        del version
        if scope != "self":
            return
        async with self._lifecycle_lock:
            await self._stop()
            self.set_plugin_config(config_data)
            if self.config.plugin.enabled:
                self._start()

    def _start(self) -> None:
        if not self.config.telegram.bot_token.strip():
            self.ctx.logger.error("Telegram 适配器缺少 bot_token，请在插件配置中填写")
            return
        self._worker = asyncio.create_task(self._run(), name="telegram-adapter")

    async def _stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        async with self._avatar_lock:
            await self._avatar_relay.close()
        self._account_id = ""
        self._username = ""

    def _require_store(self) -> AdapterStore:
        if self._store is None:
            raise RuntimeError("Telegram 存储尚未初始化")
        return self._store

    async def _set_ready(self, ready: bool) -> None:
        accepted = await self.ctx.gateway.update_state(
            gateway_name=GATEWAY_NAME,
            ready=ready,
            platform=PLATFORM,
            account_id=self._account_id,
            metadata={"connection": "telegram_bot_api"},
        )
        self._ready = ready and accepted
        if ready and not accepted:
            raise RuntimeError("MaiBot 未接受 Telegram 网关路由")

    async def _run(self) -> None:
        settings = self.config.telegram
        client: Optional[TelegramClient] = None
        try:
            client = TelegramClient(
                settings.bot_token.strip(),
                settings.api_base_url.strip(),
                settings.proxy_url.strip(),
                settings.poll_timeout_sec,
                settings.max_download_mb * 1024 * 1024,
            )
            self._client = client
            store = self._require_store()
            while True:
                try:
                    bot = await client.call("getMe")
                    self._account_id = str(bot["id"])
                    self._username = bot["username"]
                    webhook = await client.call("getWebhookInfo")
                    if webhook.get("url"):
                        raise RuntimeError("该机器人已经配置 Webhook，请先通过 deleteWebhook 移除后再启用长轮询")
                    offset = await store.offset(self._account_id)
                    await self._set_ready(True)
                    self.ctx.logger.info("Telegram 网关已就绪: @%s account=%s", self._username, self._account_id)
                    while True:
                        offset = await store.offset(self._account_id)
                        updates = await client.call(
                            "getUpdates",
                            {
                                "offset": offset,
                                "timeout": settings.poll_timeout_sec,
                                "allowed_updates": ["message", "message_reaction", "message_reaction_count"],
                            },
                        )
                        for update in updates:
                            if update["update_id"] < offset:
                                continue
                            await self._process_update(update, client)
                            offset = update["update_id"] + 1
                except TelegramAPIError as exc:
                    if exc.code < 500 and exc.code != 429:
                        raise
                    await self._set_ready(False)
                    self.ctx.logger.warning("Telegram 连接暂时不可用: %s", exc)
                    await asyncio.sleep(settings.reconnect_delay_sec)
                except TelegramTransportError as exc:
                    await self._set_ready(False)
                    self.ctx.logger.warning("%s；稍后重连", exc)
                    await asyncio.sleep(settings.reconnect_delay_sec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 连接鉴权、持久化等任务级错误保留未确认更新；内容错误由单条更新边界隔离。
            self.ctx.logger.error("Telegram 接收任务停止，未确认的更新将保留: %s", exc, exc_info=True)
        finally:
            self._ready = False
            for target in list(self._typing_tasks):
                await self._stop_typing(*target)
            try:
                await self._set_ready(False)
            finally:
                self._client = None
                if client is not None:
                    await client.close()

    async def _process_update(self, update: Dict[str, Any], client: TelegramClient) -> None:
        store = self._require_store()
        try:
            await self._receive_update(update, client)
        except Exception as exc:
            # 暂时性连接错误继续重连重试；鉴权/轮询冲突属于整个连接，不能确认为坏消息。
            if isinstance(exc, TelegramTransportError) or (
                isinstance(exc, TelegramAPIError) and (exc.code >= 500 or exc.code in {401, 409, 429})
            ):
                raise
            await store.save_failed_update(self._account_id, update, str(exc), traceback.format_exc())
            self.ctx.logger.error(
                "Telegram 单条更新处理失败，已保存至 failed_updates，继续接收: update_id=%s error=%s",
                update["update_id"], exc, exc_info=True,
            )
            return
        # 主程序策略拒收同样表示已经处理；成功更新不写入失败表。
        await store.save_offset(self._account_id, update["update_id"] + 1)

    async def _receive_update(self, update: Dict[str, Any], client: TelegramClient) -> None:
        if "message_reaction" in update or "message_reaction_count" in update:
            message = decode_reaction(update, self._account_id)
            if message is not None:
                group = await self._require_store().message_group(self._account_id, message["reply_to"])
                if group is not None:
                    message["message_info"]["group_info"] = group
                elif message["message_info"]["additional_config"]["telegram_event_payload"]["chat"].get("is_forum"):
                    # 表态更新没有 thread_id；未知原消息不能错误地路由到 General。
                    self.ctx.logger.info("跳过未知话题归属的 Telegram 表态: %s", message["reply_to"])
                    return
                await self._route_inbound(message)
            return
        payload = update.get("message")
        if payload is None:
            return
        chat = payload["chat"]
        if chat["type"] not in {"private", "group", "supergroup"}:
            self.ctx.logger.info("跳过未支持的 Telegram 聊天类型: chat=%s", chat["id"])
            return
        topic_name = ""
        if payload.get("is_topic_message"):
            topic = payload.get("forum_topic_created") or payload.get("forum_topic_edited") or {}
            topic_name = await self._require_store().topic_name(
                self._account_id, str(chat["id"]), payload["message_thread_id"], topic.get("name", "")
            )
        sender = payload.get("from")
        if sender is not None and str(sender["id"]) == self._account_id and not payload.get("sender_chat"):
            return
        media: List[Dict[str, Any]] = []
        runtime = self.ctx.paths.runtime_dir
        timeout = self.config.telegram.media_timeout_sec
        photos = payload.get("photo")
        if photos:
            photo = max(photos, key=lambda item: item["width"] * item["height"])
            binary = await client.download(photo["file_id"])
            media.append(await asyncio.to_thread(binary_segment, "image", binary))
        sticker = payload.get("sticker")
        if sticker:
            binary = await client.download(sticker["file_id"])
            rendered = await asyncio.to_thread(
                sticker_to_image,
                binary,
                sticker["is_animated"],
                sticker["is_video"],
                runtime,
                timeout,
            )
            segment = await asyncio.to_thread(binary_segment, "emoji", rendered, sticker.get("emoji", ""))
            await self._require_store().save_sticker(self._account_id, segment["hash"], sticker["file_id"])
            media.append(segment)
        voice = payload.get("voice") or payload.get("audio")
        if voice:
            binary = await client.download(voice["file_id"])
            wav = await asyncio.to_thread(voice_to_wav, binary, runtime, timeout)
            media.append(await asyncio.to_thread(binary_segment, "voice", wav))
        # TG 以“文件”方式发送的图片和音频同样接入；动图按表情段交给宿主。
        document = payload.get("document")
        animation = payload.get("animation")
        if animation:
            binary = await client.download(animation["file_id"])
            if animation.get("mime_type") != "image/gif":
                binary = await asyncio.to_thread(sticker_to_image, binary, False, True, runtime, timeout)
            media.append(await asyncio.to_thread(binary_segment, "emoji", binary))
        elif document:
            mime = str(document.get("mime_type", ""))
            if mime.startswith("image/") and mime != "image/svg+xml":
                binary = await client.download(document["file_id"])
                media.append(await asyncio.to_thread(binary_segment, "image", binary))
            elif mime.startswith("audio/"):
                binary = await client.download(document["file_id"])
                wav = await asyncio.to_thread(voice_to_wav, binary, runtime, timeout)
                media.append(await asyncio.to_thread(binary_segment, "voice", wav))
            else:
                self.ctx.logger.info("Telegram 文件类型不支持: message=%s mime=%s", payload["message_id"], mime)
        message = await asyncio.to_thread(decode_message, payload, self._account_id, self._username, media, topic_name)
        if message is None:
            return
        if "group_info" in message["message_info"]:
            await self._require_store().save_message_group(
                self._account_id, message["message_id"], message["message_info"]["group_info"]
            )
        await self._route_inbound(message)

    async def _route_inbound(self, message: Dict[str, Any]) -> None:
        await asyncio.to_thread(validate_message_size, message)
        accepted = await self.ctx.gateway.route_message(
            gateway_name=GATEWAY_NAME,
            message=message,
            external_message_id=message["message_id"],
            dedupe_key=message["message_id"],
        )
        if not accepted:
            self.ctx.logger.info("MaiBot 未接收 Telegram 消息: %s", message["message_id"])

    @HookHandler(
        "maisaka.replyer.before_request",
        name="telegram_reply_typing",
        description="选中 reply 并开始生成回复时显示 Telegram 输入状态",
        timeout_ms=3000,
    )
    async def on_reply_generation(self, session_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        if not session_id or not self._ready or self._client is None:
            return {"action": "continue"}
        streams = await self._get_streams()
        matches = [stream for stream in streams if stream["session_id"] == session_id]
        if len(matches) != 1:
            return {"action": "continue"}
        stream = matches[0]
        if stream["platform"] != PLATFORM or stream["account_id"] != self._account_id or stream["scope"]:
            return {"action": "continue"}
        target_id = stream["group_id"] if stream["is_group_session"] else stream["user_id"]
        chat_id, thread_id = parse_group_id(target_id)
        self._start_typing(chat_id, thread_id, self._client)
        return {"action": "continue"}

    async def _get_streams(self) -> List[Dict[str, Any]]:
        # SDK 已将 Host 的 success/streams 信封解包，成功时直接返回聊天流列表。
        streams = await self.ctx.chat.get_all_streams(platform=PLATFORM)
        if isinstance(streams, dict) and streams.get("success") is False:
            raise RuntimeError(f"查询聊天流失败: {streams['error']}")
        if not isinstance(streams, list):
            raise TypeError("SDK chat.get_all_streams 必须返回聊天流列表")
        return streams

    def _start_typing(self, chat_id: int, thread_id: Optional[int], client: TelegramClient) -> None:
        target = (chat_id, thread_id)
        if target in self._typing_tasks:
            return
        self._typing_tasks[target] = asyncio.create_task(
            self._refresh_typing(chat_id, thread_id, client), name=f"telegram-typing-{chat_id}-{thread_id}"
        )

    async def _refresh_typing(self, chat_id: int, thread_id: Optional[int], client: TelegramClient) -> None:
        body: Dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if thread_id is not None:
            body["message_thread_id"] = thread_id
        # Hook 已确认开始生成回复；限制刷新时间，避免生成失败且没有出站消息时任务永久存活。
        deadline = asyncio.get_running_loop().time() + 180
        try:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.wait_for(client.call("sendChatAction", body), timeout=5)
                await asyncio.sleep(4)
        except (TelegramAPIError, TelegramTransportError, TimeoutError) as exc:
            # 状态提示失败单独记录，不让它影响消息接收、游标确认或实际回复。
            self.ctx.logger.warning("Telegram 输入状态发送失败: chat=%s topic=%s error=%s", chat_id, thread_id, exc)
        finally:
            self._typing_tasks.pop((chat_id, thread_id), None)

    async def _stop_typing(self, chat_id: int, thread_id: Optional[int]) -> None:
        task = self._typing_tasks.pop((chat_id, thread_id), None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _action_client(self, platform: str, account_id: str, scope: str) -> TelegramClient:
        if platform != PLATFORM or scope or account_id != self._account_id:
            raise ValueError("聊天动作的路由身份与 Telegram 网关不一致")
        if not self._ready or self._client is None:
            raise RuntimeError("Telegram 网关尚未就绪")
        return self._client

    @Tool(
        "telegram_react_message",
        platforms="telegram",
        description=(
            "仅用于 Telegram：自主选择当前聊天的一条消息添加表态，表达赞同、感谢或情绪，"
            "也可以只表态而不发文字。每条消息最多一个普通 emoji，再次调用替换自己的表态；"
            "emoji 为空字符串时撤销自己的表态。可用例如 👍、❤、🔥、🥰、👏、😁、🎉，"
            "具体受聊天允许的表情限制，不能使用付费表态。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "msg_id": {"type": "string", "description": "当前聊天上下文中的目标消息编号"},
                "emoji": {"type": "string", "description": "一个普通 emoji；空字符串撤销表态"},
            },
            "required": ["msg_id", "emoji"],
            "additionalProperties": False,
        },
        timeout_ms=30000,
    )
    async def react_message(
        self, msg_id: str, emoji: str, stream_id: str = "", platform: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        del kwargs
        try:
            if platform != PLATFORM or not stream_id:
                raise ValueError("表态工具仅支持当前 Telegram 聊天")
            if not isinstance(msg_id, str) or not msg_id.strip() or not isinstance(emoji, str):
                raise ValueError("必须提供有效的 msg_id 和字符串 emoji")
            streams = await self._get_streams()
            matches = [stream for stream in streams if stream["session_id"] == stream_id]
            if len(matches) != 1:
                raise ValueError("当前真实 Telegram 聊天流不存在或不唯一")
            stream = matches[0]
            self._action_client(stream["platform"], stream["account_id"], stream["scope"] or "")
            # 单条消息同样由 SDK 解包，未找到时直接返回 None。
            message = await self.ctx.message.get_by_id(msg_id.strip(), stream_id=stream_id)
            if isinstance(message, dict) and message.get("success") is False:
                raise RuntimeError(f"查询目标消息失败: {message['error']}")
            if message is None or message["session_id"] != stream_id:
                raise ValueError("目标消息不属于当前真实聊天流")
            if message["platform"] != PLATFORM or message["account_id"] != self._account_id:
                raise ValueError("目标消息的账号与 Telegram 网关不一致")
            result = await self._set_reaction(
                platform=PLATFORM,
                target_id=stream["group_id"] if stream["is_group_session"] else stream["user_id"],
                message_id=message["message_id"],
                emoji=emoji,
                account_id=stream["account_id"],
                scope=stream["scope"] or "",
            )
            result["message"] = "已撤销消息表态" if not emoji else f"已给消息表态 {emoji}"
            return result
        except Exception as exc:
            self.ctx.logger.exception("Telegram 消息表态失败: stream=%s message=%s", stream_id, msg_id)
            return {"success": False, "error": str(exc)}

    async def _set_reaction(
        self,
        platform: str,
        target_id: str,
        message_id: str,
        emoji: str,
        account_id: str = "",
        scope: str = "",
    ) -> Dict[str, Any]:
        client = self._action_client(platform, account_id, scope)
        chat_id, _ = parse_group_id(target_id)
        message_chat_id, telegram_message_id = parse_message_id(message_id)
        if chat_id != message_chat_id or telegram_message_id <= 0:
            raise ValueError("表态目标消息不属于当前聊天或消息 ID 无效")
        if chat_id < 0:
            group = await self._require_store().message_group(self._account_id, message_id)
            if group is None or group["group_id"] != target_id:
                raise ValueError("表态目标消息的话题归属未知或与当前聊天不一致")
        if not isinstance(emoji, str):
            raise ValueError("emoji 必须为字符串，空字符串表示撤销表态")
        # Telegram 会校验群允许的 emoji；不替换不支持的表情，不伪造成功。
        reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
        if await client.call(
            "setMessageReaction", {"chat_id": chat_id, "message_id": telegram_message_id, "reaction": reaction}
        ) is not True:
            raise RuntimeError("Telegram 未确认消息表态")
        return {"success": True, "message_id": message_id, "emoji": emoji}

    @API("adapter.avatar.get", description="获取 Telegram 用户或群头像资源", version="1", public=True)
    async def api_get_avatar(
        self,
        platform: str,
        target_id: str,
        target_type: str = "user",
        account_id: str = "",
        scope: str = "",
    ) -> Dict[str, Any]:
        if platform != PLATFORM:
            return {"status": "unsupported"}
        if target_type not in {"user", "group"}:
            raise ValueError("target_type 必须为 user 或 group")
        if scope or (account_id and account_id != self._account_id):
            raise ValueError("头像请求的路由身份与 Telegram 网关不一致")
        chat_id = parse_group_id(target_id.strip())[0] if target_type == "group" else int(target_id.strip())
        if target_type == "group" and chat_id >= 0:
            raise ValueError("Telegram 群 ID 必须为负整数")
        if target_type == "user" and chat_id <= 0:
            raise ValueError("Telegram 用户 ID 必须为正整数")
        async with self._avatar_lock:
            if not self._ready or self._client is None:
                raise RuntimeError("Telegram 网关尚未就绪")
            if target_type == "user":
                profile = await self._client.call("getUserProfilePhotos", {"user_id": chat_id, "limit": 1})
                photos = profile["photos"]
                if not photos:
                    return {"status": "missing"}
                # 只取当前第一张头像，选择其中分辨率最大的尺寸。
                photo = max(photos[0], key=lambda item: item["width"] * item["height"])
                file_id = photo["file_id"]
            else:
                chat = await self._client.call("getChat", {"chat_id": chat_id})
                if chat.get("type") not in {"group", "supergroup"}:
                    raise ValueError("目标不是 Telegram 群聊")
                photo = chat.get("photo")
                if photo is None:
                    return {"status": "missing"}
                file_id = photo["big_file_id"]
            binary = await self._client.download(file_id)
            mime = await asyncio.to_thread(avatar_mime, binary)
            url = await self._avatar_relay.publish(binary, mime)
            return {"status": "available", "url": url, "expires_in": AVATAR_TTL}

    @MessageGateway(
        name=GATEWAY_NAME,
        route_type="duplex",
        platform=PLATFORM,
        protocol="telegram_bot_api",
        description="Telegram 文本、图片、贴纸、语音、@、回复引用与转发双向消息网关",
        timeout_ms=180000,
    )
    async def send_to_telegram(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del route, metadata, kwargs
        async with self._send_lock:
            if not self._ready or self._client is None:
                return {"success": False, "error": "Telegram 网关尚未就绪"}
            client = self._client
            account = self._account_id
            sent: List[str] = []
            try:
                if message.get("platform") != PLATFORM or message.get("account_id") != account:
                    raise ValueError("出站消息的路由身份与 Telegram 网关不一致")
                chat_id = outbound_target(message)
                thread_id = outbound_thread(message)
                reply = reply_parameters(message, chat_id)
                parts = await asyncio.to_thread(encode_segments, message["raw_message"])
                if not parts:
                    raise ValueError("出站消息为空")
                expanded = []
                album_groups = []
                for group, part in enumerate(parts):
                    if part.get("kind") == "album":
                        for index, binary in enumerate(part["images"]):
                            image_part = {"kind": "image", "binary": binary}
                            if index == 0 and "caption" in part:
                                image_part.update({"caption": part["caption"], "caption_entities": part["caption_entities"]})
                            expanded.append(image_part)
                        album_groups.extend([group] * len(part["images"]))
                    else:
                        expanded.append(part)
                        album_groups.append(None)
                # 先完成所有媒体转换与校验，再执行发送；不支持的内容不会造成前半条已发出。
                prepared = []
                for part in expanded:
                    if "method" in part:
                        prepared.append((part["method"], part["body"], None))
                        continue
                    binary = part["binary"]
                    if len(binary) > 50 * 1024 * 1024:
                        raise ValueError("出站媒体超过 50 MB")
                    kind = part["kind"]
                    if kind == "voice":
                        opus = await asyncio.to_thread(
                            voice_to_opus,
                            binary,
                            self.ctx.paths.runtime_dir,
                            self.config.telegram.media_timeout_sec,
                        )
                        prepared.append(("sendVoice", {}, ("voice", opus, "voice.ogg", "audio/ogg")))
                    else:
                        file_id = None
                        if kind == "emoji":
                            binary_hash = await asyncio.to_thread(lambda data: hashlib.sha256(data).hexdigest(), binary)
                            file_id = await self._require_store().sticker(account, binary_hash)
                        if file_id is not None:
                            prepared.append(("sendSticker", {"sticker": file_id}, None))
                        else:
                            method, field, content, name, mime = await asyncio.to_thread(
                                prepare_image, binary, kind == "emoji"
                            )
                            prepared.append((method, {}, (field, content, name, mime)))
                for part, (_, body, _) in zip(expanded, prepared, strict=True):
                    if "caption" in part:
                        body.update({"caption": part["caption"], "caption_entities": part["caption_entities"]})
                # sendMediaGroup 每组 2–10 张。动画不属于图片相册，保留其位置单独发送。
                grouped = []
                position = 0
                while position < len(prepared):
                    end = position + 1
                    group = album_groups[position]
                    if group is not None and prepared[position][0] == "sendPhoto":
                        while (
                            end < len(prepared)
                            and end - position < 10
                            and album_groups[end] == group
                            and prepared[end][0] == "sendPhoto"
                        ):
                            end += 1
                    if end - position >= 2:
                        media = []
                        uploads = []
                        for index, (_, image_body, upload) in enumerate(prepared[position:end]):
                            field = f"photo{index}"
                            _, content, name, mime = upload
                            uploads.append((field, content, name, mime))
                            media.append({"type": "photo", "media": f"attach://{field}", **image_body})
                        grouped.append(("sendMediaGroup", {"media": media}, uploads))
                    else:
                        grouped.append(prepared[position])
                    position = end
                prepared = grouped
                # forwardMessage 不接受 reply_parameters，用一条引用引导消息保留回复关系。
                if reply is not None and prepared[0][0] == "forwardMessage":
                    prepared.insert(0, ("sendMessage", {"text": "转发消息："}, None))
                # 媒体准备期间继续显示输入；发送前停掉刷新，Telegram 收到回复后自动清除状态。
                await self._stop_typing(chat_id, thread_id)
                for index, (method, body, upload) in enumerate(prepared):
                    body = {**body, "chat_id": chat_id}
                    if thread_id is not None:
                        body["message_thread_id"] = thread_id
                    if index == 0 and reply is not None:
                        body["reply_parameters"] = reply
                    result = await client.call(method, body, upload)
                    results = result if method == "sendMediaGroup" else [result]
                    if not isinstance(results, list) or not results:
                        raise RuntimeError("Telegram 相册发送结果为空或格式无效")
                    for item in results:
                        if not isinstance(item, dict) or not item.get("message_id"):
                            raise RuntimeError("Telegram 发送结果缺少 message_id")
                        sent_id = external_id(item["chat"]["id"], item["message_id"])
                        sent.append(sent_id)
                        if chat_id < 0:
                            group = message["message_info"].get("group_info") or {
                                "group_id": f"{chat_id}:{thread_id}" if thread_id is not None else str(chat_id),
                                "group_name": str(chat_id),
                            }
                            await self._require_store().save_message_group(account, sent_id, group)
                return {"success": True, "external_message_id": sent[0], "metadata": {"telegram_message_ids": sent}}
            except Exception as exc:
                error = str(exc)
                if sent:
                    error = f"已发送 {len(sent)} 条后失败，不能整体重发: {error}"
                self.ctx.logger.warning("Telegram 出站失败: %s", error)
                return {"success": False, "error": error, "metadata": {"telegram_message_ids": sent}}


def create_plugin() -> TelegramAdapterPlugin:
    return TelegramAdapterPlugin()

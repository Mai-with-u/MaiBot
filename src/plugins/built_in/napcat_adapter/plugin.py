"""内置 NapCat 适配器插件。

当前实现是一个 MVP 版本，目标仅限于跑通基础消息收发链路：
1. 作为客户端连接 NapCat / OneBot v11 WebSocket 服务。
2. 将入站消息事件转换为 Host 侧的 ``MessageDict``。
3. 将 Host 出站消息转换为 OneBot 动作并发送。

当前范围刻意收敛为：
- 单连接
- 文本、@、reply 基础转发
- 暂不处理 ``notice`` / ``meta_event``
- 暂不支持图片、语音、文件等复杂媒体
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast
from uuid import uuid4

import asyncio
import contextlib
import json
import time

from maibot_sdk import Adapter, MaiBotPlugin

if TYPE_CHECKING:
    from aiohttp import ClientWebSocketResponse as AiohttpClientWebSocketResponse

try:
    from aiohttp import ClientSession, ClientTimeout, ClientWebSocketResponse, WSMsgType

    AIOHTTP_AVAILABLE = True
except ImportError:
    ClientSession = cast(Any, None)
    ClientTimeout = cast(Any, None)
    ClientWebSocketResponse = cast(Any, None)
    WSMsgType = cast(Any, None)
    AIOHTTP_AVAILABLE = False

if not TYPE_CHECKING:
    AiohttpClientWebSocketResponse = Any


@Adapter(platform="qq", protocol="napcat", send_method="send_to_platform")
class NapCatAdapterPlugin(MaiBotPlugin):
    """NapCat 适配器 MVP 实现。"""

    def __init__(self) -> None:
        """初始化 NapCat 适配器插件实例。"""
        super().__init__()
        self._plugin_config: Dict[str, Any] = {}
        self._connection_task: Optional[asyncio.Task[None]] = None
        self._pending_actions: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()
        self._ws: Optional[AiohttpClientWebSocketResponse] = None

    def set_plugin_config(self, config: Dict[str, Any]) -> None:
        """设置插件配置内容。

        Args:
            config: Runner 注入的 ``config.toml`` 解析结果。
        """
        self._plugin_config = config if isinstance(config, dict) else {}

    async def on_load(self) -> None:
        """在插件加载时根据配置决定是否启动连接。"""
        await self._restart_connection_if_needed()

    async def on_unload(self) -> None:
        """在插件卸载时关闭连接并清理后台任务。"""
        await self._stop_connection()
        await self._cancel_background_tasks()

    async def on_config_update(self, new_config: Dict[str, Any], version: str) -> None:
        """在配置更新后重载连接状态。

        Args:
            new_config: 最新的插件配置。
            version: 配置版本号。
        """
        del version
        self.set_plugin_config(new_config)
        await self._restart_connection_if_needed()

    async def send_to_platform(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """将 Host 出站消息发送到 NapCat。

        Args:
            message: Host 侧标准 ``MessageDict``。
            route: Platform IO 生成的路由信息。
            metadata: Platform IO 附带的投递元数据。
            **kwargs: 预留的扩展参数。

        Returns:
            Dict[str, Any]: 标准化后的发送结果。
        """
        del metadata
        del kwargs

        ws = self._ws
        if ws is None or ws.closed:
            return {"success": False, "error": "NapCat is not connected"}

        try:
            action_name, params = self._build_outbound_action(message, route or {})
            response = await self._call_action(action_name, params)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        if str(response.get("status", "")).lower() != "ok":
            return {
                "success": False,
                "error": str(response.get("wording") or response.get("message") or "NapCat send failed"),
                "metadata": {"retcode": response.get("retcode")},
            }

        response_data = response.get("data", {})
        external_message_id = ""
        if isinstance(response_data, dict):
            external_message_id = str(response_data.get("message_id") or "")

        return {
            "success": True,
            "external_message_id": external_message_id or None,
            "metadata": {"action": action_name},
        }

    async def _restart_connection_if_needed(self) -> None:
        """根据当前配置重启连接循环。"""
        await self._stop_connection()
        if not self._should_connect():
            self.ctx.logger.info("NapCat 适配器保持空闲状态，因为插件或配置未启用")
            return
        if not AIOHTTP_AVAILABLE:
            self.ctx.logger.error("NapCat 适配器依赖 aiohttp，但当前环境未安装该依赖")
            return
        self._connection_task = asyncio.create_task(self._connection_loop(), name="napcat_adapter.connection")

    async def _stop_connection(self) -> None:
        """停止当前连接并让所有等待中的动作失败返回。"""
        connection_task = self._connection_task
        self._connection_task = None

        ws = self._ws
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        self._ws = None

        if connection_task is not None:
            connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connection_task

        self._fail_pending_actions("NapCat connection closed")

    async def _cancel_background_tasks(self) -> None:
        """取消所有仍在运行的入站后台任务。"""
        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _connection_loop(self) -> None:
        """维护单个 WebSocket 连接，并在断开后按配置重连。"""
        assert ClientSession is not None
        assert ClientTimeout is not None

        while self._should_connect():
            ws_url = self._get_string(self._connection_config(), "ws_url")
            if not ws_url:
                self.ctx.logger.warning("NapCat 适配器已启用，但 connection.ws_url 为空")
                return

            headers = self._build_headers()
            timeout = ClientTimeout(total=None, connect=10)
            heartbeat = self._get_positive_float(self._connection_config(), "heartbeat_sec", 30.0)

            try:
                async with ClientSession(headers=headers, timeout=timeout) as session:
                    async with session.ws_connect(ws_url, heartbeat=heartbeat or None) as ws:
                        self._ws = ws
                        self.ctx.logger.info(f"NapCat 适配器已连接: {ws_url}")
                        await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ctx.logger.warning(f"NapCat 适配器连接失败: {exc}")
            finally:
                self._ws = None
                self._fail_pending_actions("NapCat connection interrupted")

            if not self._should_connect():
                break

            await asyncio.sleep(self._get_positive_float(self._connection_config(), "reconnect_delay_sec", 5.0))

    async def _receive_loop(self, ws: AiohttpClientWebSocketResponse) -> None:
        """持续消费 WebSocket 消息并分发处理。

        Args:
            ws: 当前活跃的 WebSocket 连接对象。
        """
        assert WSMsgType is not None

        async for ws_message in ws:
            if ws_message.type != WSMsgType.TEXT:
                if ws_message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
                continue

            payload = self._parse_json_message(ws_message.data)
            if payload is None:
                continue

            if echo_id := str(payload.get("echo") or "").strip():
                self._resolve_pending_action(echo_id, payload)
                continue

            if str(payload.get("post_type") or "").strip() != "message":
                continue

            task = asyncio.create_task(self._handle_inbound_message(payload), name="napcat_adapter.inbound")
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _handle_inbound_message(self, payload: Dict[str, Any]) -> None:
        """处理单条 NapCat 入站消息并注入 Host。

        Args:
            payload: NapCat / OneBot 推送的原始事件数据。
        """
        self_id = str(payload.get("self_id") or "").strip()
        sender = payload.get("sender", {})
        if not isinstance(sender, dict):
            sender = {}

        sender_user_id = str(payload.get("user_id") or sender.get("user_id") or "").strip()
        if not sender_user_id:
            return

        if self_id and sender_user_id == self_id and self._get_bool(self._filters_config(), "ignore_self_message", True):
            return

        message_dict = self._build_inbound_message_dict(payload, self_id, sender_user_id, sender)
        route_metadata: Dict[str, Any] = {}
        if self_id:
            route_metadata["self_id"] = self_id
        if connection_id := self._get_string(self._connection_config(), "connection_id"):
            route_metadata["connection_id"] = connection_id

        external_message_id = str(payload.get("message_id") or "").strip()
        accepted = await self.ctx.adapter.receive_external_message(
            message_dict,
            route_metadata=route_metadata,
            external_message_id=external_message_id,
            dedupe_key=external_message_id,
        )
        if not accepted:
            self.ctx.logger.debug(f"Host 丢弃了 NapCat 入站消息: {external_message_id or '无消息 ID'}")

    def _build_inbound_message_dict(
        self,
        payload: Dict[str, Any],
        self_id: str,
        sender_user_id: str,
        sender: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构造 Host 侧可接受的 ``MessageDict``。

        Args:
            payload: NapCat 原始消息事件。
            self_id: 当前机器人账号 ID。
            sender_user_id: 发送者用户 ID。
            sender: 发送者信息字典。

        Returns:
            Dict[str, Any]: 规范化后的 ``MessageDict``。
        """
        message_type = str(payload.get("message_type") or "").strip() or "private"
        group_id = str(payload.get("group_id") or "").strip()
        group_name = str(payload.get("group_name") or "").strip() or (f"group_{group_id}" if group_id else "")
        user_nickname = str(sender.get("nickname") or sender.get("card") or sender_user_id).strip() or sender_user_id
        user_cardname = str(sender.get("card") or "").strip() or None

        raw_message, is_at = self._convert_inbound_segments(payload.get("message"), self_id)
        raw_message_text = str(payload.get("raw_message") or "").strip()
        if not raw_message:
            raw_message = [{"type": "text", "data": raw_message_text or "[unsupported]"}]

        plain_text = self._build_plain_text(raw_message, raw_message_text)
        timestamp_seconds = payload.get("time")
        if not isinstance(timestamp_seconds, (int, float)):
            timestamp_seconds = time.time()

        additional_config: Dict[str, Any] = {"self_id": self_id, "napcat_message_type": message_type}
        if group_id:
            additional_config["platform_io_target_group_id"] = group_id
        else:
            additional_config["platform_io_target_user_id"] = sender_user_id

        message_info: Dict[str, Any] = {
            "user_info": {
                "user_id": sender_user_id,
                "user_nickname": user_nickname,
                "user_cardname": user_cardname,
            },
            "additional_config": additional_config,
        }
        if group_id:
            message_info["group_info"] = {"group_id": group_id, "group_name": group_name}

        message_id = str(payload.get("message_id") or f"napcat-{uuid4().hex}").strip()
        return {
            "message_id": message_id,
            "timestamp": str(float(timestamp_seconds)),
            "platform": "qq",
            "message_info": message_info,
            "raw_message": raw_message,
            "is_mentioned": is_at,
            "is_at": is_at,
            "is_emoji": False,
            "is_picture": False,
            "is_command": plain_text.startswith("/"),
            "is_notify": False,
            "session_id": "",
            "processed_plain_text": plain_text,
            "display_message": plain_text,
        }

    def _convert_inbound_segments(self, message_payload: Any, self_id: str) -> tuple[List[Dict[str, Any]], bool]:
        """将 OneBot 消息段转换为 Host 消息段结构。

        Args:
            message_payload: OneBot 原始 ``message`` 字段。
            self_id: 当前机器人账号 ID。

        Returns:
            tuple[List[Dict[str, Any]], bool]: 转换后的消息段列表，以及是否 @ 到当前机器人。
        """
        if isinstance(message_payload, str):
            normalized_text = message_payload.strip()
            return ([{"type": "text", "data": normalized_text}] if normalized_text else []), False

        if not isinstance(message_payload, list):
            return [], False

        converted_segments: List[Dict[str, Any]] = []
        is_at = False
        placeholder_texts = {
            "face": "[face]",
            "file": "[file]",
            "image": "[image]",
            "json": "[json]",
            "record": "[voice]",
            "video": "[video]",
            "xml": "[xml]",
        }

        for segment in message_payload:
            if not isinstance(segment, dict):
                continue

            segment_type = str(segment.get("type") or "").strip()
            segment_data = segment.get("data", {})
            if not isinstance(segment_data, dict):
                segment_data = {}

            if segment_type == "text":
                if text_value := str(segment_data.get("text") or ""):
                    converted_segments.append({"type": "text", "data": text_value})
                continue

            if segment_type == "at":
                if target_user_id := str(segment_data.get("qq") or "").strip():
                    converted_segments.append(
                        {
                            "type": "at",
                            "data": {
                                "target_user_id": target_user_id,
                                "target_user_nickname": None,
                                "target_user_cardname": None,
                            },
                        }
                    )
                    if self_id and target_user_id == self_id:
                        is_at = True
                continue

            if segment_type == "reply":
                if target_message_id := str(segment_data.get("id") or "").strip():
                    converted_segments.append({"type": "reply", "data": target_message_id})
                continue

            if placeholder := placeholder_texts.get(segment_type):
                converted_segments.append({"type": "text", "data": placeholder})

        return converted_segments, is_at

    def _build_outbound_action(
        self,
        message: Dict[str, Any],
        route: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        """为 Host 出站消息构造 OneBot 动作。

        Args:
            message: Host 侧标准 ``MessageDict``。
            route: Platform IO 路由信息。

        Returns:
            tuple[str, Dict[str, Any]]: 动作名称与参数字典。
        """
        message_info = message.get("message_info", {})
        if not isinstance(message_info, dict):
            message_info = {}

        group_info = message_info.get("group_info", {})
        if not isinstance(group_info, dict):
            group_info = {}

        additional_config = message_info.get("additional_config", {})
        if not isinstance(additional_config, dict):
            additional_config = {}

        raw_message = message.get("raw_message", [])
        segments = self._convert_outbound_segments(raw_message)

        if target_group_id := str(
            group_info.get("group_id") or additional_config.get("platform_io_target_group_id") or ""
        ).strip():
            return "send_group_msg", {"group_id": target_group_id, "message": segments}

        if not (
            target_user_id := str(
                additional_config.get("platform_io_target_user_id")
                or additional_config.get("target_user_id")
                or route.get("target_user_id")
                or ""
            ).strip()
        ):
            raise ValueError("Outbound private message is missing target_user_id")

        return "send_private_msg", {"message": segments, "user_id": target_user_id}

    def _convert_outbound_segments(self, raw_message: Any) -> List[Dict[str, Any]]:
        """将 Host 消息段转换为 OneBot 消息段。

        Args:
            raw_message: Host 侧 ``raw_message`` 字段。

        Returns:
            List[Dict[str, Any]]: OneBot 消息段列表。
        """
        if not isinstance(raw_message, list):
            return [{"type": "text", "data": {"text": ""}}]

        outbound_segments: List[Dict[str, Any]] = []
        for item in raw_message:
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type") or "").strip()
            item_data = item.get("data")

            if item_type == "text":
                text_value = str(item_data or "")
                outbound_segments.append({"type": "text", "data": {"text": text_value}})
                continue

            if item_type == "at" and isinstance(item_data, dict):
                if target_user_id := str(item_data.get("target_user_id") or "").strip():
                    outbound_segments.append({"type": "at", "data": {"qq": target_user_id}})
                continue

            if item_type == "reply":
                if target_message_id := str(item_data or "").strip():
                    outbound_segments.append({"type": "reply", "data": {"id": target_message_id}})
                continue

            fallback_text = f"[unsupported:{item_type or 'unknown'}]"
            outbound_segments.append({"type": "text", "data": {"text": fallback_text}})

        if not outbound_segments:
            outbound_segments.append({"type": "text", "data": {"text": ""}})
        return outbound_segments

    async def _call_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 OneBot 动作并等待对应的 echo 响应。

        Args:
            action_name: OneBot 动作名称。
            params: 动作参数。

        Returns:
            Dict[str, Any]: NapCat 返回的原始响应字典。
        """
        ws = self._ws
        if ws is None or ws.closed:
            raise RuntimeError("NapCat is not connected")

        echo_id = uuid4().hex
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_actions[echo_id] = response_future

        request_payload = {"action": action_name, "params": params, "echo": echo_id}
        try:
            async with self._send_lock:
                await ws.send_str(json.dumps(request_payload, ensure_ascii=False))
            timeout_seconds = self._get_positive_float(self._connection_config(), "action_timeout_sec", 15.0)
            return await asyncio.wait_for(response_future, timeout=timeout_seconds)
        finally:
            self._pending_actions.pop(echo_id, None)

    def _resolve_pending_action(self, echo_id: str, payload: Dict[str, Any]) -> None:
        """解析等待中的动作响应。

        Args:
            echo_id: 动作请求对应的 echo 标识。
            payload: NapCat 返回的响应载荷。
        """
        response_future = self._pending_actions.get(echo_id)
        if response_future is None or response_future.done():
            return
        response_future.set_result(payload)

    def _fail_pending_actions(self, error_message: str) -> None:
        """让所有等待中的动作以异常方式结束。

        Args:
            error_message: 写入异常中的错误信息。
        """
        for response_future in self._pending_actions.values():
            if not response_future.done():
                response_future.set_exception(RuntimeError(error_message))
        self._pending_actions.clear()

    def _build_headers(self) -> Dict[str, str]:
        """构造连接 NapCat 所需的请求头。

        Returns:
            Dict[str, str]: WebSocket 握手请求头。
        """
        access_token = self._get_string(self._connection_config(), "access_token")
        return {"Authorization": f"Bearer {access_token}"} if access_token else {}

    def _parse_json_message(self, data: Any) -> Optional[Dict[str, Any]]:
        """解析 WebSocket 文本消息中的 JSON 数据。

        Args:
            data: WebSocket 收到的原始文本数据。

        Returns:
            Optional[Dict[str, Any]]: 成功时返回字典，失败时返回 ``None``。
        """
        try:
            payload = json.loads(str(data))
        except Exception as exc:
            self.ctx.logger.warning(f"NapCat 适配器解析 JSON 载荷失败: {exc}")
            return None

        return payload if isinstance(payload, dict) else None

    def _build_plain_text(self, raw_message: List[Dict[str, Any]], fallback_text: str) -> str:
        """从标准消息段中提取可展示的纯文本。

        Args:
            raw_message: 标准化后的消息段列表。
            fallback_text: 当无法拼出文本时使用的回退文本。

        Returns:
            str: 用于 Host 展示和命令判断的纯文本内容。
        """
        plain_text_parts: List[str] = []
        for item in raw_message:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            item_data = item.get("data")
            if item_type == "text":
                plain_text_parts.append(str(item_data or ""))
            elif item_type == "at" and isinstance(item_data, dict):
                plain_text_parts.append(f"@{item_data.get('target_user_id') or ''}")
            elif item_type == "reply":
                plain_text_parts.append("[reply]")

        plain_text = "".join(part for part in plain_text_parts if part).strip()
        return plain_text or fallback_text or "[unsupported]"

    def _plugin_section(self) -> Dict[str, Any]:
        """读取插件配置中的 ``plugin`` 段。

        Returns:
            Dict[str, Any]: ``plugin`` 配置字典。
        """
        plugin_section = self._plugin_config.get("plugin", {})
        return plugin_section if isinstance(plugin_section, dict) else {}

    def _connection_config(self) -> Dict[str, Any]:
        """读取插件配置中的 ``connection`` 段。

        Returns:
            Dict[str, Any]: ``connection`` 配置字典。
        """
        connection_config = self._plugin_config.get("connection", {})
        return connection_config if isinstance(connection_config, dict) else {}

    def _filters_config(self) -> Dict[str, Any]:
        """读取插件配置中的 ``filters`` 段。

        Returns:
            Dict[str, Any]: ``filters`` 配置字典。
        """
        filters_config = self._plugin_config.get("filters", {})
        return filters_config if isinstance(filters_config, dict) else {}

    def _should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。

        Returns:
            bool: 若启用了插件连接则返回 ``True``。
        """
        return self._get_bool(self._plugin_section(), "enabled", False)

    @staticmethod
    def _get_bool(mapping: Dict[str, Any], key: str, default: bool) -> bool:
        """安全读取布尔配置值。

        Args:
            mapping: 待读取的配置字典。
            key: 目标键名。
            default: 读取失败时的默认值。

        Returns:
            bool: 解析后的布尔值。
        """
        value = mapping.get(key, default)
        return value if isinstance(value, bool) else default

    @staticmethod
    def _get_positive_float(mapping: Dict[str, Any], key: str, default: float) -> float:
        """安全读取正浮点数配置值。

        Args:
            mapping: 待读取的配置字典。
            key: 目标键名。
            default: 读取失败时的默认值。

        Returns:
            float: 合法的正浮点数；否则返回默认值。
        """
        value = mapping.get(key, default)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
        return default

    @staticmethod
    def _get_string(mapping: Dict[str, Any], key: str) -> str:
        """安全读取字符串配置值。

        Args:
            mapping: 待读取的配置字典。
            key: 目标键名。

        Returns:
            str: 去除首尾空白后的字符串值。
        """
        value = mapping.get(key)
        return "" if value is None else str(value).strip()


def create_plugin() -> NapCatAdapterPlugin:
    """创建插件实例。

    Returns:
        NapCatAdapterPlugin: NapCat 内置适配器插件实例。
    """
    return NapCatAdapterPlugin()

"""内置 NapCat 适配器插件。

当前实现维持 MVP 范围，目标是跑通基础消息收发链路：
1. 作为客户端连接 NapCat / OneBot v11 WebSocket 服务。
2. 将入站消息事件转换为 Host 侧的 ``MessageDict``。
3. 将 Host 出站消息转换为 OneBot 动作并发送。

当前范围刻意收敛为：
- 单连接
- 文本、@、reply 基础转发
- 暂不处理 ``notice`` / ``meta_event`` 的完整语义归一化
- 暂不支持图片、语音、文件等复杂媒体
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import asyncio

from maibot_sdk import Adapter, MaiBotPlugin

from napcat_adapter.codec_inbound import NapCatInboundCodec
from napcat_adapter.codec_outbound import NapCatOutboundCodec
from napcat_adapter.config import NapCatPluginSettings
from napcat_adapter.filters import NapCatChatFilter
from napcat_adapter.qq_notice import NapCatNoticeCodec
from napcat_adapter.qq_queries import NapCatQueryService
from napcat_adapter.runtime_state import NapCatRuntimeStateManager
from napcat_adapter.transport import NapCatTransportClient


@Adapter(platform="qq", protocol="napcat", send_method="send_to_platform")
class NapCatAdapterPlugin(MaiBotPlugin):
    """NapCat 适配器 MVP 实现。"""

    def __init__(self) -> None:
        """初始化 NapCat 适配器插件实例。"""
        super().__init__()
        self._plugin_config: Dict[str, Any] = {}
        self._settings: Optional[NapCatPluginSettings] = None
        self._inbound_codec: Optional[NapCatInboundCodec] = None
        self._outbound_codec = NapCatOutboundCodec()
        self._chat_filter: Optional[NapCatChatFilter] = None
        self._query_service: Optional[NapCatQueryService] = None
        self._notice_codec: Optional[NapCatNoticeCodec] = None
        self._runtime_state: Optional[NapCatRuntimeStateManager] = None
        self._transport: Optional[NapCatTransportClient] = None

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
        """在插件卸载时关闭连接并清理运行时状态。"""
        await self._stop_connection()

    async def on_config_update(self, new_config: Dict[str, Any], version: str) -> None:
        """在配置更新后重载连接状态。

        Args:
            new_config: 最新的插件配置。
            version: 配置版本号。
        """
        self.set_plugin_config(new_config)
        if version:
            self.ctx.logger.debug(f"NapCat 适配器收到配置更新通知: {version}")
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

        self._ensure_runtime_components()
        transport = self._transport
        if transport is None:
            return {"success": False, "error": "NapCat transport is not initialized"}

        try:
            action_name, params = self._outbound_codec.build_outbound_action(message, route or {})
            response = await transport.call_action(action_name, params)
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
        if isinstance(response_data, Mapping):
            external_message_id = str(response_data.get("message_id") or "")

        return {
            "success": True,
            "external_message_id": external_message_id or None,
            "metadata": {"action": action_name},
        }

    def _ensure_runtime_components(self) -> None:
        """确保运行时依赖对象已经完成初始化。"""
        if self._chat_filter is None:
            self._chat_filter = NapCatChatFilter(self.ctx.logger)

        if self._transport is None:
            self._transport = NapCatTransportClient(
                logger=self.ctx.logger,
                on_connection_opened=self._bootstrap_adapter_runtime_state,
                on_connection_closed=self._handle_transport_disconnected,
                on_payload=self._handle_transport_payload,
            )

        if self._query_service is None:
            self._query_service = NapCatQueryService(self.ctx.logger, self._transport)

        if self._inbound_codec is None:
            self._inbound_codec = NapCatInboundCodec(self.ctx.logger, self._query_service)

        if self._notice_codec is None:
            self._notice_codec = NapCatNoticeCodec(self.ctx.logger, self._query_service)

        if self._runtime_state is None:
            self._runtime_state = NapCatRuntimeStateManager(self.ctx.adapter, self.ctx.logger)

    def _reload_settings(self) -> NapCatPluginSettings:
        """重新解析当前插件配置。

        Returns:
            NapCatPluginSettings: 最新的规范化配置。
        """
        self._settings = NapCatPluginSettings.from_mapping(self._plugin_config, self.ctx.logger)
        return self._settings

    async def _restart_connection_if_needed(self) -> None:
        """根据当前配置重启连接循环。"""
        self._ensure_runtime_components()
        settings = self._reload_settings()

        await self._stop_connection()
        if not settings.should_connect():
            self.ctx.logger.info("NapCat 适配器保持空闲状态，因为插件或配置未启用")
            return
        if not settings.validate(self.ctx.logger):
            return

        transport = self._transport
        assert transport is not None
        if not transport.is_available():
            self.ctx.logger.error("NapCat 适配器依赖 aiohttp，但当前环境未安装该依赖")
            return

        transport.configure(settings.napcat_server)
        await transport.start()

    async def _stop_connection(self) -> None:
        """停止当前连接。"""
        transport = self._transport
        if transport is not None:
            await transport.stop()
            return

        runtime_state = self._runtime_state
        if runtime_state is not None:
            await runtime_state.report_disconnected()

    async def _handle_transport_payload(self, payload: Dict[str, Any]) -> None:
        """处理来自传输层的非 echo 载荷。

        Args:
            payload: NapCat 推送的原始事件数据。
        """
        post_type = str(payload.get("post_type") or "").strip()
        if post_type == "message":
            await self._handle_inbound_message(payload)
            return
        if post_type == "notice":
            await self._handle_notice_event(payload)
            return
        if post_type == "meta_event":
            await self._handle_meta_event(payload)

    async def _handle_inbound_message(self, payload: Dict[str, Any]) -> None:
        """处理单条 NapCat 入站消息并注入 Host。

        Args:
            payload: NapCat / OneBot 推送的原始消息事件。
        """
        self._ensure_runtime_components()
        settings = self._settings or self._reload_settings()
        chat_filter = self._chat_filter
        inbound_codec = self._inbound_codec
        runtime_state = self._runtime_state
        assert chat_filter is not None
        assert inbound_codec is not None
        assert runtime_state is not None

        self_id = str(payload.get("self_id") or "").strip()
        if self_id:
            await runtime_state.report_connected(self_id, settings.napcat_server)

        sender = payload.get("sender", {})
        if not isinstance(sender, Mapping):
            sender = {}

        sender_user_id = str(payload.get("user_id") or sender.get("user_id") or "").strip()
        if not sender_user_id:
            return

        group_id = str(payload.get("group_id") or "").strip()
        if self_id and sender_user_id == self_id and settings.filters.ignore_self_message:
            return
        if not chat_filter.is_inbound_chat_allowed(sender_user_id, group_id, settings.chat):
            return

        message_dict = await inbound_codec.build_message_dict(payload, self_id, sender_user_id, sender)
        route_metadata: Dict[str, Any] = {}
        if self_id:
            route_metadata["self_id"] = self_id
        if settings.napcat_server.connection_id:
            route_metadata["connection_id"] = settings.napcat_server.connection_id

        external_message_id = str(payload.get("message_id") or "").strip()
        accepted = await self.ctx.adapter.receive_external_message(
            message_dict,
            route_metadata=route_metadata,
            external_message_id=external_message_id,
            dedupe_key=external_message_id,
        )
        if not accepted:
            self.ctx.logger.debug(f"Host 丢弃了 NapCat 入站消息: {external_message_id or '无消息 ID'}")

    async def _handle_notice_event(self, payload: Dict[str, Any]) -> None:
        """处理 NapCat ``notice`` 事件并注入 Host。

        Args:
            payload: NapCat 推送的通知事件。
        """
        self._ensure_runtime_components()
        notice_codec = self._notice_codec
        runtime_state = self._runtime_state
        settings = self._settings or self._reload_settings()
        assert notice_codec is not None
        assert runtime_state is not None

        self_id = str(payload.get("self_id") or "").strip()
        if self_id:
            await runtime_state.report_connected(self_id, settings.napcat_server)

        message_dict = await notice_codec.build_notice_message_dict(payload)
        if message_dict is None:
            return

        route_metadata: Dict[str, Any] = {}
        if self_id:
            route_metadata["self_id"] = self_id
        if settings.napcat_server.connection_id:
            route_metadata["connection_id"] = settings.napcat_server.connection_id

        external_message_id = str(payload.get("message_id") or payload.get("notice_type") or "").strip()
        accepted = await self.ctx.adapter.receive_external_message(
            message_dict,
            route_metadata=route_metadata,
            external_message_id=external_message_id or None,
            dedupe_key=external_message_id or None,
        )
        if not accepted:
            self.ctx.logger.debug(f"Host 丢弃了 NapCat 通知事件: {external_message_id or '无消息 ID'}")

    async def _handle_meta_event(self, payload: Dict[str, Any]) -> None:
        """处理 NapCat ``meta_event`` 事件。

        Args:
            payload: NapCat 推送的元事件。
        """
        self._ensure_runtime_components()
        notice_codec = self._notice_codec
        runtime_state = self._runtime_state
        settings = self._settings or self._reload_settings()
        assert notice_codec is not None
        assert runtime_state is not None

        self_id = str(payload.get("self_id") or "").strip()
        if self_id:
            await runtime_state.report_connected(self_id, settings.napcat_server)

        await notice_codec.handle_meta_event(payload)

    async def _bootstrap_adapter_runtime_state(self) -> None:
        """在连接建立后主动获取账号信息并激活适配器路由。"""
        transport = self._transport
        query_service = self._query_service
        runtime_state = self._runtime_state
        settings = self._settings or self._reload_settings()
        if transport is None or query_service is None or runtime_state is None:
            return

        max_attempts = 3
        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                login_info = await query_service.get_login_info()
                self_id = self._extract_self_id_from_login_response(login_info)
                await runtime_state.report_connected(self_id, settings.napcat_server)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                self.ctx.logger.warning(
                    f"NapCat 适配器获取登录信息失败，第 {attempt}/{max_attempts} 次重试: {exc}"
                )
                if attempt < max_attempts:
                    await asyncio.sleep(1.0)

        if last_error is not None:
            self.ctx.logger.error(f"NapCat 适配器未能完成路由激活，连接将保持只接收状态: {last_error}")

    async def _handle_transport_disconnected(self) -> None:
        """处理传输层断开事件。"""
        runtime_state = self._runtime_state
        if runtime_state is not None:
            await runtime_state.report_disconnected()

    @staticmethod
    def _extract_self_id_from_login_response(response: Optional[Dict[str, Any]]) -> str:
        """从 ``get_login_info`` 查询结果中提取当前账号 ID。

        Args:
            response: NapCat 返回的登录信息字典。

        Returns:
            str: 规范化后的账号 ID 字符串。

        Raises:
            ValueError: 当响应中缺少有效账号 ID 时抛出。
        """
        if not isinstance(response, Mapping):
            raise ValueError("get_login_info 响应缺少 data 字段")

        self_id = str(response.get("user_id") or "").strip()
        if not self_id:
            raise ValueError("get_login_info 响应缺少有效的 user_id")
        return self_id


def create_plugin() -> NapCatAdapterPlugin:
    """创建插件实例。

    Returns:
        NapCatAdapterPlugin: NapCat 内置适配器插件实例。
    """
    return NapCatAdapterPlugin()

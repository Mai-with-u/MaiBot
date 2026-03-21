"""NapCat 运行时路由状态管理。"""

from typing import Any, Optional

from napcat_adapter.config import NapCatServerConfig


class NapCatRuntimeStateManager:
    """NapCat 适配器路由状态上报器。"""

    def __init__(self, adapter_capability: Any, logger: Any) -> None:
        """初始化运行时状态管理器。

        Args:
            adapter_capability: SDK 提供的适配器能力对象。
            logger: 插件日志对象。
        """
        self._adapter_capability = adapter_capability
        self._logger = logger
        self._runtime_state_connected: bool = False
        self._reported_account_id: Optional[str] = None
        self._reported_scope: Optional[str] = None

    async def report_connected(self, account_id: str, server_config: NapCatServerConfig) -> bool:
        """向 Host 上报当前连接已就绪。

        Args:
            account_id: 当前 NapCat 连接对应的机器人账号 ID。
            server_config: 当前生效的 NapCat 服务端配置。

        Returns:
            bool: 若 Host 接受了运行时状态更新，则返回 ``True``。
        """
        normalized_account_id = str(account_id).strip()
        if not normalized_account_id:
            return False

        scope = server_config.connection_id or None
        if (
            self._runtime_state_connected
            and self._reported_account_id == normalized_account_id
            and self._reported_scope == scope
        ):
            return True

        accepted = False
        try:
            accepted = await self._adapter_capability.update_runtime_state(
                connected=True,
                account_id=normalized_account_id,
                scope=server_config.connection_id,
                metadata={"ws_url": server_config.build_ws_url()},
            )
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器上报连接就绪状态失败: {exc}")
            return False

        if not accepted:
            self._logger.warning("NapCat 适配器连接已建立，但 Host 未接受运行时状态更新")
            return False

        self._runtime_state_connected = True
        self._reported_account_id = normalized_account_id
        self._reported_scope = scope
        self._logger.info(
            f"NapCat 适配器已激活路由: platform=qq account_id={normalized_account_id} "
            f"scope={self._reported_scope or '*'}"
        )
        return True

    async def report_disconnected(self) -> None:
        """向 Host 上报当前连接已断开，并撤销适配器路由。"""
        if not self._runtime_state_connected:
            self._reported_account_id = None
            self._reported_scope = None
            return

        try:
            await self._adapter_capability.update_runtime_state(connected=False)
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器上报断开状态失败: {exc}")
        finally:
            self._runtime_state_connected = False
            self._reported_account_id = None
            self._reported_scope = None

"""NapCat QQ 平台查询能力。"""

from typing import TYPE_CHECKING, Any, Dict, Optional

import asyncio

if TYPE_CHECKING:
    from napcat_adapter.transport import NapCatTransportClient

try:
    from aiohttp import ClientSession, ClientTimeout

    AIOHTTP_AVAILABLE = True
except ImportError:
    ClientSession = None  # type: ignore[assignment]
    ClientTimeout = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False


class NapCatQueryService:
    """NapCat QQ 平台查询服务。"""

    def __init__(self, logger: Any, transport: "NapCatTransportClient") -> None:
        """初始化查询服务。

        Args:
            logger: 插件日志对象。
            transport: NapCat 传输层客户端。
        """
        self._logger = logger
        self._transport = transport

    async def get_login_info(self) -> Optional[Dict[str, Any]]:
        """获取当前登录账号信息。

        Returns:
            Optional[Dict[str, Any]]: 登录信息字典；失败时返回 ``None``。
        """
        return await self._call_query("get_login_info", {})

    async def get_group_info(self, group_id: str) -> Optional[Dict[str, Any]]:
        """获取群信息。

        Args:
            group_id: 群号。

        Returns:
            Optional[Dict[str, Any]]: 群信息字典；失败时返回 ``None``。
        """
        return await self._call_query("get_group_info", {"group_id": group_id})

    async def get_group_member_info(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取群成员信息。

        Args:
            group_id: 群号。
            user_id: 用户号。

        Returns:
            Optional[Dict[str, Any]]: 群成员信息字典；失败时返回 ``None``。
        """
        return await self._call_query(
            "get_group_member_info",
            {"group_id": group_id, "user_id": user_id, "no_cache": True},
        )

    async def get_stranger_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取陌生人信息。

        Args:
            user_id: 用户号。

        Returns:
            Optional[Dict[str, Any]]: 陌生人信息字典；失败时返回 ``None``。
        """
        return await self._call_query("get_stranger_info", {"user_id": user_id})

    async def get_message_detail(self, message_id: str) -> Optional[Dict[str, Any]]:
        """获取消息详情。

        Args:
            message_id: 消息 ID。

        Returns:
            Optional[Dict[str, Any]]: 消息详情字典；失败时返回 ``None``。
        """
        return await self._call_query("get_msg", {"message_id": message_id})

    async def get_forward_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """获取合并转发消息详情。

        Args:
            message_id: 转发消息 ID。

        Returns:
            Optional[Dict[str, Any]]: 合并转发消息详情；失败时返回 ``None``。
        """
        return await self._call_query("get_forward_msg", {"message_id": message_id})

    async def get_record_detail(self, file_name: str, file_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """获取语音文件详情。

        Args:
            file_name: 语音文件名。
            file_id: 可选文件 ID。

        Returns:
            Optional[Dict[str, Any]]: 语音详情字典；失败时返回 ``None``。
        """
        params: Dict[str, Any] = {"file": file_name, "out_format": "wav"}
        if file_id:
            params["file_id"] = file_id
        return await self._call_query("get_record", params)

    async def download_binary(self, url: str) -> Optional[bytes]:
        """下载远程二进制资源。

        Args:
            url: 资源 URL。

        Returns:
            Optional[bytes]: 下载到的二进制内容；失败时返回 ``None``。
        """
        if not url:
            return None
        if not AIOHTTP_AVAILABLE or ClientSession is None or ClientTimeout is None:
            self._logger.warning("NapCat 查询层缺少 aiohttp，无法下载远程资源")
            return None

        try:
            timeout = ClientTimeout(total=15)
            async with ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        self._logger.warning(f"NapCat 远程资源下载失败: status={response.status} url={url}")
                        return None
                    return await response.read()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(f"NapCat 远程资源下载失败: {exc}")
            return None

    async def _call_query(self, action_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用 OneBot 查询动作并提取 ``data`` 字段。

        Args:
            action_name: OneBot 动作名。
            params: 动作参数。

        Returns:
            Optional[Dict[str, Any]]: 查询结果中的 ``data`` 字段；失败时返回 ``None``。
        """
        try:
            response = await self._transport.call_action(action_name, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(f"NapCat 查询动作执行失败: action={action_name} error={exc}")
            return None

        if str(response.get("status") or "").lower() != "ok":
            self._logger.warning(
                f"NapCat 查询动作返回失败: action={action_name} "
                f"message={response.get('wording') or response.get('message') or 'unknown'}"
            )
            return None

        response_data = response.get("data")
        return response_data if isinstance(response_data, dict) else None

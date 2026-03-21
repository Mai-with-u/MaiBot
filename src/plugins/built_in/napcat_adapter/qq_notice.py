"""NapCat QQ 平台通知与元事件处理。"""

from typing import Any, Dict, Mapping, Optional
from uuid import uuid4

import time

from napcat_adapter.qq_queries import NapCatQueryService


class NapCatNoticeCodec:
    """NapCat QQ 通知事件编码器。"""

    def __init__(self, logger: Any, query_service: NapCatQueryService) -> None:
        """初始化通知事件编码器。

        Args:
            logger: 插件日志对象。
            query_service: QQ 查询服务。
        """
        self._logger = logger
        self._query_service = query_service

    async def build_notice_message_dict(self, payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """将 NapCat ``notice`` 事件转换为 Host 可接受的消息字典。

        Args:
            payload: NapCat 推送的原始通知事件。

        Returns:
            Optional[Dict[str, Any]]: 成功时返回标准 ``MessageDict``；无法识别时返回 ``None``。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        if not notice_type:
            return None

        group_id = str(payload.get("group_id") or "").strip()
        user_id = str(payload.get("user_id") or payload.get("operator_id") or "").strip()
        self_id = str(payload.get("self_id") or "").strip()

        user_info = await self._build_user_info(group_id=group_id, user_id=user_id)
        group_info = await self._build_group_info(group_id)
        notice_text = self._build_notice_text(payload, user_info.get("user_nickname", user_id or "系统"))
        if not notice_text:
            return None

        additional_config: Dict[str, Any] = {
            "self_id": self_id,
            "napcat_notice_type": notice_type,
            "napcat_notice_sub_type": str(payload.get("sub_type") or "").strip(),
            "napcat_notice_payload": dict(payload),
        }
        if group_id:
            additional_config["platform_io_target_group_id"] = group_id
        elif user_id:
            additional_config["platform_io_target_user_id"] = user_id

        message_info: Dict[str, Any] = {"user_info": user_info, "additional_config": additional_config}
        if group_info is not None:
            message_info["group_info"] = group_info

        timestamp_seconds = payload.get("time")
        if not isinstance(timestamp_seconds, (int, float)):
            timestamp_seconds = time.time()

        return {
            "message_id": f"napcat-notice-{uuid4().hex}",
            "timestamp": str(float(timestamp_seconds)),
            "platform": "qq",
            "message_info": message_info,
            "raw_message": [{"type": "text", "data": notice_text}],
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "is_command": False,
            "is_notify": True,
            "session_id": "",
            "processed_plain_text": notice_text,
            "display_message": notice_text,
        }

    async def handle_meta_event(self, payload: Mapping[str, Any]) -> None:
        """处理 ``meta_event`` 事件的日志与状态观测。

        Args:
            payload: NapCat 推送的原始元事件。
        """
        meta_event_type = str(payload.get("meta_event_type") or "").strip()
        self_id = str(payload.get("self_id") or "").strip() or "unknown"

        if meta_event_type == "lifecycle":
            sub_type = str(payload.get("sub_type") or "").strip()
            if sub_type == "connect":
                self._logger.info(f"NapCat 元事件：Bot {self_id} 已建立连接")
            else:
                self._logger.debug(f"NapCat 生命周期事件: self_id={self_id} sub_type={sub_type}")
            return

        if meta_event_type == "heartbeat":
            status = payload.get("status", {})
            if not isinstance(status, Mapping):
                status = {}
            is_online = bool(status.get("online", False))
            is_good = bool(status.get("good", False))
            interval_ms = payload.get("interval")
            self._logger.debug(
                f"NapCat 心跳事件: self_id={self_id} online={is_online} good={is_good} interval={interval_ms}"
            )
            if not is_online:
                self._logger.warning(f"NapCat 心跳显示 Bot {self_id} 已离线")
            elif not is_good:
                self._logger.warning(f"NapCat 心跳显示 Bot {self_id} 状态异常")

    async def _build_user_info(self, group_id: str, user_id: str) -> Dict[str, Optional[str]]:
        """构造通知消息的用户信息。

        Args:
            group_id: 群号；私聊或系统通知时为空字符串。
            user_id: 事件关联用户号。

        Returns:
            Dict[str, Optional[str]]: 规范化后的用户信息字典。
        """
        if not user_id:
            return {
                "user_id": "notice",
                "user_nickname": "系统通知",
                "user_cardname": None,
            }

        member_info: Optional[Dict[str, Any]]
        if group_id:
            member_info = await self._query_service.get_group_member_info(group_id, user_id)
        else:
            member_info = await self._query_service.get_stranger_info(user_id)

        if member_info is None:
            return {
                "user_id": user_id,
                "user_nickname": user_id,
                "user_cardname": None,
            }

        return {
            "user_id": user_id,
            "user_nickname": str(member_info.get("nickname") or user_id),
            "user_cardname": self._normalize_optional_string(member_info.get("card")),
        }

    async def _build_group_info(self, group_id: str) -> Optional[Dict[str, str]]:
        """构造通知消息的群信息。

        Args:
            group_id: 群号。

        Returns:
            Optional[Dict[str, str]]: 群信息字典；若不是群通知则返回 ``None``。
        """
        if not group_id:
            return None

        group_info = await self._query_service.get_group_info(group_id)
        group_name = str(group_info.get("group_name") or f"group_{group_id}") if group_info else f"group_{group_id}"
        return {"group_id": group_id, "group_name": group_name}

    def _build_notice_text(self, payload: Mapping[str, Any], actor_name: str) -> str:
        """根据 NapCat 通知事件生成可读文本。

        Args:
            payload: 原始通知事件。
            actor_name: 事件操作者显示名。

        Returns:
            str: 生成的可读通知文本。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        sub_type = str(payload.get("sub_type") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()

        if notice_type in {"group_recall", "friend_recall"}:
            return f"{actor_name} 撤回了一条消息"
        if notice_type == "notify" and sub_type == "poke":
            target_text = f" -> {target_id}" if target_id else ""
            return f"{actor_name} 发起了戳一戳{target_text}"
        if notice_type == "notify" and sub_type == "group_name":
            return f"{actor_name} 修改了群名称"
        if notice_type == "group_ban" and sub_type == "ban":
            duration = payload.get("duration")
            return f"{actor_name} 触发了群禁言，时长 {duration} 秒"
        if notice_type == "group_ban" and sub_type == "lift_ban":
            return f"{actor_name} 触发了解除禁言"
        if notice_type == "group_upload":
            file_info = payload.get("file", {})
            file_name = ""
            if isinstance(file_info, Mapping):
                file_name = str(file_info.get("name") or "").strip()
            return f"{actor_name} 上传了文件{f'：{file_name}' if file_name else ''}"
        if notice_type == "group_increase":
            return f"{actor_name} 加入了群聊"
        if notice_type == "group_decrease":
            return f"{actor_name} 离开了群聊"
        if notice_type == "group_admin":
            return f"{actor_name} 的群管理员状态发生变化"
        if notice_type == "essence":
            return f"{actor_name} 触发了精华消息事件"
        if notice_type == "group_msg_emoji_like":
            return f"{actor_name} 给一条消息添加了表情回应"
        return f"[notice] {notice_type}.{sub_type}".strip(".")

    @staticmethod
    def _normalize_optional_string(value: Any) -> Optional[str]:
        """将任意值规范化为可选字符串。

        Args:
            value: 待规范化的值。

        Returns:
            Optional[str]: 规范化后的字符串；若值为空则返回 ``None``。
        """
        if value is None:
            return None
        normalized_value = str(value).strip()
        return normalized_value if normalized_value else None

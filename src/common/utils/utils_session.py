from typing import Optional

import hashlib


class SessionUtils:
    @staticmethod
    def calculate_session_id(platform: str, *, user_id: Optional[str] = None, group_id: Optional[str] = None) -> str:
        """计算session_id

        Args:
            platform: 平台名称
            user_id: 用户ID（如果是私聊）
            group_id: 群ID（如果是群聊）
        Returns:
            str: 计算得到的会话ID
        Raises:
            ValueError: 当 user_id 和 group_id 都未提供时抛出
        """
        if not user_id and not group_id:
            raise ValueError("UserID 或 GroupID 必须提供其一")
        if group_id:
            components = [platform, group_id]
        else:
            components = [platform, user_id, "private"]
        return hashlib.md5("_".join(components).encode()).hexdigest()

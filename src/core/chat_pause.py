"""本次运行内的聊天暂停状态；独立于回复频率配置，重启后解除。"""

from threading import RLock
from typing import Dict, Set


class ChatPauseState:
    """类型规则覆盖之后创建的会话，单流命令可覆盖类型规则。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._types: Set[bool] = set()
        self._sessions: Dict[str, bool] = {}
        self._session_types: Dict[str, bool] = {}

    def is_paused(self, session_id: str, is_group: bool) -> bool:
        with self._lock:
            return self._sessions.get(session_id, is_group in self._types)

    def set_type(self, is_group: bool, paused: bool) -> None:
        with self._lock:
            if paused:
                self._types.add(is_group)
            else:
                self._types.discard(is_group)
            # 批量命令重新统一该类型，清除之前的单流覆盖。
            for session_id, session_type in list(self._session_types.items()):
                if session_type == is_group:
                    del self._sessions[session_id]
                    del self._session_types[session_id]

    def set_session(self, session_id: str, is_group: bool, paused: bool) -> None:
        with self._lock:
            self._sessions[session_id] = paused
            self._session_types[session_id] = is_group


chat_pause_state = ChatPauseState()

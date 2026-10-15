"""被整理过的工具原始结果的暂存。

超长工具结果写入 Planner 历史时只保留子代理整理后的结论，原文暂存在这里，
Planner 需要整理里没有的细节时可以用 read_tool_result 工具按编号取回。
原文只保存在内存里：每个聊天保留最近若干条，进程重启后清空。
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

# 每个聊天最多暂存的原始结果条数，超出后丢弃最早的
MAX_STORED_RESULTS_PER_SESSION = 20


@dataclass(frozen=True, slots=True)
class StoredToolResult:
    """一条暂存的工具原始结果。"""

    result_id: str
    tool_name: str
    content: str
    created_at: datetime


class ToolResultStore:
    """按聊天分组暂存工具原始结果。"""

    def __init__(self) -> None:
        self._results_by_session: Dict[str, "OrderedDict[str, StoredToolResult]"] = {}
        self._next_index_by_session: Dict[str, int] = {}

    def save(self, session_id: str, tool_name: str, content: str) -> str:
        """暂存一条原始结果，返回在该聊天内唯一的短编号（如 ``r3``）。"""

        next_index = self._next_index_by_session.get(session_id, 0) + 1
        self._next_index_by_session[session_id] = next_index
        result_id = f"r{next_index}"
        session_results = self._results_by_session.setdefault(session_id, OrderedDict())
        session_results[result_id] = StoredToolResult(
            result_id=result_id,
            tool_name=tool_name,
            content=content,
            created_at=datetime.now(),
        )
        while len(session_results) > MAX_STORED_RESULTS_PER_SESSION:
            session_results.popitem(last=False)
        return result_id

    def get(self, session_id: str, result_id: str) -> Optional[StoredToolResult]:
        """按编号取回原始结果；已被淘汰或进程重启后不存在时返回 None。"""

        return self._results_by_session.get(session_id, OrderedDict()).get(result_id.strip())


tool_result_store = ToolResultStore()

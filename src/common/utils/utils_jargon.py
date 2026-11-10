"""黑话数据的会话归属解析。"""

from typing import Dict, Optional

import json


def parse_session_id_dict(session_id_dict_str: Optional[str]) -> Dict[str, int]:
    """读取存储的会话计数。损坏数据或非字典格式不提供会话归属。"""
    if not session_id_dict_str:
        return {}

    try:
        parsed = json.loads(session_id_dict_str)
    except (ValueError, TypeError, RecursionError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    session_counts: Dict[str, int] = {}
    for session_id, count in parsed.items():
        if isinstance(count, int):
            session_counts[session_id] = count
        else:
            try:
                session_counts[session_id] = int(count)
            except (TypeError, ValueError, OverflowError):
                session_counts[session_id] = 0
    return session_counts

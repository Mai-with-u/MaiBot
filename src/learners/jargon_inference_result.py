"""黑话推断引擎与后台调度器共用的终态结果。"""

from dataclasses import dataclass
from typing import Optional

import hashlib
import json


@dataclass(frozen=True)
class InferenceResult:
    status: str
    reason: str = ""
    meaning: str = ""
    is_jargon: Optional[bool] = None
    requests: int = 0
    evidence_hash: str = ""


def evidence_hash(value: Optional[str]) -> str:
    """证据组顺序不影响新证据判断，实际引用内容决定快照指纹。"""
    try:
        groups = json.loads(value or "[]")
        canonical = sorted(json.dumps(group, sort_keys=True, ensure_ascii=False) for group in groups)
        value = json.dumps(canonical, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    return hashlib.sha256((value or "").encode()).hexdigest()

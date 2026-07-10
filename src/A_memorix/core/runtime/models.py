from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class KernelSearchRequest:
    query: str = ""
    limit: int = 5
    mode: str = "search"
    chat_id: str = ""
    shared_chat_ids: Sequence[str] = ()
    person_id: str = ""
    time_start: Optional[str | float] = None
    time_end: Optional[str | float] = None
    respect_filter: bool = True
    user_id: str = ""
    group_id: str = ""


@dataclass
class _NormalizedSearchTimeWindow:
    numeric_start: Optional[float] = None
    numeric_end: Optional[float] = None
    query_start: Optional[str] = None
    query_end: Optional[str] = None

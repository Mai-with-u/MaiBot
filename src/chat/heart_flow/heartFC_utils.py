from dataclasses import dataclass, field
from typing import Optional, Dict, TypedDict

import time


class CyclePlanInfo(TypedDict): ...  # TODO: 根据实际需要补充字段


@dataclass
class CycleDetail:
    """循环信息记录类"""

    cycle_id: int
    thinking_id: str = ""
    """思考ID"""
    start_time: float = field(default_factory=time.time)
    """开始时间，单位为秒"""
    end_time: Optional[float] = None
    """结束时间，单位为秒，None表示未结束"""
    time_records: Dict[str, float] = field(default_factory=dict)
    """计时器记录，key为计时器名称，value为用时，单位为秒"""
    loop_plan_info: Optional[CyclePlanInfo] = None
    """循环计划记录"""

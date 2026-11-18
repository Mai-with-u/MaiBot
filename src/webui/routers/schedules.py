"""WebUI 只读事项列表，文件读取由同步端点在线程池中执行。"""

from typing import Any, Dict, List

from fastapi import APIRouter

from src.services.schedules.repository import read_schedule_items, schedules_directory

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
def list_schedules(include_completed: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """返回所有真实聊天流的事项，可同时查看已完成和已取消的记录。"""
    return {"items": read_schedule_items(schedules_directory(), include_completed=include_completed)}

"""人工发起的探索任务与主人会话私有的候选图式存储。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json
import threading
import time

from src.common.paths import get_instance_root


_store_lock = threading.Lock()


def validate_goal(goal: str) -> str:
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
        raise ValueError("探索目标必须为 1 至 2000 字")
    return goal.strip()


def schema_records(session_id: str, record: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """在线程池中调用；按真实会话隔离，原子替换文件并串行化读改写。"""
    path: Path = get_instance_root() / "data" / "desktop_pet" / "exploration_schemas.json"
    with _store_lock:
        records = json.loads(path.read_text("utf-8")) if path.exists() else []
        if record is not None:
            record = {**record, "id": uuid4().hex, "session_id": session_id, "status": "candidate"}
            records.append(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            return [record]
        return [item for item in records if item["session_id"] == session_id]


_run_id = uuid4().hex


def exploration_tasks(
    session_id: str, action: str = "list", task_id: str = "", *, goal: str = "",
    summary: str = "", result: str = "",
) -> Any:
    """只在工作线程调用。状态由明确的上报更新，旧进程的未结束任务显示为中断。"""
    path = get_instance_root() / "data" / "desktop_pet" / "exploration_tasks.json"
    with _store_lock:
        records = json.loads(path.read_text("utf-8")) if path.exists() else []
        own = [item for item in records if item["session_id"] == session_id]
        def visible(item: Dict[str, Any]) -> Dict[str, Any]:
            data = dict(item)
            if data["status"] in {"queued", "running"} and data["run_id"] != _run_id:
                data["status"] = "interrupted"
            return data
        if action == "list":
            return [visible(item) for item in reversed(own)]
        if action == "get":
            item = next((item for item in own if item["id"] == task_id), None)
            if item is None:
                raise ValueError("探索任务不存在或不属于当前会话")
            return visible(item)
        now = time.time()
        if action == "create":
            item = {"id": uuid4().hex, "session_id": session_id, "run_id": _run_id,
                    "goal": validate_goal(goal), "status": "queued", "created_at": now,
                    "updated_at": now, "result": "", "events": []}
            records.append(item)
            summary = "探索任务已记录，等待执行"
        elif action in {"progress", "complete", "fail"}:
            item = next((item for item in own if item["id"] == task_id), None)
            if item is None:
                raise ValueError("探索任务不存在或不属于当前会话")
            if visible(item)["status"] not in {"queued", "running"}:
                raise ValueError("探索任务已结束，不能重复更新")
            if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 2000:
                raise ValueError("步骤说明必须为 1 至 2000 字")
            if not isinstance(result, str) or len(result) > 30000:
                raise ValueError("结果必须是最多 30000 字的文本")
            if action == "complete" and not result.strip():
                raise ValueError("完成任务必须提供实际结果")
            item["status"] = {"progress": "running", "complete": "completed", "fail": "failed"}[action]
            item["updated_at"] = now
            if action in {"complete", "fail"}:
                item["result"] = result.strip() if result.strip() else summary.strip()
        else:
            raise ValueError("未知探索任务操作")
        item["events"].append({"time": now, "status": item["status"], "text": summary.strip()})
        item["events"] = item["events"][-100:]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return dict(item)

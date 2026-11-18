"""人工发起的探索任务与主人会话私有的候选图式存储。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import hashlib
import json
import threading
import time

from src.common.paths import get_instance_root

from .exploration_results import ExperienceReuse, ExplorationProducts


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


def experience_ref(experience: Dict[str, Any]) -> str:
    """内容版本指纹，防止任务产物替换或重排后把反馈记到另一条经验。"""
    return hashlib.sha256(json.dumps(experience, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def reuse_summary(task: Dict[str, Any], reference: str) -> Dict[str, Any]:
    records = [entry for entry in task.get("reuse_records", []) if entry["experience_ref"] == reference]
    counts = {outcome: sum(entry["report"]["outcome"] == outcome for entry in records)
              for outcome in ("success", "partial", "failed")}
    return {"total": len(records), **counts, "recent": list(reversed(records[-5:])),
            "latest_success": next((entry for entry in reversed(records) if entry["report"]["outcome"] == "success"), None),
            "latest_failure": next((entry for entry in reversed(records) if entry["report"]["outcome"] != "success"), None)}


def record_experience_reuse(
    session_id: str, task_id: str, reference: str, reuse_key: str, report: Dict[str, Any],
) -> Dict[str, Any]:
    """原子追加复用证据，不改写原实验结论或已结束任务状态；同一尝试的重试幂等。"""
    if not isinstance(reuse_key, str) or not 1 <= len(reuse_key.strip()) <= 100:
        raise ValueError("reuse_key 必须为本次尝试的唯一标识，1 至 100 字")
    reuse_key = reuse_key.strip()
    validated = ExperienceReuse.model_validate(report).model_dump()
    path = get_instance_root() / "data" / "desktop_pet" / "exploration_tasks.json"
    with _store_lock:
        tasks = json.loads(path.read_text("utf-8")) if path.exists() else []
        task = next((item for item in tasks if item["session_id"] == session_id and item["id"] == task_id), None)
        if task is None:
            raise ValueError("原探索任务不存在或不属于当前会话")
        records = task.get("reuse_records", [])
        previous = next((item for item in records if item["reuse_key"] == reuse_key), None)
        if previous is not None:
            if previous["experience_ref"] != reference or previous["report"] != validated:
                raise ValueError("该 reuse_key 已记录其他反馈；新尝试请使用新标识")
            return {"record": previous, "reuse": reuse_summary(task, reference)}
        if not any(experience_ref(item) == reference for item in task.get("experiences", [])):
            raise ValueError("经验版本已变化或引用无效，请重新检索，不要猜测经验引用")
        entry = {"id": uuid4().hex, "reuse_key": reuse_key, "experience_ref": reference,
                 "created_at": time.time(), "report": validated}
        task.setdefault("reuse_records", []).append(entry)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return {"record": entry, "reuse": reuse_summary(task, reference)}


def exploration_tasks(
    session_id: str, action: str = "list", task_id: str = "", *, goal: str = "",
    summary: str = "", result: str = "", products: Optional[Dict[str, Any]] = None,
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
                    "updated_at": now, "result": "", "events": [], "result_version": 2,
                    "resources": [], "experiences": []}
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
            if item.get("result_version") == 2:
                if len(result) > 400:
                    raise ValueError("结果概述最多 400 字，详细产物请分为资源和经验")
                if action == "complete" and products is None:
                    raise ValueError("完成任务必须提供 products，包含 resources 和 experiences 两类产物；无产物可为空列表")
            if products is not None:
                if set(products) != {"resources", "experiences"}:
                    raise ValueError("products 必须同时包含 resources 和 experiences")
                validated = ExplorationProducts.model_validate(products)
                item.update(validated.model_dump())
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

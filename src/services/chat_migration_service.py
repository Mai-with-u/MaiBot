"""在线聊天流复制的预览、冲突确认、执行与失败恢复。"""

from typing import Any, Dict, Set

import asyncio

from src.common.database.database import engine
from src.common.runtime_loop import run_on_main_loop
from src.config.config import BOT_CONFIG_PATH, config_manager

from . import chat_migration_config as configuration
from .chat_migration_gate import chat_migration_gate, settled_thread
from .chat_migration_store import LABELS, MigrationStore, digest


store = MigrationStore(engine)
running: Dict[str, asyncio.Task] = {}


async def initialize_migrations() -> None:
    """进程异常退出后的任务必须先恢复，不能让半迁移数据直接恢复写入。"""
    await asyncio.to_thread(store.initialize)
    for item in await asyncio.to_thread(store.list, True):
        if item["status"] in {"preview", "completed", "failed"}:
            continue
        job = await asyncio.to_thread(store.get, item["id"])
        chat_migration_gate.blocked.update({job["source"], job["target"]})
        await asyncio.to_thread(
            store.update, job["id"], "recovery_required", job["payload"], "上次迁移被中断，请恢复后重新预览"
        )


async def memory(action: str, **kwargs: Any) -> Dict[str, Any]:
    from src.A_memorix.host_service import a_memorix_host_service

    if not a_memorix_host_service.is_enabled():
        if (a_memorix_host_service.get_runtime_data_dir() / "metadata" / "metadata.db").exists():
            raise ValueError("已有长期记忆，但记忆服务未启用；请启用后迁移，不能跳过现有记忆")
        return {"fingerprint": "disabled", "counts": {"source": 0, "target": 0}, "success": True}
    result = await a_memorix_host_service.invoke("memory_chat_migration", {"action": action, **kwargs})
    if not isinstance(result, dict) or (not result.get("fingerprint") and not result.get("success")):
        raise ValueError(f"长期记忆服务暂不可用: {result}")
    return result


async def preview(source: str, target: str) -> Dict[str, Any]:
    from src.services.statistics_usage_hourly import refresh_usage_hourly

    await asyncio.to_thread(store.initialize)
    usage_state = await asyncio.to_thread(refresh_usage_hourly, time_budget_seconds=10)
    if not usage_state.ready:
        raise ValueError("模型用量统计仍在汇总，请稍后重新预览迁移")
    snapshot = await asyncio.to_thread(store.snapshot, source, target)
    config = await asyncio.to_thread(
        configuration.snapshot, BOT_CONFIG_PATH, snapshot["identities"][source], snapshot["identities"][target]
    )
    mem = await memory("snapshot", source=source, target=target)
    conflicts = snapshot["conflicts"] + [section for section in config["sections"] if not section["automatic"]]
    source_groups = store.source_rows(snapshot, "sharing_group_members", source)
    target_groups = store.source_rows(snapshot, "sharing_group_members", target)
    if source_groups and {row["group_id"] for row in source_groups} != {row["group_id"] for row in target_groups}:
        from src.services.sharing_group_service import list_groups

        groups = await asyncio.to_thread(list_groups)
        group_names = {group["id"]: group["name"] for group in groups}
        conflicts.append(
            {
                "id": "sharing_groups",
                "kind": "config",
                "label": "相通组成员关系",
                "source": [{"group_name": group_names[row["group_id"]]} for row in source_groups],
                "target": [{"group_name": group_names[row["group_id"]]} for row in target_groups],
                "choices": ["source", "target", "both"],
            }
        )
    counts = [
        {
            "key": name,
            "label": LABELS[name],
            "source": len(store.source_rows(snapshot, name, source)),
            "target": len(store.source_rows(snapshot, name, target)),
            "pending": sum(
                not store.already_copied(snapshot, name, row, source, target)
                for row in store.source_rows(snapshot, name, source)
            ),
        }
        for name in snapshot["tables"]
    ]
    payload = {
        "fingerprint": snapshot["fingerprint"],
        "config": config,
        "memory": {"fingerprint": mem["fingerprint"], "counts": mem["counts"]},
        "conflicts": conflicts,
        "counts": counts,
        "identities": snapshot["identities"],
    }
    job_id = await asyncio.to_thread(store.create, source, target, payload)
    return public_job(await asyncio.to_thread(store.get, job_id))


def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job["payload"]
    return {key: value for key, value in job.items() if key != "payload"} | {
        "conflicts": payload["conflicts"],
        "automatic_config": [section for section in payload["config"]["sections"] if section.get("automatic")],
        "counts": payload["counts"],
        "memory": payload["memory"]["counts"],
        "identities": payload["identities"],
        "result": payload.get("result"),
    }


async def start(job_id: str, decisions: Dict[str, str], confirm_memory: bool) -> Dict[str, Any]:
    return await run_on_main_loop(_start(job_id, decisions, confirm_memory))


async def _start(job_id: str, decisions: Dict[str, str], confirm_memory: bool) -> Dict[str, Any]:
    job = await asyncio.to_thread(store.get, job_id)
    if job["status"] != "preview" or job_id in running:
        raise ValueError("该迁移已执行或正在执行，请重新预览")
    if not confirm_memory:
        raise ValueError("必须确认目标长期记忆将被全量覆盖")
    conflicts = job["payload"]["conflicts"]
    if set(decisions) != {item["id"] for item in conflicts}:
        raise ValueError("冲突处理项与预览不一致")
    if any(decisions[item["id"]] not in item["choices"] for item in conflicts):
        raise ValueError("存在未处理的冲突")
    source, target = job["source"], job["target"]
    if {source, target}.intersection(chat_migration_gate.blocked):
        raise ValueError("所选聊天流正在迁移或等待恢复")
    try:
        await settled_thread(store.claim, job_id, decisions)
    except asyncio.CancelledError:
        claimed = await asyncio.to_thread(store.get, job_id)
        if claimed["status"] == "queued":
            await asyncio.to_thread(
                store.update, job_id, "failed", claimed["payload"], "执行请求在开始前被取消，请重新预览"
            )
        raise
    task = asyncio.create_task(execute(job_id), name=f"chat-migration:{job_id}")
    running[job_id] = task
    task.add_done_callback(lambda completed: running.pop(job_id, None))
    return {"id": job_id, "status": "queued"}


async def execute(job_id: str) -> None:
    from src.chat.heart_flow.heartflow_manager import heartflow_manager

    job = await asyncio.to_thread(store.get, job_id)
    payload = job["payload"]
    sessions: Set[str] = {job["source"], job["target"]}
    paused = False
    try:
        await chat_migration_gate.pause(sessions)
        paused = True
        await heartflow_manager.stop_migrating_chats(sessions)
        await asyncio.to_thread(store.update, job_id, "validating", payload)
        current = await asyncio.to_thread(store.snapshot, job["source"], job["target"])
        if current["fingerprint"] != payload["fingerprint"]:
            raise ValueError("数据在预览后发生变化，请重新预览并处理冲突")
        raw = await asyncio.to_thread(BOT_CONFIG_PATH.read_text, encoding="utf-8")
        if digest(raw) != payload["config"]["fingerprint"]:
            raise ValueError("配置在预览后发生变化，请重新预览")
        payload["rendered_config"] = configuration.render(payload["config"], payload["decisions"])
        payload["memory_started"] = True
        await asyncio.to_thread(store.update, job_id, "memory", payload)
        await memory(
            "apply",
            job_id=job_id,
            source=job["source"],
            target=job["target"],
            expected=payload["memory"]["fingerprint"],
        )
        if payload["rendered_config"] != raw:
            payload["config_started"] = True
            await asyncio.to_thread(store.update, job_id, "configuration", payload)
            await settled_thread(configuration.replace, BOT_CONFIG_PATH, digest(raw), payload["rendered_config"])
            if not await config_manager.reload_config(changed_scopes=["bot"]):
                raise RuntimeError("迁移配置热重载失败")
        await asyncio.to_thread(store.update, job_id, "database", payload)
        await settled_thread(store.apply, job_id, payload["fingerprint"], payload["decisions"])
        await finalize(job_id)
    except BaseException as exc:
        # 提交结果与完成状态在同一事务内；断连或取消不能将已完成任务误报为失败。
        latest = await asyncio.to_thread(store.get, job_id)
        if latest["payload"].get("database_committed"):
            await asyncio.to_thread(
                store.update, job_id, "recovery_required", latest["payload"], f"数据已提交，需恢复运行状态: {exc}"
            )
            paused = False
        elif latest["status"] != "completed":
            try:
                await rollback(job_id, payload)
                await asyncio.to_thread(store.update, job_id, "failed", payload, str(exc) or type(exc).__name__)
            except BaseException as recovery_error:
                await asyncio.to_thread(
                    store.update, job_id, "recovery_required", payload, f"{exc}; 恢复失败: {recovery_error}"
                )
                paused = False  # 保留这两条聊天流的暂停，等待显式恢复。
    finally:
        if paused:
            chat_migration_gate.resume(sessions)


async def finalize(job_id: str) -> None:
    from src.chat.message_receive.chat_manager import chat_manager
    from src.services.expression_vector_service import expression_vector_service
    from src.services.sharing_group_service import refresh as refresh_sharing_groups

    job = await asyncio.to_thread(store.get, job_id)
    chat_manager.last_messages.pop(job["target"], None)
    await asyncio.to_thread(refresh_sharing_groups)
    expression_vector_service._notify_index()
    await asyncio.to_thread(store.update, job_id, "completed", job["payload"])


async def rollback(job_id: str, payload: Dict[str, Any]) -> None:
    if payload.get("config_started"):
        raw = await asyncio.to_thread(BOT_CONFIG_PATH.read_text, encoding="utf-8")
        if raw != payload["config"]["raw"]:
            await settled_thread(
                configuration.replace, BOT_CONFIG_PATH, digest(payload["rendered_config"]), payload["config"]["raw"]
            )
            if not await config_manager.reload_config(changed_scopes=["bot"]):
                raise RuntimeError("恢复配置热重载失败")
    if payload.get("memory_started"):
        await memory("restore", job_id=job_id)


async def recover(job_id: str) -> Dict[str, Any]:
    job = await asyncio.to_thread(store.get, job_id)
    if job_id in running or job["status"] in {"preview", "completed", "failed"}:
        raise ValueError("该任务不需要恢复或仍在执行")
    running[job_id] = asyncio.current_task()
    try:
        if job["payload"].get("database_committed"):
            await finalize(job_id)
        else:
            await rollback(job_id, job["payload"])
            await asyncio.to_thread(store.update, job_id, "failed", job["payload"], "迁移已恢复，请重新创建预览")
        chat_migration_gate.resume({job["source"], job["target"]})
        return {"success": True}
    finally:
        running.pop(job_id, None)

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from src.services.memory_service import MemorySearchResult, memory_service
from src.webui.dependencies import require_auth


router = APIRouter(prefix="/api/webui/memory", tags=["memory"], dependencies=[Depends(require_auth)])
compat_router = APIRouter(prefix="/api", tags=["memory-compat"], dependencies=[Depends(require_auth)])
STAGING_ROOT = Path(__file__).resolve().parents[3] / "data" / "memory_upload_staging"


class NodeRequest(BaseModel):
    name: str = Field(..., min_length=1)


class NodeRenameRequest(BaseModel):
    old_name: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


class EdgeCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    confidence: float = Field(1.0, ge=0.0)


class EdgeDeleteRequest(BaseModel):
    hash: str = ""
    subject: str = ""
    object: str = ""


class EdgeWeightRequest(BaseModel):
    hash: str = ""
    subject: str = ""
    object: str = ""
    weight: float = Field(..., ge=0.0)


class SourceDeleteRequest(BaseModel):
    source: str = Field(..., min_length=1)


class SourceBatchDeleteRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)


class EpisodeRebuildRequest(BaseModel):
    source: str = ""
    sources: list[str] = Field(default_factory=list)
    all: bool = False


class EpisodeProcessPendingRequest(BaseModel):
    limit: int = Field(20, ge=1, le=200)
    max_retry: int = Field(3, ge=1, le=20)


class ProfileOverrideRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    override_text: str = ""
    updated_by: str = ""
    source: str = "webui"


class MaintainRequest(BaseModel):
    target: str = Field(..., min_length=1)
    hours: Optional[float] = None


class AutoSaveRequest(BaseModel):
    enabled: bool


class TuningApplyProfileRequest(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    reason: str = "manual"


class V5ActionRequest(BaseModel):
    target: str = Field(..., min_length=1)
    strength: Optional[float] = Field(default=None, ge=0.0)
    reason: str = ""
    updated_by: str = "webui"


class DeleteActionRequest(BaseModel):
    mode: str = Field(..., min_length=1)
    selector: dict[str, Any] | str = Field(default_factory=dict)
    reason: str = ""
    requested_by: str = "webui"


class DeleteRestoreRequest(BaseModel):
    operation_id: str = ""
    mode: str = ""
    selector: dict[str, Any] | str = Field(default_factory=dict)
    reason: str = ""
    requested_by: str = "webui"


class DeletePurgeRequest(BaseModel):
    grace_hours: Optional[float] = Field(default=None, ge=0.0)
    limit: int = Field(1000, ge=1, le=5000)


def _build_import_guide_markdown(settings: dict[str, Any]) -> str:
    path_aliases = settings.get("path_aliases") if isinstance(settings.get("path_aliases"), dict) else {}
    alias_lines = [
        f"- `{name}` -> `{path}`"
        for name, path in sorted(path_aliases.items())
        if str(name).strip() and str(path).strip()
    ]
    if not alias_lines:
        alias_lines = ["- 当前未配置路径别名"]
    return "\n".join(
        [
            "# 长期记忆导入说明",
            "",
            "支持的导入方式：",
            "- 上传文件：适合零散文档、日志、聊天导出文本。",
            "- 粘贴文本：适合一次性导入少量整理好的内容。",
            "- Raw Scan：扫描白名单目录内的原始文本文件。",
            "- LPMM OpenIE / Convert：处理既有 LPMM 数据。",
            "- Temporal Backfill：补回已有数据中的时间信息。",
            "- MaiBot Migration：从宿主数据库迁移历史聊天记忆。",
            "",
            "当前路径别名：",
            *alias_lines,
            "",
            "执行建议：",
            "- 首次导入先小批量试跑，确认切分和抽取结果正常。",
            "- 大批量导入时优先关注任务状态、失败块与重试结果。",
            "- 若路径解析失败，请先检查路径别名与相对路径是否仍然有效。",
        ]
    )


def _unwrap_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    nested = raw.get("payload")
    if isinstance(nested, dict):
        return dict(nested)
    return dict(raw)


async def _graph_get(limit: int) -> dict:
    return await memory_service.graph_admin(action="get_graph", limit=limit)


async def _graph_create_node(payload: NodeRequest) -> dict:
    return await memory_service.graph_admin(action="create_node", name=payload.name)


async def _graph_delete_node(payload: NodeRequest) -> dict:
    return await memory_service.graph_admin(action="delete_node", name=payload.name)


async def _graph_rename_node(payload: NodeRenameRequest) -> dict:
    return await memory_service.graph_admin(action="rename_node", old_name=payload.old_name, new_name=payload.new_name)


async def _graph_create_edge(payload: EdgeCreateRequest) -> dict:
    return await memory_service.graph_admin(
        action="create_edge",
        subject=payload.subject,
        predicate=payload.predicate,
        object=payload.object,
        confidence=payload.confidence,
    )


async def _graph_delete_edge(payload: EdgeDeleteRequest) -> dict:
    return await memory_service.graph_admin(
        action="delete_edge",
        hash=payload.hash,
        subject=payload.subject,
        object=payload.object,
    )


async def _graph_update_edge_weight(payload: EdgeWeightRequest) -> dict:
    return await memory_service.graph_admin(
        action="update_edge_weight",
        hash=payload.hash,
        subject=payload.subject,
        object=payload.object,
        weight=payload.weight,
    )


async def _source_list() -> dict:
    return await memory_service.source_admin(action="list")


async def _source_delete(payload: SourceDeleteRequest) -> dict:
    return await memory_service.source_admin(action="delete", source=payload.source)


async def _source_batch_delete(payload: SourceBatchDeleteRequest) -> dict:
    return await memory_service.source_admin(action="batch_delete", sources=payload.sources)


async def _query_aggregate(
    query: str,
    *,
    limit: int,
    chat_id: str,
    person_id: str,
    time_start: float | None,
    time_end: float | None,
) -> dict:
    result: MemorySearchResult = await memory_service.search(
        query,
        limit=limit,
        mode="aggregate",
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
        respect_filter=False,
    )
    return {"success": True, **result.to_dict()}


async def _episode_list(
    *,
    query: str,
    limit: int,
    source: str,
    person_id: str,
    time_start: float | None,
    time_end: float | None,
) -> dict:
    return await memory_service.episode_admin(
        action="list",
        query=query,
        limit=limit,
        source=source,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


async def _episode_get(episode_id: str) -> dict:
    return await memory_service.episode_admin(action="get", episode_id=episode_id)


async def _episode_rebuild(payload: EpisodeRebuildRequest) -> dict:
    return await memory_service.episode_admin(
        action="rebuild",
        source=payload.source,
        sources=payload.sources,
        all=payload.all,
    )


async def _episode_status(limit: int) -> dict:
    return await memory_service.episode_admin(action="status", limit=limit)


async def _episode_process_pending(payload: EpisodeProcessPendingRequest) -> dict:
    return await memory_service.episode_admin(
        action="process_pending",
        limit=payload.limit,
        max_retry=payload.max_retry,
    )


async def _profile_query(*, person_id: str, person_keyword: str, limit: int, force_refresh: bool) -> dict:
    return await memory_service.profile_admin(
        action="query",
        person_id=person_id,
        person_keyword=person_keyword,
        limit=limit,
        force_refresh=force_refresh,
    )


async def _profile_list(limit: int) -> dict:
    return await memory_service.profile_admin(action="list", limit=limit)


async def _profile_set_override(payload: ProfileOverrideRequest) -> dict:
    return await memory_service.profile_admin(
        action="set_override",
        person_id=payload.person_id,
        override_text=payload.override_text,
        updated_by=payload.updated_by,
        source=payload.source,
    )


async def _profile_delete_override(person_id: str) -> dict:
    return await memory_service.profile_admin(action="delete_override", person_id=person_id)


async def _runtime_save() -> dict:
    return await memory_service.runtime_admin(action="save")


async def _runtime_config() -> dict:
    return await memory_service.runtime_admin(action="get_config")


async def _runtime_self_check(refresh: bool) -> dict:
    return await memory_service.runtime_admin(action="refresh_self_check" if refresh else "self_check")


async def _runtime_auto_save(enabled: bool | None = None) -> dict:
    if enabled is None:
        config = await memory_service.runtime_admin(action="get_config")
        return {"success": bool(config.get("success", False)), "auto_save": bool(config.get("auto_save", False))}
    return await memory_service.runtime_admin(action="set_auto_save", enabled=enabled)


async def _maintenance_recycle_bin(limit: int) -> dict:
    return await memory_service.get_recycle_bin(limit=limit)


async def _maintenance_restore(payload: MaintainRequest) -> dict:
    return (await memory_service.restore_memory(target=payload.target)).to_dict()


async def _maintenance_reinforce(payload: MaintainRequest) -> dict:
    return (await memory_service.reinforce_memory(target=payload.target)).to_dict()


async def _maintenance_freeze(payload: MaintainRequest) -> dict:
    return (await memory_service.freeze_memory(target=payload.target)).to_dict()


async def _maintenance_protect(payload: MaintainRequest) -> dict:
    return (await memory_service.protect_memory(target=payload.target, hours=payload.hours)).to_dict()


async def _v5_status(target: str, limit: int) -> dict:
    return await memory_service.v5_admin(action="status", target=target, limit=limit)


async def _v5_recycle_bin(limit: int) -> dict:
    return await memory_service.v5_admin(action="recycle_bin", limit=limit)


async def _v5_action(action: str, payload: V5ActionRequest) -> dict:
    kwargs: dict[str, Any] = {
        "target": payload.target,
        "reason": payload.reason,
        "updated_by": payload.updated_by,
    }
    if payload.strength is not None:
        kwargs["strength"] = payload.strength
    return await memory_service.v5_admin(action=action, **kwargs)


async def _delete_preview(payload: DeleteActionRequest) -> dict:
    return await memory_service.delete_admin(action="preview", mode=payload.mode, selector=payload.selector)


async def _delete_execute(payload: DeleteActionRequest) -> dict:
    return await memory_service.delete_admin(
        action="execute",
        mode=payload.mode,
        selector=payload.selector,
        reason=payload.reason,
        requested_by=payload.requested_by,
    )


async def _delete_restore(payload: DeleteRestoreRequest) -> dict:
    return await memory_service.delete_admin(
        action="restore",
        mode=payload.mode,
        selector=payload.selector,
        operation_id=payload.operation_id,
        reason=payload.reason,
        requested_by=payload.requested_by,
    )


async def _delete_list(limit: int, mode: str) -> dict:
    return await memory_service.delete_admin(action="list_operations", limit=limit, mode=mode)


async def _delete_get(operation_id: str) -> dict:
    return await memory_service.delete_admin(action="get_operation", operation_id=operation_id)


async def _delete_purge(payload: DeletePurgeRequest) -> dict:
    return await memory_service.delete_admin(
        action="purge",
        grace_hours=payload.grace_hours,
        limit=payload.limit,
    )


async def _import_settings() -> dict:
    return await memory_service.import_admin(action="get_settings")


async def _import_path_aliases() -> dict:
    return await memory_service.import_admin(action="get_path_aliases")


async def _import_guide() -> dict:
    payload = await memory_service.import_admin(action="get_guide")
    if not isinstance(payload, dict):
        payload = {"success": False, "error": "invalid_payload"}
    if isinstance(payload.get("content"), str):
        return payload

    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else None
    if settings is None:
        settings_payload = await memory_service.import_admin(action="get_settings")
        settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}

    return {
        "success": True,
        "source": "local",
        "path": "generated://memory_import_guide",
        "content": _build_import_guide_markdown(settings or {}),
        "settings": settings or {},
    }


async def _import_resolve_path(payload: dict[str, Any]) -> dict:
    return await memory_service.import_admin(action="resolve_path", **_unwrap_payload(payload))


async def _import_create(action: str, payload: dict[str, Any]) -> dict:
    return await memory_service.import_admin(action=action, **_unwrap_payload(payload))


async def _import_list(limit: int) -> dict:
    listing = await memory_service.import_admin(action="list", limit=limit)
    if not isinstance(listing, dict):
        listing = {"success": False, "items": []}
    settings_payload = await memory_service.import_admin(action="get_settings")
    settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}
    listing.setdefault("success", True)
    listing.setdefault("items", [])
    listing["settings"] = settings
    return listing


async def _import_get(task_id: str, include_chunks: bool) -> dict:
    return await memory_service.import_admin(action="get", task_id=task_id, include_chunks=include_chunks)


async def _import_chunks(task_id: str, file_id: str, offset: int, limit: int) -> dict:
    return await memory_service.import_admin(
        action="get_chunks",
        task_id=task_id,
        file_id=file_id,
        offset=offset,
        limit=limit,
    )


async def _import_cancel(task_id: str) -> dict:
    return await memory_service.import_admin(action="cancel", task_id=task_id)


async def _import_retry(task_id: str, payload: dict[str, Any]) -> dict:
    raw = _unwrap_payload(payload)
    overrides = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else raw
    return await memory_service.import_admin(action="retry_failed", task_id=task_id, overrides=overrides)


async def _tuning_settings() -> dict:
    return await memory_service.tuning_admin(action="get_settings")


async def _tuning_profile() -> dict:
    profile = await memory_service.tuning_admin(action="get_profile")
    if not isinstance(profile, dict):
        profile = {"success": False, "profile": {}}
    if not isinstance(profile.get("settings"), dict):
        settings = await memory_service.tuning_admin(action="get_settings")
        profile["settings"] = settings.get("settings") if isinstance(settings.get("settings"), dict) else {}
    return profile


async def _tuning_apply_profile(payload: TuningApplyProfileRequest) -> dict:
    return await memory_service.tuning_admin(action="apply_profile", profile=payload.profile, reason=payload.reason)


async def _tuning_rollback_profile() -> dict:
    return await memory_service.tuning_admin(action="rollback_profile")


async def _tuning_export_profile() -> dict:
    return await memory_service.tuning_admin(action="export_profile")


async def _tuning_create_task(payload: dict[str, Any]) -> dict:
    return await memory_service.tuning_admin(action="create_task", payload=_unwrap_payload(payload))


async def _tuning_list_tasks(limit: int) -> dict:
    return await memory_service.tuning_admin(action="list_tasks", limit=limit)


async def _tuning_get_task(task_id: str, include_rounds: bool) -> dict:
    return await memory_service.tuning_admin(action="get_task", task_id=task_id, include_rounds=include_rounds)


async def _tuning_get_rounds(task_id: str, offset: int, limit: int) -> dict:
    return await memory_service.tuning_admin(action="get_rounds", task_id=task_id, offset=offset, limit=limit)


async def _tuning_cancel(task_id: str) -> dict:
    return await memory_service.tuning_admin(action="cancel", task_id=task_id)


async def _tuning_apply_best(task_id: str) -> dict:
    return await memory_service.tuning_admin(action="apply_best", task_id=task_id)


async def _tuning_report(task_id: str, fmt: str) -> dict:
    payload = await memory_service.tuning_admin(action="get_report", task_id=task_id, format=fmt)
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    return {
        "success": bool(payload.get("success", False)),
        "format": report.get("format", fmt),
        "content": report.get("content", ""),
        "path": report.get("path", ""),
        "error": payload.get("error", ""),
    }


async def _stage_upload_files(files: list[UploadFile]) -> tuple[Path, list[dict[str, Any]]]:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_dir = STAGING_ROOT / uuid.uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_files: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        filename = Path(upload.filename or f"upload_{index}.txt").name
        target = staging_dir / f"{index:03d}_{filename}"
        content = await upload.read()
        target.write_bytes(content)
        staged_files.append(
            {
                "filename": filename,
                "staged_path": str(target.resolve()),
                "size": len(content),
            }
        )
    return staging_dir, staged_files


@router.get("/graph")
async def get_memory_graph(limit: int = Query(200, ge=1, le=5000)):
    return await _graph_get(limit)


@router.post("/graph/node")
async def create_memory_node(payload: NodeRequest):
    return await _graph_create_node(payload)


@router.delete("/graph/node")
async def delete_memory_node(payload: NodeRequest):
    return await _graph_delete_node(payload)


@router.post("/graph/node/rename")
async def rename_memory_node(payload: NodeRenameRequest):
    return await _graph_rename_node(payload)


@router.post("/graph/edge")
async def create_memory_edge(payload: EdgeCreateRequest):
    return await _graph_create_edge(payload)


@router.delete("/graph/edge")
async def delete_memory_edge(payload: EdgeDeleteRequest):
    return await _graph_delete_edge(payload)


@router.post("/graph/edge/weight")
async def update_memory_edge_weight(payload: EdgeWeightRequest):
    return await _graph_update_edge_weight(payload)


@router.get("/sources")
async def list_memory_sources():
    return await _source_list()


@router.post("/sources/delete")
async def delete_memory_source(payload: SourceDeleteRequest):
    return await _source_delete(payload)


@router.post("/sources/batch-delete")
async def batch_delete_memory_sources(payload: SourceBatchDeleteRequest):
    return await _source_batch_delete(payload)


@router.get("/query/aggregate")
async def query_memory_aggregate(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    chat_id: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _query_aggregate(
        query,
        limit=limit,
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@router.get("/episodes")
async def list_memory_episodes(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    source: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _episode_list(
        query=query,
        limit=limit,
        source=source,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@router.get("/episodes/{episode_id}")
async def get_memory_episode(episode_id: str):
    return await _episode_get(episode_id)


@router.post("/episodes/rebuild")
async def rebuild_memory_episodes(payload: EpisodeRebuildRequest):
    return await _episode_rebuild(payload)


@router.get("/episodes/status")
async def get_memory_episode_status(limit: int = Query(20, ge=1, le=200)):
    return await _episode_status(limit)


@router.post("/episodes/process-pending")
async def process_memory_episode_pending(payload: EpisodeProcessPendingRequest):
    return await _episode_process_pending(payload)


@router.get("/profiles/query")
async def query_memory_profile(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    limit: int = Query(12, ge=1, le=100),
    force_refresh: bool = Query(False),
):
    return await _profile_query(
        person_id=person_id,
        person_keyword=person_keyword,
        limit=limit,
        force_refresh=force_refresh,
    )


@router.get("/profiles")
async def list_memory_profiles(limit: int = Query(50, ge=1, le=200)):
    return await _profile_list(limit)


@router.post("/profiles/override")
async def set_memory_profile_override(payload: ProfileOverrideRequest):
    return await _profile_set_override(payload)


@router.delete("/profiles/override/{person_id}")
async def delete_memory_profile_override(person_id: str):
    return await _profile_delete_override(person_id)


@router.post("/runtime/save")
async def save_memory_runtime():
    return await _runtime_save()


@router.get("/runtime/config")
async def get_memory_runtime_config():
    return await _runtime_config()


@router.get("/runtime/self-check")
async def get_memory_runtime_self_check():
    return await _runtime_self_check(False)


@router.post("/runtime/self-check/refresh")
async def refresh_memory_runtime_self_check():
    return await _runtime_self_check(True)


@router.get("/runtime/auto-save")
async def get_memory_runtime_auto_save():
    return await _runtime_auto_save(None)


@router.post("/runtime/auto-save")
async def set_memory_runtime_auto_save(payload: AutoSaveRequest):
    return await _runtime_auto_save(payload.enabled)


@router.get("/maintenance/recycle-bin")
async def get_memory_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _maintenance_recycle_bin(limit)


@router.post("/maintenance/restore")
async def restore_memory_relation(payload: MaintainRequest):
    return await _maintenance_restore(payload)


@router.post("/maintenance/reinforce")
async def reinforce_memory_relation(payload: MaintainRequest):
    return await _maintenance_reinforce(payload)


@router.post("/maintenance/freeze")
async def freeze_memory_relation(payload: MaintainRequest):
    return await _maintenance_freeze(payload)


@router.post("/maintenance/protect")
async def protect_memory_relation(payload: MaintainRequest):
    return await _maintenance_protect(payload)


@router.get("/v5/status")
async def get_memory_v5_status(
    target: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    return await _v5_status(target, limit)


@router.get("/v5/recycle-bin")
async def get_memory_v5_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _v5_recycle_bin(limit)


@router.post("/v5/reinforce")
async def reinforce_memory_v5(payload: V5ActionRequest):
    return await _v5_action("reinforce", payload)


@router.post("/v5/weaken")
async def weaken_memory_v5(payload: V5ActionRequest):
    return await _v5_action("weaken", payload)


@router.post("/v5/remember-forever")
async def remember_forever_memory_v5(payload: V5ActionRequest):
    return await _v5_action("remember_forever", payload)


@router.post("/v5/forget")
async def forget_memory_v5(payload: V5ActionRequest):
    return await _v5_action("forget", payload)


@router.post("/v5/restore")
async def restore_memory_v5(payload: V5ActionRequest):
    return await _v5_action("restore", payload)


@router.post("/delete/preview")
async def preview_memory_delete(payload: DeleteActionRequest):
    return await _delete_preview(payload)


@router.post("/delete/execute")
async def execute_memory_delete(payload: DeleteActionRequest):
    return await _delete_execute(payload)


@router.post("/delete/restore")
async def restore_memory_delete(payload: DeleteRestoreRequest):
    return await _delete_restore(payload)


@router.get("/delete/operations")
async def list_memory_delete_operations(
    limit: int = Query(50, ge=1, le=200),
    mode: str = Query(""),
):
    return await _delete_list(limit, mode)


@router.get("/delete/operations/{operation_id}")
async def get_memory_delete_operation(operation_id: str):
    return await _delete_get(operation_id)


@router.post("/delete/purge")
async def purge_memory_delete(payload: DeletePurgeRequest):
    return await _delete_purge(payload)


@router.get("/import/settings")
async def get_memory_import_settings():
    return await _import_settings()


@router.get("/import/path-aliases")
async def get_memory_import_path_aliases():
    return await _import_path_aliases()


@router.get("/import/guide")
async def get_memory_import_guide():
    return await _import_guide()


@router.post("/import/resolve-path")
async def resolve_memory_import_path(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_resolve_path(payload)


@router.post("/import/upload")
async def create_memory_import_upload(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    staging_dir, staged_files = await _stage_upload_files(files)
    try:
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["staged_files"] = staged_files
        return await _import_create("create_upload", payload)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


@router.post("/import/paste")
async def create_memory_import_paste(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@router.post("/import/raw-scan")
async def create_memory_import_raw_scan(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@router.post("/import/lpmm-openie")
async def create_memory_import_lpmm_openie(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@router.post("/import/lpmm-convert")
async def create_memory_import_lpmm_convert(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@router.post("/import/temporal-backfill")
async def create_memory_import_temporal_backfill(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@router.post("/import/maibot-migration")
async def create_memory_import_maibot_migration(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@router.get("/import/tasks")
async def list_memory_import_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _import_list(limit)


@router.get("/import/tasks/{task_id}")
async def get_memory_import_task(task_id: str, include_chunks: bool = Query(False)):
    return await _import_get(task_id, include_chunks)


@router.get("/import/tasks/{task_id}/chunks/{file_id}")
async def get_memory_import_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@router.post("/import/tasks/{task_id}/cancel")
async def cancel_memory_import_task(task_id: str):
    return await _import_cancel(task_id)


@router.post("/import/tasks/{task_id}/retry")
async def retry_memory_import_task(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@router.get("/retrieval_tuning/settings")
async def get_memory_tuning_settings():
    return await _tuning_settings()


@router.get("/retrieval_tuning/profile")
async def get_memory_tuning_profile():
    return await _tuning_profile()


@router.post("/retrieval_tuning/profile/apply")
async def apply_memory_tuning_profile(payload: TuningApplyProfileRequest):
    return await _tuning_apply_profile(payload)


@router.post("/retrieval_tuning/profile/rollback")
async def rollback_memory_tuning_profile():
    return await _tuning_rollback_profile()


@router.get("/retrieval_tuning/profile/export")
async def export_memory_tuning_profile():
    return await _tuning_export_profile()


@router.post("/retrieval_tuning/tasks")
async def create_memory_tuning_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _tuning_create_task(payload)


@router.get("/retrieval_tuning/tasks")
async def list_memory_tuning_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _tuning_list_tasks(limit)


@router.get("/retrieval_tuning/tasks/{task_id}")
async def get_memory_tuning_task(task_id: str, include_rounds: bool = Query(False)):
    return await _tuning_get_task(task_id, include_rounds)


@router.get("/retrieval_tuning/tasks/{task_id}/rounds")
async def get_memory_tuning_rounds(
    task_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _tuning_get_rounds(task_id, offset, limit)


@router.post("/retrieval_tuning/tasks/{task_id}/cancel")
async def cancel_memory_tuning_task(task_id: str):
    return await _tuning_cancel(task_id)


@router.post("/retrieval_tuning/tasks/{task_id}/apply-best")
async def apply_best_memory_tuning_profile(task_id: str):
    return await _tuning_apply_best(task_id)


@router.get("/retrieval_tuning/tasks/{task_id}/report")
async def get_memory_tuning_report(task_id: str, format: str = Query("md")):
    return await _tuning_report(task_id, format)


@compat_router.get("/graph")
async def compat_get_graph(limit: int = Query(200, ge=1, le=5000)):
    return await _graph_get(limit)


@compat_router.post("/node")
async def compat_create_node(payload: NodeRequest):
    return await _graph_create_node(payload)


@compat_router.delete("/node")
async def compat_delete_node(payload: NodeRequest):
    return await _graph_delete_node(payload)


@compat_router.post("/node/rename")
async def compat_rename_node(payload: NodeRenameRequest):
    return await _graph_rename_node(payload)


@compat_router.post("/edge")
async def compat_create_edge(payload: EdgeCreateRequest):
    return await _graph_create_edge(payload)


@compat_router.delete("/edge")
async def compat_delete_edge(payload: EdgeDeleteRequest):
    return await _graph_delete_edge(payload)


@compat_router.post("/edge/weight")
async def compat_update_edge_weight(payload: EdgeWeightRequest):
    return await _graph_update_edge_weight(payload)


@compat_router.get("/source/list")
async def compat_list_sources():
    return await _source_list()


@compat_router.post("/source/delete")
async def compat_delete_source(payload: SourceDeleteRequest):
    return await _source_delete(payload)


@compat_router.post("/source/batch_delete")
async def compat_batch_delete_sources(payload: SourceBatchDeleteRequest):
    return await _source_batch_delete(payload)


@compat_router.get("/query/aggregate")
async def compat_query_aggregate(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    chat_id: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _query_aggregate(
        query,
        limit=limit,
        chat_id=chat_id,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@compat_router.get("/episodes")
async def compat_list_episodes(
    query: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    source: str = Query(""),
    person_id: str = Query(""),
    time_start: float | None = Query(None),
    time_end: float | None = Query(None),
):
    return await _episode_list(
        query=query,
        limit=limit,
        source=source,
        person_id=person_id,
        time_start=time_start,
        time_end=time_end,
    )


@compat_router.get("/episodes/{episode_id}")
async def compat_get_episode(episode_id: str):
    return await _episode_get(episode_id)


@compat_router.post("/episodes/rebuild")
async def compat_rebuild_episodes(payload: EpisodeRebuildRequest):
    return await _episode_rebuild(payload)


@compat_router.get("/episodes/status")
async def compat_episode_status(limit: int = Query(20, ge=1, le=200)):
    return await _episode_status(limit)


@compat_router.post("/episodes/process_pending")
async def compat_process_episode_pending(payload: EpisodeProcessPendingRequest):
    return await _episode_process_pending(payload)


@compat_router.get("/person_profile/query")
async def compat_profile_query(
    person_id: str = Query(""),
    person_keyword: str = Query(""),
    limit: int = Query(12, ge=1, le=100),
    force_refresh: bool = Query(False),
):
    return await _profile_query(
        person_id=person_id,
        person_keyword=person_keyword,
        limit=limit,
        force_refresh=force_refresh,
    )


@compat_router.get("/person_profile/list")
async def compat_profile_list(limit: int = Query(50, ge=1, le=200)):
    return await _profile_list(limit)


@compat_router.post("/person_profile/override")
async def compat_set_profile_override(payload: ProfileOverrideRequest):
    return await _profile_set_override(payload)


@compat_router.delete("/person_profile/override/{person_id}")
async def compat_delete_profile_override(person_id: str):
    return await _profile_delete_override(person_id)


@compat_router.post("/save")
async def compat_runtime_save():
    return await _runtime_save()


@compat_router.get("/config")
async def compat_runtime_config():
    return await _runtime_config()


@compat_router.get("/runtime/self_check")
async def compat_runtime_self_check():
    return await _runtime_self_check(False)


@compat_router.post("/runtime/self_check/refresh")
async def compat_refresh_runtime_self_check():
    return await _runtime_self_check(True)


@compat_router.get("/config/auto_save")
async def compat_runtime_auto_save():
    return await _runtime_auto_save(None)


@compat_router.post("/config/auto_save")
async def compat_set_runtime_auto_save(payload: AutoSaveRequest):
    return await _runtime_auto_save(payload.enabled)


@compat_router.get("/memory/recycle_bin")
async def compat_get_recycle_bin(limit: int = Query(50, ge=1, le=200)):
    return await _maintenance_recycle_bin(limit)


@compat_router.post("/memory/restore")
async def compat_restore_memory(payload: MaintainRequest):
    return await _maintenance_restore(payload)


@compat_router.post("/memory/reinforce")
async def compat_reinforce_memory(payload: MaintainRequest):
    return await _maintenance_reinforce(payload)


@compat_router.post("/memory/freeze")
async def compat_freeze_memory(payload: MaintainRequest):
    return await _maintenance_freeze(payload)


@compat_router.post("/memory/protect")
async def compat_protect_memory(payload: MaintainRequest):
    return await _maintenance_protect(payload)


@compat_router.get("/import/settings")
async def compat_import_settings():
    return await _import_settings()


@compat_router.get("/import/path_aliases")
async def compat_import_path_aliases():
    return await _import_path_aliases()


@compat_router.get("/import/guide")
async def compat_import_guide():
    return await _import_guide()


@compat_router.post("/import/resolve_path")
async def compat_import_resolve_path(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_resolve_path(payload)


@compat_router.post("/import/upload")
async def compat_import_upload(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    return await create_memory_import_upload(files=files, payload_json=payload_json)


@compat_router.post("/import/tasks/upload")
async def compat_import_upload_task(
    files: list[UploadFile] = File(...),
    payload_json: str = Form("{}"),
):
    return await create_memory_import_upload(files=files, payload_json=payload_json)


@compat_router.post("/import/paste")
async def compat_import_paste(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@compat_router.post("/import/tasks/paste")
async def compat_import_paste_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_paste", payload)


@compat_router.post("/import/raw_scan")
async def compat_import_raw_scan(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@compat_router.post("/import/tasks/raw_scan")
async def compat_import_raw_scan_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_raw_scan", payload)


@compat_router.post("/import/lpmm_openie")
async def compat_import_lpmm_openie(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@compat_router.post("/import/tasks/lpmm_openie")
async def compat_import_lpmm_openie_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_openie", payload)


@compat_router.post("/import/lpmm_convert")
async def compat_import_lpmm_convert(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@compat_router.post("/import/tasks/lpmm_convert")
async def compat_import_lpmm_convert_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_lpmm_convert", payload)


@compat_router.post("/import/temporal_backfill")
async def compat_import_temporal_backfill(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@compat_router.post("/import/tasks/temporal_backfill")
async def compat_import_temporal_backfill_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_temporal_backfill", payload)


@compat_router.post("/import/maibot_migration")
async def compat_import_maibot_migration(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@compat_router.post("/import/tasks/maibot_migration")
async def compat_import_maibot_migration_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_create("create_maibot_migration", payload)


@compat_router.get("/import/tasks")
async def compat_import_list(limit: int = Query(50, ge=1, le=200)):
    return await _import_list(limit)


@compat_router.get("/import/tasks/{task_id}")
async def compat_import_get(task_id: str, include_chunks: bool = Query(False)):
    return await _import_get(task_id, include_chunks)


@compat_router.get("/import/tasks/{task_id}/chunks/{file_id}")
async def compat_import_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@compat_router.get("/import/tasks/{task_id}/files/{file_id}/chunks")
async def compat_import_file_chunks(
    task_id: str,
    file_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _import_chunks(task_id, file_id, offset, limit)


@compat_router.post("/import/tasks/{task_id}/cancel")
async def compat_import_cancel(task_id: str):
    return await _import_cancel(task_id)


@compat_router.post("/import/tasks/{task_id}/retry")
async def compat_import_retry(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@compat_router.post("/import/tasks/{task_id}/retry_failed")
async def compat_import_retry_failed(task_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return await _import_retry(task_id, payload)


@compat_router.get("/retrieval_tuning/settings")
async def compat_tuning_settings():
    return await _tuning_settings()


@compat_router.get("/retrieval_tuning/profile")
async def compat_tuning_profile():
    return await _tuning_profile()


@compat_router.post("/retrieval_tuning/profile/apply")
async def compat_apply_tuning_profile(payload: TuningApplyProfileRequest):
    return await _tuning_apply_profile(payload)


@compat_router.post("/retrieval_tuning/profile/rollback")
async def compat_rollback_tuning_profile():
    return await _tuning_rollback_profile()


@compat_router.get("/retrieval_tuning/profile/export")
async def compat_export_tuning_profile():
    return await _tuning_export_profile()


@compat_router.get("/retrieval_tuning/profile/export_toml")
async def compat_export_tuning_profile_toml():
    return await _tuning_export_profile()


@compat_router.post("/retrieval_tuning/tasks")
async def compat_create_tuning_task(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _tuning_create_task(payload)


@compat_router.get("/retrieval_tuning/tasks")
async def compat_list_tuning_tasks(limit: int = Query(50, ge=1, le=200)):
    return await _tuning_list_tasks(limit)


@compat_router.get("/retrieval_tuning/tasks/{task_id}")
async def compat_get_tuning_task(task_id: str, include_rounds: bool = Query(False)):
    return await _tuning_get_task(task_id, include_rounds)


@compat_router.get("/retrieval_tuning/tasks/{task_id}/rounds")
async def compat_get_tuning_rounds(
    task_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _tuning_get_rounds(task_id, offset, limit)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/cancel")
async def compat_cancel_tuning_task(task_id: str):
    return await _tuning_cancel(task_id)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/apply_best")
async def compat_apply_best_tuning_profile(task_id: str):
    return await _tuning_apply_best(task_id)


@compat_router.post("/retrieval_tuning/tasks/{task_id}/apply-best")
async def compat_apply_best_tuning_profile_kebab(task_id: str):
    return await _tuning_apply_best(task_id)


@compat_router.get("/retrieval_tuning/tasks/{task_id}/report")
async def compat_get_tuning_report(task_id: str, format: str = Query("md")):
    return await _tuning_report(task_id, format)

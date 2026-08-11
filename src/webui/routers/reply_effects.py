"""MaiSaka 回复效果分析与迁移接口。"""

from collections import defaultdict
from datetime import datetime
from difflib import unified_diff
from io import BytesIO
from math import isfinite, sqrt
from statistics import pstdev, variance
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scipy.stats import t as student_t
from sqlmodel import col, select

import gzip
import json
import zlib

from src.common.database.database import get_db_session
from src.common.database.database_model import MaisakaReplyEffect, Messages
from src.common.reply_effect_fingerprint import (
    extract_generation_fingerprints,
    extract_system_prompt_from_metadata,
)
from src.common.reply_effect_record_codec import decode_record_payload
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.reply_effect.models import (
    COMPLETE_OBSERVATION_REASONS,
    SCHEMA_VERSION,
    reply_effect_record_from_dict,
)
from src.maisaka.reply_effect.storage import ReplyEffectStorage
from src.maisaka.reply_effect.tracker import clear_active_reply_effect_trackers
from src.webui.dependencies import require_auth
from src.webui.routers.avatar import build_webui_avatar_url

router = APIRouter(prefix="/reply-effects", tags=["reply-effects"], dependencies=[Depends(require_auth)])

_EXPORT_FORMAT = "maibot-reply-effects"
_EXPORT_FORMAT_VERSION = 2
_MAX_IMPORT_FILE_BYTES = 64 * 1024 * 1024
_MAX_IMPORT_JSON_BYTES = 256 * 1024 * 1024
_SIGNIFICANCE_ALPHA = 0.05
_SIGNIFICANCE_METRICS = {
    "response_score": "回应度",
    "reception_score": "情感接受度",
    "conversation_score": "聊天推动度",
}


class ReplyEffectComparisonGroup(BaseModel):
    """版本统计表中的一个可比较项目。"""

    name: str = Field(min_length=1, max_length=300)
    model_names: list[str] = Field(min_length=1, max_length=200)
    prompt_fingerprints: list[str] = Field(min_length=1, max_length=200)
    evaluation_versions: list[int] = Field(min_length=1, max_length=20)


class ReplyEffectComparisonRequest(BaseModel):
    """两个模型与 Prompt 聚合项目的显著性对比请求。"""

    left: ReplyEffectComparisonGroup
    right: ReplyEffectComparisonGroup
    session_id: str = ""
    strategy: str = ""
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _load_context_message_rows(message_ids: list[str], session_id: str) -> dict[str, Messages]:
    """读取上下文消息对应的发送者信息，供详情页复用麦麦推理的头像展示。"""

    normalized_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
    if not normalized_ids:
        return {}

    statement = select(Messages).where(col(Messages.message_id).in_(normalized_ids))
    if session_id:
        statement = statement.where(Messages.session_id == session_id)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(statement).all()
    return {str(row.message_id): row for row in rows}


def _build_context_snapshot_for_display(payload: dict[str, Any], session_id: str) -> list[dict[str, Any]]:
    """补齐上下文的纯正文、发送者与头像，同时兼容尚未结构化的旧记录。"""

    context_snapshot = payload.get("context_snapshot")
    if not isinstance(context_snapshot, list):
        return []

    message_ids = [
        str(item.get("message_id") or "").strip()
        for item in context_snapshot
        if isinstance(item, dict)
    ]
    message_rows = _load_context_message_rows(message_ids, session_id)
    display_items: list[dict[str, Any]] = []
    for raw_item in context_snapshot:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        message_id = str(item.get("message_id") or "").strip()
        raw_text = str(item.get("text") or "")
        speaker_name, parsed_text = parse_speaker_content(raw_text)
        item["display_text"] = parsed_text.strip()

        stored_sender = item.get("sender")
        sender = dict(stored_sender) if isinstance(stored_sender, dict) else {}
        message_row = message_rows.get(message_id)
        if message_row is not None:
            sender.setdefault("user_id", str(message_row.user_id or ""))
            sender.setdefault("nickname", str(message_row.user_nickname or ""))
            sender.setdefault("cardname", str(message_row.user_cardname or ""))
            sender.setdefault("platform", str(message_row.platform or ""))

        display_name = str(
            sender.get("display_name")
            or sender.get("cardname")
            or sender.get("nickname")
            or speaker_name
            or ""
        ).strip()
        if display_name:
            sender["display_name"] = display_name

        platform = str(sender.get("platform") or "").strip()
        user_id = str(sender.get("user_id") or "").strip()
        sender["avatar_url"] = build_webui_avatar_url(platform, user_id)
        if any(sender.values()):
            item["sender"] = sender
        display_items.append(item)
    return display_items


def _build_followups_for_display(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """为后续消息补齐与上下文消息一致的头像字段。"""

    raw_followups = payload.get("followup_messages")
    if not isinstance(raw_followups, list):
        return []
    session_payload = payload.get("session")
    platform = str(session_payload.get("platform") or "") if isinstance(session_payload, dict) else ""

    followups: list[dict[str, Any]] = []
    for raw_followup in raw_followups:
        if not isinstance(raw_followup, dict):
            continue
        followup = dict(raw_followup)
        user_id = str(followup.get("user_id") or "").strip()
        followup["avatar_url"] = build_webui_avatar_url(platform, user_id)
        followups.append(followup)
    return followups


def _filtered_rows(
    *,
    session_id: str = "",
    strategy: str = "",
    model_name: str = "",
    prompt_fingerprint: str = "",
    evaluation_version: int = 0,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    min_confidence: float = 0.0,
    finalized_only: bool = False,
    status: str = "",
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> list[MaisakaReplyEffect]:
    statement = select(MaisakaReplyEffect)
    if session_id:
        statement = statement.where(MaisakaReplyEffect.session_id == session_id)
    if strategy:
        statement = statement.where(MaisakaReplyEffect.strategy_primary == strategy)
    if model_name:
        statement = statement.where(MaisakaReplyEffect.model_name == model_name)
    if evaluation_version > 0:
        statement = statement.where(MaisakaReplyEffect.evaluation_version == evaluation_version)
    if start_at:
        statement = statement.where(MaisakaReplyEffect.created_at >= start_at)
    if end_at:
        statement = statement.where(MaisakaReplyEffect.created_at <= end_at)
    if min_confidence > 0:
        statement = statement.where(MaisakaReplyEffect.confidence >= min_confidence)
    if finalized_only:
        statement = statement.where(MaisakaReplyEffect.status == "finalized")
    sort_column = {
        "created_at": col(MaisakaReplyEffect.created_at),
        "response_score": col(MaisakaReplyEffect.response_score),
        "reception_score": col(MaisakaReplyEffect.reception_score),
        "conversation_score": col(MaisakaReplyEffect.conversation_score),
        "confidence": col(MaisakaReplyEffect.confidence),
    }[sort_by]
    order_expression = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    with get_db_session(auto_commit=False) as session:
        rows = list(
            session.exec(
                statement.order_by(order_expression, col(MaisakaReplyEffect.created_at).desc())
            ).all()
        )
    if min_confidence > 0:
        rows = [row for row in rows if _row_observation_complete(row) and _row_has_evidence(row)]
    if finalized_only:
        rows = [row for row in rows if _row_observation_complete(row)]
    if status:
        rows = [row for row in rows if _normalized_row_status(row) == status]
    if prompt_fingerprint:
        rows = [row for row in rows if _resolve_row_fingerprints(row)[1] == prompt_fingerprint]
    return rows


@router.get("/overview")
async def get_reply_effect_overview(
    session_id: str = "",
    strategy: str = "",
    model_name: str = "",
    prompt_fingerprint: str = "",
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    collapse_versions: bool = False,
    collapse_models: bool = False,
) -> dict[str, Any]:
    rows = _filtered_rows(
        session_id=session_id,
        strategy=strategy,
        model_name=model_name,
        prompt_fingerprint=prompt_fingerprint,
        start_at=start_at,
        end_at=end_at,
        min_confidence=min_confidence,
        finalized_only=True,
    )
    filter_rows = _filtered_rows(finalized_only=True)
    strategy_groups: dict[str, list[MaisakaReplyEffect]] = defaultdict(list)
    version_groups: dict[tuple[str, str, int], list[MaisakaReplyEffect]] = defaultdict(list)
    trend_groups: dict[str, list[MaisakaReplyEffect]] = defaultdict(list)
    for row in rows:
        strategy_groups[row.strategy_primary].append(row)
        _, prompt_version_fingerprint = _resolve_row_fingerprints(row)
        version_groups[
            "" if collapse_models else row.model_name or "unknown",
            "" if collapse_versions else prompt_version_fingerprint,
            row.evaluation_version,
        ].append(row)
        trend_groups[row.created_at.date().isoformat()].append(row)
    versions = [
        _aggregate_version_group(
            items,
            model_name=model_group,
            prompt_fingerprint=prompt_group,
            evaluation_version=evaluation_group,
            collapse_models=collapse_models,
            collapse_versions=collapse_versions,
        )
        for (model_group, prompt_group, evaluation_group), items in version_groups.items()
    ]
    versions.sort(key=lambda item: (item["first_seen"], item["name"]))
    return {
        "summary": _aggregate(rows),
        "strategies": [_aggregate(items, name=name) for name, items in sorted(strategy_groups.items())],
        "versions": versions,
        "trend": [_aggregate(items, name=date) for date, items in sorted(trend_groups.items())],
        "filters": {
            "sessions": sorted(
                {row.session_id: row.session_name or row.session_id for row in filter_rows}.items(),
                key=lambda item: item[1],
            ),
            "strategies": sorted({row.strategy_primary for row in filter_rows}),
            "models": sorted({row.model_name for row in filter_rows if row.model_name}),
        },
    }


@router.get("")
async def list_reply_effects(
    session_id: str = "",
    strategy: str = "",
    model_name: str = "",
    prompt_fingerprint: str = "",
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    status: str = Query(default="", pattern="^(|pending|evaluating|finalized|incomplete|evaluation_failed)$"),
    sort_by: str = Query(
        default="created_at",
        pattern="^(created_at|response_score|reception_score|conversation_score|confidence)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    rows = _filtered_rows(
        session_id=session_id,
        strategy=strategy,
        model_name=model_name,
        prompt_fingerprint=prompt_fingerprint,
        start_at=start_at,
        end_at=end_at,
        min_confidence=min_confidence,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    selected = rows[cursor : cursor + limit]
    return {
        "items": [_row_summary(row) for row in selected],
        "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
        "total": len(rows),
    }


@router.post("/compare")
async def compare_reply_effect_projects(request: ReplyEffectComparisonRequest) -> dict[str, Any]:
    """对当前筛选范围内的两个版本项目执行双侧 Welch t 检验。"""

    rows = _filtered_rows(
        session_id=request.session_id,
        strategy=request.strategy,
        start_at=request.start_at,
        end_at=request.end_at,
        min_confidence=request.min_confidence,
        finalized_only=True,
    )
    left_rows = _select_comparison_rows(rows, request.left)
    right_rows = _select_comparison_rows(rows, request.right)
    if not left_rows or not right_rows:
        raise HTTPException(status_code=400, detail="所选项目在当前筛选范围内没有可比较记录")
    if {row.effect_id for row in left_rows} == {row.effect_id for row in right_rows}:
        raise HTTPException(status_code=400, detail="请选择两个不同的项目")

    metrics = []
    for field_name, label in _SIGNIFICANCE_METRICS.items():
        left_values = [float(value) for row in left_rows if (value := getattr(row, field_name)) is not None]
        right_values = [float(value) for row in right_rows if (value := getattr(row, field_name)) is not None]
        metrics.append(
            {
                "field": field_name,
                "label": label,
                **_welch_comparison(left_values, right_values, alpha=_SIGNIFICANCE_ALPHA),
            }
        )

    return {
        "method": "two_sided_welch_t_test",
        "alpha": _SIGNIFICANCE_ALPHA,
        "left": {"name": request.left.name, "record_count": len(left_rows)},
        "right": {"name": request.right.name, "record_count": len(right_rows)},
        "metrics": metrics,
        "significant_count": sum(metric["significant"] for metric in metrics),
    }


@router.get("/prompt-versions/{prompt_fingerprint}")
async def get_prompt_version_detail(
    prompt_fingerprint: str,
    model_name: str = "",
    session_id: str = "",
    evaluation_version: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """返回版本的代表 Prompt，以及与同聊天流最新实发 Prompt 的差异。"""

    version_rows = _filtered_rows(
        model_name=model_name,
        prompt_fingerprint=prompt_fingerprint,
        evaluation_version=evaluation_version,
        finalized_only=True,
    )
    if not version_rows:
        raise HTTPException(status_code=404, detail="Prompt 版本不存在")

    sessions = _build_prompt_version_sessions(version_rows)
    selected_session_id = session_id or version_rows[0].session_id
    selected_rows = [row for row in version_rows if row.session_id == selected_session_id]
    if not selected_rows:
        raise HTTPException(status_code=404, detail="该聊天流未使用此 Prompt 版本")
    representative = selected_rows[0]
    resolved_model_name = model_name or representative.model_name
    system_prompt = _extract_row_system_prompt(representative)

    current_rows = _filtered_rows(
        session_id=selected_session_id,
        model_name=resolved_model_name,
    )
    current_row = next((row for row in current_rows if _extract_row_system_prompt(row)), representative)
    _, current_prompt_fingerprint = _resolve_row_fingerprints(current_row)
    current_system_prompt = _extract_row_system_prompt(current_row)
    diff_lines = list(
        unified_diff(
            system_prompt.splitlines(),
            current_system_prompt.splitlines(),
            fromfile="所选版本",
            tofile="当前版本",
            lineterm="",
        )
    )
    return {
        "prompt_fingerprint": prompt_fingerprint,
        "evaluation_version": representative.evaluation_version,
        "model_name": resolved_model_name,
        "sample_count": len(version_rows),
        "first_seen": min(row.created_at for row in version_rows).isoformat(),
        "last_seen": max(row.created_at for row in version_rows).isoformat(),
        "sessions": sessions,
        "selected_session_id": selected_session_id,
        "system_prompt": system_prompt,
        "current_prompt_fingerprint": current_prompt_fingerprint,
        "current_system_prompt": current_system_prompt,
        "current_created_at": current_row.created_at.isoformat(),
        "is_current": prompt_fingerprint == current_prompt_fingerprint,
        "diff_lines": diff_lines,
    }


@router.get("/export")
async def export_reply_effects() -> Response:
    """导出全部评分记录，供另一套 MaiBot 直接导入。"""

    rows = _filtered_rows()
    package = {
        "format": _EXPORT_FORMAT,
        "format_version": _EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "record_count": len(rows),
        "records": [_load_row_payload(row) for row in rows],
    }
    serialized = json.dumps(
        package,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    compressed = gzip.compress(serialized, compresslevel=9, mtime=0)
    filename = f"maibot-reply-effects-{datetime.now().astimezone():%Y%m%d-%H%M%S}.json.gz"
    return Response(
        content=compressed,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_reply_effects(file: UploadFile = File(...)) -> dict[str, int]:
    """导入完整评分记录；相同记录跳过，冲突记录不覆盖。"""

    uploaded = await file.read(_MAX_IMPORT_FILE_BYTES + 1)
    if len(uploaded) > _MAX_IMPORT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="评分数据文件不能超过 64 MiB")
    try:
        serialized = _decompress_import_file(uploaded)
        package = json.loads(serialized)
    except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError, zlib.error) as exc:
        raise HTTPException(status_code=400, detail="评分数据文件不是有效的 JSON 或 JSON.GZ") from exc
    if not isinstance(package, dict):
        raise HTTPException(status_code=400, detail="评分数据文件根节点必须是对象")
    if package.get("format") != _EXPORT_FORMAT or package.get("format_version") != _EXPORT_FORMAT_VERSION:
        raise HTTPException(status_code=400, detail="评分数据文件格式或版本不受支持")

    raw_records = package.get("records")
    if not isinstance(raw_records, list):
        raise HTTPException(status_code=400, detail="评分数据文件缺少 records 数组")
    expected_count = package.get("record_count")
    if expected_count != len(raw_records):
        raise HTTPException(status_code=400, detail="评分数据文件记录数量校验失败")

    records = []
    incoming_payloads: dict[str, dict[str, Any]] = {}
    try:
        for raw_record in raw_records:
            if not isinstance(raw_record, dict) or int(raw_record.get("schema_version", 0)) != SCHEMA_VERSION:
                raise ValueError(f"仅支持 schema v{SCHEMA_VERSION} 回复效果记录")
            record = reply_effect_record_from_dict(raw_record)
            if record.effect_id in incoming_payloads:
                raise ValueError(f"评分数据文件包含重复 effect_id：{record.effect_id}")
            incoming_payloads[record.effect_id] = raw_record
            records.append(record)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"评分记录结构无效：{exc}") from exc

    imported_records = []
    skipped = 0
    conflicts = 0
    with get_db_session(auto_commit=False) as session:
        for record in records:
            existing = session.get(MaisakaReplyEffect, record.effect_id)
            if existing is None:
                imported_records.append(record)
                continue
            if _load_row_payload(existing) == incoming_payloads[record.effect_id]:
                skipped += 1
            else:
                conflicts += 1

    storage = ReplyEffectStorage()
    storage.restore_record_ids([record.effect_id for record in imported_records])
    for record in sorted(imported_records, key=lambda item: item.created_at):
        storage.create_record_file(record)

    return {
        "total": len(records),
        "imported": len(imported_records),
        "skipped": skipped,
        "conflicts": conflicts,
    }


@router.delete("/clear")
async def clear_reply_effects() -> dict[str, int | bool]:
    """清空全部评分记录、镜像及仍在运行的观察任务。"""

    tracker_count = await clear_active_reply_effect_trackers()
    record_count, mirror_count, space_reclaimed = ReplyEffectStorage().clear_all_records()
    return {
        "deleted_records": record_count,
        "deleted_mirrors": mirror_count,
        "cleared_trackers": tracker_count,
        "space_reclaimed": space_reclaimed,
    }


@router.get("/{effect_id}")
async def get_reply_effect_detail(effect_id: str) -> dict[str, Any]:
    with get_db_session(auto_commit=False) as session:
        row = session.get(MaisakaReplyEffect, effect_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回复效果记录不存在")
    try:
        payload = _load_row_payload(row)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="回复效果详情损坏") from exc
    reply = payload.get("reply")
    reply = reply if isinstance(reply, dict) else {}
    request_fingerprint, prompt_version_fingerprint = _resolve_row_fingerprints(row, payload)
    reply["request_fingerprint"] = request_fingerprint
    reply["prompt_fingerprint"] = prompt_version_fingerprint
    payload["reply"] = reply
    payload["context_snapshot"] = _build_context_snapshot_for_display(payload, row.session_id)
    payload["followup_messages"] = _build_followups_for_display(payload)
    return payload


def _row_summary(row: MaisakaReplyEffect) -> dict[str, Any]:
    payload = _load_row_payload(row)
    reply = payload.get("reply") or {}
    incomplete = payload["status"] == "incomplete"
    request_fingerprint, prompt_version_fingerprint = _resolve_row_fingerprints(row, payload)
    return {
        "effect_id": row.effect_id,
        "session_id": row.session_id,
        "session_name": row.session_name or row.session_id,
        "status": payload["status"],
        "created_at": row.created_at.isoformat(),
        "finalize_reason": str(payload.get("finalize_reason") or ""),
        "strategy_primary": row.strategy_primary,
        "model_name": row.model_name,
        "request_fingerprint": request_fingerprint,
        "prompt_fingerprint": prompt_version_fingerprint,
        "evaluation_version": row.evaluation_version,
        "reply_text": str(reply.get("reply_text") or ""),
        "response_score": None if incomplete else row.response_score,
        "reception_score": None if incomplete else row.reception_score,
        "conversation_score": None if incomplete else row.conversation_score,
        "confidence": row.confidence if not incomplete and _row_has_evidence(row) else None,
        "evaluation_error": str(payload.get("evaluation_error") or ""),
    }


def _select_comparison_rows(
    rows: list[MaisakaReplyEffect],
    group: ReplyEffectComparisonGroup,
) -> list[MaisakaReplyEffect]:
    """按模型集合与 Prompt 指纹集合还原版本统计表中的项目。"""

    model_names = set(group.model_names)
    prompt_fingerprints = set(group.prompt_fingerprints)
    evaluation_versions = set(group.evaluation_versions)
    return [
        row
        for row in rows
        if (row.model_name or "unknown") in model_names
        and _resolve_row_fingerprints(row)[1] in prompt_fingerprints
        and row.evaluation_version in evaluation_versions
    ]


def _welch_comparison(
    left_values: list[float],
    right_values: list[float],
    *,
    alpha: float,
) -> dict[str, Any]:
    """计算双侧 Welch t 检验、均值差置信区间与 Hedges' g。"""

    left_count = len(left_values)
    right_count = len(right_values)
    left_mean = sum(left_values) / left_count if left_values else None
    right_mean = sum(right_values) / right_count if right_values else None
    mean_difference = (
        left_mean - right_mean if left_mean is not None and right_mean is not None else None
    )
    base_result: dict[str, Any] = {
        "left_count": left_count,
        "right_count": right_count,
        "left_mean": left_mean,
        "right_mean": right_mean,
        "mean_difference": mean_difference,
        "confidence_interval": None,
        "p_value": None,
        "significant": False,
        "hedges_g": None,
        "sufficient": False,
        "reason": "",
    }
    if left_count < 2 or right_count < 2:
        base_result["reason"] = "两组都至少需要 2 个有效样本"
        return base_result

    left_variance = variance(left_values)
    right_variance = variance(right_values)
    standard_error_squared = left_variance / left_count + right_variance / right_count
    assert mean_difference is not None
    if standard_error_squared == 0:
        p_value = 1.0 if mean_difference == 0 else 0.0
        confidence_interval = [mean_difference, mean_difference]
    else:
        standard_error = sqrt(standard_error_squared)
        degrees_of_freedom_denominator = (
            (left_variance / left_count) ** 2 / (left_count - 1)
            + (right_variance / right_count) ** 2 / (right_count - 1)
        )
        degrees_of_freedom = standard_error_squared**2 / degrees_of_freedom_denominator
        test_statistic = mean_difference / standard_error
        p_value = float(2 * student_t.sf(abs(test_statistic), degrees_of_freedom))
        critical_value = float(student_t.ppf(1 - alpha / 2, degrees_of_freedom))
        margin = critical_value * standard_error
        confidence_interval = [mean_difference - margin, mean_difference + margin]

    pooled_variance = (
        (left_count - 1) * left_variance + (right_count - 1) * right_variance
    ) / (left_count + right_count - 2)
    if pooled_variance > 0:
        cohens_d = mean_difference / sqrt(pooled_variance)
        correction = 1 - 3 / (4 * (left_count + right_count - 2) - 1)
        hedges_g: Optional[float] = cohens_d * correction
    elif mean_difference == 0:
        hedges_g = 0.0
    else:
        hedges_g = None

    if not isfinite(p_value):
        base_result["reason"] = "样本方差无法支持稳定检验"
        return base_result
    base_result.update(
        {
            "confidence_interval": confidence_interval,
            "p_value": p_value,
            "significant": p_value < alpha,
            "hedges_g": hedges_g,
            "sufficient": True,
        }
    )
    return base_result


def _aggregate_version_group(
    rows: list[MaisakaReplyEffect],
    *,
    model_name: str,
    prompt_fingerprint: str,
    evaluation_version: int,
    collapse_models: bool,
    collapse_versions: bool,
) -> dict[str, Any]:
    """构建支持独立折叠模型和 Prompt 版本的聚合行。"""

    aggregate = _aggregate(rows)
    model_names = sorted({row.model_name or "unknown" for row in rows})
    prompt_fingerprints = sorted({_resolve_row_fingerprints(row)[1] for row in rows})
    if collapse_models and collapse_versions:
        name = f"全部模型 · 全部版本 · 评估标准 v{evaluation_version}"
    elif collapse_versions:
        name = f"{model_name} · 全部版本 · 评估标准 v{evaluation_version}"
    elif collapse_models:
        name = (
            f"全部模型 · {prompt_fingerprint[:8] or '无版本指纹'} · "
            f"评估标准 v{evaluation_version}"
        )
    else:
        name = (
            f"{model_name} · {prompt_fingerprint[:8] or '无版本指纹'} · "
            f"评估标准 v{evaluation_version}"
        )
    aggregate.update(
        {
            "name": name,
            "model_name": "" if collapse_models else model_name,
            "prompt_fingerprint": "" if collapse_versions else prompt_fingerprint,
            "evaluation_version": evaluation_version,
            "model_names": model_names,
            "prompt_fingerprints": prompt_fingerprints,
            "evaluation_versions": [evaluation_version],
            "first_seen": min(row.created_at for row in rows).isoformat(),
            "last_seen": max(row.created_at for row in rows).isoformat(),
            "collapsed_models": collapse_models,
            "collapsed_versions": collapse_versions,
            "score_distributions": {
                field_name: _score_distribution(rows, field_name)
                for field_name in (
                    "response_score",
                    "reception_score",
                    "conversation_score",
                )
            },
        }
    )
    return aggregate


def _score_distribution(
    rows: list[MaisakaReplyEffect],
    field_name: str,
) -> dict[str, Any]:
    """按 5 分区间统计真实评分分布，并转换为组内样本占比。"""

    bucket_width = 5
    bucket_count = 100 // bucket_width
    counts = [0] * bucket_count
    values = [float(value) for row in rows if (value := getattr(row, field_name)) is not None]
    for value in values:
        if not 0 <= value <= 100:
            raise ValueError(f"回复效果分数超出 0～100：field={field_name} value={value}")
        bucket_index = min(int(value // bucket_width), bucket_count - 1)
        counts[bucket_index] += 1

    sample_count = len(values)
    return {
        "sample_count": sample_count,
        "buckets": [
            {
                "score": bucket_index * bucket_width,
                "range": (
                    f"{bucket_index * bucket_width}～"
                    f"{(bucket_index + 1) * bucket_width}"
                ),
                "count": count,
                "percentage": round(count * 100 / sample_count, 2) if sample_count else 0.0,
            }
            for bucket_index, count in enumerate(counts)
        ],
    }


def _build_prompt_version_sessions(rows: list[MaisakaReplyEffect]) -> list[dict[str, Any]]:
    grouped: dict[str, list[MaisakaReplyEffect]] = defaultdict(list)
    for row in rows:
        grouped[row.session_id].append(row)
    return [
        {
            "session_id": session_id,
            "session_name": items[0].session_name or session_id,
            "sample_count": len(items),
            "last_seen": max(item.created_at for item in items).isoformat(),
        }
        for session_id, items in sorted(grouped.items(), key=lambda item: item[1][0].session_name or item[0])
    ]


def _extract_row_system_prompt(row: MaisakaReplyEffect) -> str:
    payload = _load_row_payload(row)
    reply = payload.get("reply")
    reply = reply if isinstance(reply, dict) else {}
    metadata = reply.get("reply_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return extract_system_prompt_from_metadata(metadata)


def _resolve_row_fingerprints(
    row: MaisakaReplyEffect,
    payload: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """兼容迁移后仍由旧进程写入的记录，并返回两类指纹。"""

    record_payload = payload if payload is not None else _load_row_payload(row)
    reply = record_payload.get("reply")
    reply = reply if isinstance(reply, dict) else {}
    metadata = reply.get("reply_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    _, calculated_request_fingerprint, calculated_prompt_fingerprint = extract_generation_fingerprints(metadata)
    payload_request_fingerprint = str(reply.get("request_fingerprint") or "")
    row_request_fingerprint = str(row.request_fingerprint or "")
    if payload_request_fingerprint or row_request_fingerprint:
        request_fingerprint = payload_request_fingerprint or row_request_fingerprint
        prompt_fingerprint = calculated_prompt_fingerprint or str(
            reply.get("prompt_fingerprint") or row.prompt_fingerprint or ""
        )
        return request_fingerprint, prompt_fingerprint
    request_fingerprint = str(row.prompt_fingerprint or "") or calculated_request_fingerprint
    return request_fingerprint, calculated_prompt_fingerprint


def _load_row_payload(row: MaisakaReplyEffect) -> dict[str, Any]:
    """透明读取明文或无损压缩的完整评估详情。"""

    payload = decode_record_payload(row.record_json, row.record_blob)
    payload.pop("judge_version", None)
    payload.pop("scorer_version", None)
    payload["evaluation_version"] = row.evaluation_version
    payload["status"] = _normalized_row_status(row, payload)
    scores = payload.get("scores")
    followup_summary = payload.get("followup_summary")
    if (
        isinstance(scores, dict)
        and isinstance(followup_summary, dict)
        and int(followup_summary.get("associated_count", 0)) == 0
    ):
        scores["confidence"] = None
    if isinstance(scores, dict):
        scores.pop("raw_score", None)
    if payload["status"] == "incomplete":
        payload["scores"] = None
        payload["confidence_note"] = "观察窗口不完整，未进行评分。"
    return payload


def _decompress_import_file(uploaded: bytes) -> str:
    """读取 gzip 或明文 JSON，并限制解压后的最大体积。"""

    if uploaded.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=BytesIO(uploaded), mode="rb") as compressed_file:
            payload = compressed_file.read(_MAX_IMPORT_JSON_BYTES + 1)
    else:
        payload = uploaded
    if len(payload) > _MAX_IMPORT_JSON_BYTES:
        raise HTTPException(status_code=413, detail="评分数据解压后不能超过 256 MiB")
    return payload.decode("utf-8")


def _aggregate(rows: list[MaisakaReplyEffect], *, name: str = "") -> dict[str, Any]:
    def summarize(field_name: str) -> tuple[Optional[float], Optional[float]]:
        values = [
            float(value)
            for row in rows
            if (field_name != "confidence" or _row_has_evidence(row))
            if (value := getattr(row, field_name)) is not None
        ]
        if not values:
            return None, None
        return round(sum(values) / len(values), 2), round(pstdev(values), 2)

    score_fields = (
        "response_score",
        "reception_score",
        "conversation_score",
        "confidence",
    )
    score_summaries = {field_name: summarize(field_name) for field_name in score_fields}

    aggregate: dict[str, Any] = {
        "name": name,
        "count": len(rows),
    }
    for field_name, (average, standard_deviation) in score_summaries.items():
        aggregate[field_name] = average
        aggregate[f"{field_name}_std"] = standard_deviation
        aggregate[f"{field_name}_count"] = sum(
            getattr(row, field_name) is not None
            and (field_name != "confidence" or _row_has_evidence(row))
            for row in rows
        )
    return aggregate


def _row_has_evidence(row: MaisakaReplyEffect) -> bool:
    """判断记录是否包含与当前 Bot 回复相关的有效信息。"""

    return any(
        value not in {None, 0.0}
        for value in (row.response_score, row.reception_score, row.conversation_score)
    )


def _row_observation_complete(row: MaisakaReplyEffect) -> bool:
    """判断记录是否完整走完观察窗口。"""

    if row.status != "finalized":
        return False
    payload = decode_record_payload(row.record_json, row.record_blob)
    return str(payload.get("finalize_reason") or "") in COMPLETE_OBSERVATION_REASONS


def _normalized_row_status(
    row: MaisakaReplyEffect,
    payload: Optional[dict[str, Any]] = None,
) -> str:
    """把旧记录中误标为已完成的不完整观察归一化。"""

    if row.status != "finalized":
        return row.status
    resolved_payload = payload or decode_record_payload(row.record_json, row.record_blob)
    finalize_reason = str(resolved_payload.get("finalize_reason") or "")
    return "finalized" if finalize_reason in COMPLETE_OBSERVATION_REASONS else "incomplete"

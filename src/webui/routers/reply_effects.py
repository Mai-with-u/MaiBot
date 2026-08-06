"""MaiSaka 回复效果只读分析接口。"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import MaisakaReplyEffect
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/reply-effects", tags=["reply-effects"], dependencies=[Depends(require_auth)])


def _filtered_rows(
    *,
    session_id: str = "",
    strategy: str = "",
    model_name: str = "",
    prompt_fingerprint: str = "",
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    min_confidence: float = 0.0,
    finalized_only: bool = False,
) -> list[MaisakaReplyEffect]:
    statement = select(MaisakaReplyEffect)
    if session_id:
        statement = statement.where(MaisakaReplyEffect.session_id == session_id)
    if strategy:
        statement = statement.where(MaisakaReplyEffect.strategy_primary == strategy)
    if model_name:
        statement = statement.where(MaisakaReplyEffect.model_name == model_name)
    if prompt_fingerprint:
        statement = statement.where(MaisakaReplyEffect.prompt_fingerprint == prompt_fingerprint)
    if start_at:
        statement = statement.where(MaisakaReplyEffect.created_at >= start_at)
    if end_at:
        statement = statement.where(MaisakaReplyEffect.created_at <= end_at)
    if min_confidence > 0:
        statement = statement.where(MaisakaReplyEffect.confidence >= min_confidence)
    if finalized_only:
        statement = statement.where(
            MaisakaReplyEffect.status == "finalized",
            MaisakaReplyEffect.scorer_version == 2,
        )
    with get_db_session(auto_commit=False) as session:
        return list(session.exec(statement.order_by(col(MaisakaReplyEffect.created_at).desc())).all())


@router.get("/overview")
async def get_reply_effect_overview(
    session_id: str = "",
    strategy: str = "",
    model_name: str = "",
    prompt_fingerprint: str = "",
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    min_confidence: float = Query(default=0.6, ge=0.0, le=1.0),
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
    version_groups: dict[str, list[MaisakaReplyEffect]] = defaultdict(list)
    trend_groups: dict[str, list[MaisakaReplyEffect]] = defaultdict(list)
    for row in rows:
        strategy_groups[row.strategy_primary].append(row)
        version_groups[f"{row.model_name or 'unknown'} · {row.prompt_fingerprint[:8] or '无指纹'}"].append(row)
        trend_groups[row.created_at.date().isoformat()].append(row)
    return {
        "summary": _aggregate(rows),
        "strategies": [_aggregate(items, name=name) for name, items in sorted(strategy_groups.items())],
        "versions": [_aggregate(items, name=name) for name, items in sorted(version_groups.items())],
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
    )
    selected = rows[cursor : cursor + limit]
    return {
        "items": [_row_summary(row) for row in selected],
        "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
        "total": len(rows),
    }


@router.get("/{effect_id}")
async def get_reply_effect_detail(effect_id: str) -> dict[str, Any]:
    with get_db_session(auto_commit=False) as session:
        row = session.get(MaisakaReplyEffect, effect_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回复效果记录不存在")
    try:
        return json.loads(row.record_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="回复效果详情损坏") from exc


def _row_summary(row: MaisakaReplyEffect) -> dict[str, Any]:
    payload = json.loads(row.record_json)
    reply = payload.get("reply") or {}
    return {
        "effect_id": row.effect_id,
        "session_id": row.session_id,
        "session_name": row.session_name or row.session_id,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "strategy_primary": row.strategy_primary,
        "model_name": row.model_name,
        "prompt_fingerprint": row.prompt_fingerprint,
        "reply_text": str(reply.get("reply_text") or ""),
        "response_score": row.response_score,
        "reception_score": row.reception_score,
        "conversation_score": row.conversation_score,
        "raw_score": row.raw_score,
        "relative_score": row.relative_score,
        "confidence": row.confidence,
        "evaluation_error": str(payload.get("evaluation_error") or ""),
    }


def _aggregate(rows: list[MaisakaReplyEffect], *, name: str = "") -> dict[str, Any]:
    def average(field_name: str) -> Optional[float]:
        values = [float(value) for row in rows if (value := getattr(row, field_name)) is not None]
        return round(sum(values) / len(values), 2) if values else None

    return {
        "name": name,
        "count": len(rows),
        "response_score": average("response_score"),
        "reception_score": average("reception_score"),
        "conversation_score": average("conversation_score"),
        "raw_score": average("raw_score"),
        "relative_score": average("relative_score"),
        "confidence": average("confidence"),
    }

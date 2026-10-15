"""将旧自动提取的人物事实按当前准入规则恢复为稳定事实。"""

from typing import Any, Dict, List, Tuple

import asyncio
import json
import sqlite3

from src.A_memorix.host_service import a_memorix_host_service
from src.common.logger import get_logger
from src.services.memory_service import memory_service


logger = get_logger("person_fact_reverification")


def _historical_fact_batch(cursor: str, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    db_path = a_memorix_host_service.get_runtime_data_dir() / "metadata" / "metadata.db"
    if not db_path.exists():
        return [], False
    connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.claim_id, c.scope_id AS person_id, c.value_text,
                   p.metadata AS paragraph_metadata
            FROM fact_claims c
            JOIN fact_evidence e ON e.claim_id = c.claim_id
              AND e.evidence_type = 'paragraph' AND e.stance = 'support'
            JOIN paragraphs p ON p.hash = e.evidence_id
            WHERE c.scope_type = 'person' AND c.status = 'active'
              AND c.authority = 'summary_derived' AND c.stability = 'uncertain'
              AND c.profile_section = 'uncertain_notes' AND c.claim_id > ?
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
              AND e.evidence_id = (
                  SELECT MIN(e2.evidence_id) FROM fact_evidence e2
                  JOIN paragraphs p2 ON p2.hash = e2.evidence_id
                  WHERE e2.claim_id = c.claim_id AND e2.evidence_type = 'paragraph'
                    AND e2.stance = 'support'
                    AND (p2.is_deleted IS NULL OR p2.is_deleted = 0)
                    AND json_valid(p2.metadata)
                    AND json_extract(p2.metadata, '$.evidence_source') = 'user_supported'
              )
            ORDER BY c.claim_id ASC LIMIT ?
            """,
            (cursor, max(1, limit) + 1),
        ).fetchall()
        return [dict(row) for row in rows[:limit]], len(rows) > limit
    finally:
        connection.close()


async def reclassify_historical_person_facts(cursor: str = "", limit: int = 50) -> Dict[str, Any]:
    """处理一批旧事实；调用方持久化 next_cursor 后可安全续跑。"""

    rows, has_more = await asyncio.to_thread(_historical_fact_batch, cursor, limit)
    promoted = 0
    for row in rows:
        try:
            metadata = json.loads(str(row["paragraph_metadata"] or "{}"))
        except (TypeError, ValueError):
            logger.warning(f"历史人物事实证据元数据无效: claim_id={row['claim_id']}")
            continue
        if not isinstance(metadata, dict):
            continue
        if metadata.get("evidence_source") != "user_supported":
            continue
        current = await memory_service.fact_admin(action="get", claim_id=str(row["claim_id"]))
        claim = current.get("claim") if isinstance(current, dict) else None
        if not isinstance(claim, dict) or claim.get("authority") != "summary_derived":
            continue
        result = await memory_service.fact_admin(
            action="update",
            claim_id=str(row["claim_id"]),
            authority="direct_user",
            stability="stable",
            profile_section="stable_facts",
            confidence=1.0,
            reason="historical_person_fact_reclassified",
        )
        if not result.get("success"):
            raise RuntimeError(f"历史人物事实晋升失败: {row['claim_id']}: {result.get('error')}")
        promoted += 1
    return {
        "processed": len(rows),
        "promoted": promoted,
        "next_cursor": str(rows[-1]["claim_id"]) if rows else cursor,
        "has_more": has_more,
    }

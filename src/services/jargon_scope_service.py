"""按释义归属写入学习记录；成员变化不会改动来源和历史关联。"""

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import Select, and_, case, false, func, or_
from sqlmodel import Session, col, select
import json

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    Jargon,
    JargonDeferredLearning,
    JargonEvidenceLink,
    JargonLearningRecord,
)
from src.common.database.jargon_snapshots import inherited_source, materialize, read_jargons
from src.common.database.jargon_scope_transfers import stored_jargon_members, term_key

from .sharing_group_service import stable_jargon_membership


def scope_row(session: Session, content: str, kind: str, target: str) -> Optional[Jargon]:
    model = read_jargons(chats=[target]) if kind == "chat" else Jargon
    return session.exec(
        select(model).where(
            col(model.archived) == false(),
            model.scope_kind == kind,
            model.scope_id == target,
            func.lower(func.trim(col(model.content))) == term_key(content),
        )
    ).first()


def effective_scope_ids(chat: str, groups: List[str]) -> Select:
    """在聊天所属的学习作用域和单独的全局词库中选择生效版本。"""
    version = read_jargons(chats=[chat]) if not groups else Jargon
    owner = (and_(version.scope_kind == "group", col(version.scope_id).in_(groups)) if groups
             else and_(version.scope_kind == "chat", version.scope_id == chat))
    eligible = or_(owner, version.scope_kind == "global")
    decided = or_(
        col(version.is_jargon).is_(False), and_(col(version.is_jargon).is_(True), func.trim(version.meaning) != "")
    )
    priority = case((version.scope_kind == "global", 0), else_=1)
    ranked = (
        select(
            version.id,
            func.row_number()
            .over(
                partition_by=func.lower(func.trim(version.content)),
                order_by=[case((decided, 0), else_=1), priority, col(version.count).desc(), col(version.id).desc()],
            )
            .label("position"),
        )
        .where(col(version.archived) == false(), eligible)
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.position == 1)


def _learning_owner(session: Session, content: str, chat: str, groups: List[str]) -> Tuple[Jargon, bool]:
    kind, target = ("group", groups[0]) if groups else ("chat", chat)
    existing = scope_row(session, content, kind, target)
    if existing:
        materialize(session.connection(), [existing.id])
        return existing, False
    return Jargon(content=content, meaning="", scope_kind=kind, scope_id=target), True


def _attach_records(session: Session, row: Jargon, chat: Optional[str], ids: List[int], observed: datetime) -> None:
    row.count += len(ids)
    if chat is not None:
        if row.scope_kind == "group":
            owners = set(json.loads(row.contributing_chats or "[]"))
            owners.add(chat)
            row.contributing_chats = json.dumps(sorted(owners))
        counts = json.loads(row.session_id_dict)
        counts[chat] = counts.get(chat, 0) + len(ids)
        row.session_id_dict = json.dumps(counts, ensure_ascii=False)
    row.updated_timestamp = max(row.updated_timestamp, observed) if row.updated_timestamp else observed
    session.add(row)
    session.flush()
    session.add_all([JargonEvidenceLink(jargon_id=row.id, record_id=record_id) for record_id in ids])
    session.flush()


def attach_common_learning(session: Session, event: JargonLearningRecord) -> Optional[Jargon]:
    """为同名通用词条关联本次已入库的学习记录，保留原始来源。"""
    common = scope_row(session, event.content, "global", "")
    if common:
        assert event.id is not None and event.recorded_at is not None
        _attach_records(session, common, event.session_id, [event.id], event.recorded_at)
    return common


def collect_learning(
    content: str, chat: str, evidence: List[List[Dict[str, str]]], excerpts: Set[str]
) -> Tuple[Optional[Jargon], bool, Optional[Jargon]]:
    with stable_jargon_membership(), get_db_session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        groups = [group for group, _ in stored_jargon_members(session.connection(), chat)]
        row, created = None, False
        if len(groups) <= 1:
            # 外部事务也可变更成员；在本轮推断前先接上尚未回放的线索。
            assign_deferred_learning(session, {chat})
            row, created = _learning_owner(session, content, chat, groups)
        observed = datetime.now()
        event = JargonLearningRecord(
            content=content,
            session_id=chat,
            scope_kind=row.scope_kind if row else "chat",
            scope_id=row.scope_id if row else chat,
            evidence=json.dumps(evidence, ensure_ascii=False),
            excerpt="\n".join(sorted(excerpts)),
            recorded_at=observed,
        )
        session.add(event)
        session.flush()
        if row is None:
            session.add(JargonDeferredLearning(record_id=event.id, source_session_id=chat))
        else:
            _attach_records(session, row, chat, [event.id], observed)
        return row, created, attach_common_learning(session, event)


def assign_deferred_learning(session: Session, chats: Set[str]) -> None:
    """调用方已持有写事务。只分配从未关联释义的队列记录，便于重试与重启。"""
    if not chats:
        return
    # 队列通常为空。从队列主键查找事件，避免每次学习扫描整张历史线索表。
    records = session.connection().exec_driver_sql(
        """SELECT e.session_id, e.content, e.id, e.recorded_at
        FROM jargon_deferred_learning q CROSS JOIN jargon_learning_records e ON e.id=q.record_id
        WHERE q.needs_dispatch=1 AND q.source_session_id IN (SELECT value FROM json_each(?))
        AND NOT EXISTS (SELECT 1 FROM jargon_evidence_links l JOIN jargons j ON j.id=l.jargon_id
            WHERE l.record_id=q.record_id AND j.scope_kind!='global')
        ORDER BY e.id""",
        (json.dumps(sorted(chats)),),
    )
    pending: Dict[str, Dict[str, List[Tuple[int, datetime, str]]]] = {}
    for chat, content, record_id, timestamp in records:
        pending.setdefault(chat, {}).setdefault(term_key(content), []).append(
            (record_id, datetime.fromisoformat(timestamp), content)
        )
    for chat, terms in pending.items():
        groups = [group for group, _ in stored_jargon_members(session.connection(), chat)]
        if len(groups) > 1:
            continue
        for records in terms.values():
            row, _ = _learning_owner(session, records[0][2], chat, groups)
            row.revision += 1
            _attach_records(session, row, chat, [record[0] for record in records], max(record[1] for record in records))


def should_infer_meaning(row: Jargon) -> bool:
    if not row.allow_update:
        return False
    if row.scope_kind == "global" and row.last_inference_count >= 100:
        return row.count - row.last_inference_count >= 100
    if row.is_complete and row.scope_kind != "global":
        return False
    return any(row.last_inference_count < threshold <= row.count for threshold in (4, 8, 25, 100))


def deferred_chats() -> List[str]:
    with get_db_session(auto_commit=False) as session:
        return list(
            session.exec(
                select(JargonDeferredLearning.source_session_id)
                .where(col(JargonDeferredLearning.needs_dispatch).is_(True))
                .distinct()
            ).all()
        )


def prepare_deferred_inference(chat: str) -> List[int]:
    """找到需后台推断的版本。低于门槛的线索留给正常学习的下一轮推断。"""
    with stable_jargon_membership(), get_db_session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        groups = [group for group, _ in stored_jargon_members(session.connection(), chat)]
        if len(groups) > 1:
            return []
        assign_deferred_learning(session, {chat})
        stmt = (
            select(JargonEvidenceLink.record_id, Jargon)
            .join(Jargon, Jargon.id == JargonEvidenceLink.jargon_id)
            .join(JargonDeferredLearning, JargonDeferredLearning.record_id == JargonEvidenceLink.record_id)
            .where(
                JargonDeferredLearning.source_session_id == chat, col(JargonDeferredLearning.needs_dispatch).is_(True)
            )
        )
        jobs, ready, inspected = set(), set(), set()
        for record_id, row in session.exec(stmt):
            inspected.add(record_id)
            if (
                not row.archived
                and should_infer_meaning(row)
                and (
                    (row.scope_kind == "chat" and row.scope_id == chat)
                    or (row.scope_kind == "group" and row.scope_id in groups)
                )
            ):
                jobs.add(row.id)
                ready.add(record_id)
        if waiting := inspected - ready:
            session.connection().exec_driver_sql(
                """UPDATE jargon_deferred_learning SET needs_dispatch=0
                WHERE record_id IN (SELECT value FROM json_each(?))""",
                (json.dumps(sorted(waiting)),),
            )
        return sorted(jobs)


def finish_deferred_inference(session: Session, jargon_id: int) -> None:
    """与推断进度一起提交。冲突期间的原始学习记录及其关联始终保留。"""
    session.connection().exec_driver_sql(
        """DELETE FROM jargon_deferred_learning WHERE record_id IN
        (SELECT record_id FROM jargon_evidence_links WHERE jargon_id=?)""",
        (jargon_id,),
    )


def learning_material(jargon_id: int) -> Tuple[List[str], List[List[Dict[str, str]]]]:
    """读取新素材与代表性的历史素材，完整事件仍留在数据库。"""
    with get_db_session(auto_commit=False) as session:
        session.connection().exec_driver_sql("BEGIN")
        version = session.get(Jargon, jargon_id)
        recent_limit = 24
        if version and version.scope_kind == "global":
            recent_limit = max(recent_limit, version.count - version.last_inference_count)
        inherited = inherited_source(session.connection(), jargon_id)
        stmt = (
            select(JargonLearningRecord)
            .join(JargonEvidenceLink, JargonEvidenceLink.record_id == JargonLearningRecord.id)
            .where(JargonEvidenceLink.jargon_id == (inherited[0] if inherited else jargon_id))
        )
        recent = session.exec(stmt.order_by(col(JargonLearningRecord.id).desc()).limit(recent_limit)).all()
        first = session.exec(stmt.order_by(col(JargonLearningRecord.id)).limit(8)).all()
        deferred = session.exec(
            stmt.join(JargonDeferredLearning, JargonDeferredLearning.record_id == JargonLearningRecord.id)
            .order_by(col(JargonLearningRecord.id).desc())
            .limit(8)
        ).all()
        events = {event.id: event for event in [*first, *reversed(deferred), *reversed(recent)]}
        excerpts, refs = [], []
        for event in events.values():
            if event.excerpt:
                excerpts.append(event.excerpt)
            elif event.evidence:
                refs.extend(json.loads(event.evidence))
        return excerpts, refs


def copy_version(session: Session, source: Jargon, kind: str, target: str) -> Jargon:
    data = source.model_dump(exclude={"id", "contributing_chats"})
    data.update(
        evidence_messages=None,
        scope_kind=kind,
        scope_id=target,
        is_global=kind == "global",
        archived=False,
        revision=0,
        baseline_id=source.baseline_id or source.id,
    )
    row = Jargon(**data)
    session.add(row)
    session.flush()
    inherit_evidence(session, row, [source])
    return row


def inherit_evidence(session: Session, target: Jargon, sources: List[Jargon]) -> None:
    if target.scope_kind == "group":
        owners = set(json.loads(target.contributing_chats or "[]"))
        for source in sources:
            if source.scope_kind == "chat":
                owners.add(source.scope_id)
            elif source.scope_kind == "group" and source.scope_id == target.scope_id:
                owners.update(json.loads(source.contributing_chats or "[]"))
        target.contributing_chats = json.dumps(sorted(owners))
    session.flush()
    session.connection().exec_driver_sql(
        """INSERT OR IGNORE INTO jargon_evidence_links (jargon_id, record_id)
        SELECT ?, record_id FROM jargon_evidence_links WHERE jargon_id IN (SELECT value FROM json_each(?))""",
        (target.id, json.dumps([row.id for row in sources])),
    )

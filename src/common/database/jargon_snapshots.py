"""退组共享快照。负 ID 唯一标识一个聊天继承的词；首次写入沿用该 ID。"""

from typing import List, Optional, Sequence, Tuple, Type
from uuid import uuid4

from sqlalchemy import exists, literal, select, union_all
from sqlalchemy.engine import Connection
from sqlalchemy.orm import aliased
import json

from .database_model import Jargon, JargonSnapshotMember


_FIELDS = ("content", "meaning", "session_id_dict", "count", "is_jargon", "is_complete",
           "last_inference_count", "created_timestamp", "updated_timestamp", "created_by", "allow_update")
_COLUMNS = ", ".join(_FIELDS)
_SOURCE = ", ".join(f"s.{name}" for name in _FIELDS)


def read_jargons(*, representative: bool = False, chats: Optional[Sequence[str]] = None) -> Type[Jargon]:
    """组合实存版本和继承版本。词库分页时每个快照词只取一个代表，当前页再展开。"""
    physical = Jargon.__table__
    member = JargonSnapshotMember.__table__
    source = physical.alias("frozen")
    virtual_id = -(member.c.shift + source.c.id)
    available = ~exists(select(1).select_from(physical).where(physical.c.id == virtual_id).correlate(member, source))
    member_filter = member.c.chat.is_not(None) if chats is None else member.c.chat.in_(chats)
    if representative:
        first = select(member.c.end_id).where(
            member.c.snapshot == source.c.scope_id, member_filter, available
        ).order_by(member.c.end_id).limit(1).correlate(source).scalar_subquery()
        member_filter = member.c.end_id == first
    overrides = {"id": virtual_id, "scope_kind": literal("chat"), "scope_id": member.c.chat,
                 "archived": literal(False), "revision": literal(0)}
    inherited = select(*(overrides.get(c.name, c).label(c.name) for c in source.c)).select_from(
        source.join(member, member.c.snapshot == source.c.scope_id)
    ).where(source.c.scope_kind == "snapshot", member_filter, available)
    both = union_all(select(physical).where(physical.c.scope_kind != "snapshot"), inherited).subquery()
    return aliased(Jargon, both, adapt_on_names=True)


def detach_snapshots(db: Connection, chats: List[str]) -> None:
    """释放最后一位成员退出的冻结副本，保留 ID 区间和所有原始学习记录。"""
    ids = json.dumps(chats)
    touched = db.exec_driver_sql("""SELECT DISTINCT snapshot FROM jargon_snapshot_members
        WHERE chat IN (SELECT value FROM json_each(?))""", (ids,)).scalars().all()
    if not touched:
        return
    db.exec_driver_sql("UPDATE jargon_snapshot_members SET chat=NULL WHERE chat IN (SELECT value FROM json_each(?))", (ids,))
    for snapshot in touched:
        if db.exec_driver_sql("SELECT 1 FROM jargon_snapshot_members WHERE snapshot=? AND chat IS NOT NULL LIMIT 1",
                              (snapshot,)).first():
            continue
        db.exec_driver_sql("""DELETE FROM jargon_evidence_links WHERE jargon_id IN (
            SELECT id FROM jargons WHERE scope_kind='snapshot' AND scope_id=?)""", (snapshot,))
        db.exec_driver_sql("DELETE FROM jargons WHERE scope_kind='snapshot' AND scope_id=?", (snapshot,))


def freeze_group(db: Connection, group: str, chats: List[str]) -> None:
    snapshot = uuid4().hex
    db.exec_driver_sql(f"""INSERT INTO jargons ({_COLUMNS}, is_global, scope_kind, scope_id, archived, revision, baseline_id, contributing_chats)
        SELECT {_SOURCE}, 0, 'snapshot', ?, 1, 0, coalesce(s.baseline_id,s.id), s.contributing_chats
        FROM jargons s WHERE s.archived=0 AND s.scope_kind='group' AND s.scope_id=?""", (snapshot, group))
    first, last = db.exec_driver_sql("SELECT min(id), max(id) FROM jargons WHERE scope_kind='snapshot' AND scope_id=?",
                                     (snapshot,)).one()
    if first is None:
        return
    db.exec_driver_sql("""INSERT INTO jargon_evidence_links
        SELECT f.id, e.record_id FROM jargons s JOIN jargon_evidence_links e ON e.jargon_id=s.id
        JOIN jargons f ON f.scope_kind='snapshot' AND f.scope_id=? AND lower(trim(f.content))=lower(trim(s.content))
        WHERE s.scope_kind='group' AND s.scope_id=? AND s.archived=0""", (snapshot, group))
    end = db.exec_driver_sql("SELECT coalesce(max(end_id),0) FROM jargon_snapshot_members").scalar_one()
    width = last - first + 1
    db.exec_driver_sql("INSERT INTO jargon_snapshot_members(end_id,shift,snapshot,chat) VALUES(?,?,?,?)", [
        (end + n * width, end + n * width - last, snapshot, chat) for n, chat in enumerate(chats, 1)
    ])
    # 冲突期遗留的本群版本和快照同名时，以退组释义为准，并继承本群素材。
    collisions = db.exec_driver_sql("""SELECT c.id,-(m.shift+f.id) FROM jargon_snapshot_members m
        JOIN jargons c ON c.scope_kind='chat' AND c.scope_id=m.chat AND c.archived=0
        JOIN jargons f ON f.scope_kind='snapshot' AND f.scope_id=m.snapshot AND lower(trim(f.content))=lower(trim(c.content))
        WHERE m.snapshot=?""", (snapshot,)).all()
    for old, inherited in collisions:
        db.exec_driver_sql("UPDATE jargons SET archived=1,revision=revision+1 WHERE id=?", (old,))
        materialize(db, [inherited])
        db.exec_driver_sql("""INSERT OR IGNORE INTO jargon_evidence_links
            SELECT ?,record_id FROM jargon_evidence_links WHERE jargon_id=?""", (inherited, old))


def inherit_contributed_words(db: Connection, group: str, chats: List[str]) -> None:
    """全局范围收缩时，只为有归属的聊天和词创建独立释义，并继承该释义的完整线索。"""
    if not chats:
        return
    db.exec_driver_sql("""CREATE TEMP TABLE jargon_departures AS
        SELECT g.id source,owner.value chat,lower(trim(g.content)) term
        FROM jargons g JOIN json_each(g.contributing_chats) owner
        WHERE g.archived=0 AND g.scope_kind='group' AND g.scope_id=?
        AND owner.value IN (SELECT value FROM json_each(?))""", (group, json.dumps(chats)))
    # 解决旧冲突时仍可能有独立版本，保留该版本并合入共享素材。
    db.exec_driver_sql("""UPDATE jargons AS c SET revision=revision+1 FROM jargon_departures d
        WHERE c.archived=0 AND c.scope_kind='chat' AND c.scope_id=d.chat AND lower(trim(c.content))=d.term""")
    db.exec_driver_sql(f"""INSERT INTO jargons ({_COLUMNS},is_global,scope_kind,scope_id,archived,revision,baseline_id)
        SELECT {_SOURCE},0,'chat',d.chat,0,0,coalesce(s.baseline_id,s.id)
        FROM jargon_departures d JOIN jargons s ON s.id=d.source
        WHERE NOT EXISTS(SELECT 1 FROM jargons c WHERE c.archived=0 AND c.scope_kind='chat'
          AND c.scope_id=d.chat AND lower(trim(c.content))=d.term)""")
    db.exec_driver_sql("""INSERT OR IGNORE INTO jargon_evidence_links(jargon_id,record_id)
        SELECT c.id,l.record_id FROM jargon_departures d JOIN jargon_evidence_links l ON l.jargon_id=d.source
        JOIN jargons c ON c.archived=0 AND c.scope_kind='chat' AND c.scope_id=d.chat AND lower(trim(c.content))=d.term""")
    db.exec_driver_sql("DROP TABLE jargon_departures")


def materialize(db: Connection, ids: Sequence[int]) -> None:
    """调用者持有写事务；已分离或已失效的 ID 不会再次生成。"""
    negatives = sorted({i for i in ids if i < 0})
    if not negatives:
        return
    rows = db.exec_driver_sql("""WITH requested(id) AS (SELECT value FROM json_each(?))
        SELECT r.id, s.id, m.chat FROM requested r
        JOIN jargon_snapshot_members m ON m.end_id=(SELECT min(end_id) FROM jargon_snapshot_members WHERE end_id>=-r.id)
        JOIN jargons s ON s.id=-r.id-m.shift AND s.scope_id=m.snapshot AND s.scope_kind='snapshot'
        WHERE m.chat IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jargons WHERE id=r.id)""",
        (json.dumps(negatives),)).all()
    if rows:
        db.exec_driver_sql(f"""INSERT INTO jargons(id,{_COLUMNS},is_global,scope_kind,scope_id,archived,revision,baseline_id)
            SELECT ?,{_SOURCE},0,'chat',?,0,0,s.baseline_id FROM jargons s WHERE s.id=?""",
            [(vid, chat, sid) for vid, sid, chat in rows])
        db.exec_driver_sql("""INSERT INTO jargon_evidence_links SELECT ?,record_id FROM jargon_evidence_links WHERE jargon_id=?""",
                           [(vid, sid) for vid, sid, _ in rows])


def inherited_source(db: Connection, version_id: int) -> Optional[Tuple[int, str]]:
    if version_id >= 0:
        return None
    row = db.exec_driver_sql("""SELECT s.id,m.chat FROM jargon_snapshot_members m JOIN jargons s
        ON s.id=?-m.shift AND s.scope_kind='snapshot' AND s.scope_id=m.snapshot
        WHERE m.end_id=(SELECT min(end_id) FROM jargon_snapshot_members WHERE end_id>=?)
        AND m.chat IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jargons WHERE id=?)""",
        (-version_id, -version_id, version_id)).first()
    return (row[0], row[1]) if row else None

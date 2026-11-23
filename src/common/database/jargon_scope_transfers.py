"""在同一事务内继承已有释义及其线索；共享范围变化本身不会触发推断。"""

from typing import List, Optional, Tuple

from sqlalchemy.engine import Connection

import json

from .jargon_snapshots import detach_snapshots, freeze_group


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def term_key(content: str) -> str:
    """与 SQLite lower(trim(content)) 的唯一索引保持一致。"""
    return content.strip(" ").translate(_ASCII_LOWER)


def stored_jargon_members(db: Connection, chat: Optional[str] = None, *, snapshots: bool = True) -> List[Tuple[str, str]]:
    """在当前数据库事务内解析成员。迁移时跳过旧重叠，单聊天查询保留所有匹配以便检测冲突。"""
    # 全平台全目标的规则也覆盖仅有黑话历史、已无聊天元数据的来源。
    historical_chats = (
        "SELECT DISTINCT scope_id AS id FROM jargons WHERE scope_kind='chat' AND archived=0"
        if chat is None else "SELECT :chat AS id"
    )
    if chat is None and snapshots:
        historical_chats += " UNION SELECT chat FROM jargon_snapshot_members WHERE chat IS NOT NULL"
    query = f"""WITH active AS (
        SELECT g.id FROM sharing_groups g JOIN sharing_group_capabilities cap ON cap.group_id=g.id
        WHERE g.enabled AND cap.capability='jargon' AND cap.enabled
    ), members AS (
        SELECT m.group_id, m.session_id FROM sharing_group_members m JOIN active ON active.id=m.group_id
        UNION SELECT r.group_id, c.session_id FROM sharing_group_rules r JOIN active ON active.id=r.group_id
        JOIN chat_sessions c ON (r.platform='*' OR r.platform=c.platform)
        AND (r.rule_type='all' OR r.rule_type=CASE WHEN coalesce(c.group_id,'')!='' THEN 'group' ELSE 'private' END)
        AND (r.item_id='*' OR r.item_id=coalesce(nullif(c.group_id,''),c.user_id))
        UNION SELECT r.group_id, h.id FROM sharing_group_rules r JOIN active ON active.id=r.group_id
        CROSS JOIN ({historical_chats}) h WHERE r.platform='*' AND r.item_id='*' AND r.rule_type='all'
    )"""
    if chat is None:
        rows = db.exec_driver_sql(
            query + " SELECT min(group_id), session_id FROM members GROUP BY session_id HAVING count(*)=1"
        )
    else:
        rows = db.exec_driver_sql(query + " SELECT group_id, session_id FROM members WHERE session_id=:chat", {"chat": chat})
    return [(group_id, session_id) for group_id, session_id in rows]


def inherit_configured_groups(db: Connection, *, snapshots: bool = True) -> None:
    by_group = {}
    for group, chat in stored_jargon_members(db, snapshots=snapshots):
        by_group.setdefault(group, []).append(chat)
    for group, chats in by_group.items():
        inherit_scope_members(db, group, chats, joining=True, snapshots=snapshots)


def inherit_scope_members(db: Connection, group: str, chats: List[str], joining: bool, snapshots: bool = True) -> None:
    if not chats:
        return
    if not joining:
        freeze_group(db, group, chats)
        return
    # 记录来源版本，避免 SQL 插入过程中读到刚创建的记录。
    db.exec_driver_sql("CREATE TEMP TABLE jargon_transfers (source INTEGER, target TEXT)")
    # 保留所有来源 ID；获选版本提供释义和进度，其余版本只贡献线索。
    db.exec_driver_sql("""CREATE TEMP TABLE jargon_join_sources AS
        WITH locals AS (
            SELECT id, lower(trim(content)) AS term FROM jargons WHERE archived=0 AND scope_kind='chat'
            AND scope_id IN (SELECT value FROM json_each(?))
        )
        SELECT id, term FROM locals UNION ALL
        SELECT g.id, lower(trim(g.content)) FROM jargons g WHERE g.archived=0
        AND g.scope_kind='group' AND g.scope_id=? AND lower(trim(g.content)) IN (SELECT term FROM locals)""",
        (json.dumps(chats), group))
    if snapshots:
        # 同时入组的多个聊天可能继承同一份词库，每个仍被其中至少一人引用的词只参与一次合并。
        db.exec_driver_sql("""INSERT INTO jargon_join_sources
            SELECT f.id, lower(trim(f.content)) FROM jargons f WHERE f.scope_kind='snapshot'
            AND EXISTS (SELECT 1 FROM jargon_snapshot_members m WHERE m.snapshot=f.scope_id
                AND m.chat IN (SELECT value FROM json_each(?))
                AND NOT EXISTS (SELECT 1 FROM jargons WHERE id=-(m.shift+f.id)))""", (json.dumps(chats),))
        db.exec_driver_sql("""INSERT INTO jargon_join_sources
            SELECT g.id,lower(trim(g.content)) FROM jargons g WHERE g.scope_kind='group' AND g.scope_id=?
            AND g.archived=0 AND lower(trim(g.content)) IN (SELECT term FROM jargon_join_sources)
            AND g.id NOT IN (SELECT id FROM jargon_join_sources)""", (group,))
    protected = "j.allow_update=0" if snapshots else "j.created_by='MANUAL'"
    db.exec_driver_sql(f"""CREATE TEMP TABLE jargon_winners AS
        SELECT id, term, scope_kind FROM (
            SELECT j.id, s.term, j.scope_kind, row_number() OVER (PARTITION BY s.term ORDER BY
                ({protected}) DESC, (j.is_jargon IS 1 AND trim(j.meaning)!='') DESC,
                (j.is_jargon IS NOT NULL) DESC, (j.scope_kind='group') DESC, j.count DESC, j.id DESC) AS priority
            FROM jargon_join_sources s JOIN jargons j ON j.id=s.id
        ) WHERE priority=1""")
    db.exec_driver_sql("""INSERT INTO jargon_transfers
        SELECT id, ? FROM jargon_winners WHERE scope_kind!='group'""", (group,))
    db.exec_driver_sql("""UPDATE jargons SET archived=1, revision=revision+1
        WHERE id IN (SELECT id FROM jargon_join_sources)
        AND scope_kind!='snapshot' AND id NOT IN (SELECT id FROM jargon_winners WHERE scope_kind='group')""")
    fields = (
        "content",
        "meaning",
        "session_id_dict",
        "count",
        "is_jargon",
        "is_complete",
        "last_inference_count",
        "created_timestamp",
        "updated_timestamp",
        "created_by",
    ) + (("allow_update",) if snapshots else ())
    db.exec_driver_sql(
        f"""INSERT INTO jargons ({", ".join(fields)}, is_global, scope_kind, scope_id,
            baseline_id, archived, revision)
        SELECT {", ".join("j." + field for field in fields)}, 0, ?, t.target,
            coalesce(j.baseline_id, j.id), 0, 0 FROM jargon_transfers t JOIN jargons j ON j.id=t.source""",
        ("group",),
    )
    db.exec_driver_sql(
        """INSERT INTO jargon_evidence_links
        SELECT d.id, e.record_id FROM jargon_transfers t JOIN jargons s ON s.id=t.source
        JOIN jargons d ON d.archived=0 AND d.scope_kind=? AND d.scope_id=t.target
            AND lower(trim(d.content))=lower(trim(s.content))
        JOIN jargon_evidence_links e ON e.jargon_id=t.source""",
        ("group",),
    )
    db.exec_driver_sql("""INSERT OR IGNORE INTO jargon_evidence_links (jargon_id, record_id)
        SELECT g.id, e.record_id FROM jargon_join_sources s
        JOIN jargons g ON g.scope_kind='group' AND g.scope_id=? AND g.archived=0
            AND lower(trim(g.content))=s.term
        JOIN jargon_evidence_links e ON e.jargon_id=s.id""", (group,))
    if snapshots:
        # 释义和进度来自获选版本，贡献归属则取所有来源。快照中被聊天覆盖的词不再参与。
        db.exec_driver_sql("""WITH owners(term,chat) AS MATERIALIZED (
            SELECT s.term,j.scope_id FROM jargon_join_sources s JOIN jargons j ON j.id=s.id WHERE j.scope_kind='chat'
            UNION SELECT s.term,o.value FROM jargon_join_sources s JOIN jargons j ON j.id=s.id
                JOIN json_each(j.contributing_chats) o WHERE j.scope_kind='group'
            UNION SELECT s.term,m.chat FROM jargon_join_sources s JOIN jargons j ON j.id=s.id
                JOIN json_each(j.contributing_chats) o JOIN jargon_snapshot_members m
                ON m.snapshot=j.scope_id AND m.chat=o.value WHERE j.scope_kind='snapshot'
                AND m.chat IN (SELECT value FROM json_each(?))
                AND NOT EXISTS(SELECT 1 FROM jargons fork WHERE fork.id=-(m.shift+j.id))
        ), per_term AS (SELECT term,json_group_array(chat) chats FROM owners GROUP BY term)
        UPDATE jargons AS g SET contributing_chats=p.chats FROM per_term p WHERE g.archived=0
            AND g.scope_kind='group' AND g.scope_id=? AND lower(trim(g.content))=p.term""", (json.dumps(chats), group))
    # 合入线索也会使进行中的推断、编辑快照失效。
    db.exec_driver_sql("""UPDATE jargons SET revision=revision+1
        WHERE id IN (SELECT id FROM jargon_winners WHERE scope_kind='group')""")
    db.exec_driver_sql("DROP TABLE jargon_join_sources")
    db.exec_driver_sql("DROP TABLE jargon_winners")
    if snapshots:
        detach_snapshots(db, chats)
    db.exec_driver_sql("DROP TABLE jargon_transfers")

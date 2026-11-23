"""保留原 ID 的只读基线，并为各作用域展开可独立演进的释义。"""

from src.common.database.database_model import JargonSnapshotMember
from src.common.database.jargon_scope_transfers import inherit_configured_groups
from .models import MigrationExecutionContext


def migrate_v57_to_v63(context: MigrationExecutionContext) -> None:
    db = context.connection
    # sqlite3 的旧事务模式在 DDL 前不会自动 BEGIN，通过空写入把 DDL 和版本号纳入同一事务。
    db.exec_driver_sql("UPDATE jargons SET id=id WHERE 0")
    if db.exec_driver_sql("PRAGMA user_version").scalar_one() == 63:
        return
    for column in (
        "scope_kind TEXT NOT NULL DEFAULT 'chat'",
        "scope_id TEXT NOT NULL DEFAULT ''",
        "baseline_id INTEGER",
        "archived BOOLEAN NOT NULL DEFAULT 0",
        "revision INTEGER NOT NULL DEFAULT 0",
    ):
        db.exec_driver_sql(f"ALTER TABLE jargons ADD COLUMN {column}")
    db.exec_driver_sql("""CREATE TABLE jargon_learning_records (
        id INTEGER PRIMARY KEY, content TEXT NOT NULL, session_id TEXT, scope_kind TEXT NOT NULL,
        scope_id TEXT NOT NULL, evidence TEXT, excerpt TEXT NOT NULL, recorded_at DATETIME)""")
    db.exec_driver_sql("""CREATE TABLE jargon_evidence_links (
        jargon_id INTEGER NOT NULL REFERENCES jargons(id), record_id INTEGER NOT NULL REFERENCES jargon_learning_records(id), PRIMARY KEY(jargon_id, record_id))""")
    db.exec_driver_sql(
        "UPDATE jargons SET archived=1, baseline_id=id, scope_kind='baseline', scope_id=CAST(id AS TEXT)"
    )
    db.exec_driver_sql("""CREATE TEMP TABLE jargon_targets AS
        SELECT id AS source, 'global' AS kind, '' AS target FROM jargons WHERE is_global
        UNION ALL SELECT j.id, 'chat', e.key FROM jargons j, json_each(
            CASE WHEN json_valid(j.session_id_dict) THEN j.session_id_dict ELSE '{}' END) e
            WHERE NOT j.is_global AND typeof(e.key)='text' AND trim(e.key)!=''""")
    db.exec_driver_sql("""INSERT INTO jargon_learning_records
        (id, content, session_id, scope_kind, scope_id, evidence, excerpt, recorded_at)
        SELECT j.id, content, CASE WHEN count(t.source)=1 AND min(t.kind)='chat' THEN min(t.target) END,
        'baseline', CAST(j.id AS TEXT), evidence_messages, '', NULL
        FROM jargons j LEFT JOIN jargon_targets t ON t.source=j.id
        WHERE evidence_messages IS NOT NULL GROUP BY j.id""")
    fields = (
        "content",
        "meaning",
        "session_id_dict",
        "count",
        "is_jargon",
        "is_complete",
        "is_global",
        "last_inference_count",
        "created_timestamp",
        "updated_timestamp",
        "created_by",
    )
    names = ", ".join(fields)
    values = ", ".join("j." + field for field in fields)
    db.exec_driver_sql(f"""INSERT INTO jargons ({names}, scope_kind, scope_id, baseline_id, archived)
        SELECT {values}, t.kind, t.target, j.id,
        ROW_NUMBER() OVER(PARTITION BY lower(trim(j.content)), t.kind, t.target
            ORDER BY (j.is_jargon IS 1 AND trim(j.meaning)!='') DESC, j.count DESC, j.id DESC)>1
        FROM jargon_targets t JOIN jargons j ON j.id=t.source""")
    db.exec_driver_sql("""INSERT INTO jargon_evidence_links SELECT j.id, e.id FROM jargons j
        JOIN jargon_learning_records e ON e.id=j.baseline_id""")
    db.exec_driver_sql("DROP TABLE jargon_targets")
    db.exec_driver_sql("""CREATE UNIQUE INDEX ux_jargon_active_scope ON jargons(scope_kind,scope_id,lower(trim(content)))
        WHERE archived=0""")
    db.exec_driver_sql("CREATE INDEX ix_jargon_term ON jargons(lower(trim(content)))")
    # 使用已经保存的黑话组展开成员。旧库中存在重叠时，保留该聊天自己的版本。
    inherit_configured_groups(db, snapshots=False)

    db.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_jargon_evidence_links_record_id ON jargon_evidence_links(record_id)"
    )
    db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS jargon_deferred_learning (
        record_id INTEGER PRIMARY KEY REFERENCES jargon_learning_records(id) ON DELETE CASCADE,
        source_session_id TEXT NOT NULL, needs_dispatch BOOLEAN NOT NULL DEFAULT 1)""")
    db.exec_driver_sql("""CREATE INDEX IF NOT EXISTS ix_jargon_deferred_learning_source_session_id
        ON jargon_deferred_learning(source_session_id)""")

    db.exec_driver_sql("""UPDATE jargons SET archived=1, revision=revision+1
        WHERE archived=0 AND scope_kind='group' AND scope_id NOT IN (
            SELECT g.id FROM sharing_groups g JOIN sharing_group_capabilities c ON c.group_id=g.id
            WHERE g.enabled AND c.enabled AND c.capability='jargon')""")

    JargonSnapshotMember.__table__.create(db, checkfirst=True)
    db.exec_driver_sql("""CREATE INDEX IF NOT EXISTS ix_jargon_snapshot_term
        ON jargons(scope_id,lower(trim(content))) WHERE scope_kind='snapshot'""")

    db.exec_driver_sql("ALTER TABLE jargons ADD COLUMN contributing_chats TEXT")
    # 只还原当前组词条可追溯的归属。迁移前共享的基线可能没有唯一消息来源。
    db.exec_driver_sql("""WITH active AS MATERIALIZED (
        SELECT id,scope_id,scope_kind,baseline_id,lower(trim(content)) term FROM jargons
        WHERE (archived=0 AND scope_kind='group') OR scope_kind='snapshot'
    ), owners(id,chat) AS (
        SELECT a.id,r.session_id FROM active a JOIN jargon_evidence_links l ON l.jargon_id=a.id
        JOIN jargon_learning_records r ON r.id=l.record_id WHERE r.session_id IS NOT NULL
        AND ((r.scope_kind='group' AND (r.scope_id=a.scope_id OR a.scope_kind='snapshot'))
            OR r.scope_kind IN ('chat','baseline'))
        UNION SELECT a.id,c.scope_id FROM active a JOIN jargons c ON lower(trim(c.content))=a.term
        WHERE c.scope_kind='chat' AND c.archived=1
        AND (c.baseline_id IS NULL OR EXISTS (
            SELECT 1 FROM jargons b JOIN json_each(b.session_id_dict) owner
            WHERE b.id=c.baseline_id AND b.scope_kind='baseline' AND owner.key=c.scope_id))
        AND (
            coalesce(c.baseline_id,c.id)=a.baseline_id OR EXISTS (
                SELECT 1 FROM jargon_evidence_links cl JOIN jargon_evidence_links gl
                ON gl.record_id=cl.record_id AND gl.jargon_id=a.id WHERE cl.jargon_id=c.id))
    ), collected AS (SELECT id,json_group_array(chat) chats FROM owners GROUP BY id)
    UPDATE jargons SET contributing_chats=c.chats FROM collected c WHERE jargons.id=c.id""")

    db.exec_driver_sql("ALTER TABLE jargons ADD COLUMN allow_update BOOLEAN NOT NULL DEFAULT 1")
    db.exec_driver_sql("UPDATE jargons SET allow_update=0 WHERE created_by='MANUAL'")
    db.exec_driver_sql("UPDATE jargons SET is_complete=0 WHERE scope_kind='global' AND archived=0 AND allow_update AND is_complete")

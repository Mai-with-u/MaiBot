"""聊天迁移计划、幂等来源账本和主数据库复制。所有入口在线程池运行。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import base64
import hashlib
import json
import uuid

from sqlalchemy import MetaData, Table, UniqueConstraint, inspect, select
from sqlalchemy.engine import Connection, Engine


from src.common.database.chat_migration_models import copies, jobs, metadata


# 表归属须显式声明，新增聊天流数据表时不得静默漏迁。
SCOPES = {
    "mai_messages": "session_id",
    "tool_records": "session_id",
    "llm_usage": "session_id",
    "maisaka_monitor_events": "session_id",
    "expressions": "session_id",
    "file_bindings": "session_id",
    "statistics_usage_hourly": "session_id",
    "high_frequency_terms": "chat_id",
    "sharing_group_members": "session_id",
    "maisaka_reply_effects": "session_id",
    "learned_commands": "session_id",
}
# 消息小时统计按真实收发对象聚合，是实例历史统计，不属于聊天流副本。
EXCLUDED = {"chat_sessions", "webui_help_turns", "statistics_message_hourly"}
LABELS = {
    "mai_messages": "聊天消息",
    "tool_records": "工具调用",
    "llm_usage": "模型用量",
    "maisaka_monitor_events": "观察记录",
    "expressions": "表达",
    "file_bindings": "附件绑定",
    "statistics_message_hourly": "消息统计",
    "statistics_usage_hourly": "用量统计",
    "high_frequency_terms": "词频",
    "sharing_group_members": "相通组成员关系",
    "jargons": "黑话",
    "maisaka_reply_effects": "回复评估记录",
    "learned_commands": "学习的命令",
}


def encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"无法序列化迁移数据: {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode)


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: value for key, value in row.items() if key not in {"raw_content", "id", "session_id"}}
    if "session_id_dict" in row:
        count = len(json.loads(row["session_id_dict"]))
        result["scope_label"] = (
            "全局" if row["is_global"] else (f"共享于 {count} 个聊天流" if count > 1 else "单个聊天流")
        )
    return result


class MigrationStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def initialize(self) -> None:
        metadata.create_all(self.engine, tables=[jobs, copies])

    def create(self, source: str, target: str, payload: Dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    id=job_id,
                    source=source,
                    target=target,
                    status="preview",
                    created_at=datetime.now().isoformat(),
                    payload=dumps(payload),
                    error="",
                )
            )
        return job_id

    def get(self, job_id: str) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one_or_none()
            if row is None:
                raise ValueError("迁移任务不存在")
            return {**dict(row), "payload": json.loads(row["payload"])}

    def list(self, unfinished: bool = False) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            statement = select(
                jobs.c.id, jobs.c.source, jobs.c.target, jobs.c.status, jobs.c.created_at, jobs.c.error
            ).order_by(jobs.c.created_at.desc())
            statement = (
                statement.where(jobs.c.status.not_in(["preview", "completed", "failed"]))
                if unfinished
                else statement.limit(100)
            )
            return [dict(row) for row in conn.execute(statement).mappings()]

    def update(self, job_id: str, status: str, payload: Dict[str, Any], error: str = "") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .values(
                    status=status,
                    payload=dumps(payload),
                    error=error,
                )
            )

    def claim(self, job_id: str, decisions: Dict[str, str]) -> None:
        with self.engine.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            job = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
            if job["status"] != "preview":
                raise ValueError("迁移任务已被执行，请勿重复提交")
            sessions = [job["source"], job["target"]]
            conflict = conn.execute(
                select(jobs.c.id).where(
                    jobs.c.status.not_in(["preview", "completed", "failed"]),
                    (jobs.c.source.in_(sessions) | jobs.c.target.in_(sessions)),
                )
            ).first()
            if conflict:
                raise ValueError("所选聊天流正在迁移或等待恢复")
            payload = json.loads(job["payload"])
            payload["decisions"] = decisions
            conn.execute(jobs.update().where(jobs.c.id == job_id).values(status="queued", payload=dumps(payload)))

    @staticmethod
    def _table(conn: Connection, name: str) -> Table:
        return Table(name, MetaData(), autoload_with=conn)

    def snapshot(self, source: str, target: str, conn: Optional[Connection] = None) -> Dict[str, Any]:
        if source == target:
            raise ValueError("来源和目标不能是同一个聊天流")
        if conn is None:
            with self.engine.connect() as opened:
                return self.snapshot(source, target, opened)
        inspector = inspect(conn)
        names = inspector.get_table_names()
        unknown = []
        for name in names:
            if name in SCOPES or name in EXCLUDED:
                continue
            if {column["name"] for column in inspector.get_columns(name)} & {"session_id", "chat_id"}:
                unknown.append(name)
        if unknown:
            raise ValueError("以下聊天数据表尚未声明迁移规则: " + ", ".join(unknown))
        sessions = self._table(conn, "chat_sessions")
        identities = {
            row["session_id"]: dict(row)
            for row in conn.execute(select(sessions).where(sessions.c.session_id.in_([source, target]))).mappings()
        }
        if len(identities) != 2:
            raise ValueError("来源或目标聊天流已不存在，请重新选择真实聊天流")
        data: Dict[str, Any] = {"identities": identities, "tables": {}, "conflicts": []}
        for name, scope in SCOPES.items():
            if name not in names:
                continue
            table = self._table(conn, name)
            rows = [
                dict(row) for row in conn.execute(select(table).where(table.c[scope].in_([source, target]))).mappings()
            ]
            rows.sort(key=dumps)
            data["tables"][name] = rows
        table = self._table(conn, "jargons")
        data["tables"]["jargons"] = [
            dict(row)
            for row in conn.execute(select(table).order_by(table.c.id)).mappings()
            if {source, target}.intersection(json.loads(row["session_id_dict"]))
        ]
        ledger = [dict(row) for row in conn.execute(select(copies)).mappings()]
        data["ledger"] = ledger
        for name in ("expressions", "jargons", "learned_commands"):
            for row in self.source_rows(data, name, source):
                if self.already_copied(data, name, row, source, target):
                    continue
                matches = self.matches(data, name, row, target)
                if matches:
                    data["conflicts"].append(
                        {
                            "id": f"{name}:{row['id']}",
                            "kind": name,
                            "label": row.get("content") or row.get("command") or f"{row['situation']} / {row['style']}",
                            "source": public_row(row),
                            "target": [public_row(item) for item in matches],
                            "choices": ["source", "target", "both"],
                        }
                    )
        identity_fields = ("session_id", "platform", "user_id", "group_id", "account_id", "scope")
        data["fingerprint"] = digest(
            {
                "tables": data["tables"],
                "identities": {
                    key: {field: row.get(field) for field in identity_fields} for key, row in identities.items()
                },
            }
        )
        return data

    @staticmethod
    def source_rows(data: Dict[str, Any], name: str, source: str) -> List[Dict[str, Any]]:
        rows = [
            row
            for row in data["tables"].get(name, [])
            if (
                source in json.loads(row["session_id_dict"]) and not row["is_global"]
                if name == "jargons"
                else row[SCOPES[name]] == source
            )
        ]
        if not (name.startswith("statistics_") or name == "high_frequency_terms"):
            return rows
        # 聚合行可以同时包含多个来源的贡献；按来源拆开，避免 A→B→A 或 A→B→C→A 重复累加。
        transfers: List[Dict[str, Any]] = []
        for row in rows:
            native = MigrationStore.amounts(row)
            for item in data["ledger"]:
                if item["table_name"] != name or item["target"] != source or item["target_key"] != str(row["id"]):
                    continue
                values = json.loads(item["amounts"])
                for key, value in values.items():
                    native[key] -= value
                transfers.append({**row, **values, "__migration_origin": item["origin"]})
            if any(native.values()):
                transfers.append({**row, **native, "__migration_origin": f"{source}:{row['id']}"})
        return transfers

    @staticmethod
    def origin(data: Dict[str, Any], name: str, row: Dict[str, Any], source: str) -> str:
        if "__migration_origin" in row:
            return row["__migration_origin"]
        key = str(row.get("id", row.get("event_id", row.get("effect_id", row.get("group_id", "")))))
        for copy in data["ledger"]:
            if copy["table_name"] == name and copy["target"] == source and copy["target_key"] == key:
                return copy["origin"]
        return f"{source}:{key}"

    def already_copied(self, data: Dict[str, Any], name: str, row: Dict[str, Any], source: str, target: str) -> bool:
        # 黑话可由多个聊天流直接共用；目标已在作用域内时，并不存在待迁入的副本。
        if name == "jargons" and (row["is_global"] or target in json.loads(row["session_id_dict"])):
            return True
        origin = self.origin(data, name, row, source)
        if origin.startswith(f"{target}:"):
            return True
        existing = next(
            (
                item
                for item in data["ledger"]
                if item["table_name"] == name and item["origin"] == origin and item["target"] == target
            ),
            None,
        )
        if existing is None:
            return False
        if name.startswith("statistics_") or name == "high_frequency_terms":
            return json.loads(existing["amounts"]) == self.amounts(row)
        if not existing["target_key"]:
            return True  # 用户选择了保留目标，保留该条处理决定。
        return any(
            str(item.get("id", item.get("event_id", item.get("effect_id", item.get("group_id", "")))))
            == existing["target_key"]
            for item in self.source_rows(data, name, target)
        )

    @staticmethod
    def amounts(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if isinstance(value, (int, float)) and key not in {"id", "rank", "frequency", "message_frequency"}
        }

    @staticmethod
    def matches(data: Dict[str, Any], name: str, row: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
        candidates = MigrationStore.source_rows(data, name, target)
        keys = (
            ("situation", "style")
            if name == "expressions"
            else (("command", "target") if name == "learned_commands" else ("content",))
        )
        return [item for item in candidates if item["id"] != row["id"] and all(item[key] == row[key] for key in keys)]

    def apply(self, job_id: str, expected: str, decisions: Dict[str, str]) -> Dict[str, int]:
        job = self.get(job_id)
        source, target = job["source"], job["target"]
        counts: Dict[str, int] = {}
        with self.engine.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            data = self.snapshot(source, target, conn)
            if data["fingerprint"] != expected:
                raise ValueError("聊天数据已变化，请重新预览并处理冲突")
            for conflict in data["conflicts"]:
                if decisions.get(conflict["id"]) not in conflict["choices"]:
                    raise ValueError("存在未处理的冲突")
            for name in data["tables"]:
                table = self._table(conn, name)
                counts[name] = 0
                if name == "sharing_group_members":
                    group_choice = decisions.get("sharing_groups", "both")
                    if group_choice == "target":
                        continue
                    if group_choice == "source":
                        conn.execute(table.delete().where(table.c.session_id == target))
                for original in self.source_rows(data, name, source):
                    if self.already_copied(data, name, original, source, target):
                        continue
                    row = dict(original)
                    row.pop("__migration_origin", None)
                    origin = self.origin(data, name, original, source)
                    previous = next(
                        (
                            item
                            for item in data["ledger"]
                            if item["table_name"] == name and item["origin"] == origin and item["target"] == target
                        ),
                        None,
                    )
                    choice = decisions.get(f"{name}:{row.get('id')}", "both")
                    matches = (
                        self.matches(data, name, row, target)
                        if name in {"expressions", "jargons", "learned_commands"}
                        else []
                    )
                    target_key = ""
                    if choice == "source":
                        for match in matches:
                            if name == "jargons":
                                mapping = json.loads(match["session_id_dict"])
                                mapping.pop(target, None)
                                conn.execute(
                                    table.update()
                                    .where(table.c.id == match["id"])
                                    .values(session_id_dict=dumps(mapping))
                                )
                            else:
                                conn.execute(table.delete().where(table.c.id == match["id"]))
                    if choice != "target":
                        row.pop("id", None)
                        if name == "maisaka_monitor_events":
                            row.pop("event_id", None)
                        if name == "maisaka_reply_effects":
                            row["effect_id"] = uuid.uuid4().hex
                        if name == "jargons":
                            mapping = json.loads(row["session_id_dict"])
                            row["session_id_dict"] = dumps({target: mapping[source]})
                            row["is_global"] = False
                        else:
                            row[SCOPES[name]] = target
                        existing = self._unique_match(conn, table, row)
                        if existing is not None:
                            # 聚合值在本行所属统计维度内累加，不能覆盖已有目标数据。
                            if name.startswith("statistics_") or name == "high_frequency_terms":
                                old_amounts = json.loads(previous["amounts"]) if previous else {}
                                additive = {
                                    key: existing[key] + value - old_amounts.get(key, 0)
                                    for key, value in self.amounts(row).items()
                                }
                                conn.execute(table.update().where(table.c.id == existing["id"]).values(**additive))
                            target_key = str(existing.get("id", existing.get("group_id", "")))
                        else:
                            result = conn.execute(table.insert().values(**row))
                            target_key = str(result.inserted_primary_key[0])
                            counts[name] += 1
                    values = {
                        "table_name": name,
                        "origin": origin,
                        "target": target,
                        "target_key": target_key,
                        "amounts": dumps(self.amounts(original)),
                    }
                    if previous:
                        conn.execute(copies.update().where(copies.c.id == previous["id"]).values(**values))
                    else:
                        conn.execute(copies.insert().values(**values))
            payload = job["payload"]
            payload["result"] = counts
            payload["database_committed"] = True
            conn.execute(
                jobs.update().where(jobs.c.id == job_id).values(status="finalizing", payload=dumps(payload), error="")
            )
        return counts

    @staticmethod
    def _unique_match(conn: Connection, table: Table, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        keys = [
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        if table.name == "sharing_group_members":
            keys.append(("group_id", "session_id"))
        for columns in keys:
            found = conn.execute(select(table).where(*(table.c[key] == row[key] for key in columns))).mappings().first()
            if found is not None:
                return dict(found)
        return None

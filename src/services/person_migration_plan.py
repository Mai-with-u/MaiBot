"""在线人物迁移的 SQLite 变更计划和逐字段冲突。无业务模块初始化副作用。"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import base64
import hashlib
import json
import sqlite3


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def dumps(value: Any) -> str:
    def encode(item: Any) -> Dict[str, str]:
        if isinstance(item, bytes):
            return {"$bytes": base64.b64encode(item).decode("ascii")}
        raise TypeError(f"不能序列化迁移数据: {type(item).__name__}")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode)


def loads(value: str) -> Any:
    return json.loads(value, object_hook=lambda item: base64.b64decode(item["$bytes"]) if set(item) == {"$bytes"} else item)


def fingerprint(conn: sqlite3.Connection) -> str:
    """包含结构和全部数据；执行前核对数据库快照是否过期。"""
    digest = hashlib.sha256()
    for line in conn.iterdump():
        digest.update(line.encode())
        digest.update(b"\n")
    return digest.hexdigest()


class DatabasePlan:
    def __init__(self, name: str, path: Path, decisions: Optional[Mapping[str, str]] = None, *, verify_fingerprint: bool = True) -> None:
        self.name = name
        self.path = path.resolve()
        self.conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        # 在线预览的指纹和逐表读取必须来自同一个只读快照。
        self.conn.execute("BEGIN")
        self.conn.row_factory = sqlite3.Row
        self.tables = {
            row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        self.fingerprint = fingerprint(self.conn) if verify_fingerprint else None
        self.decisions = decisions or {}
        self.conflicts: List[Dict[str, Any]] = []
        self.changes: List[Dict[str, Any]] = []

    def close(self) -> None:
        self.conn.close()

    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(f"SELECT * FROM {quote(table)}")]

    def columns(self, table: str) -> List[str]:
        return [row[1] for row in self.conn.execute(f"PRAGMA table_info({quote(table)})")]

    def keys(self, table: str) -> List[str]:
        columns = self.conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
        keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        if not keys:
            raise ValueError(f"{self.name}.{table} 缺少主键，不能安全迁移")
        return keys

    def choose(self, key: str, field: str, source: Any, target: Any) -> Any:
        if source == target or source is None or source == "":
            return target
        if target is None or target == "":
            return source
        conflict_id = f"{self.name}:{key}:{field}"
        self.conflicts.append({"id": conflict_id, "field": field, "source": source, "target": target,
                               "choices": ["source", "target"]})
        choice = self.decisions.get(conflict_id, "target")
        if choice not in {"source", "target"}:
            raise ValueError(f"冲突选择无效: {conflict_id}")
        return source if choice == "source" else target

    def combine(self, table: str, source: Dict[str, Any], target: Dict[str, Any], key: str) -> Dict[str, Any]:
        """集合并集、计数累加、时间取边界；其它不同值逐字段确认。"""
        result = dict(target)
        for field, value in source.items():
            current = target[field]
            if field in {"count", "know_counts", "message_count", "occurrence_count", "request_count", "prompt_tokens",
                         "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens", "cost", "duration_count",
                         "duration_sum", "duration_sum_sq", "response_time_count", "response_time_sum"}:
                result[field] = (value or 0) + (current or 0)
            elif value == current:
                continue
            elif field in {"created_at", "first_seen", "first_observed_at", "first_known_time", "created_timestamp"}:
                result[field] = min(item for item in (value, current) if item is not None)
            elif field in {"updated_at", "last_seen", "last_seen_at", "last_confirmed_at", "last_known_time", "last_active_timestamp"}:
                result[field] = max(item for item in (value, current) if item is not None)
            elif field == "is_known":
                result[field] = bool(value or current)
            else:
                result[field] = self.choose(key, field, value, current)
        return result

    def transform(
        self, table: str, transform: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        *, merge: Optional[Callable[[Dict[str, Any], Dict[str, Any], str], Dict[str, Any]]] = None,
        where: str = "1", parameters: Tuple[Any, ...] = (),
    ) -> None:
        if table not in self.tables:
            return
        keys = self.keys(table)
        original = [dict(row) for row in self.conn.execute(f"SELECT * FROM {quote(table)} WHERE {where}", parameters)]
        current = {tuple(row[key] for key in keys): row for row in original}
        moves: List[Tuple[Tuple[Any, ...], Optional[Dict[str, Any]]]] = []
        for row in original:
            after = transform(dict(row))
            if after != row:
                moves.append((tuple(row[key] for key in keys), after))
        changed_keys = {key for key, _ in moves}
        for key, _ in moves:
            del current[key]
        # 为唯一业务键建立索引，避免每条迁移记录都扫描整张表。
        unique_fields = []
        for index in self.conn.execute(f"PRAGMA index_list({quote(table)})"):
            if index[2] and not index[4]:
                fields = tuple(row[2] for row in self.conn.execute(f"PRAGMA index_info({quote(index[1])})"))
                if fields and all(field is not None for field in fields):
                    unique_fields.append(fields)
        lookups = [{tuple(row[field] for field in fields): key for key, row in current.items()
                    if all(row[field] is not None for field in fields)} for fields in unique_fields]
        for _, after in moves:
            if after is None:
                continue
            for fields, lookup in zip(unique_fields, lookups, strict=True):
                match = lookup.get(tuple(after[field] for field in fields))
                if match is not None:
                    for field in keys:
                        after[field] = current[match][field]
            key = tuple(after[field] for field in keys)
            if key in current:
                previous = current[key]
                label = f"{table}:{dumps(key)}"
                after = merge(after, previous, label) if merge else self.combine(table, after, previous, label)
                for fields, lookup in zip(unique_fields, lookups, strict=True):
                    lookup.pop(tuple(previous[field] for field in fields), None)
            current[key] = after
            changed_keys.add(key)
            for fields, lookup in zip(unique_fields, lookups, strict=True):
                if all(after[field] is not None for field in fields):
                    lookup[tuple(after[field] for field in fields)] = key
        before_by_key = {tuple(row[key] for key in keys): row for row in original}
        for key in sorted(changed_keys, key=dumps):
            before, after = before_by_key.get(key), current.get(key)
            if before != after:
                self.changes.append({"table": table, "keys": keys, "before": before, "after": after})

    def export(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for change in self.changes:
            counts[change["table"]] = counts.get(change["table"], 0) + 1
        return {"name": self.name, "path": str(self.path), "fingerprint": self.fingerprint,
                "changes": self.changes, "counts": counts}


def apply_database(plan: Dict[str, Any]) -> None:
    conn = sqlite3.connect(plan["path"])
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        if fingerprint(conn) != plan["fingerprint"]:
            raise ValueError(f"{plan['name']} 在预览后发生变化，请重新预览")
        for change in plan["changes"]:
            if change["before"] is not None:
                where = " AND ".join(f"{quote(key)} IS ?" for key in change["keys"])
                conn.execute(f"DELETE FROM {quote(change['table'])} WHERE {where}",
                             tuple(change["before"][key] for key in change["keys"]))
        for change in plan["changes"]:
            after = change["after"]
            if after is not None:
                conn.execute(f"INSERT INTO {quote(change['table'])} ({','.join(quote(key) for key in after)}) "
                             f"VALUES ({','.join('?' for _ in after)})", tuple(after.values()))
        errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if errors:
            raise ValueError(f"{plan['name']} 迁移后外键校验失败: {errors[:5]}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

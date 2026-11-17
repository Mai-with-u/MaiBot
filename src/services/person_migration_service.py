"""人物身份的在线预览、事务合并和任务恢复。"""

from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import hashlib
import json
import os
import sqlite3
import tomllib
import uuid

from src.person_info.identity import load_aliases, person_id_for, resolve_alias

from .person_memory_migration import CurrentMemoryPlan
from .person_migration_plan import DatabasePlan, dumps, fingerprint, loads, quote


@dataclass
class MigrationExtension:
    """插件可登记自己的 SQLite 数据库及归属转换；统一纳入预览、事务和任务记录。"""
    name: str
    database: Path
    build_plan: Callable[[DatabasePlan, Dict[str, Any]], None]


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _file_hash(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _check_pending_memory_writes(directory: Path, identities: List[str]) -> None:
    """队列日志保留已完成记录，只检查涉及本次迁移的未完成写入。"""
    def records(name: str) -> List[Dict[str, Any]]:
        path = directory / name
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    queued = records("startup_write_queue.jsonl")
    done = {record["record_id"] for record in records("startup_write_queue.done.jsonl")}

    def relevant(value: Any) -> bool:
        if isinstance(value, dict):
            return any(relevant(item) for key, item in value.items() if key != "image_base64")
        if isinstance(value, list):
            return any(relevant(item) for item in value)
        return isinstance(value, str) and any(identity in value for identity in identities)

    pending = [record for record in queued if record["record_id"] not in done and relevant(record["payload"])]
    if not pending:
        return
    failures = {record["record_id"]: record["error"] for record in records("startup_write_queue.failed.jsonl")}
    reasons = list(dict.fromkeys(failures[record["record_id"]] for record in pending if record["record_id"] in failures))
    if reasons:
        raise ValueError(f"该用户有 {len(pending)} 条记忆尚未写入，其中存在失败任务：{'；'.join(reasons)}。请先处理失败任务后再迁移")
    raise ValueError(f"该用户有 {len(pending)} 条记忆正在等待写入，请完成后重新预览")


def _main_plan(plan: DatabasePlan, context: Dict[str, Any]) -> None:
    platform, source, target = context["platform"], context["source"], context["target"]
    source_person, target_person = context["source_person"], context["target_person"]
    context.update(user_map={source: target}, person_map={source_person: target_person}, platform_sessions=[])

    def combine_person(incoming: Dict[str, Any], existing: Dict[str, Any], key: str) -> Dict[str, Any]:
        source_values, target_values = dict(incoming), dict(existing)
        source_cards = json.loads(source_values.pop("group_cardname") or "[]")
        target_cards = json.loads(target_values.pop("group_cardname") or "[]")
        cards = {row["group_id"]: row for row in target_cards}
        for row in source_cards:
            group = row["group_id"]
            if group in cards:
                cards[group] = {"group_id": group, "group_cardname": plan.choose(
                    key, f"group_cardname.{group}", row["group_cardname"], cards[group]["group_cardname"],
                )}
            else:
                cards[group] = row
        merged = plan.combine("person_info", source_values, target_values, key)
        return {**merged, "group_cardname": json.dumps(list(cards.values()), ensure_ascii=False)}

    if plan.conn.execute("SELECT 1 FROM person_info WHERE platform=? AND user_id=? LIMIT 1", (platform, source)).fetchone() is None:
        raise ValueError("来源身份没有人物资料")
    plan.transform("person_info", lambda row: {**row, "user_id": target, "person_id": target_person}
                   if row["platform"] == platform and row["user_id"] == source else row, merge=combine_person,
                   where="platform=? AND user_id IN (?,?)", parameters=(platform, source, target))


def preview(
    root: Path, platform: str, source: str, target: str, *, decisions: Optional[Mapping[str, str]] = None,
    extensions: Optional[List[MigrationExtension]] = None,
) -> Dict[str, Any]:
    root = root.resolve()
    if not platform or not source or not target or any(value != value.strip() for value in (platform, source, target)):
        raise ValueError("平台和来源、目标 ID 必须为无首尾空白的非空字符串")
    if source == target:
        raise ValueError("来源和目标必须不同")
    return _preview(root, platform, source, target, decisions or {}, extensions or [])


def _preview(root: Path, platform: str, source: str, target: str, decisions: Mapping[str, str],
             extensions: List[MigrationExtension]) -> Dict[str, Any]:
    alias_path = root / "data" / "person_identity_aliases.json"
    state = deepcopy(load_aliases(alias_path))
    alias_before = hashlib.sha256(dumps(state).encode()).hexdigest()
    aliases = state["aliases"]
    mappings = aliases.get(platform, {})
    if resolve_alias(mappings, source) != source:
        raise ValueError("来源已迁移，请使用它当前指向的身份")
    target = resolve_alias(mappings, target)
    if source == target:
        raise ValueError("来源和目标已经是同一身份")
    context: Dict[str, Any] = {"platform": platform, "source": source, "target": target,
                               "source_person": person_id_for(platform, source), "target_person": person_id_for(platform, target),
                               "sessions": {}, "private_routes": state["private_routes"]}
    main = DatabasePlan("main", root / "data" / "MaiBot.db", decisions, verify_fingerprint=False)
    plans = [main]
    try:
        memory_dirs = [root / "data" / "a-memorix", root / "data" / "plugins" / "a-dawn.a-memorix"]
        config_path = root / "config" / "bot_config.toml"
        if config_path.exists():
            configuration = tomllib.loads(config_path.read_text(encoding="utf-8"))
            directory = Path(configuration.get("a_memorix", {}).get("storage", {}).get("data_dir", "./data/a-memorix"))
            memory_dirs.append(directory if directory.is_absolute() else root / directory)
        memory_paths = list(dict.fromkeys((directory / "metadata" / "metadata.db").resolve() for directory in memory_dirs))
        # 先检查前置条件，避免扫描历史记录后才发现记忆队列尚未处理。
        for path in memory_paths:
            if path.exists():
                if not path.is_relative_to(root):
                    raise ValueError("记忆数据库位于实例外，需先将数据归入实例后再迁移")
                _check_pending_memory_writes(path.parent.parent, [source, context["source_person"]])
        _main_plan(main, context)
        transports = state.setdefault("user_transports", {}).setdefault(platform, {})
        source_transport = transports.pop(source, "")
        if source_transport:
            transports[target] = main.choose("user_transport", "transport_user_id", source_transport, transports.get(target, ""))
        for index, path in enumerate(memory_paths):
            if path.exists():
                from src.A_memorix.core.person_migration import build_person_migration

                memory = CurrentMemoryPlan(f"memory-{index}", path, decisions, context)
                plans.append(memory)
                build_person_migration(memory, context)
        for extension in extensions:
            if extension.name in {plan.name for plan in plans}:
                raise ValueError(f"重复迁移扩展名称: {extension.name}")
            path = extension.database.resolve()
            if not path.is_relative_to(root) or path in {plan.path for plan in plans}:
                raise ValueError("扩展数据库必须属于当前实例且不能重复登记")
            plan = DatabasePlan(extension.name, path, decisions)
            plans.append(plan)
            extension.build_plan(plan, context)
        aliases.setdefault(platform, {})[source] = target
        for old in list(aliases[platform]):
            aliases[platform][old] = resolve_alias(aliases[platform], old)
        conflicts = [conflict for plan in plans for conflict in plan.conflicts]
        expected = {conflict["id"] for conflict in conflicts}
        if set(decisions) - expected:
            raise ValueError("冲突选择包含预览中不存在的项目")
        result = {"version": 3, "root": str(root), "identity": context,
                "databases": [plan.export() for plan in plans], "conflicts": conflicts,
                "decisions": dict(decisions), "ready": expected == set(decisions),
                "configuration": {str(config_path): _file_hash(config_path)},
                "aliases": {"path": str(alias_path), "before": alias_before, "after": dumps(state)}}
        result["digest"] = hashlib.sha256(dumps(result).encode()).hexdigest()
        return result
    finally:
        for plan in plans:
            plan.close()


def _apply_transaction_rows(conn: sqlite3.Connection, schema: str, database: Dict[str, Any]) -> None:
    """使用已锁定的连接写入变更，所有参与库提交前仍可统一回滚。"""
    for change in database["changes"]:
        if change["before"] is not None:
            where = " AND ".join(f"{quote(key)} IS ?" for key in change["keys"])
            conn.execute(f"DELETE FROM {quote(schema)}.{quote(change['table'])} WHERE {where}",
                         tuple(change["before"][key] for key in change["keys"]))
    for change in database["changes"]:
        after = change["after"]
        if after is not None:
            conn.execute(f"INSERT INTO {quote(schema)}.{quote(change['table'])} ({','.join(quote(key) for key in after)}) "
                         f"VALUES ({','.join('?' for _ in after)})", tuple(after.values()))
    if conn.execute(f"PRAGMA {quote(schema)}.foreign_key_check").fetchall():
        raise ValueError(f"{database['name']} 迁移后外键校验失败")


def execute(plan: Dict[str, Any], *, expected_root: Path) -> Path:
    """在线执行。数据库写锁保护复核与提交，不停止实例或覆盖整库备份。"""
    root = expected_root.resolve()
    if plan.get("digest") != hashlib.sha256(dumps({key: value for key, value in plan.items() if key != "digest"}).encode()).hexdigest():
        raise ValueError("迁移计划已被修改，请重新预览")
    if plan.get("version") != 3 or Path(plan["root"]).resolve() != root:
        raise ValueError("迁移计划版本或实例不匹配，请重新预览")
    if not plan["ready"]:
        raise ValueError("请逐项确认全部冲突并重新生成计划")
    paths = [Path(item["path"]).resolve() for item in plan["databases"]]
    if not paths or paths[0] != root / "data/MaiBot.db" or len(set(paths)) != len(paths) or any(not path.is_relative_to(root) for path in paths):
        raise ValueError("迁移数据库超出实例目录或重复")
    job = root / "data/person-migrations" / uuid.uuid4().hex
    job.mkdir(parents=True)
    state = {"version": 2, "status": "validating", "root": str(root)}
    _atomic_write(job / "state.json", dumps(state))
    with closing(sqlite3.connect(paths[0], timeout=30)) as conn:
        schemas = {str(paths[0]): "main"}
        for index, path in enumerate(paths[1:], 1):
            schema = f"migration_{index}"
            conn.execute(f"ATTACH DATABASE ? AS {quote(schema)}", (str(path),))
            schemas[str(path)] = schema
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            alias_path = root / "data/person_identity_aliases.json"
            if hashlib.sha256(dumps(load_aliases(alias_path)).encode()).hexdigest() != plan["aliases"]["before"]:
                raise ValueError("用户身份已变化，请重新预览")
            for path, expected in plan["configuration"].items():
                if _file_hash(Path(path)) != expected:
                    raise ValueError("配置已变化，请重新预览")
            identity = plan["identity"]
            builtins = {item["name"] for item in plan["databases"] if item["name"] == "main" or item["name"].startswith("memory-")}
            choices = {key: value for key, value in plan["decisions"].items() if key.split(":", 1)[0] in builtins}
            fresh = _preview(root, identity["platform"], identity["source"], identity["target"], choices, [])
            extras = [item for item in plan["databases"] if item["name"] not in builtins]
            # 自定义扩展由调用方生成计划；扩展库有变化时必须重新调用扩展预览。
            for item in extras:
                with closing(sqlite3.connect(Path(item["path"]).as_uri() + "?mode=ro", uri=True)) as reader:
                    if fingerprint(reader) != item["fingerprint"]:
                        raise ValueError("扩展数据已变化，请重新预览")
            reviewed = [item for item in plan["conflicts"] if item["id"].split(":", 1)[0] in builtins]
            if sorted(reviewed, key=lambda item: item["id"]) != sorted(fresh["conflicts"], key=lambda item: item["id"]):
                raise ValueError("冲突内容已变化，请重新预览并确认")
            fresh["databases"].extend(extras)
            if {item["path"] for item in fresh["databases"]} != set(schemas):
                raise ValueError("迁移数据库范围已变化，请重新预览")
            _atomic_write(job / "plan.json", dumps(fresh))
            state["status"] = "applying"
            _atomic_write(job / "state.json", dumps(state))
            for item in fresh["databases"]:
                schema = schemas[item["path"]]
                _apply_transaction_rows(conn, schema, item)
                conn.execute(f"CREATE TABLE IF NOT EXISTS {quote(schema)}.person_migration_commits (job_id TEXT PRIMARY KEY)")
                conn.execute(f"INSERT INTO {quote(schema)}.person_migration_commits VALUES (?)", (job.name,))
            conn.execute("CREATE TABLE IF NOT EXISTS person_identity_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO person_identity_state VALUES (1, ?)", (fresh["aliases"]["after"],))
            conn.commit()
        except BaseException:
            conn.rollback()
            state["status"] = "failed"
            _atomic_write(job / "state.json", dumps(state))
            raise
    state["status"] = "completed"
    _atomic_write(job / "state.json", dumps(state))
    return job


def recover(job: Path, *, expected_root: Path) -> None:
    """恢复任务记录，不用旧备份覆盖在线数据库。"""
    root, job = expected_root.resolve(), job.resolve()
    if not job.is_relative_to(root / "data/person-migrations"):
        raise ValueError("任务不属于当前实例")
    state = loads((job / "state.json").read_text(encoding="utf-8"))
    if state.get("version") != 2:
        raise ValueError("旧离线任务不支持在线恢复，请保留原记录单独处理")
    plan_path = job / "plan.json"
    if not plan_path.exists():
        state["status"] = "failed"
    else:
        plan = loads(plan_path.read_text(encoding="utf-8"))
        committed = []
        for database in plan["databases"]:
            path = Path(database["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("迁移数据库超出实例目录")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='person_migration_commits'").fetchone()
                committed.append(bool(exists and conn.execute("SELECT 1 FROM person_migration_commits WHERE job_id=?", (job.name,)).fetchone()))
        if any(committed) and not all(committed):
            raise ValueError("迁移被中断且参与库提交状态不一致，请根据任务记录修复；不会覆盖在线数据")
        state["status"] = "completed" if all(committed) else "failed"
    _atomic_write(job / "state.json", dumps(state))

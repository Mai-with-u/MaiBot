"""宿主停机窗口内的导出快照和可恢复导入；所有目标必须属于指定 Bot。"""
from pathlib import Path
from typing import Any, Set
from uuid import uuid4

from pydantic import ValidationError

import json
import os
import shutil
import tomlkit
import zipfile

from src.common.process_lease import ProcessLease
from src.config.shared_models import CATALOG_FILENAME, merge_catalog, read_catalog
from src.config.shared_settings import merge_retention, read_retention, strip_retention

from .config_migration import strip_host_settings
from .config_transaction import write_private
from .model_migration import plan_model_catalog
from .registry import BotRecord


def portable_config(bot: BotRecord, host: Path, staging: Path):
    """导出任务实际使用的公共定义，避免带入其他 Bot 专用的模型凭据。"""
    files, excluded = [], set()
    model_path = _target(bot, host, "config/model_config.toml")
    if model_path.exists():
        with ProcessLease(host / "model-config.lock"):
            model = merge_catalog(tomlkit.parse(model_path.read_text(encoding="utf-8")).unwrap(), read_catalog(host / CATALOG_FILENAME))
        names = set()
        def collect(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "model_list":
                        names.update(value)
                    else:
                        collect(value)
        tasks = model.get("model_task_config", {})
        for key, task in tasks.items():
            if key != "web_search":
                collect(task)
        model["models"] = [entry for entry in model["models"] if entry["name"] in names]
        providers = {entry["api_provider"] for entry in model["models"]}
        model["api_providers"] = [entry for entry in model["api_providers"] if entry["name"] in providers]
        selected = set(tasks.get("web_search", {}).get("model_list", []))
        model["web"]["services"] = {name: entry for name, entry in model["web"]["services"].items() if name in selected}
        target = staging / "model_config.toml"
        write_private(target, tomlkit.dumps(model))
        files.append((target, "config/model_config.toml", target.stat().st_size))
        excluded.add(model_path.resolve())
    bot_path = _target(bot, host, "config/bot_config.toml")
    if bot_path.exists():
        doc = merge_retention(tomlkit.parse(bot_path.read_text(encoding="utf-8")).unwrap(), read_retention(host / "config.toml"))
        target = staging / "bot_config.toml"
        write_private(target, tomlkit.dumps(doc))
        files.append((target, "config/bot_config.toml", target.stat().st_size))
        excluded.add(bot_path.resolve())
    # 历史配置副本可能含已失效的模型密钥，不作为当前实例恢复数据导出。
    excluded.add((bot.directory / "config" / "old").resolve())
    return files, excluded


def _target(bot: BotRecord, host: Path, name: str) -> Path:
    if name == "@catalog":
        return host / CATALOG_FILENAME
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or relative.parts[0] not in {"config", "data", "plugins", "logs"} or ".." in relative.parts:
        raise ValueError("导入目标不属于当前 Bot")
    target = bot.directory / relative
    if not target.resolve().is_relative_to(bot.directory.resolve()):
        raise ValueError("导入目标越过实例目录")
    # 即使链接指向实例内部，也不能让两个文件名成为同一个写入目标。
    for path in (target, *target.parents):
        if path == bot.directory.parent:
            break
        if path.is_symlink() or path.is_junction():
            raise ValueError("导入目标不能经过符号链接或目录联接")
    return target


def recover_import(bot: BotRecord, host: Path) -> None:
    journal = bot.directory / "temp" / "data-import" / "transaction.json"
    if not journal.exists():
        return
    record = json.loads(journal.read_text(encoding="utf-8"))
    work = journal.parent / record["id"]
    if not work.resolve().is_relative_to(journal.parent.resolve()):
        raise ValueError("无效的导入恢复目录")
    for index, entry in reversed(list(enumerate(record["files"]))):
        target = _target(bot, host, entry["name"])
        backup, staged = work / "backup" / str(index), work / "prepared" / str(index)
        if entry["existed"]:
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
        elif not staged.exists():
            target.unlink(missing_ok=True)
    journal.unlink()


def import_archive(bot: BotRecord, host: Path, service: Any, archive_path: Path, parts: Set[str], job: Any) -> None:
    base = bot.directory / "temp" / "data-import"
    work = base / uuid4().hex
    incoming = work / "incoming"
    incoming.mkdir(parents=True)
    # 一次只修改目标 Bot；共享目录锁覆盖读取、增量合并、提交和回滚。
    with ProcessLease(host / "model-config.lock"):
        recover_import(bot, host)
        with zipfile.ZipFile(archive_path) as archive:
            job.manifest = service._load_archive_manifest(archive)
            service._validate_archive_members(archive)
            seen = set()
            for member in archive.infolist():
                top, relative = service._safe_zip_member_path(member.filename)
                if top not in parts or not relative or member.is_dir():
                    continue
                name = f"{top}/{relative}"
                normalized = os.path.normcase(str(Path(name)))
                if normalized in seen:
                    raise ValueError(f"导入包存在重复路径: {name}")
                seen.add(normalized)
                if name.startswith("config/old/") or name.endswith(".lock"):
                    continue
                _target(bot, host, name)
                target = incoming / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        # 旧包只决定当前 Bot 的业务配置；公共网络、凭据和保留策略绝不从包覆盖。
        bot_config = incoming / "config" / "bot_config.toml"
        if bot_config.exists():
            doc = tomlkit.parse(bot_config.read_text(encoding="utf-8"))
            strip_retention(doc)
            strip_host_settings(doc)
            write_private(bot_config, tomlkit.dumps(doc))
        catalog = host / CATALOG_FILENAME
        staged_catalog = None
        if (incoming / "config" / "model_config.toml").exists():
            isolated = work / "catalog-merge"
            (isolated / "host").mkdir(parents=True)
            staged_catalog = isolated / "host" / CATALOG_FILENAME
            shutil.copyfile(catalog, staged_catalog)
            try:
                local_path = bot.directory / "config" / "model_config.toml"
                local = tomlkit.parse((incoming / "config" / "model_config.toml").read_text(encoding="utf-8"))
                shared = tomlkit.parse(staged_catalog.read_text(encoding="utf-8"))
                plan_model_catalog(shared, [bot], {local_path: local})
                write_private(staged_catalog, tomlkit.dumps(shared))
                write_private(incoming / "config" / "model_config.toml", tomlkit.dumps(local))
            except ValidationError as exc:
                details = "; ".join(item["msg"] for item in exc.errors(include_input=False, include_context=False, include_url=False))
                raise ValueError(f"导入模型配置无效: {details}") from None
        entries = [(path, path.relative_to(incoming).as_posix()) for path in incoming.rglob("*") if path.is_file() and not path.relative_to(incoming).as_posix().startswith("config/old/")]
        # 覆盖 SQLite 主文件时清除旧实例遗留的旁路日志，不能混用两个快照的 WAL。
        removals = []
        incoming_names = {name for _, name in entries}
        for source, name in entries:
            with source.open("rb") as stream:
                sqlite_file = stream.read(16) == b"SQLite format 3\x00"
            if sqlite_file:
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = name + suffix
                    if sidecar not in incoming_names and _target(bot, host, sidecar).exists():
                        removals.append((None, sidecar))
        entries.extend(removals)
        if staged_catalog is not None and staged_catalog.read_bytes() != catalog.read_bytes():
            entries.append((staged_catalog, "@catalog"))
        (work / "prepared").mkdir()
        (work / "backup").mkdir()
        records = []
        for index, (source, name) in enumerate(entries):
            target = _target(bot, host, name)
            if target.exists() and not target.is_file():
                raise ValueError(f"导入目标不是普通文件: {name}")
            if source is not None:
                shutil.copyfile(source, work / "prepared" / str(index))
            if target.exists():
                shutil.copyfile(target, work / "backup" / str(index))
            records.append({"name": name, "existed": target.exists(), "delete": source is None})
        journal = base / "transaction.json"
        write_private(journal, json.dumps({"id": work.name, "files": records}, ensure_ascii=False))
        job.total_files = len(records)
        try:
            for index, record in enumerate(records):
                target = _target(bot, host, record["name"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if record["delete"]:
                    target.unlink()
                else:
                    os.replace(work / "prepared" / str(index), target)
                job.processed_files = index + 1
                job.progress = int(100 * job.processed_files / max(1, job.total_files))
            # 保留原始文件与清单，便于用户检查；不把旧包导入任务误报为全工作区恢复。
            write_private(work / "backup-manifest.json", json.dumps(records, ensure_ascii=False))
            journal.unlink()
        except BaseException:
            recover_import(bot, host)
            raise

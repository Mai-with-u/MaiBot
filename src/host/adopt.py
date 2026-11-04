"""手动迁移旧实例：register 只登记路径；move 在同一文件系统内重命名进工作区。两者都不转换数据格式。"""

from pathlib import Path
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

import json
import os

from src.common.process_lease import ProcessLease

from .layout import MOVED_MARKER, Workspace, copy_builtin_plugin_configs, moved_target
from .registry import BotRecord, path_contains


AdoptMode = Literal["register", "move"]
_BOT_CONFIG = Path("config") / "bot_config.toml"


def _journal_path(workspace: Workspace, bot_id: str) -> Path:
    return workspace.host_directory / "migrations" / f"{bot_id}.json"


def _write_journal(path: Path, journal: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_journal(path: Path) -> Dict[str, Any]:
    journal = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(journal, dict) or journal.get("mode") not in {"register", "move"}:
        raise ValueError(f"迁移日志无效: {path}")
    return journal


def _pending_move(workspace: Workspace, source: Path) -> Optional[Dict[str, Any]]:
    directory = workspace.host_directory / "migrations"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        journal = _read_journal(path)
        if journal["mode"] == "move" and journal["step"] != "done" and Path(journal["source"]) == source:
            return journal
    return None


def _ensure_stopped(directory: Path) -> None:
    """取得实例锁即证明没有 Worker 在使用该目录；锁文件随目录移动，必须在重命名前释放。"""
    lock = directory / "temp" / "instance.lock"
    if lock.parent.is_dir():
        with ProcessLease(lock):
            pass


def _validate_source(workspace: Workspace, source: Path) -> None:
    if moved_target(source):
        raise ValueError(f"此目录已迁移到：{moved_target(source)}")
    if not (source / _BOT_CONFIG).is_file():
        raise ValueError(f"不是 MaiBot 实例目录，缺少 {source / _BOT_CONFIG}")
    if path_contains(workspace.root, source) or path_contains(source, workspace.root):
        raise ValueError("旧实例目录与工作区不能互相包含")


def adopt(workspace: Workspace, source: Path, mode: AdoptMode, name: Optional[str] = None) -> BotRecord:
    source = source.expanduser().resolve()
    if mode == "move":
        pending = _pending_move(workspace, source)
        if pending is not None:
            return _continue_move(workspace, pending)
    _validate_source(workspace, source)
    bot_name = name or source.name
    if mode == "register":
        copy_builtin_plugin_configs(source)
        record = workspace.registry.add_bot(bot_name, source, owned=False, initialization="initialized")
        _write_journal(_journal_path(workspace, record.bot_id), {
            "mode": "register", "bot_id": record.bot_id, "source": str(source), "step": "done",
        })
        return record
    bots_root = workspace.root / "bots"
    bots_root.mkdir(parents=True, exist_ok=True)
    if os.stat(source).st_dev != os.stat(bots_root).st_dev:
        raise ValueError("move 只支持同一文件系统内的重命名；跨盘请自行复制后使用 --mode register")
    bot_id = uuid4().hex
    journal = {
        "mode": "move", "bot_id": bot_id, "name": bot_name, "source": str(source),
        "target": str(bots_root / bot_id), "step": "prepared",
    }
    _write_journal(_journal_path(workspace, bot_id), journal)
    return _continue_move(workspace, journal)


def _continue_move(workspace: Workspace, journal: Dict[str, Any]) -> BotRecord:
    """每一步完成后记录进度；中断后重新执行同一命令即可从断点继续。"""
    path = _journal_path(workspace, journal["bot_id"])
    source, target = Path(journal["source"]), Path(journal["target"])
    if journal["step"] == "prepared":
        _ensure_stopped(source)
        os.rename(source, target)
        journal["step"] = "moved"
        _write_journal(path, journal)
    if journal["step"] == "moved":
        # 旧位置留下标记，防止在旧位置启动时被当成新用户初始化出一个空 Bot。
        source.mkdir(exist_ok=True)
        (source / MOVED_MARKER).write_text(str(target), encoding="utf-8")
        journal["step"] = "marked"
        _write_journal(path, journal)
    if journal["step"] == "marked":
        copy_builtin_plugin_configs(target)
        workspace.registry.add_bot(journal["name"], target, owned=True, initialization="initialized", bot_id=journal["bot_id"])
        journal["step"] = "done"
        _write_journal(path, journal)
    return workspace.registry.get_bot(journal["bot_id"])


def undo(workspace: Workspace, bot_id: str) -> None:
    """撤销 adopt：register 删除登记；move 把目录重命名回原位置。"""
    path = _journal_path(workspace, bot_id)
    if not path.is_file():
        raise ValueError("只能撤销通过 adopt 加入的 Bot")
    journal = _read_journal(path)
    if journal["step"] != "done":
        raise ValueError("迁移尚未完成；请先重新执行同一条 adopt 命令完成迁移，再撤销")
    record = workspace.registry.get_bot(bot_id)
    _ensure_stopped(record.directory)
    if journal["mode"] == "move":
        source = Path(journal["source"])
        marker = source / MOVED_MARKER
        if marker.is_file():
            marker.unlink()
        if source.exists():
            source.rmdir()  # 只允许删除空目录；用户放入了新文件时报错而不是覆盖。
        os.rename(record.directory, source)
    workspace.registry.remove_bot(bot_id)
    path.unlink()

"""Bot 模板克隆和可恢复归档；文件操作由宿主在线程池执行。"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import json
import os
import shutil

from src.common.process_lease import ProcessLease, instance_lock_path

from .layout import Workspace
from .registry import BotRecord, validate_name


def archive_bot(workspace: Workspace, bot: BotRecord) -> Dict[str, Any]:
    archive = workspace.host_directory / "archives" / bot.bot_id
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / "instance"
    # 只移动由本工作区创建且路径仍符合登记规则的 Bot，绝不移动根目录或外部目录。
    if bot.owned and (bot.directory.resolve() != (workspace.root / "bots" / bot.bot_id).resolve()
                      or not bot.directory.resolve().is_relative_to(workspace.root.resolve())):
        raise ValueError("Bot 目录不在预期的宿主管理路径，拒绝归档移动")
    if bot.directory.is_symlink() or bot.directory.is_junction():
        raise ValueError("Bot 目录不能是链接或目录联接")
    with ProcessLease(instance_lock_path(bot.directory)):
        pass  # 也拒绝被另一个宿主占用的外部登记目录。
    record = {**asdict(bot), "directory": str(bot.directory), "archive_directory": str(target) if bot.owned else None}
    journal = archive / "record.json"
    temporary = archive / "record.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, journal)
    if bot.owned:
        # 同工作区重命名；失败时保留登记。登记删除前崩溃可由 recover_archives 完成。
        bot.directory.rename(target)
    workspace.registry.remove_bot(bot.bot_id)
    return {"archived": True, "record": str(journal), "directory": str(target if bot.owned else bot.directory)}


def recover_archives(workspace: Workspace) -> None:
    for bot in workspace.registry.list_bots():
        archive = workspace.host_directory / "archives" / bot.bot_id
        if bot.owned and not bot.directory.exists() and (archive / "record.json").is_file() and (archive / "instance").is_dir():
            workspace.registry.remove_bot(bot.bot_id)


def clone_bot(workspace: Workspace, source: BotRecord, name: str) -> BotRecord:
    name = validate_name(name)
    if source.initialization != "initialized":
        raise ValueError("源 Bot 尚未初始化完成")
    with ProcessLease(instance_lock_path(source.directory)):
        pass
    identifier = uuid4().hex
    destination = workspace.root / "bots" / identifier
    if not destination.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("克隆目标超出当前工作区")
    bot = workspace.registry.add_bot(name, destination, owned=True, initialization="initializing", bot_id=identifier)
    workspace.registry.set_desired_state(identifier, "stopped")
    try:
        destination.mkdir(parents=True)
        for section in ("config", "plugins"):
            source_path = source.directory / section
            if not source_path.exists():
                if section == "config":
                    raise FileNotFoundError(source_path)
                continue
            # 不沿链接复制实例外的数据，也不复制插件仓库历史、缓存和配置备份。
            for directory, dirs, files in os.walk(source_path, followlinks=False):
                base = Path(directory)
                dirs[:] = [name for name in dirs if name not in {".git", "__pycache__", ".venv", "old"}]
                for item in [base, *(base / name for name in dirs + files)]:
                    if item.is_symlink() or item.is_junction():
                        raise ValueError(f"模板目录包含链接，无法安全复制：{item}")
            shutil.copytree(source_path, destination / section,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "old"))
        for section in ("data", "logs", "temp"):
            (destination / section).mkdir()
        return workspace.registry.set_initialization(bot.bot_id, "initialized")
    except BaseException as exc:
        workspace.registry.set_initialization(bot.bot_id, "failed", f"克隆失败：{exc}")
        raise

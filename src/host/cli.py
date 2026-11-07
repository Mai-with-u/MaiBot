"""maibot host 子命令：查询与控制运行中的宿主、创建 Bot、手动迁移旧实例。"""

from pathlib import Path
from typing import Any

import argparse
import asyncio
import json
import os
import sqlite3

from src.common.paths import INSTANCE_DIRECTORY_ENV

from .adopt import adopt, undo
from .layout import create_bot, open_workspace
from .registry import BotRecord


def add_host_command(commands: Any) -> None:
    parser = commands.add_parser("host", help="管理工作区内的 Bot")
    subcommands = parser.add_subparsers(dest="host_command", required=True)
    for command, description in (
        ("list", "列出已登记的 Bot"),
        ("create-bot", "创建新 Bot 的配置和数据目录"),
        ("adopt", "手动迁移旧实例：登记路径或在同盘内移动到工作区"),
        ("status", "查询运行中宿主内各 Bot 的状态"),
        ("open-webui", "取得 WebUI 的单次登录链接"),
        ("start", "启动指定 Bot，并记为期望运行"),
        ("stop", "停止指定 Bot，并记为期望停止"),
        ("restart", "重启指定 Bot"),
        ("plugin-deps", "列出各 Bot 缺少的插件依赖；加 --install 停止全部 Bot 后统一安装"),
        ("shutdown", "关闭宿主及全部 Bot（不改变期望状态）"),
    ):
        subparser = subcommands.add_parser(command, help=description)
        subparser.add_argument("--directory", "-d", type=Path, default=Path.cwd(), help="工作区目录，默认当前目录")
        if command == "create-bot":
            subparser.add_argument("--name", required=True, help="Bot 显示名称")
        elif command in {"start", "stop", "restart"}:
            subparser.add_argument("--bot-id", required=True, help="maibot host list 返回的 Bot ID")
        elif command == "plugin-deps":
            subparser.add_argument("--install", action="store_true", help="停止全部 Bot，安装后按期望状态恢复")
        elif command == "adopt":
            group = subparser.add_mutually_exclusive_group(required=True)
            group.add_argument("--from", dest="source", type=Path, help="旧实例目录")
            group.add_argument("--undo", dest="undo_bot_id", help="撤销一次 adopt")
            subparser.add_argument("--mode", choices=("register", "move"), default="register", help="register 只登记路径；move 同盘重命名")
            subparser.add_argument("--name", help="Bot 显示名称，默认使用目录名")


def _record_payload(bot: BotRecord) -> dict:
    return {
        "bot_id": bot.bot_id, "name": bot.name, "directory": str(bot.directory), "owned": bot.owned,
        "initialization": bot.initialization, "error": bot.error,
        "desired_state": bot.desired_state, "restart_policy": bot.restart_policy,
    }


def run_host_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    root = args.directory.expanduser().resolve()
    os.environ[INSTANCE_DIRECTORY_ENV] = str(root)
    try:
        if args.host_command in {"status", "start", "stop", "restart", "shutdown", "open-webui", "plugin-deps"}:
            from .control import control_request

            bot_id = args.bot_id if args.host_command in {"start", "stop", "restart"} else None
            action = args.host_command.replace("-", "_")
            if args.host_command == "plugin-deps":
                action = "install_plugin_dependencies" if args.install else "plugin_dependencies"
            print(json.dumps(asyncio.run(control_request(root, action, bot_id)), ensure_ascii=False, indent=2))
            return 0
        workspace = open_workspace(root)
        if args.host_command == "list":
            print(json.dumps([_record_payload(bot) for bot in workspace.registry.list_bots()], ensure_ascii=False, indent=2))
        elif args.host_command == "create-bot":
            print(json.dumps(_record_payload(create_bot(workspace, args.name)), ensure_ascii=False, indent=2))
        elif args.undo_bot_id is not None:
            undo(workspace, args.undo_bot_id)
            print(f"已撤销 {args.undo_bot_id}")
        else:
            print(json.dumps(_record_payload(adopt(workspace, args.source, args.mode, args.name)), ensure_ascii=False, indent=2))
    except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    return 0

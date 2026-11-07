"""MaiBot 唯一的启动入口：确定工作区布局，在终端完成升级确认，然后运行宿主。"""

from pathlib import Path
from typing import Set

import asyncio
import os
import sys

from src.common.paths import INSTANCE_DIRECTORY_ENV
from src.config.shared_models import CATALOG_FILENAME, SHARED_MODELS_ENV
from src.config.legacy_upgrade_confirmation import LEGACY_UPGRADE_CONFIRM_ENV, collect_legacy_upgrade_reasons

from .layout import Workspace, open_workspace
from .launcher import UPDATE_REQUESTED


def confirm_upgrades(workspace: Workspace) -> Set[str]:
    """0.x 升级需要真实终端确认，必须在派生无控制台的 Worker 之前完成。

    无法交互的部署（如 Docker）用 MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED=1 预先确认全部 Bot。
    """
    confirmed: Set[str] = set()
    preconfirmed = os.environ.get(LEGACY_UPGRADE_CONFIRM_ENV) == "1"
    for bot in workspace.registry.list_bots():
        if bot.desired_state != "running" or bot.initialization != "initialized":
            continue
        reasons = collect_legacy_upgrade_reasons(bot.directory)
        if not reasons:
            continue
        if preconfirmed:
            confirmed.add(bot.bot_id)
            continue
        print()
        print("=" * 72)
        print(f"MaiBot 升级提示：{bot.name}（{bot.directory}）")
        print("检测到此 Bot 可能是从 1.0.0 以前的 0.x.x 版本升级而来。")
        print("继续启动将会执行自动升级，可能包括数据库结构更新和配置文件更新。")
        print("建议在继续前备份 data/ 与 config/ 目录。")
        print()
        for reason in reasons:
            print(f"- {reason}")
        print("=" * 72)
        try:
            answer = input("确认继续升级并启动这个 Bot 吗？请输入 y 后回车：").strip().lower()
        except EOFError:
            answer = ""
        if answer == "y":
            confirmed.add(bot.bot_id)
        else:
            print(f"未确认升级，本次不启动 {bot.name}。")
    return confirmed


def main(root: Path) -> int:
    os.environ[INSTANCE_DIRECTORY_ENV] = str(root.resolve())
    # 仅宿主入口提供此标记，Bot 不得自行升级共享环境。
    os.environ["MAIBOT_PACKAGE_RUNNER"] = "1"
    workspace = open_workspace(root)
    os.environ[SHARED_MODELS_ENV] = str(workspace.host_directory / CATALOG_FILENAME)
    confirmed = confirm_upgrades(workspace)
    from .update_locks import register_host_instance
    from .control import run_host

    with register_host_instance(workspace.root):
        update_requested = asyncio.run(run_host(workspace, upgrade_confirmed=confirmed))
    # 先完整退出并释放模块，再由稳定启动器启动更新阶段；Windows execv 会提前退出父 PID。
    return UPDATE_REQUESTED if update_requested else 0


if __name__ == "__main__":
    try:
        code = main(Path(sys.argv[1]))
    except (RuntimeError, ValueError) as exc:
        print(f"宿主启动失败：{exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)

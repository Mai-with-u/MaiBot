"""maibot 命令入口：run 运行宿主，init 生成单个 Bot 的配置，host 管理工作区。"""

from pathlib import Path
from typing import Optional, Sequence

import argparse
import os
import sys

from src.common.paths import INSTANCE_DIRECTORY_ENV
from src.common.version import read_project_version


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="maibot", description="MaiBot 工作区运行与管理")
    parser.add_argument("--version", action="version", version=read_project_version())
    commands = parser.add_subparsers(dest="command", required=True)
    for command, description in (("run", "运行宿主及工作区内的全部 Bot"), ("init", "在目录中生成 Bot 配置（保留已有配置）")):
        subparser = commands.add_parser(command, help=description)
        subparser.add_argument("--directory", "-d", type=Path, default=Path.cwd(), help="工作区或 Bot 目录，默认当前目录")
    # run 的父进程只加载标准库，避免更新期间持有被替换的扩展模块。
    if argv and argv[0] == "host":
        from src.host.cli import add_host_command, run_host_command

        add_host_command(commands)
    else:
        commands.add_parser("host", help="管理工作区内的 Bot")
    args = parser.parse_args(argv)
    if args.command == "host":
        # host 子命令使用工作区路径，单 Bot 的配置在独立进程中加载。
        return run_host_command(args, parser)
    directory = args.directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if args.command == "run":
        # 宿主按目录内容决定布局：已有配置原地运行，空目录创建新工作区。
        from src.host.launcher import main as run_host

        try:
            return run_host(directory)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    os.environ[INSTANCE_DIRECTORY_ENV] = str(directory)
    os.chdir(directory)
    # 只复用配置生成器，已有配置的迁移留到正常启动时处理。
    os.environ["MAIBOT_INITIALIZE_ONLY"] = "1"
    from src.config.config import (
        BOT_CONFIG_PATH,
        CONFIG_VERSION,
        MODEL_CONFIG_PATH,
        MODEL_CONFIG_VERSION,
        Config,
        ModelConfig,
        generate_new_config_file,
    )

    for config_class, path, version, override in (
        (Config, BOT_CONFIG_PATH, CONFIG_VERSION, False),
        (ModelConfig, MODEL_CONFIG_PATH, MODEL_CONFIG_VERSION, True),
    ):
        if not path.exists():
            generate_new_config_file(config_class, path, version, override)

    for name in ("data", "logs", "plugins", "temp"):
        (directory / name).mkdir(exist_ok=True)
    print(f"MaiBot 实例已初始化：{directory}")
    print(f"配置文件：{BOT_CONFIG_PATH}、{MODEL_CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

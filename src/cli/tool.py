"""安装版命令入口，在加载业务模块前确定实例目录。"""

from pathlib import Path
from typing import Optional, Sequence

import argparse
import os
import runpy
import sys

from src.common.paths import INSTANCE_DIRECTORY_ENV, PACKAGED_INSTALL
from src.common.version import read_project_version


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="maibot", description="MaiBot 实例初始化与运行")
    parser.add_argument("--version", action="version", version=read_project_version())
    commands = parser.add_subparsers(dest="command", required=True)
    for command, description in (("init", "初始化实例（保留已有配置）"), ("run", "运行实例")):
        subparser = commands.add_parser(command, help=description)
        subparser.add_argument("--directory", "-d", type=Path, default=Path.cwd(), help="实例目录，默认当前目录")
    args = parser.parse_args(argv)
    directory = args.directory.expanduser().resolve()
    if args.command == "run":
        missing = [name for name in ("bot_config.toml", "model_config.toml") if not (directory / "config" / name).is_file()]
        if missing:
            parser.error(f"实例未初始化，缺少 {', '.join(missing)}；请先执行 maibot init --directory \"{directory}\"")
    directory.mkdir(parents=True, exist_ok=True)
    os.environ[INSTANCE_DIRECTORY_ENV] = str(directory)
    os.chdir(directory)
    if args.command == "init":
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
        print("填写模型配置后，执行 maibot run --directory <实例目录>")
        return 0
    os.environ.pop("MAIBOT_INITIALIZE_ONLY", None)
    if PACKAGED_INSTALL and (Path(sys.prefix) / "uv-receipt.toml").is_file():
        from src.common.package_update import PACKAGE_RUNNER_ENV, register_instance

        # 只有最外层 CLI 持有租约；Runner/Worker 重启沿用实例环境。
        with register_instance(directory):
            os.environ[PACKAGE_RUNNER_ENV] = "1"
            runpy.run_module("src.runtime", run_name="__main__")
    else:
        runpy.run_module("src.runtime", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

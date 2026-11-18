"""工作区布局：首次启动判定新旧用户，之后以注册表为准。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import json
import os
import shutil
import subprocess
import sys
import tomllib


from src.common.logger import get_logger
from src.common.paths import INSTANCE_DIRECTORY_ENV, PACKAGED_INSTALL, SOURCE_ROOT
from src.common.process_lease import ProcessLease
from src.common.runtime_context import RUNTIME_CONTEXT_ENV
from src.config.shared_models import SHARED_MODELS_ENV
from src.config.legacy_upgrade_confirmation import LEGACY_UPGRADE_CONFIRM_ENV

from .config_transaction import write_private
from .config import HostConfig, config_from_bot, load_host_config, save_host_config
from .registry import BotRecord, Registry
from .legacy_runner import LEGACY_PACKAGE_INSTANCE_ENV


MOVED_MARKER = "MOVED_TO_WORKSPACE.txt"
_BOT_CONFIG = Path("config") / "bot_config.toml"
logger = get_logger("host.layout")


@dataclass(frozen=True)
class Workspace:
    root: Path
    registry: Registry
    config: HostConfig
    webui_migration_error: str = ""

    @property
    def host_directory(self) -> Path:
        return self.root / "host"


def moved_target(directory: Path) -> str:
    """adopt --mode move 之后的旧目录不能再被当作空目录初始化成新 Bot。"""
    marker = directory / MOVED_MARKER
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    return ""


def open_workspace(root: Path) -> Workspace:
    root = root.expanduser().resolve()
    if target := moved_target(root):
        raise RuntimeError(f"此目录的 Bot 已移动到宿主工作区，请在新位置启动：{target}")
    (root / "host").mkdir(parents=True, exist_ok=True)
    # 运行中的宿主持有同一租约；恢复与迁移不能触碰它正在写入的文件。
    with ProcessLease(root / "host" / "run.lock"):
        registry = Registry(root)
        if not registry.exists():
            if (root / _BOT_CONFIG).is_file():
                _adopt_in_place(root, registry)
            else:
                _create_fresh(root, registry)
        from .bot_operations import recover_archives

        recover_archives(Workspace(root=root, registry=registry, config=load_host_config(root)))
        from .transfer_storage import recover_import

        webui_migration_error = ""
        for bot in registry.list_bots():
            recover_import(bot, root / "host")
            credentials = root / "host" / "webui.json" if bot.directory == root else None
            try:
                migrate_legacy_webui_data(bot.directory, credentials)
            except (OSError, ValueError):
                legacy = bot.directory / "data" / "webui.json"
                logger.exception(f"旧版 WebUI 设置迁移失败，已保留原文件：{legacy}")
                if credentials is not None and not credentials.exists():
                    webui_migration_error = f"旧版 WebUI 凭据未能迁移，请检查宿主日志：{legacy}"
        from .workspace_migration import migrate_workspace_config

        migrate_workspace_config(root, registry.list_bots())
        return Workspace(
            root=root, registry=registry, config=load_host_config(root), webui_migration_error=webui_migration_error,
        )


def migrate_legacy_webui_data(directory: Path, credentials: Optional[Path]) -> None:
    """Move the Bot preference and, for an in-place upgrade, retain the host login."""
    legacy = directory / "data" / "webui.json"
    if not legacy.is_file():
        return
    data = json.loads(legacy.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid legacy WebUI settings: {legacy}")

    preference = legacy.with_name("adapter_config_path.txt")
    adapter_path = data.get("adapter_config_path")
    if adapter_path is not None and not isinstance(adapter_path, str):
        raise ValueError(f"Invalid legacy adapter configuration path: {legacy}")
    if adapter_path and not preference.exists():
        write_private(preference, adapter_path)

    credential_keys = {
        "access_token", "token_source", "first_setup_completed", "created_at", "updated_at", "setup_completed_at",
    }
    if credentials is not None and not credentials.exists() and "access_token" in data:
        if not isinstance(data["access_token"], str) or not data["access_token"]:
            raise ValueError(f"Invalid legacy WebUI credentials: {legacy}")
        write_private(credentials, json.dumps({key: data[key] for key in data if key in credential_keys}))

    # Old mirror settings have no readers. Keep the source if it contains any unrecognized keys.
    if data.keys() <= credential_keys | {"adapter_config_path", "git_mirrors", "git_mirrors_schema_version"}:
        legacy.unlink()


def _adopt_in_place(root: Path, registry: Registry) -> None:
    """旧用户：工作区根目录原地登记为 Bot，不移动、不复制、不转换数据。"""
    # 每一步都可重复执行；注册表最后创建，中断后再次启动会从头完成。
    bot_config = root / _BOT_CONFIG
    if not (root / "host" / "config.toml").exists():
        save_host_config(root, config_from_bot(bot_config))
    copy_builtin_plugin_configs(root)
    with bot_config.open("rb") as stream:
        nickname = tomllib.load(stream).get("bot", {}).get("nickname", "")
    name = nickname.strip()[:100] if isinstance(nickname, str) and nickname.strip() else "MaiBot"
    registry.create()
    registry.add_bot(name, root, owned=False, initialization="initialized")


def _create_fresh(root: Path, registry: Registry) -> None:
    """新用户：在 bots/<id>/ 下创建第一个 Bot。"""
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "host" / "config.toml").exists():
        save_host_config(root, HostConfig())
    registry.create()
    create_bot(Workspace(root=root, registry=registry, config=load_host_config(root)), "MaiBot")


def copy_builtin_plugin_configs(directory: Path) -> None:
    """只迁入该旧部署自身的内置插件配置；不能从当前宿主的程序目录复制用户设置。"""
    builtin_root = directory / "src" / "plugins" / "built_in"
    for source in sorted(builtin_root.glob("*/config.toml")):
        target = directory / "config" / "builtin_plugins" / source.parent.name / "config.toml"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def bot_environment(directory: Path) -> Dict[str, str]:
    """在某个 Bot 的实例目录中运行程序代码的环境；路径与配置模块在导入时固定实例目录。"""
    env = os.environ.copy()
    for key in (
        RUNTIME_CONTEXT_ENV, LEGACY_UPGRADE_CONFIRM_ENV, SHARED_MODELS_ENV,
        "MAIBOT_PACKAGE_RUNNER", LEGACY_PACKAGE_INSTANCE_ENV,
    ):
        env.pop(key, None)
    env[INSTANCE_DIRECTORY_ENV] = str(directory)
    if not PACKAGED_INSTALL:
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) + (os.pathsep + pythonpath if pythonpath else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_in_bot(directory: Path, module_args: List[str], *, timeout: float, purpose: str, shared_catalog: Optional[Path] = None) -> None:
    """在独立进程中以该 Bot 的实例目录执行一段程序代码；失败时附带完整输出。"""
    env = bot_environment(directory)
    if shared_catalog is not None:
        env[SHARED_MODELS_ENV] = str(shared_catalog)
    result = subprocess.run(
        [sys.executable, "-m", *module_args], cwd=directory, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{purpose}失败，退出码 {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


def _run_initializer(directory: Path) -> None:
    """在独立进程中生成配置：配置模块导入时固定实例路径，不能在宿主进程里加载。"""
    run_in_bot(directory, ["src.host.initialize", str(directory.parent.parent / "host")], timeout=120, purpose="Bot 配置初始化")


def create_bot(workspace: Workspace, name: str) -> BotRecord:
    """先登记再初始化；失败时保留记录和目录，原因写入注册表。"""
    bot_id = uuid4().hex
    directory = workspace.root / "bots" / bot_id
    record = workspace.registry.add_bot(name, directory, owned=True, initialization="initializing", bot_id=bot_id)
    try:
        directory.mkdir(parents=True)
        _run_initializer(directory)
    except Exception as exc:
        workspace.registry.set_initialization(bot_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    return workspace.registry.set_initialization(record.bot_id, "initialized")

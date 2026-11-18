"""读取和保存 /pm 的实例级快捷别名。"""

from pathlib import Path
from threading import Lock
from typing import Any, Dict

import shlex
import tomlkit

from src.common.paths import get_instance_root
from src.webui.utils.toml_utils import save_toml_with_format

PM_COMMAND_ID = "core.pm"
PM_PATTERN = r"^/pm(?:\s+.+)?\s*$"
_ALIAS_LOCK = Lock()

def _config_path() -> Path:
    return get_instance_root() / "config" / "commands" / "pm.toml"


def _read_config() -> Dict[str, Any]:
    try:
        with _config_path().open("r", encoding="utf-8") as config_file:
            return tomlkit.load(config_file)
    except FileNotFoundError:
        return {}


def load_aliases() -> Dict[str, str]:
    with _ALIAS_LOCK:
        config = _read_config()
    config.setdefault("aliases", {}).setdefault("management_prefixes", ["/插件管理"])
    return CommandAliases._build_alias_mapping_from_config(config)


def update_shortcut(alias: str, target: str | None) -> bool:
    with _ALIAS_LOCK:
        config = _read_config()
        shortcuts = config.setdefault("aliases", {}).setdefault("shortcuts", {})
        if target is None:
            if alias not in shortcuts:
                return False
            del shortcuts[alias]
        else:
            shortcuts[alias] = target
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_toml_with_format(config, str(path))
        return True


class CommandAliases:
    def resolve(self, raw_command: str) -> str:
        normalized_command = raw_command.strip()
        if not normalized_command or self._is_pm_command(normalized_command):
            return normalized_command

        alias_mapping = load_aliases()
        for alias, target in sorted(alias_mapping.items(), key=lambda item: len(item[0]), reverse=True):
            if not self._command_startswith_alias(normalized_command, alias):
                continue
            suffix = normalized_command[len(alias) :].strip()
            return f"{target} {suffix}".strip()
        return normalized_command

    @staticmethod
    def _build_alias_mapping_from_config(config: dict[str, object]) -> dict[str, str]:
        aliases_config = config.get("aliases") if isinstance(config, dict) else None
        if not isinstance(aliases_config, dict):
            return {}

        mapping: dict[str, str] = {}
        prefixes = aliases_config.get("management_prefixes", [])
        if isinstance(prefixes, list):
            for alias in prefixes:
                normalized_alias = str(alias).strip()
                if normalized_alias:
                    mapping[normalized_alias] = "/pm"

        shortcuts = aliases_config.get("shortcuts", {})
        if isinstance(shortcuts, dict):
            for alias, target in shortcuts.items():
                normalized_alias = str(alias).strip()
                normalized_target = CommandAliases._normalize_alias_target(str(target))
                if normalized_alias and CommandAliases._is_pm_command(normalized_target):
                    mapping[normalized_alias] = normalized_target

        return mapping

    @staticmethod
    def _command_startswith_alias(command: str, alias: str) -> bool:
        if not command.startswith(alias):
            return False
        return len(command) == len(alias) or command[len(alias)].isspace()

    @staticmethod
    def _normalize_alias_target(raw_target: str) -> str:
        return " ".join(CommandAliases._split_command(str(raw_target).strip()))

    @staticmethod
    def _is_pm_command(command: str) -> bool:
        return CommandAliases._command_startswith_alias(command, "/pm")

    @staticmethod
    def _validate_alias_shortcut(alias: str, target: str) -> str:
        if not alias:
            return "别名不能为空"
        if not alias.startswith("/"):
            return "别名必须以 / 开头"
        if any(char.isspace() for char in alias):
            return "别名不能包含空格"
        if alias == "/pm":
            return "不能把 /pm 本身设置为别名"
        if not target:
            return "目标命令不能为空"
        if not CommandAliases._is_pm_command(target):
            return "目标命令必须以 /pm 开头"
        return ""

    @staticmethod
    def _split_command(raw_command: str) -> list[str]:
        try:
            return shlex.split(raw_command)
        except ValueError:
            return raw_command.split()

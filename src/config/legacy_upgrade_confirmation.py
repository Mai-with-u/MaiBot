from pathlib import Path

import os
import re
import sqlite3
import sys

from src.common.paths import PACKAGE_ROOT

LEGACY_UPGRADE_CONFIRM_ENV = "MAIBOT_LEGACY_0X_UPGRADE_CONFIRMED"
LEGACY_0X_BOT_CONFIG_BOUNDARY = "8.9.4"
LEGACY_0X_MODEL_CONFIG_BOUNDARY = "1.14.1"


def _parse_semver(version: str) -> tuple[int, int, int] | None:
    """解析三段式版本号。"""
    parts = version.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(part) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def _read_config_constant(project_root: Path, name: str) -> str | None:
    """从配置模块源码读取版本常量，避免提前触发配置加载和自动迁移。"""
    config_source_path = PACKAGE_ROOT / "config" / "config.py"
    try:
        config_source = config_source_path.read_text(encoding="utf-8")
    except OSError:
        return None

    pattern = rf'^{re.escape(name)}:\s*str\s*=\s*"([^"]+)"'
    match = re.search(pattern, config_source, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1)


def _read_inner_config_version(config_path: Path) -> str | None:
    """读取配置文件 [inner].version，失败时返回 None。"""
    if not config_path.exists():
        return None

    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    in_inner_table = False
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_inner_table = line == "[inner]"
            continue
        if not in_inner_table:
            continue
        match = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if match is not None:
            return match.group(1)
    return None


def _is_version_lower(current_version: str | None, target_version: str | None) -> bool:
    current_parts = _parse_semver(current_version or "")
    target_parts = _parse_semver(target_version or "")
    if current_parts is None or target_parts is None:
        return False
    return current_parts < target_parts


def _needs_legacy_config_confirmation(
    current_version: str | None,
    target_version: str | None,
    legacy_boundary_version: str,
) -> bool:
    """判断配置更新是否属于 0.x -> 1.0.0 边界升级。"""
    return _is_version_lower(current_version, legacy_boundary_version) and _is_version_lower(
        current_version,
        target_version,
    )


def _load_sqlite_schema(db_path: Path) -> tuple[int, dict[str, set[str]]] | None:
    """读取 SQLite user_version 与用户表列名，不创建新数据库文件。"""
    if not db_path.exists():
        return None

    database_uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            user_version_row = connection.execute("PRAGMA user_version").fetchone()
            if user_version_row is None:
                return None

            table_rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
            tables: dict[str, set[str]] = {}
            for (table_name,) in table_rows:
                table_name = str(table_name)
                escaped_table_name = table_name.replace('"', '""')
                column_rows = connection.execute(f'PRAGMA table_info("{escaped_table_name}")').fetchall()
                tables[table_name] = {str(row[1]) for row in column_rows}
    except sqlite3.Error:
        return None

    return int(user_version_row[0]), tables


def _detect_legacy_0x_database(db_path: Path) -> bool:
    """检测旧版 0.x 数据库结构。"""
    schema = _load_sqlite_schema(db_path)
    if schema is None:
        return False

    user_version, tables = schema
    if user_version == 1:
        return True
    if user_version > 1 or not tables:
        return False

    legacy_exclusive_tables = {
        "chat_streams",
        "emoji",
        "emoji_description_cache",
        "expression",
        "group_info",
        "image_descriptions",
        "jargon",
        "messages",
        "thinking_back",
    }
    if legacy_exclusive_tables.intersection(tables):
        return True

    legacy_shared_markers = (
        ("action_records", ("chat_id", "time")),
        ("chat_history", ("chat_id", "original_text")),
        ("images", ("emoji_hash", "path", "type")),
        ("llm_usage", ("model_api_provider", "status")),
        ("online_time", ("duration",)),
        ("person_info", ("nickname", "group_nick_name")),
    )
    for table_name, required_columns in legacy_shared_markers:
        table_columns = tables.get(table_name)
        if table_columns is not None and all(column_name in table_columns for column_name in required_columns):
            return True
    return False


def collect_legacy_upgrade_reasons(project_root: Path) -> list[str]:
    """收集启动前需要用户确认的 0.x 升级风险项。"""
    reasons: list[str] = []

    db_path = project_root / "data" / "MaiBot.db"
    if _detect_legacy_0x_database(db_path):
        reasons.append(f"检测到旧版 0.x 数据库结构，将更新数据库：{db_path}")

    bot_config_path = project_root / "config" / "bot_config.toml"
    bot_config_version = _read_inner_config_version(bot_config_path)
    target_bot_config_version = _read_config_constant(project_root, "CONFIG_VERSION")
    if _needs_legacy_config_confirmation(
        bot_config_version,
        target_bot_config_version,
        LEGACY_0X_BOT_CONFIG_BOUNDARY,
    ):
        reasons.append(
            "检测到主配置文件版本较旧，将更新配置文件："
            f"{bot_config_path} ({bot_config_version} -> {target_bot_config_version})"
        )

    model_config_path = project_root / "config" / "model_config.toml"
    model_config_version = _read_inner_config_version(model_config_path)
    target_model_config_version = _read_config_constant(project_root, "MODEL_CONFIG_VERSION")
    if _needs_legacy_config_confirmation(
        model_config_version,
        target_model_config_version,
        LEGACY_0X_MODEL_CONFIG_BOUNDARY,
    ):
        reasons.append(
            "检测到模型配置文件版本较旧，将更新配置文件："
            f"{model_config_path} ({model_config_version} -> {target_model_config_version})"
        )

    return reasons


def require_legacy_upgrade_confirmation(project_root: Path) -> None:
    """在执行 0.x 升级迁移前要求用户显式确认。"""
    if os.getenv(LEGACY_UPGRADE_CONFIRM_ENV) == "1":
        return

    reasons = collect_legacy_upgrade_reasons(project_root)
    if not reasons:
        return

    print()
    print("=" * 72)
    print("MaiBot 升级提示")
    print("检测到当前实例可能是从 1.0.0 以前的 0.x.x 版本升级而来。")
    print("继续启动将会执行自动升级，可能包括数据库结构更新和配置文件更新。")
    print("建议在继续前备份 data/ 与 config/ 目录。")
    print()
    for reason in reasons:
        print(f"- {reason}")
    print("=" * 72)

    try:
        user_input = input("确认继续升级并启动吗？请输入 y 后回车：").strip().lower()
    except EOFError:
        user_input = ""
    if user_input != "y":
        print("未确认升级，启动已取消。")
        sys.exit(1)

    os.environ[LEGACY_UPGRADE_CONFIRM_ENV] = "1"

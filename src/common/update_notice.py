from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import json
import re
import tempfile

from src.common.logger import get_logger
from src.common.version import PROJECT_ROOT, read_project_version


logger = get_logger("update_notice")

NoticeChannel = Literal["terminal", "webui"]
StateValue = str | int

_DATA_DIR = PROJECT_ROOT / "data"
_STATE_PATH = _DATA_DIR / "update_notice_state.json"
_CHANGELOG_PATH = PROJECT_ROOT / "changelogs" / "changelog.md"
_VERSION_HEADING_RE = re.compile(r"^# \[(?P<version>[^\]]+)\](?P<suffix>[^\n]*)$", re.MULTILINE)
_TERMINAL_NOTICE_REPEAT_COUNT = 3
_TERMINAL_NOTICE_VERSION_KEY = "terminal_notice_version"
_TERMINAL_NOTICE_COUNT_KEY = "terminal_notice_count"
_CHANNEL_STATE_KEYS: dict[NoticeChannel, str] = {
    "terminal": "terminal_notified_version",
    "webui": "webui_ack_version",
}


@dataclass(frozen=True)
class ChangelogEntry:
    version: str
    title: str
    markdown: str


@dataclass(frozen=True)
class UpdateNotice:
    current_version: str
    from_version: str
    versions: list[str]
    content: str


def _normalize_version(version: str) -> str:
    normalized = version.strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return normalized


def _version_key(version: str) -> tuple[tuple[int, ...], int, tuple[tuple[int, int | str], ...]]:
    """生成可排序版本键，支持 1.0.10 与 1.0.0-rc.5 这类 changelog 版本。"""

    normalized = _normalize_version(version)
    core, separator, prerelease = normalized.partition("-")
    numeric_parts = tuple(int(part) for part in re.findall(r"\d+", core))
    prerelease_rank = 1 if not separator else 0
    prerelease_parts: list[tuple[int, int | str]] = []
    for part in re.split(r"[.\-_]", prerelease):
        if not part:
            continue
        if part.isdigit():
            prerelease_parts.append((0, int(part)))
        else:
            prerelease_parts.append((1, part.casefold()))
    return numeric_parts, prerelease_rank, tuple(prerelease_parts)


def _is_version_newer(left: str, right: str) -> bool:
    return _version_key(left) > _version_key(right)


def _is_version_in_range(version: str, from_version: str, current_version: str) -> bool:
    return _is_version_newer(version, from_version) and not _is_version_newer(version, current_version)


def _read_json_state(state_path: Path = _STATE_PATH) -> dict[str, StateValue]:
    if not state_path.exists():
        return {}
    try:
        with state_path.open("r", encoding="utf-8") as state_file:
            data = json.load(state_file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"读取更新公告状态失败，将重新初始化: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, (str, int))}


def _write_json_state(state: dict[str, StateValue], state_path: Path = _STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        delete=False,
        prefix=f"{state_path.name}.",
        suffix=".tmp",
    ) as temp_file:
        json.dump(state, temp_file, ensure_ascii=False, indent=2)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)
    temp_path.replace(state_path)


def _get_state_text(state: dict[str, StateValue], key: str) -> str | None:
    value = state.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _get_state_int(state: dict[str, StateValue], key: str, default: int = 0) -> int:
    value = state.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return default


def _initialize_state_for_first_run(
    current_version: str,
    state_path: Path = _STATE_PATH,
) -> dict[str, StateValue] | None:
    if state_path.exists():
        return None

    state = {
        "terminal_notified_version": current_version,
        _TERMINAL_NOTICE_VERSION_KEY: current_version,
        _TERMINAL_NOTICE_COUNT_KEY: _TERMINAL_NOTICE_REPEAT_COUNT,
        "webui_ack_version": current_version,
    }
    _write_json_state(state, state_path)
    return state


def parse_changelog_entries(changelog_path: Path = _CHANGELOG_PATH) -> list[ChangelogEntry]:
    """按 changelog 标题格式读取版本块，保留每个版本的 Markdown 原文。"""

    try:
        changelog_text = changelog_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"读取更新日志失败: {changelog_path}, error={exc}")
        return []

    matches = list(_VERSION_HEADING_RE.finditer(changelog_text))
    entries: list[ChangelogEntry] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog_text)
        block = changelog_text[start:end].strip()
        if not block:
            continue
        title = match.group(0).strip()
        version = match.group("version").strip()
        entries.append(ChangelogEntry(version=version, title=title, markdown=block))
    return entries


def build_update_notice(
    from_version: str,
    current_version: str,
    changelog_path: Path = _CHANGELOG_PATH,
) -> UpdateNotice:
    entries = parse_changelog_entries(changelog_path)
    selected_entries = [
        entry for entry in entries if _is_version_in_range(entry.version, from_version, current_version)
    ]
    selected_entries.sort(key=lambda entry: _version_key(entry.version), reverse=True)

    if selected_entries:
        blocks = "\n\n".join(entry.markdown for entry in selected_entries)
        content = f"# 麦麦已从 v{from_version} 更新到 v{current_version}\n\n{blocks}"
        return UpdateNotice(
            current_version=current_version,
            from_version=from_version,
            versions=[entry.version for entry in selected_entries],
            content=content,
        )

    content = (
        f"# 麦麦已从 v{from_version} 更新到 v{current_version}\n\n"
        "未在 `changelogs/changelog.md` 中找到对应版本的更新日志条目。"
    )
    return UpdateNotice(current_version=current_version, from_version=from_version, versions=[], content=content)


def get_pending_update_notice(
    channel: NoticeChannel,
    current_version: str | None = None,
    state_path: Path = _STATE_PATH,
    changelog_path: Path = _CHANGELOG_PATH,
) -> UpdateNotice | None:
    current = current_version or read_project_version(PROJECT_ROOT)
    initialized_state = _initialize_state_for_first_run(current, state_path)
    if initialized_state is not None:
        return None

    state = _read_json_state(state_path)
    if channel == "terminal":
        completed_version = _get_state_text(state, _CHANNEL_STATE_KEYS["terminal"])
        if not completed_version:
            state[_CHANNEL_STATE_KEYS["terminal"]] = current
            state[_TERMINAL_NOTICE_VERSION_KEY] = current
            state[_TERMINAL_NOTICE_COUNT_KEY] = _TERMINAL_NOTICE_REPEAT_COUNT
            _write_json_state(state, state_path)
            return None
        if not _is_version_newer(current, completed_version):
            return None

        notice_version = _get_state_text(state, _TERMINAL_NOTICE_VERSION_KEY)
        notice_count = _get_state_int(state, _TERMINAL_NOTICE_COUNT_KEY)
        if notice_version != current:
            notice_count = 0
        if notice_count >= _TERMINAL_NOTICE_REPEAT_COUNT:
            state[_CHANNEL_STATE_KEYS["terminal"]] = current
            _write_json_state(state, state_path)
            return None
        return build_update_notice(completed_version, current, changelog_path)

    state_key = _CHANNEL_STATE_KEYS[channel]
    from_version = _get_state_text(state, state_key)
    if not from_version:
        state[state_key] = current
        _write_json_state(state, state_path)
        return None
    if not _is_version_newer(current, from_version):
        return None
    return build_update_notice(from_version, current, changelog_path)


def mark_update_notice_seen(
    channel: NoticeChannel,
    current_version: str | None = None,
    state_path: Path = _STATE_PATH,
) -> None:
    current = current_version or read_project_version(PROJECT_ROOT)
    state = _read_json_state(state_path)
    if channel == "terminal":
        notice_version = _get_state_text(state, _TERMINAL_NOTICE_VERSION_KEY)
        notice_count = _get_state_int(state, _TERMINAL_NOTICE_COUNT_KEY)
        if notice_version != current:
            notice_count = 0
        notice_count += 1
        state[_TERMINAL_NOTICE_VERSION_KEY] = current
        state[_TERMINAL_NOTICE_COUNT_KEY] = notice_count
        if notice_count >= _TERMINAL_NOTICE_REPEAT_COUNT:
            state[_CHANNEL_STATE_KEYS["terminal"]] = current
        _write_json_state(state, state_path)
        return

    state[_CHANNEL_STATE_KEYS[channel]] = current
    _write_json_state(state, state_path)


def emit_terminal_update_notice_if_needed() -> None:
    notice = get_pending_update_notice("terminal")
    if notice is None:
        return

    logger.warning(
        f"更新公告\n"
        f"{'=' * 48}\n"
        f"{notice.content}\n"
        f"{'=' * 48}"
    )
    mark_update_notice_seen("terminal", notice.current_version)

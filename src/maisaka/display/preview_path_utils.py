"""Maisaka Prompt 预览路径工具。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import os
import re

from src.chat.message_receive.chat_manager import chat_manager


REPO_ROOT = Path(__file__).parent.parent.parent.parent.absolute().resolve()
SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_preview_name(value: str) -> str:
    normalized_value = SAFE_NAME_PATTERN.sub("_", str(value or "").strip()).strip("._")
    if normalized_value:
        return normalized_value
    return "unknown"


def normalize_platform_name(platform: str) -> str:
    normalized_platform = str(platform or "").strip().lower()
    platform_aliases = {
        "telegram": "tg",
    }
    return normalize_preview_name(platform_aliases.get(normalized_platform, normalized_platform))


def build_preview_chat_dir_name(chat_id: str) -> str:
    session = chat_manager.get_session_by_session_id(chat_id)
    if session is not None:
        platform = normalize_platform_name(session.platform)
        if session.is_group_session and session.group_id:
            return f"{platform}_group_{normalize_preview_name(session.group_id)}"
        if session.user_id:
            return f"{platform}_private_{normalize_preview_name(session.user_id)}"

    normalized_chat_id = normalize_preview_name(chat_id)
    if normalized_chat_id != "unknown":
        return normalized_chat_id
    return "unknown_chat"


def build_display_path(file_path: Path) -> str:
    """构造用于展示的路径，项目内文件优先显示相对路径。

    这里只做路径字符串规范化，不做 realpath：Windows 上的 realpath 需要访问
    文件系统，而预览相关路径本身已是绝对路径，解析符号链接对展示没有意义。
    """
    absolute_path = Path(os.path.abspath(file_path))
    try:
        return absolute_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return absolute_path.as_posix()


def build_file_uri(file_path: Path) -> str:
    normalized = Path(os.path.abspath(file_path)).as_posix()
    return f"file:///{quote(normalized, safe='/:')}"

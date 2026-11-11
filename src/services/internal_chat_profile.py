"""内部聊天的共享主人昵称，不依赖某个浏览器或插件配置。"""

from pathlib import Path
from threading import RLock
from typing import Optional

import json

from src.common.atomic_file import quarantine_file, write_private
from src.common.logger import get_logger
from src.common.paths import get_instance_root

logger = get_logger("internal_chat_profile")
PROFILE_PATH = get_instance_root() / "data" / "internal_chat_profile.json"
_profile_lock = RLock()
DEFAULT_USER_NAME = "人类"


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if not 1 <= len(normalized) <= 64:
        raise ValueError("内部聊天昵称长度必须为 1～64 个字符")
    return normalized


def get_internal_chat_user_name(initial_name: Optional[str] = None) -> str:
    """首次网页连接可提供旧浏览器昵称；已有共享昵称不被其他浏览器的旧值覆盖。"""
    with _profile_lock:
        if PROFILE_PATH.exists():
            try:
                profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
                if not isinstance(profile, dict) or not isinstance(profile.get("user_name"), str):
                    raise ValueError("内部聊天昵称必须是字符串")
                return _validate_name(profile["user_name"])
            except ValueError:
                backup = quarantine_file(PROFILE_PATH)
                logger.warning(f"内部聊天昵称文件损坏，已保留原文件：{backup}", exc_info=True)
        if initial_name is not None:
            return set_internal_chat_user_name(initial_name)
        return DEFAULT_USER_NAME


def set_internal_chat_user_name(name: str) -> str:
    """原子保存昵称。同步文件访问由异步调用方交给线程池。"""
    normalized = _validate_name(name)
    with _profile_lock:
        write_private(PROFILE_PATH, json.dumps({"user_name": normalized}, ensure_ascii=False))
    return normalized

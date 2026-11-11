"""宿主持有的 WebUI 登录凭据；旧用户首次进入宿主时由布局判定复制原密码。"""

from pathlib import Path
from typing import Any, Dict, Tuple
import json
import secrets
import threading
import time

from src.common.atomic_file import write_private


class HostCredentials:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save({"access_token": secrets.token_hex(32), "token_source": "temporary", "first_setup_completed": False})
            return
        self._data = self._read(self.path)
        if self.token_source == "temporary":
            # 临时密码只在本次宿主运行内有效，重启后重新生成并打印。
            self._save({**self._data, "access_token": secrets.token_hex(32)})

    @staticmethod
    def _read(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"WebUI 凭据文件无法解析：{path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise ValueError(f"WebUI 凭据文件无效，拒绝覆盖：{path}")
        if data.get("token_source") not in {"temporary", "configured"}:
            token = data["access_token"]
            data["token_source"] = "temporary" if len(token) == 64 and all(c in "0123456789abcdef" for c in token) else "configured"
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        write_private(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        self._data = data

    @property
    def token(self) -> str:
        return self._data["access_token"]

    @property
    def token_source(self) -> str:
        return self._data["token_source"]

    def verify(self, token: str) -> bool:
        return secrets.compare_digest(self.token.encode("utf-8"), token.encode("utf-8"))

    def setup_status(self) -> Dict[str, Any]:
        return {
            "is_first_setup": not self._data.get("first_setup_completed", False),
            "token_source": self.token_source,
            "requires_custom_token": self.token_source == "temporary",
        }

    def update(self, new_token: str) -> None:
        if len(new_token) < 10 or not any("A" <= c <= "Z" for c in new_token) or not any("a" <= c <= "z" for c in new_token):
            raise ValueError("Token 至少 10 位，且包含大写字母、小写字母和特殊符号")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?/" for c in new_token):
            raise ValueError("Token 必须包含特殊符号")
        with self._lock:
            self._save({**self._data, "access_token": new_token, "token_source": "configured"})

    def regenerate(self) -> str:
        token = secrets.token_hex(32)
        with self._lock:
            self._save({**self._data, "access_token": token, "token_source": "configured"})
        return token

    def complete_setup(self) -> None:
        if self.token_source == "temporary":
            raise ValueError("请先设置固定 WebUI 密码")
        with self._lock:
            self._save({**self._data, "first_setup_completed": True})

    def reset_setup(self, reset_password: bool) -> None:
        with self._lock:
            data = {**self._data, "first_setup_completed": False}
            if reset_password:
                data.update(access_token=secrets.token_hex(32), token_source="temporary")
            self._save(data)


class LoginAttempts:
    """认证失败限流；不信任浏览器传来的转发 IP。"""

    def __init__(self) -> None:
        self._failures: Dict[str, Tuple[int, float]] = {}

    def blocked(self, address: str) -> bool:
        now = time.monotonic()
        self._failures = {key: entry for key, entry in self._failures.items() if entry[1] > now}
        entry = self._failures.get(address)
        return entry is not None and entry[0] >= 5

    def record(self, address: str, success: bool) -> None:
        if success:
            self._failures.pop(address, None)
            return
        if len(self._failures) >= 2048 and address not in self._failures:
            raise ValueError("登录请求过多，请稍后再试")
        count, deadline = self._failures.get(address, (0, time.monotonic() + 600))
        self._failures[address] = (count + 1, deadline)

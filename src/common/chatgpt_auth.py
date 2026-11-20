"""App-owned Sign in with ChatGPT registrations (never Codex/browser credentials).

Network and disk operations are synchronous; runtime callers use asyncio.to_thread.
The initial credential-store implementation requires POSIX owner-only permissions.
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import base64
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import time
import uuid
import webbrowser

import httpx
import jwt

from src.common.paths import get_instance_root

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
USAGE_URL = "https://chatgpt.com/settings/usage"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
LABEL_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class ChatGPTAuthError(RuntimeError):
    """Safe user-facing error: never includes an HTTP body, URL, code or token."""


def _private_directory(path: Path) -> None:
    if os.name != "posix":
        raise ChatGPTAuthError("ChatGPT 凭据存储目前支持 macOS/Linux；Windows 尚待受保护存储实现。")
    if path.is_symlink():
        raise ChatGPTAuthError("ChatGPT 存储目录不能是符号链接。")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _read(path: Path) -> Dict[str, Any]:
    if path.is_symlink():
        raise ChatGPTAuthError("ChatGPT 存储文件不能是符号链接。")
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ChatGPTAuthError("ChatGPT 凭据权限必须为 0600；请先修复文件权限。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, OSError):
        raise ChatGPTAuthError("ChatGPT 本地注册记录无法读取。") from None


def _write(path: Path, data: Dict[str, Any]) -> None:
    if path.is_symlink():
        raise ChatGPTAuthError("ChatGPT 存储文件不能是符号链接。")
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    import fcntl

    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ChatGPTAuthError("此 ChatGPT 注册正在登录或刷新，请稍后重试。") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _json_request(method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
    """No redirects, environment credentials or raw provider errors in diagnostics."""
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, url, **kwargs)
        if response.status_code != 200:
            raise ChatGPTAuthError(f"ChatGPT 请求失败（HTTP {response.status_code}）；请重试或重新登录。")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ChatGPTAuthError:
        raise
    except (httpx.HTTPError, ValueError):
        raise ChatGPTAuthError("ChatGPT 网络或响应格式错误；未切换账号或 API 计费。") from None


def _discovery() -> Dict[str, Any]:
    data = _json_request("GET", f"{ISSUER}/.well-known/openid-configuration")
    if data.get("issuer") != ISSUER:
        raise ChatGPTAuthError("ChatGPT 身份服务 issuer 不匹配。")
    for name in ("jwks_uri", "revocation_endpoint"):
        value = data.get(name)
        parsed = urlsplit(value) if isinstance(value, str) else None
        if parsed is None or parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.fragment:
            raise ChatGPTAuthError("ChatGPT 身份服务端点不可信。")
    return data


def _verify_identity(token: str, client_id: str, nonce: str | None = None) -> Dict[str, Any]:
    keys = _json_request("GET", _discovery()["jwks_uri"])
    try:
        header = jwt.get_unverified_header(token)
        candidates = [key for key in keys["keys"] if key.get("kid") == header.get("kid")]
        if header.get("alg") != "RS256" or len(candidates) != 1:
            raise ValueError
        key = jwt.PyJWK.from_dict(candidates[0], algorithm="RS256").key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            leeway=5,
        )
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError
        if claims.get("azp", client_id) != client_id:
            raise ValueError
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id:
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise ChatGPTAuthError("ChatGPT 身份签名或身份声明校验失败；未替换已有账号。") from None


def _credentials(
    data: Dict[str, Any],
    old: Dict[str, Any],
    nonce: str | None = None,
    received_at: float | None = None,
) -> Dict[str, Any]:
    try:
        expiry = data["expires_in"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= 0:
            raise ValueError
        if str(data["token_type"]).lower() != "bearer" or not isinstance(data["scope"], str):
            raise ValueError
        for field in ("access_token", "refresh_token"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise ValueError
        scopes = data["scope"].split()
        if not PLAN_SCOPES.issubset(scopes):
            raise ChatGPTAuthError("未授权 ChatGPT 套餐推理权限；请在官方页面重新授权。")
        record = {key: value for key, value in old.items() if key != "pending_grant"}
        id_token = data.get("id_token")
        if nonce is not None and not id_token:
            raise ValueError
        if id_token:
            claims = _verify_identity(id_token, old["client_id"], nonce)
            if old.get("subject") and claims["sub"] != old["subject"]:
                raise ChatGPTAuthError("返回的 ChatGPT 账号与所选注册不同；未替换已有账号。")
            record.update(subject=claims["sub"], email=claims.get("email", ""), id_token=id_token)
        if not record.get("subject"):
            raise ValueError
        record.update(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            scopes=scopes,
            expires_at=(received_at if received_at is not None else time.time()) + expiry,
            token_type="Bearer",
            issuer=ISSUER,
        )
        return record
    except (KeyError, TypeError, ValueError):
        raise ChatGPTAuthError("ChatGPT 未返回完整凭据；请重新登录。") from None


def _callback(query: str, state: str, saved_client: str | None) -> tuple[str, str]:
    values = parse_qs(query, keep_blank_values=True)
    if len(values.get("state", [])) != 1 or not secrets.compare_digest(values["state"][0], state):
        raise ChatGPTAuthError("登录回调 state 不匹配。")
    if "error" in values:
        raise ChatGPTAuthError("ChatGPT 登录未获授权或已取消。")
    if len(values.get("code", [])) != 1 or not values["code"][0] or len(values.get("client_id", [])) > 1:
        raise ChatGPTAuthError("ChatGPT 注册回调不完整。")
    client_id = values.get("client_id", [saved_client])[0]
    if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", client_id):
        raise ChatGPTAuthError("ChatGPT 未返回有效的独立客户端注册。")
    if client_id == "dynamic_agent_client" or (saved_client and client_id != saved_client):
        raise ChatGPTAuthError("ChatGPT 客户端注册不匹配。")
    return values["code"][0], client_id


class ChatGPTAccountStore:
    """One named registration per file; each provider explicitly selects its label."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else get_instance_root() / "data" / "chatgpt"

    def _path(self, label: str) -> Path:
        if not LABEL_PATTERN.fullmatch(label):
            raise ChatGPTAuthError("账号标签仅允许 1–64 个字母、数字、下划线或短横线。")
        return self.directory / f"account-{label}.json"

    @contextmanager
    def locked(self, label: str) -> Iterator[Path]:
        path = self._path(label)
        _private_directory(self.directory)
        with _lock(path.with_suffix(".lock")):
            yield path

    def host_id(self) -> str:
        _private_directory(self.directory)
        with _lock(self.directory / "host.lock"):
            path = self.directory / "host.json"
            record = _read(path)
            if not record:
                record = {"id": f"urn:uuid:{uuid.uuid4()}"}
                _write(path, record)
            return record["id"]

    def accounts(self) -> list[Dict[str, Any]]:
        if not self.directory.exists():
            return []
        result = []
        for path in sorted(self.directory.glob("account-*.json")):
            record = _read(path)
            result.append(
                {
                    "label": path.stem[8:],
                    "email": record.get("email", ""),
                    "signed_in": bool(record.get("access_token")),
                    "plan_usage": PLAN_SCOPES.issubset(record.get("scopes", [])),
                }
            )
        return result

    def access_token(self, label: str) -> str:
        with self.locked(label) as path:
            record = _read(path)
            if "pending_grant" in record:
                pending = record["pending_grant"]
                record = _credentials(pending["response"], record, pending.get("nonce"), pending["received_at"])
                _write(path, record)
            if not record.get("access_token") or not PLAN_SCOPES.issubset(record.get("scopes", [])):
                raise ChatGPTAuthError(f"账号 {label} 尚未登录；请运行 maibot chatgpt login --account {label}。")
            if record.get("expires_at", 0) <= time.time() + 60:
                data = _json_request(
                    "POST",
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    },
                )
                # Persist rotation before network-dependent ID-token verification.
                # A temporary JWKS outage must not lose the new renewable session.
                received_at = time.time()
                record["pending_grant"] = {"response": data, "received_at": received_at}
                _write(path, record)
                record = _credentials(data, record, received_at=received_at)
                _write(path, record)
            return record["access_token"]

    def models(self, label: str) -> list[Dict[str, str]]:
        data = _json_request(
            "GET", f"{RESOURCE}/models", headers={"Authorization": f"Bearer {self.access_token(label)}"}
        )
        models = data.get("models")
        if not isinstance(models, list):
            raise ChatGPTAuthError("ChatGPT 模型目录格式不正确。")
        return [
            {"id": model["slug"], "name": model.get("display_name", model["slug"])}
            for model in models
            if isinstance(model, dict)
            and model.get("visibility") == "list"
            and isinstance(model.get("slug"), str)
            and model["slug"]
        ]

    def logout(self, label: str) -> bool:
        """Clear local tokens even if revocation is unavailable; retain registration."""
        confirmed = True
        with self.locked(label) as path:
            record = _read(path)
            refresh = record.get("pending_grant", {}).get("response", {}).get("refresh_token") or record.get(
                "refresh_token"
            )
            if refresh:
                try:
                    endpoint = _discovery()["revocation_endpoint"]
                    confirmed = False
                    for attempt in range(3):
                        try:
                            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                                response = client.post(
                                    endpoint,
                                    data={
                                        "token": refresh,
                                        "token_type_hint": "refresh_token",
                                        "client_id": record["client_id"],
                                    },
                                )
                            confirmed = response.status_code == 200
                            if response.status_code < 500:
                                break
                        except httpx.HTTPError:
                            pass
                        if attempt < 2:
                            time.sleep(0.1 * 2**attempt)
                except (ChatGPTAuthError, httpx.HTTPError):
                    confirmed = False
            if record:
                _write(
                    path,
                    {
                        key: value
                        for key, value in record.items()
                        if key in {"client_id", "subject", "email", "issuer", "ext_agent_host_id"}
                    },
                )
        return confirmed

    def login(self, label: str, timeout: float = 180) -> None:
        host_id = self.host_id()
        with self.locked(label) as path:
            old = _read(path)
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            saved_client = old.get("client_id")
            result: list[tuple[str, str] | ChatGPTAuthError] = []

            class CallbackHandler(BaseHTTPRequestHandler):
                timeout = 2

                def log_message(self, *_args: Any) -> None:
                    pass  # Callback query contains a single-use authorization code.

                def do_GET(self) -> None:
                    uri = urlsplit(self.path)
                    if (
                        self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                        or uri.path != "/auth/callback"
                        or uri.scheme
                        or uri.netloc
                        or uri.fragment
                    ):
                        self.send_error(404)
                        return
                    values = parse_qs(uri.query, keep_blank_values=True)
                    if values.get("state") != [state]:
                        self.send_error(400, "Invalid sign-in state")
                        return
                    try:
                        result.append(_callback(uri.query, state, saved_client))
                    except ChatGPTAuthError as error:
                        result.append(error)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(b"Return to MaiBot. It will verify your ChatGPT connection.")

            with HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
                server.timeout = 0.5
                redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
                params = {
                    "client_id": saved_client or "dynamic_agent_client",
                    "ext_agent_host_id": host_id,
                    "response_type": "code",
                    "redirect_uri": redirect,
                    "scope": SCOPES,
                    "resource": RESOURCE,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge_method": "S256",
                    "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .decode()
                    .rstrip("="),
                }
                if saved_client:
                    if old.get("id_token"):
                        params["id_token_hint"] = old["id_token"]
                else:
                    params["agent_name_hint"] = "MaiBot"
                # Never print this URL: reauthorization may carry an ID-token hint.
                if not webbrowser.open(f"{AUTHORIZE_URL}?{urlencode(params)}"):
                    raise ChatGPTAuthError("无法打开系统浏览器；请设置默认浏览器后重新登录。")
                deadline = time.monotonic() + timeout
                while not result and time.monotonic() < deadline:
                    server.handle_request()
            if not result:
                raise ChatGPTAuthError("ChatGPT 登录超时；没有保存新授权。")
            if isinstance(result[0], ChatGPTAuthError):
                raise result[0]
            code, client_id = result[0]
            old = {**old, "client_id": client_id, "ext_agent_host_id": host_id}
            # Retain an issued registration even if code exchange fails; never mark it signed in.
            if not saved_client:
                _write(path, old)
            data = _json_request(
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect,
                    "resource": RESOURCE,
                },
            )
            received_at = time.time()
            old["pending_grant"] = {"response": data, "received_at": received_at, "nonce": nonce}
            _write(path, old)
            record = _credentials(data, old, nonce, received_at)
            _write(path, record)

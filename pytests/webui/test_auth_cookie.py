from http.cookies import SimpleCookie
from typing import Optional

from fastapi import Request, Response

import pytest

from src.config.config import global_config
from src.webui.core.auth import COOKIE_NAME, clear_auth_cookie, set_auth_cookie


@pytest.mark.parametrize(
    ("mode", "secure_cookie", "scheme", "forwarded_proto", "expected_secure"),
    [
        ("development", True, "http", None, True),
        ("production", False, "http", None, False),
        ("production", False, "https", None, False),
        ("production", False, "http", "https", False),
        ("production", True, "http", None, True),
        ("production", True, "https", None, True),
        ("production", True, "http", "https", True),
        ("development", True, "http", "https", True),
        ("development", True, "https", None, True),
        ("development", False, "http", None, False),
    ],
)
def test_cookie_security_follows_configuration(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    secure_cookie: bool,
    scheme: str,
    forwarded_proto: Optional[str],
    expected_secure: bool,
) -> None:
    monkeypatch.setattr(global_config.webui, "mode", mode)
    monkeypatch.setattr(global_config.webui, "secure_cookie", secure_cookie)
    headers = [] if forwarded_proto is None else [(b"x-forwarded-proto", forwarded_proto.encode())]
    request = Request(
        {"type": "http", "scheme": scheme, "server": ("example.com", 7999), "path": "/", "headers": headers}
    )
    response = Response()

    set_auth_cookie(response, "test-session-token", request)
    cookie = SimpleCookie(response.headers["set-cookie"])[COOKIE_NAME]
    assert bool(cookie["secure"]) is expected_secure
    assert cookie["httponly"]

    cleared = Response()
    clear_auth_cookie(cleared)
    assert bool(SimpleCookie(cleared.headers["set-cookie"])[COOKIE_NAME]["secure"]) is expected_secure

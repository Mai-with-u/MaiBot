"""WebUI 启动端口预检测试：本机不可用的监听地址不应被误报为端口占用（#2111）。"""

from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

import socket
import sys

import pytest
import uvicorn

from src.common import event_loop_watchdog
from src.common.utils import port_checker
from src.webui import webui_server

WEBUI_PORT = 8001
# Linux 下的 EADDRINUSE / EADDRNOTAVAIL
ADDRESS_IN_USE: Tuple[int, str] = (98, "Address already in use")
ADDRESS_NOT_AVAILABLE: Tuple[int, str] = (99, "Cannot assign requested address")


class _RecordingLogger:
    """记录日志调用，便于断言警告/错误内容。"""

    def __init__(self) -> None:
        self.records: List[Tuple[str, str]] = []

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.records.append(("debug", message))

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.records.append(("info", message))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.records.append(("warning", message))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.records.append(("error", message))

    def messages(self, level: str) -> List[str]:
        return [message for record_level, message in self.records if record_level == level]


class _FakeSocket:
    """按地址模拟 bind 结果的 socket，不依赖本机真实的 IPv4/IPv6 环境。"""

    def __init__(self, bind_errors: Dict[str, Tuple[int, str]], family: int) -> None:
        self._bind_errors = bind_errors
        self.family = family
        self.bound_address: Optional[Tuple[Any, ...]] = None
        self.closed = False

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def settimeout(self, timeout: float) -> None:
        pass

    def setsockopt(self, *args: Any) -> None:
        pass

    def bind(self, address: Tuple[Any, ...]) -> None:
        bind_error = self._bind_errors.get(address[0])
        if bind_error is not None:
            raise OSError(*bind_error)
        self.bound_address = address

    def listen(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _install_fake_sockets(
    monkeypatch: pytest.MonkeyPatch,
    bind_errors: Dict[str, Tuple[int, str]],
) -> List[_FakeSocket]:
    created_sockets: List[_FakeSocket] = []

    def fake_socket(family: int = socket.AF_INET, type: int = socket.SOCK_STREAM, proto: int = 0) -> _FakeSocket:
        fake = _FakeSocket(bind_errors, family)
        created_sockets.append(fake)
        return fake

    def fake_getaddrinfo(host: str, port: int, *args: Any) -> List[Tuple[Any, ...]]:
        if ":" in host:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, port, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]

    monkeypatch.setattr(socket, "socket", fake_socket)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return created_sockets


@pytest.fixture
def recording_logger(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    fake_logger = _RecordingLogger()
    monkeypatch.setattr(webui_server, "logger", fake_logger)
    return fake_logger


@pytest.fixture
def served_sockets(monkeypatch: pytest.MonkeyPatch) -> List[List[Any]]:
    """替换 WebUI 启动依赖的重量级组件，并记录交给 uvicorn 的监听 socket。"""
    served: List[List[Any]] = []

    class _FakeUvicornServer:
        def __init__(self, config: Any) -> None:
            self.config = config

        async def serve(self, sockets: Optional[List[Any]] = None) -> None:
            served.append(list(sockets or []))

    fake_app_module = ModuleType("src.webui.app")
    fake_app_module.create_app = lambda **kwargs: object()  # type: ignore[attr-defined]
    fake_app_module.show_access_token = lambda: None  # type: ignore[attr-defined]
    fake_app_module.limit_sync_endpoint_concurrency = lambda: 1  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.webui.app", fake_app_module)
    monkeypatch.setattr(event_loop_watchdog, "start_watchdog", lambda loop_name: None)
    monkeypatch.setattr(uvicorn, "Server", _FakeUvicornServer)
    return served


@pytest.mark.parametrize("error_number", [47, 49, 97, 99, 10047, 10049])
def test_address_unavailable_errnos_are_not_port_conflicts(error_number: int) -> None:
    error = OSError(error_number, "address not available")

    assert port_checker.is_address_unavailable_error(error)
    assert not port_checker.is_port_conflict_error(error)


@pytest.mark.parametrize("error_number", [48, 98, 10048])
def test_port_conflict_errnos_are_not_address_unavailable(error_number: int) -> None:
    error = OSError(error_number, "Address already in use")

    assert port_checker.is_port_conflict_error(error)
    assert not port_checker.is_address_unavailable_error(error)


def test_check_port_available_reports_conflict_as_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sockets(monkeypatch, {"127.0.0.1": ADDRESS_IN_USE})
    fake_logger = _RecordingLogger()

    assert port_checker.check_port_available("127.0.0.1", WEBUI_PORT) is False
    with pytest.raises(OSError, match=r"端口 8001 已被占用 \(host=127\.0\.0\.1\)"):
        port_checker.assert_port_available(
            host="127.0.0.1",
            port=WEBUI_PORT,
            service_name="WebUI 服务器",
            logger=fake_logger,
        )
    assert any("已被占用" in message for message in fake_logger.messages("error"))


def test_check_port_available_raises_address_unavailable_instead_of_occupied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sockets(monkeypatch, {"::1": ADDRESS_NOT_AVAILABLE})
    fake_logger = _RecordingLogger()

    with pytest.raises(OSError) as check_error:
        port_checker.check_port_available("::1", WEBUI_PORT, allow_reuse_addr=True)
    assert check_error.value.errno == ADDRESS_NOT_AVAILABLE[0]

    with pytest.raises(OSError) as assert_error:
        port_checker.assert_port_available(
            host="::1",
            port=WEBUI_PORT,
            service_name="WebUI 服务器",
            logger=fake_logger,
            allow_reuse_addr=True,
        )
    assert assert_error.value.errno == ADDRESS_NOT_AVAILABLE[0]
    assert fake_logger.messages("error") == []


@pytest.mark.asyncio
async def test_webui_start_skips_host_unavailable_on_this_machine(
    monkeypatch: pytest.MonkeyPatch,
    recording_logger: _RecordingLogger,
    served_sockets: List[List[Any]],
) -> None:
    server = webui_server.WebUIServer(hosts=["127.0.0.1", "::1"], port=WEBUI_PORT, register_config_reload=False)
    _install_fake_sockets(monkeypatch, {"::1": ADDRESS_NOT_AVAILABLE})

    await server.start()

    assert len(served_sockets) == 1
    assert [sock.bound_address for sock in served_sockets[0]] == [("127.0.0.1", WEBUI_PORT)]
    assert any("::1" in message for message in recording_logger.messages("warning"))
    assert not any("已被占用" in message for message in recording_logger.messages("error"))
    assert not any("[::1]" in message for message in recording_logger.messages("info"))


@pytest.mark.asyncio
async def test_webui_start_aborts_when_no_host_is_available(
    monkeypatch: pytest.MonkeyPatch,
    recording_logger: _RecordingLogger,
    served_sockets: List[List[Any]],
) -> None:
    server = webui_server.WebUIServer(hosts=["::1"], port=WEBUI_PORT, register_config_reload=False)
    _install_fake_sockets(monkeypatch, {"::1": ADDRESS_NOT_AVAILABLE})

    with pytest.raises(OSError, match="WebUI 无法绑定到任何指定地址"):
        await server.start()

    assert served_sockets == []
    assert not any("已被占用" in message for message in recording_logger.messages("error"))


@pytest.mark.asyncio
async def test_webui_start_still_aborts_on_port_conflict(
    monkeypatch: pytest.MonkeyPatch,
    recording_logger: _RecordingLogger,
    served_sockets: List[List[Any]],
) -> None:
    server = webui_server.WebUIServer(hosts=["127.0.0.1", "::1"], port=WEBUI_PORT, register_config_reload=False)
    _install_fake_sockets(monkeypatch, {"127.0.0.1": ADDRESS_IN_USE})

    with pytest.raises(OSError, match=r"端口 8001 已被占用 \(host=127\.0\.0\.1\)"):
        await server.start()

    assert served_sockets == []
    assert any("已被占用" in message for message in recording_logger.messages("error"))

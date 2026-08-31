"""进程级关停状态。"""

from threading import Event

import contextlib
import signal

_shutdown_requested = Event()


def request_shutdown(reason: str = "") -> None:
    """标记当前进程正在关停。"""

    del reason
    _shutdown_requested.set()


def is_shutdown_requested() -> bool:
    """返回当前进程是否已经进入关停流程。"""

    return _shutdown_requested.is_set()


@contextlib.contextmanager
def application_signal_handlers(handler):
    """Worker 统一拥有停止信号，包括进程内的第三方 Uvicorn 服务。

    Uvicorn 的 capture_signals 会覆盖应用 handler，并在退出时重新发送信号。
    在 Worker 生命周期内关闭这个捕获层，避免 HTTP 服务关闭代替应用关闭。
    作用域退出时还原；不影响 Runner、独立服务器或其他进程。
    """
    from uvicorn import Server

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous = {sig: signal.signal(sig, handler) for sig in signals}
    capture_signals = Server.capture_signals

    @contextlib.contextmanager
    def application_owned_signals(_server):
        yield

    Server.capture_signals = application_owned_signals
    try:
        yield
    finally:
        Server.capture_signals = capture_signals
        for sig, original in previous.items():
            signal.signal(sig, original)

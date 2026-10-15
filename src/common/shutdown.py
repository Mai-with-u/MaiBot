"""进程级关停状态。"""

from threading import Event
from types import FrameType
from typing import Callable, Iterator

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
def application_signal_handlers(handler: Callable[[int, FrameType | None], None]) -> Iterator[None]:
    """Worker 统一拥有停止信号，包括进程内的第三方 Uvicorn 服务。

    Uvicorn 的 capture_signals 会覆盖应用 handler，并在退出时重新发送信号。
    在 Worker 生命周期内关闭这个捕获层，避免 HTTP 服务关闭代替应用关闭。
    作用域退出时还原；不影响 Runner、独立服务器或其他进程。
    """
    from uvicorn import Server

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    # 依赖 uvicorn 非公开调用链 Server.serve() → capture_signals()；uv.lock 锁定并验证于 0.45.0。
    # maim_message 的额外 API 会自行创建 uvicorn.Server，无法只在本项目构造处子类化，因此替换类属性。
    # 先读取属性：未来版本移除该方法时，在修改任何信号处理器之前明确失败。
    capture_signals = Server.capture_signals
    previous = {sig: signal.signal(sig, handler) for sig in signals}

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

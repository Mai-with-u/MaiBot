"""Maim Message - A message handling library"""

from typing import TYPE_CHECKING, Any, Callable

__version__ = "0.2.0"

if TYPE_CHECKING:
    from maim_message import MessageServer


def get_global_api() -> "MessageServer":
    """懒加载 maim_message，避免主程序唤醒前阻塞在消息库导入上。"""

    from .api import get_global_api as _get_global_api

    return _get_global_api()


def register_inbound_handler(handler: Callable[..., Any]) -> None:
    """向 MessageServer 注册普通入站处理器，供 Legacy 驱动接管 WS 入站。"""

    get_global_api().register_message_handler(handler)


def unregister_inbound_handler(handler: Callable[..., Any]) -> None:
    """从 MessageServer 移除普通入站处理器。"""

    handlers = get_global_api().message_handlers
    if handler in handlers:
        handlers.remove(handler)


__all__ = ["get_global_api", "register_inbound_handler", "unregister_inbound_handler"]

"""阻断存储测试不需要的宿主导入；A_Memorix 内核/生命周期/存储全部保持真实。"""

from contextlib import contextmanager
from types import ModuleType

import logging
import socket
import sys
import threading


def forbidden(*args, **kwargs):
    raise AssertionError("Host configuration, messaging and network are forbidden in storage tests")


class UnusedHost:
    def __getattr__(self, name):
        return forbidden(name)


def install(monkeypatch) -> None:
    modules = {
        "src.chat": {"__path__": []},
        "src.chat.message_receive": {"__path__": []},
        "src.chat.message_receive.chat_manager": {"chat_manager": UnusedHost()},
        "src.services.llm_service": {"LLMServiceClient": UnusedHost},
        "src.services.message_service": {
            "get_messages_by_time_in_chat": forbidden,
            "build_readable_messages": forbidden,
        },
        "src.llm_models.model_client": {"__path__": []},
        "src.llm_models.model_client.base_client": {"EmbeddingRequest": UnusedHost, "client_registry": UnusedHost()},
        "src.common.data_models.llm_service_data_models": {"LLMServiceResult": UnusedHost},
        "src.common.database.database": {"get_db_session": forbidden, "SHUTDOWN_TEST_STUB": True},
        "src.common.database.database_model": {"PersonInfo": UnusedHost},
        "src.config.config": {"config_manager": UnusedHost(), "global_config": UnusedHost()},
        "src.common.logger": {"get_logger": logging.getLogger},
    }
    for name, exports in modules.items():
        module = ModuleType(name)
        module.__dict__.update(exports)
        monkeypatch.setitem(sys.modules, name, module)
    original_connect, original_pair = socket.socket.connect, socket.socketpair
    internal = threading.local()

    def connect(sock, address):
        if getattr(internal, "socketpair", False):
            return original_connect(sock, address)
        return forbidden()

    def socketpair(*args, **kwargs):
        # Windows asyncio 自唤醒管道使用 stdlib 的 loopback socketpair，不是业务网络。
        internal.socketpair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            internal.socketpair = False

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@contextmanager
def isolated():
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as patch:
        install(patch)
        yield patch

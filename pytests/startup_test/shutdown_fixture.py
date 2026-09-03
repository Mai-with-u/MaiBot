"""隔离加载真实 bot 生命周期函数，不导入配置、模型、数据库或网络客户端。"""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypeVar

import ast
import asyncio
import os
import signal
import sys
import traceback
import time

from src.common.shutdown import application_signal_handlers

ROOT = Path(__file__).resolve().parents[2]


def load_bot_functions(events, *, memory_failure=False, memory_delay=0, install_module=None):
    class Logger:
        def info(self, text, **kwargs):
            events.append(str(text))

        warning = info
        error = info
        debug = info
        exception = info

    def record(name):
        async def run(*args, **kwargs):
            events.append(name)

        return run

    async def stop_memory():
        events.append("memory_stop")
        await asyncio.sleep(memory_delay)
        if memory_failure:
            raise RuntimeError("fixture persist failed")
        events.extend(["persist", "metadata_close", "writer_lock_release"])

    modules = {
        "src.config.config": {"config_manager": SimpleNamespace(stop_file_watcher=record("watcher_stop"))},
        "src.core.event_bus": {"event_bus": SimpleNamespace(emit=record("on_stop"))},
        "src.core.types": {"EventType": SimpleNamespace(ON_STOP="on_stop")},
        "src.plugin_runtime.integration": {
            "get_plugin_runtime_manager": lambda: SimpleNamespace(stop=record("plugin_stop"))
        },
        "src.A_memorix.host_service": {"a_memorix_host_service": SimpleNamespace(stop=stop_memory)},
        "src.services.memory_flow_service": {
            "memory_automation_service": SimpleNamespace(shutdown=record("memory_producer_stop"))
        },
        "src.emoji_system.emoji_manager": {
            "emoji_manager": SimpleNamespace(shutdown=lambda: events.append("emoji_stop"))
        },
        "src.mcp_module.service": {"get_mcp_service": lambda: SimpleNamespace(close=record("mcp_close"))},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        if install_module is None:
            sys.modules[name] = module
        else:
            install_module(name, module)

    namespace = {
        "asyncio": asyncio,
        "sys": sys,
        "signal": signal,
        "traceback": traceback,
        "Path": Path,
        "time": time,
        "MainSystem": SimpleNamespace,
        "_RunResultT": TypeVar("T"),
        "logger": Logger(),
        "t": lambda key, **kw: key,
        "tn": lambda key, count: key,
        "request_shutdown": lambda reason: events.append("request_shutdown"),
        "async_task_manager": SimpleNamespace(stop_and_wait_all_tasks=record("manager_stop")),
        "_active_main_loop": None,
        "_active_main_task": None,
        "_shutdown_signal_count": 0,
        "_shutdown_task": None,
        "_shutdown_deadline": None,
        "SHUTDOWN_TIMEOUT": 50.0,
    }
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / "bot.py"), "exec"), namespace)
    system = SimpleNamespace(
        webui_server=SimpleNamespace(shutdown=record("webui_stop")), app=SimpleNamespace(stop=record("app_stop"))
    )
    return namespace, system, tree


def run_fixture_worker(fail=False, self_stop=False, restart=False, early_stop=False, shutdown_at=None, repeated=False):
    """供 POSIX 子进程测试使用；执行真实 Worker __main__，业务服务均为 fake。"""

    class Events(list):
        def append(self, value):
            super().append(value)
            print(value, flush=True)

        def extend(self, values):
            for value in values:
                self.append(value)

    events = Events()
    ns, system, tree = load_bot_functions(events, memory_failure=fail, memory_delay=0.2)

    def stop():
        signal.raise_signal(signal.SIGTERM)
        if repeated:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGTERM)

    if shutdown_at in {"before_publish", "after_publish"}:
        publish = ns["_set_active_main_task"]

        def publish_at_boundary(task):
            if task.get_coro().__name__ == "schedule" and shutdown_at == "before_publish":
                stop()
            publish(task)
            if task.get_coro().__name__ == "schedule" and shutdown_at == "after_publish":
                stop()

        ns["_set_active_main_task"] = publish_at_boundary
    if shutdown_at == "after_init":
        drive = ns["_run_until_complete"]

        def drive_at_boundary(loop, task):
            result = drive(loop, task)
            if task.get_coro().__name__ == "initialize":
                stop()
            return result

        ns["_run_until_complete"] = drive_at_boundary
    if early_stop:
        ns["_install_early_worker_signal_handlers"]()
        print("worker_booting", flush=True)
        if self_stop:
            signal.raise_signal(signal.SIGTERM)
        time.sleep(0.5)

    async def initialize():
        events.append("initialize")
        if shutdown_at == "init_pending":
            stop()
            await asyncio.Event().wait()
        if shutdown_at == "init_return":
            # 精确 P1：取消回调看到 initialize 已 done 后返回，停止标记必须留给下一任务。
            stop()

    async def schedule():
        from uvicorn import Config, Server

        if shutdown_at:
            events.append("schedule_entered")
            if shutdown_at not in {"after_publish", "scheduled"}:
                raise SystemExit(9)  # fail fast：不允许丢失停止请求后挂住测试子进程。
        if restart:
            raise SystemExit(42)

        class TestServer(Server):
            async def _serve(self, sockets=None):
                print("worker_ready", flush=True)
                if self_stop:
                    asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGTERM)
                if shutdown_at == "scheduled":
                    stop()
                await asyncio.Event().wait()

        try:
            await TestServer(Config(app=None, log_config=None)).serve()
        except asyncio.CancelledError:
            events.append("schedule_cancelled")
            raise

    system.initialize = initialize
    system.schedule_tasks = schedule
    module = ModuleType("src.common.logger")
    module.initialize_ws_handler = lambda loop: None
    sys.modules[module.__name__] = module
    ns.update(
        {
            "__name__": "__main__",
            "raw_main": lambda: system,
            "os": os,
            "loop": None,
            "set_main_loop": lambda loop: None,
            "shutdown_logging": lambda: None,
            "application_signal_handlers": application_signal_handlers,
            "RESTART_EXIT_CODE": 42,
        }
    )
    # 最后一个 __main__ 是 Worker 入口；不运行 bot 顶层的真实依赖初始化。
    exec(compile(ast.Module(body=[tree.body[-1]], type_ignores=[]), "bot.py", "exec"), ns)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        run_fixture_worker(
            "--fail" in sys.argv,
            "--self-stop" in sys.argv,
            "--restart" in sys.argv,
            "--early-stop" in sys.argv,
            next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--shutdown-at=")), None),
            "--repeated" in sys.argv,
        )
    else:
        from src.common.process_runner import supervise_worker
        import logging

        logging.basicConfig(level=logging.INFO)
        code = supervise_worker(
            [sys.executable, "-m", "pytests.startup_test.shutdown_fixture", "--worker"]
            + (["--fail"] if "--fail" in sys.argv else [])
            + (["--early-stop"] if "--early-stop" in sys.argv else []),
            os.environ.copy(),
            logging.getLogger("fixture"),
        )
        print("runner_exit=" + str(code), flush=True)
        raise SystemExit(code)

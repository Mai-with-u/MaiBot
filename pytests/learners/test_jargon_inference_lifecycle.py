"""推断服务故障不阻断组件生命周期；CLI 路径在销毁依赖前等待推断退出。"""

from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sqlmodel import Session, SQLModel, create_engine, select

import ast
import asyncio
import sys
import time

import pytest

from src import main
from src.common.database.database_model import JargonInferenceJob, JargonInferenceState
from src.learners import jargon_inference_service


@pytest.fixture
def lifecycle(monkeypatch):
    """用可注入失败的组件记录启动和关停顺序，不启动实际网络或插件服务。"""
    events = []

    def asynchronous(name, error=None):
        """创建带顺序记录的异步替身，并在指定组件上抛出传入错误。"""

        async def operation(*_args, **_kwargs):
            """先记录组件已被调用，再模拟该组件的可选失败。"""
            events.append(name)
            if error is not None:
                raise error

        return AsyncMock(side_effect=operation)

    plugin = SimpleNamespace(
        supervisors=[],
        start=asynchronous("plugin_start"),
        stop=asynchronous("plugin_stop"),
        bridge_event=asynchronous("plugin_event"),
    )
    memory = SimpleNamespace(
        register_config_reload_callback=Mock(),
        start=asynchronous("memory_start"),
        stop=asynchronous("memory_stop"),
    )
    mcp = SimpleNamespace(register_config_reload_callback=Mock(), close=asynchronous("mcp_close"))
    expression = SimpleNamespace(start=Mock(), stop=asynchronous("expression_stop"))
    emoji = SimpleNamespace(load_emojis_from_db=Mock(), shutdown=Mock())
    chat = SimpleNamespace(initialize=asynchronous("chat_start"), regularly_save_sessions=AsyncMock())
    automation = SimpleNamespace(start=asynchronous("automation_start"), shutdown=asynchronous("automation_stop"))
    agent = SimpleNamespace(start=asynchronous("agent_start"), stop=asynchronous("agent_stop"))
    pet = SimpleNamespace(start=asynchronous("pet_start"), stop=asynchronous("pet_stop"))
    bus = SimpleNamespace(emit=asynchronous("event"))
    heart = SimpleNamespace(shutdown=asynchronous("heart_stop"))
    image = SimpleNamespace(shutdown=asynchronous("image_stop"))
    config = SimpleNamespace(start_file_watcher=AsyncMock(), stop_file_watcher=asynchronous("watcher_stop"))
    tasks = SimpleNamespace(add_task=AsyncMock(), stop_and_wait_all_tasks=asynchronous("tasks_stop"))
    modules = {
        "src.plugin_runtime.integration": {"get_plugin_runtime_manager": lambda: plugin},
        "src.A_memorix.host_service": {"a_memorix_host_service": memory},
        "src.mcp_module.service": {"get_mcp_service": lambda: mcp},
        "src.services.expression_vector_service": {"expression_vector_service": expression},
        "src.emoji_system.emoji_manager": {"emoji_manager": emoji},
        "src.chat.message_receive.chat_manager": {"chat_manager": chat},
        "src.services.memory_flow_service": {"memory_automation_service": automation},
        "src.agent.service": {"agent_service": agent},
        "src.desktop_pet.service": {"desktop_pet_service": pet},
        "src.core.event_bus": {"event_bus": bus},
        "src.core.types": {"EventType": SimpleNamespace(ON_START="on_start", ON_STOP="on_stop")},
        "src.chat.utils.statistic": {"OnlineTimeRecordTask": Mock(), "StatisticOutputTask": Mock()},
        "src.common.remote": {"TelemetryHeartBeatTask": Mock(), "TelemetryStatsUploadTask": Mock()},
        "src.chat.heart_flow.heartflow_manager": {"heartflow_manager": heart},
        "src.chat.image_system.image_manager": {"image_manager": image},
        "src.common.http_service.console": {"close_console": asynchronous("console_stop")},
        "src.common.shutdown": {"request_shutdown": Mock()},
        "src.common.shutdown_diagnostics": {"shutdown_step": lambda *_: nullcontext()},
    }
    for name, members in modules.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**members))
    from src.common import logger as logger_module

    monkeypatch.setattr(logger_module, "stop_ws_broadcasts", asynchronous("broadcast_stop"))
    monkeypatch.setattr(main, "logger", Mock())
    monkeypatch.setattr(main, "config_manager", config)
    monkeypatch.setattr(main, "async_task_manager", tasks)
    monkeypatch.setattr(main, "prompt_manager", SimpleNamespace(load_prompts=Mock()))
    monkeypatch.setattr(main, "diagnostic_stage", lambda *_: nullcontext())
    monkeypatch.setattr(main, "_wait_for_plugin_runners_spawned", AsyncMock())
    monkeypatch.setattr(main.MainSystem, "_start_http_server", lambda _self: events.append("http_start"))
    return SimpleNamespace(events=events, async_op=asynchronous, plugin=plugin, config=config, tasks=tasks)


@pytest.mark.asyncio
async def test_corrupt_job_recovery_preserves_data_and_other_components_start(tmp_path, monkeypatch, lifecycle):
    """损坏任务导致恢复事务整体回滚，但其余组件仍可启动。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'corrupt.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[JargonInferenceJob.__table__, JargonInferenceState.__table__])

    @contextmanager
    def session_scope():
        """使恢复操作与测试断言读取同一 SQLite，异常时回滚事务。"""
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    valid_items = '[{"status":"queued"}]'
    corrupt_items = "{ damaged job data"
    with session_scope() as session:
        session.add(JargonInferenceJob(id="good", source="manual", status="queued", items_json=valid_items))
        session.add(JargonInferenceJob(id="bad", source="manual", status="running", items_json=corrupt_items))
        session.add(JargonInferenceState(jargon_id=1, status="running"))
    service = jargon_inference_service.JargonInferenceService()
    monkeypatch.setattr(jargon_inference_service, "get_db_session", session_scope)
    monkeypatch.setattr(jargon_inference_service, "get_jargon_inference_service", lambda: service)
    manager = Mock()
    monkeypatch.setattr(jargon_inference_service, "config_manager", manager)
    try:
        await main.MainSystem()._init_components()
        assert "agent_start" in lifecycle.events and "pet_start" in lifecycle.events
        assert "event" in lifecycle.events
        assert lifecycle.tasks.add_task.await_count == 4
        main.logger.exception.assert_called_once()
        assert service._loop is None and service._dispatcher is None and service._scheduler is None
        manager.register_reload_callback.assert_not_called()
        with pytest.raises(RuntimeError, match="尚未就绪"):
            await service.preview({"ids": [1]})
        with session_scope() as session:
            rows = {row.id: row for row in session.exec(select(JargonInferenceJob)).all()}
            assert (rows["good"].status, rows["good"].items_json) == ("queued", valid_items)
            assert (rows["bad"].status, rows["bad"].items_json) == ("running", corrupt_items)
            assert session.get(JargonInferenceState, 1).status == "running"
    finally:
        await service.stop()
        engine.dispose()


@pytest.mark.asyncio
async def test_main_cleanup_continues_after_inference_stop_failure(monkeypatch, lifecycle):
    """保留推断关停的原始异常，同时验证其余依赖完成清理。"""
    failure = RuntimeError("inference state write failed")
    stop = lifecycle.async_op("inference_stop", failure)
    monkeypatch.setattr(jargon_inference_service, "get_jargon_inference_service", lambda: SimpleNamespace(stop=stop))
    system = SimpleNamespace(
        initialize=AsyncMock(),
        schedule_tasks=AsyncMock(),
        watchdog_task=None,
        app=SimpleNamespace(stop=lifecycle.async_op("app_stop")),
        http_server=SimpleNamespace(shutdown=lifecycle.async_op("http_stop")),
    )
    monkeypatch.setattr(main, "MainSystem", lambda: system)
    loop_setter = Mock()
    monkeypatch.setattr(main, "set_main_loop", loop_setter)
    with pytest.raises(RuntimeError, match="未完成关停") as caught:
        await main.main()
    assert caught.value.__cause__ is failure
    assert lifecycle.events.index("inference_stop") < lifecycle.events.index("http_stop")
    assert "plugin_stop" in lifecycle.events and "mcp_close" in lifecycle.events and "watcher_stop" in lifecycle.events
    loop_setter.assert_called_with(None)


def runtime_shutdown_functions(lifecycle, monkeypatch):
    # runtime 的模块入口会申请真实进程锁；只加载同一源码中的关停函数，保留其完整控制流。
    """加载 CLI 关停控制流并替换其依赖，避免在测试进程内申请真实进程锁。"""
    path = Path(__file__).resolve().parents[2] / "src/runtime.py"
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in parsed.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name in {"graceful_shutdown", "_await_shutdown_step", "_shutdown_time_left"}
    ]
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "MainSystem": main.MainSystem,
        "SHUTDOWN_TIMEOUT": 50.0,
        "MEMORY_WRITER_TIMEOUT": 5.0,
        "MEMORY_KERNEL_TIMEOUT": 30.0,
        "FINAL_CLEANUP_TIMEOUT": 5.0,
        "IMAGE_SYNC_TIMEOUT": 5.0,
        "_shutdown_deadline": None,
        "request_shutdown": Mock(),
        "report_phase": Mock(),
        "logger": Mock(),
        "t": lambda key, **_: key,
        "tn": lambda key, *_: key,
        "async_task_manager": lifecycle.tasks,
    }
    monkeypatch.setitem(sys.modules, "src.config.config", SimpleNamespace(config_manager=lifecycle.config))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["graceful_shutdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("write failed")])
async def test_cli_stops_inference_before_dependencies_and_continues_on_failure(monkeypatch, lifecycle, failure):
    stop = lifecycle.async_op("inference_stop", failure)
    monkeypatch.setattr(jargon_inference_service, "get_jargon_inference_service", lambda: SimpleNamespace(stop=stop))
    shutdown = runtime_shutdown_functions(lifecycle, monkeypatch)
    system = SimpleNamespace(app=None, http_server=SimpleNamespace(shutdown=lifecycle.async_op("http_stop")))
    assert await shutdown(system) is (failure is None)
    assert lifecycle.events.index("inference_stop") < lifecycle.events.index("http_stop")
    assert lifecycle.events.index("inference_stop") < lifecycle.events.index("plugin_stop")
    assert lifecycle.events.index("inference_stop") < lifecycle.events.index("memory_stop")
    assert "tasks_stop" in lifecycle.events

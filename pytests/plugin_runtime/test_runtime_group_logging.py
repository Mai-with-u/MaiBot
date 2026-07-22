from typing import List

import asyncio
import logging

from src.common.logger_color_and_mapping import MODULE_ALIASES
from src.plugin_runtime.host.logger_bridge import RunnerLogBridge
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.envelope import Envelope, LogBatchPayload, LogEntry, MessageType


def test_runtime_group_logger_aliases_are_user_facing() -> None:
    assert MODULE_ALIASES["plugin_runtime.group.core"] == "核心插件"
    assert MODULE_ALIASES["plugin_runtime.group.extension"] == "扩展插件"


def test_runner_log_bridge_groups_runtime_logs_and_preserves_plugin_logs(caplog) -> None:
    entries = [
        LogEntry(
            timestamp_ms=1,
            level=logging.INFO,
            logger_name="plugin_runtime.runner.main",
            message="Runner 日志",
        ),
        LogEntry(
            timestamp_ms=2,
            level=logging.INFO,
            logger_name="plugin.example",
            message="插件日志",
        ),
    ]
    envelope = Envelope(
        request_id=1,
        message_type=MessageType.BROADCAST,
        method="runner.log_batch",
        payload=LogBatchPayload(entries=entries).model_dump(),
    )

    bridge = RunnerLogBridge(runtime_logger_name="plugin_runtime.group.core")
    with caplog.at_level(logging.INFO):
        asyncio.run(bridge.handle_log_batch(envelope))

    captured_records: List[logging.LogRecord] = caplog.records
    assert [(record.name, record.getMessage()) for record in captured_records] == [
        ("plugin_runtime.group.core", "Runner 日志"),
        ("plugin.example", "插件日志"),
    ]


def test_startup_summary_plugin_list_is_compact() -> None:
    assert PluginRunnerSupervisor._summarize_plugin_ids([]) == "0"
    assert PluginRunnerSupervisor._summarize_plugin_ids(["plugin.one", "plugin.two"]) == (
        "2（plugin.one, plugin.two）"
    )
    assert PluginRunnerSupervisor._summarize_plugin_ids([f"plugin.{index}" for index in range(7)]) == (
        "7（plugin.0, plugin.1, plugin.2, plugin.3, plugin.4 等 7 个）"
    )

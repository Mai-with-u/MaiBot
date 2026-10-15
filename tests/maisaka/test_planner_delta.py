"""Planner 传输投影及增量协议，不导入运行中的数据库/机器人。"""

from copy import deepcopy
from types import ModuleType

from pytest import raises

import asyncio
import json
import logging
import sys

from src.maisaka.monitor.planner_delta import MAX_PLANNER_BASES, PlannerDeltaEncoder


def message(event_id=1, event="planner.progress", **changes):
    payload = {
        "session_id": "group-a", "run_id": "run-1", "cycle_id": 1,
        "event_id": event_id, "timestamp": event_id,
        "request": {"messages": [{"content": "完整请求"}], "selected_history_count": 10},
        "planner": {"content": "思考", "prompt_html_uri": "/reasoning/1"},
        "tools": [], "active_tool_call_id": "tool-1", "final_state": {},
        **changes,
    }
    return {"op": "event", "domain": "maisaka_monitor", "topic": "main", "event": event, "data": payload}


def test_first_snapshot_projects_only_unused_details_without_mutation():
    original = message(tools=[{"tool_call_id": "tool-1", "summary": "结果", "detail": {"large": "详情"}}])
    before = deepcopy(original)
    wire = PlannerDeltaEncoder().encode(original)
    assert wire["event"] == "planner.progress"
    assert "messages" not in wire["data"]["request"]
    assert "detail" not in wire["data"]["tools"][0]
    assert wire["data"]["tools"][0]["summary"] == "结果"
    assert original == before


def test_progress_sends_only_changed_fields_and_new_tools():
    encoder = PlannerDeltaEncoder()
    encoder.encode(message())
    tool = {"tool_call_id": "tool-1", "summary": "结果"}
    wire = encoder.encode(message(2, tools=[tool], active_tool_call_id="tool-2"))
    assert wire["event"] == "planner.delta"
    delta = wire["data"]
    assert delta["base_event_id"] == 1
    assert delta["changes"] == {"event_id": 2, "timestamp": 2, "active_tool_call_id": "tool-2"}
    assert delta["tools_from"] == 0
    assert delta["tools"] == [tool]
    other = {"tool_call_id": "tool-2", "summary": "结果2"}
    delta = encoder.encode(message(3, tools=[tool, other]))["data"]
    assert delta["tools_from"] == 1
    assert delta["tools"] == [other]


def test_final_event_is_delta_and_next_round_or_connection_starts_full():
    encoder = PlannerDeltaEncoder()
    encoder.encode(message())
    assert encoder.encode(message(2, "planner.finalized"))["data"]["event_type"] == "planner.finalized"
    assert encoder.encode(message(3, cycle_id=2))["event"] == "planner.progress"
    assert PlannerDeltaEncoder().encode(message(4))["event"] == "planner.progress"
    assert encoder.encode(message(5))["event"] == "planner.progress"


def test_changed_and_removed_tools_are_represented_exactly():
    encoder = PlannerDeltaEncoder()
    encoder.encode(message(tools=[{"summary": "a"}, {"summary": "b"}]))
    delta = encoder.encode(message(2, tools=[{"summary": "a"}, {"summary": "c"}]))["data"]
    assert delta["tools_from"] == 1
    assert delta["tools"] == [{"summary": "c"}]
    delta = encoder.encode(message(3, tools=[]))["data"]
    assert delta["tools_from"] == 0
    assert delta["tools"] == []


def test_interleaved_groups_and_replay_versions_have_independent_bases():
    encoder = PlannerDeltaEncoder()
    encoder.encode(message(10))
    assert encoder.encode(message(11, session_id="group-b"))["event"] == "planner.progress"
    # 回放事件即使旧于实时事件，也按实际发送顺序衔接；视图层另行拒绝状态回滚。
    assert encoder.encode(message(5))["data"]["base_event_id"] == 10
    assert encoder.encode(message(12))["data"]["base_event_id"] == 5
    assert encoder.encode(message(13, session_id="group-b"))["data"]["base_event_id"] == 11


def test_evicted_round_restarts_with_full_snapshot():
    encoder = PlannerDeltaEncoder()
    for index in range(MAX_PLANNER_BASES + 1):
        encoder.encode(message(index + 1, cycle_id=index))
    assert encoder.encode(message(200, cycle_id=0))["event"] == "planner.progress"


def test_removed_fields_and_null_values_are_not_confused():
    encoder = PlannerDeltaEncoder()
    encoder.encode(message())
    next_message = message(2, request=None)
    del next_message["data"]["active_tool_call_id"]
    delta = encoder.encode(next_message)["data"]
    assert delta["changes"]["request"] is None
    assert delta["removed_fields"] == ["active_tool_call_id"]


def test_non_planner_events_pass_through_and_unversioned_snapshots_fail():
    encoder = PlannerDeltaEncoder()
    original = message(event="message.ingested")
    assert encoder.encode(original) is original
    for invalid in (message(run_id=""), message(event_id=None), message(event_id=0)):
        with raises(ValueError, match="run_id/event_id"):
            encoder.encode(invalid)


def test_sender_always_encodes_and_resets_in_queue_order(monkeypatch):
    # 发送协议测试不启动应用日志的文件扫描/清理任务。
    logger_module = ModuleType("src.common.logger")
    logger_module.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, "src.common.logger", logger_module)
    from src.webui.routers.websocket.manager import UnifiedWebSocketManager, WebSocketConnection

    class Socket:
        def __init__(self):
            self.frames = []

        async def send_text(self, value):
            self.frames.append(json.loads(value))

    async def send():
        socket = Socket()
        connection = WebSocketConnection(connection_id="test", websocket=socket)
        frames = [
            message(), message(2),
            {"op": "event", "domain": "maisaka_monitor", "event": "planner.reset", "data": {}},
            message(3), message(4),
            {"op": "event", "domain": "maisaka_monitor", "event": "planner.reset", "data": {}},
            message(5), message(6, "planner.finalized"),
        ]
        for frame in frames:
            await connection.send_queue.put(frame)
        await connection.send_queue.put(None)
        await UnifiedWebSocketManager()._sender_loop(connection)
        return socket.frames

    frames = asyncio.run(send())
    assert [frame["event"] for frame in frames] == [
        "planner.progress", "planner.delta", "planner.reset", "planner.progress",
        "planner.delta", "planner.reset", "planner.progress", "planner.delta",
    ]
    # 无需协商即可裁剪并增量发送；每次重订阅按出站队列顺序重建基准。
    assert "messages" not in frames[0]["data"]["request"]
    assert frames[1]["data"]["base_event_id"] == 1
    assert "messages" not in frames[3]["data"]["request"]
    assert frames[4]["data"]["base_event_id"] == 3
    assert frames[7]["data"]["base_event_id"] == 5

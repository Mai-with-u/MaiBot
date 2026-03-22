from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.memory_system.retrieval_tools import query_long_term_memory as tool_module
from src.memory_system.retrieval_tools import init_all_tools
from src.memory_system.retrieval_tools.query_long_term_memory import (
    _resolve_time_expression,
    query_long_term_memory,
    register_tool,
)
from src.memory_system.retrieval_tools.tool_registry import get_tool_registry
from src.services.memory_service import MemoryHit, MemorySearchResult


def test_resolve_time_expression_supports_relative_and_absolute_inputs():
    now = datetime(2026, 3, 18, 15, 30)

    start_ts, end_ts, start_text, end_text = _resolve_time_expression("今天", now=now)
    assert datetime.fromtimestamp(start_ts) == datetime(2026, 3, 18, 0, 0)
    assert datetime.fromtimestamp(end_ts) == datetime(2026, 3, 18, 23, 59)
    assert start_text == "2026/03/18 00:00"
    assert end_text == "2026/03/18 23:59"

    start_ts, end_ts, start_text, end_text = _resolve_time_expression("最近7天", now=now)
    assert datetime.fromtimestamp(start_ts) == datetime(2026, 3, 12, 0, 0)
    assert datetime.fromtimestamp(end_ts) == datetime(2026, 3, 18, 23, 59)
    assert start_text == "2026/03/12 00:00"
    assert end_text == "2026/03/18 23:59"

    start_ts, end_ts, start_text, end_text = _resolve_time_expression("2026/03/18", now=now)
    assert datetime.fromtimestamp(start_ts) == datetime(2026, 3, 18, 0, 0)
    assert datetime.fromtimestamp(end_ts) == datetime(2026, 3, 18, 23, 59)
    assert start_text == "2026/03/18 00:00"
    assert end_text == "2026/03/18 23:59"

    start_ts, end_ts, start_text, end_text = _resolve_time_expression("2026/03/18 09:30", now=now)
    assert datetime.fromtimestamp(start_ts) == datetime(2026, 3, 18, 9, 30)
    assert datetime.fromtimestamp(end_ts) == datetime(2026, 3, 18, 9, 30)
    assert start_text == "2026/03/18 09:30"
    assert end_text == "2026/03/18 09:30"


def test_register_tool_exposes_mode_and_time_expression():
    register_tool()
    tool = get_tool_registry().get_tool("search_long_term_memory")

    assert tool is not None
    params = {item["name"]: item for item in tool.parameters}
    assert "mode" in params
    assert params["mode"]["enum"] == ["search", "time", "episode", "aggregate"]
    assert "time_expression" in params
    assert params["query"]["required"] is False


def test_init_all_tools_registers_long_term_memory_tool():
    init_all_tools()

    tool = get_tool_registry().get_tool("search_long_term_memory")
    assert tool is not None


@pytest.mark.asyncio
async def test_query_long_term_memory_search_mode_maps_to_hybrid(monkeypatch):
    captured = {}

    async def fake_search(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return MemorySearchResult(
            hits=[MemoryHit(content="Alice 喜欢猫", score=0.9, hit_type="paragraph")],
        )

    monkeypatch.setattr(tool_module, "memory_service", SimpleNamespace(search=fake_search))

    text = await query_long_term_memory("Alice 喜欢什么", chat_id="stream-1", person_id="person-1")

    assert "Alice 喜欢猫" in text
    assert captured == {
        "query": "Alice 喜欢什么",
        "kwargs": {
            "limit": 5,
            "mode": "hybrid",
            "chat_id": "stream-1",
            "person_id": "person-1",
            "time_start": None,
            "time_end": None,
        },
    }


@pytest.mark.asyncio
async def test_query_long_term_memory_time_mode_parses_expression(monkeypatch):
    captured = {}

    async def fake_search(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return MemorySearchResult(
            hits=[
                MemoryHit(
                    content="昨天晚上广播站停播了十分钟。",
                    score=0.8,
                    hit_type="paragraph",
                    metadata={"event_time_start": 1773797400.0},
                )
            ]
        )

    monkeypatch.setattr(tool_module, "memory_service", SimpleNamespace(search=fake_search))
    monkeypatch.setattr(
        tool_module,
        "_resolve_time_expression",
        lambda expression, now=None: (1773795600.0, 1773881940.0, "2026/03/17 00:00", "2026/03/17 23:59"),
    )

    text = await query_long_term_memory(
        query="广播站",
        mode="time",
        time_expression="昨天",
        chat_id="stream-1",
    )

    assert "指定时间范围" in text
    assert "广播站停播" in text
    assert captured == {
        "query": "广播站",
        "kwargs": {
            "limit": 5,
            "mode": "time",
            "chat_id": "stream-1",
            "person_id": "",
            "time_start": 1773795600.0,
            "time_end": 1773881940.0,
        },
    }


@pytest.mark.asyncio
async def test_query_long_term_memory_episode_and_aggregate_format_output(monkeypatch):
    responses = {
        "episode": MemorySearchResult(
            hits=[
                MemoryHit(
                    content="苏弦在灯塔拆开了那封冬信。",
                    title="冬信重见天日",
                    hit_type="episode",
                    metadata={"participants": ["苏弦"], "keywords": ["冬信", "灯塔"]},
                )
            ]
        ),
        "aggregate": MemorySearchResult(
            hits=[
                MemoryHit(
                    content="唐未在广播站值夜班时带着黑狗墨点。",
                    hit_type="paragraph",
                    metadata={"source_branches": ["search", "time"]},
                )
            ]
        ),
    }

    async def fake_search(query, **kwargs):
        return responses[kwargs["mode"]]

    monkeypatch.setattr(tool_module, "memory_service", SimpleNamespace(search=fake_search))

    episode_text = await query_long_term_memory("那封冬信后来怎么样了", mode="episode")
    aggregate_text = await query_long_term_memory("唐未最近有什么线索", mode="aggregate")

    assert "事件《冬信重见天日》" in episode_text
    assert "参与者：苏弦" in episode_text
    assert "[search,time][paragraph]" in aggregate_text


@pytest.mark.asyncio
async def test_query_long_term_memory_invalid_time_expression_returns_retryable_message():
    text = await query_long_term_memory(query="广播站", mode="time", time_expression="明年春分后第三周")

    assert "无法解析" in text
    assert "最近7天" in text

from contextlib import closing

import json
import sqlite3

import pytest

from src.webui.services import ai_help_diagnostics as diagnostics


@pytest.mark.asyncio
async def test_recent_logs_limit_io_and_redact_credentials_and_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(diagnostics, "_LOG_TAIL_BYTES", 1024)
    rows = [
        {"level": "ERROR", "event": "OUTSIDE_TAIL " + "a" * 1600},
        {"level": "INFO", "event": "INFO_ONLY"},
        {
            "level": "WARNING",
            "event": "Upstream https://username:URL_SECRET@example.test/private-path?unknown=QUERY_SECRET failed.",
            "timestamp": "11-05 14:02:03",
            "logger_name": "maibot.adapter",
            "payload": {"body": "STRUCTURED_PRIVATE_BODY"},
        },
        {"level": "ERROR", "event": 'HTTP 401 {"token":"TOKEN_VALUE_WITHOUT_KNOWN_PREFIX"}'},
        {"level": "ERROR", "event": "Network timeout\nSTACK_OR_MESSAGE_DETAILS"},
    ]
    file_path = tmp_path / "app_20261105_140203.log.jsonl"
    file_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = await diagnostics.execute_diagnostic_tool("read_recent_logs", {"limit": 3})
    entries = result["logs"]
    assert [entry["level"] for entry in entries] == ["ERROR", "ERROR", "WARNING"]
    assert entries[0]["message"] == "Network timeout"
    assert entries[1]["http_status"] == 401
    assert entries[2] == {
        "level": "WARNING",
        "message": "Upstream https://example.test failed.",
        "timestamp": "11-05 14:02:03",
        "module": "maibot.adapter",
    }
    for forbidden in ("OUTSIDE_TAIL", "INFO_ONLY", "SECRET", "TOKEN_VALUE", "DETAILS", "PRIVATE_BODY"):
        assert forbidden not in json.dumps(result)

    critical_only = await diagnostics.execute_diagnostic_tool("read_recent_logs", {"level": "CRITICAL"})
    assert critical_only == {"logs": []}
    assert "error" in await diagnostics.execute_diagnostic_tool("read_recent_logs", {"limit": 100})
    assert "error" in await diagnostics.execute_diagnostic_tool("read_recent_logs", {"path": "../outside"})


@pytest.mark.asyncio
async def test_reasoning_selector_projects_safe_metadata_from_recent_and_older_records(tmp_path, monkeypatch):
    from src.webui.routers import reasoning_process

    root = tmp_path / "reasoning"
    monkeypatch.setattr(diagnostics, "_REASONING_ROOT", root)
    monkeypatch.setattr(reasoning_process, "PROMPT_LOG_ROOT", root)
    current = root / "llm_error" / "private-session" / "123.json"
    current.parent.mkdir(parents=True)
    old = current.with_name("122.json")
    payload = {
        "metadata": {"model_name": "openai/gpt-test", "status": "failed", "duration_ms": 140, "total_tokens": 25},
        "request_items": [{"parts": [{"text": "PRIVATE_PROMPT"}]}],
        "output_items": [
            {"item_type": "ReasoningItem", "text_parts": ["PRIVATE_THOUGHT"]},
            {
                "item_type": "FunctionCallItem",
                "tool_call": {"func_name": "lookup_model", "args": {"key": "PRIVATE_ARGUMENT"}},
            },
            {
                "item_type": "FunctionCallOutputItem",
                "tool_name": "lookup_model",
                "success": False,
                "output": "PRIVATE_RESULT",
            },
            {"item_type": "AssistantMessageItem", "parts": [{"text": "PRIVATE_CHAT_REPLY"}]},
        ],
        "generation_attempts": [
            {"error": {"type": "BadRequestError", "status_code": 400, "message": "PRIVATE_ERROR_BODY"}}
        ],
    }
    for path in (current, old):
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    current.with_suffix(".events.jsonl").write_text(
        '{"type":"attempt_status","status":"final_failed","request":"PRIVATE_EVENT"}\n', encoding="utf-8"
    )
    with closing(sqlite3.connect(root / diagnostics._REASONING_INDEX_NAME)) as conn, conn:
        conn.execute("CREATE TABLE steps(stage TEXT, session TEXT, stem TEXT, trace_id TEXT)")
        conn.executemany(
            "INSERT INTO steps VALUES (?, ?, ?, ?)",
            [
                ("llm_error", "private-session", "123", "trace"),
                ("llm_error", "../escape", "124", "trace"),
            ],
        )

    [listed] = (await diagnostics.execute_diagnostic_tool("list_recent_reasoning", {"limit": 2}))["records"]
    recent_result = await diagnostics.execute_diagnostic_tool("read_reasoning_record", {"record_id": listed["id"]})
    assert recent_result["record"] == {
        "id": listed["id"],
        "stage": "llm_error",
        "model": "openai/gpt-test",
        "status": "final_failed",
        "duration_ms": 140,
        "total_tokens": 25,
        "attempt_count": 1,
        "tools": [{"name": "lookup_model", "success": False}],
        "last_error": {"type": "BadRequestError", "http_status": 400},
    }
    exact = await diagnostics.execute_diagnostic_tool(
        "read_reasoning_record", {"record_path": "llm_error/private-session/122.json"}
    )
    assert exact["record"]["id"] != listed["id"]
    assert exact["record"]["status"] == "failed"
    assert "PRIVATE" not in json.dumps([listed, recent_result, exact])
    assert "private-session" not in json.dumps([listed, recent_result, exact])

    escape = await diagnostics.execute_diagnostic_tool("read_reasoning_record", {"record_path": "../../data.json"})
    assert "error" in escape
    both = await diagnostics.execute_diagnostic_tool(
        "read_reasoning_record", {"record_id": listed["id"], "record_path": "llm_error/private-session/122.json"}
    )
    assert "error" in both


@pytest.mark.asyncio
async def test_large_reasoning_record_only_projects_bounded_metadata(tmp_path, monkeypatch):
    from src.webui.routers import reasoning_process

    monkeypatch.setattr(reasoning_process, "PROMPT_LOG_ROOT", tmp_path)
    monkeypatch.setattr(diagnostics, "_REASONING_MAX_BYTES", 1024)
    monkeypatch.setattr(diagnostics, "_REASONING_METADATA_BYTES", 512)
    record_path = "planner/session/123.json"
    file_path = tmp_path / record_path
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        json.dumps({"metadata": {"model_name": "test-model"}, "request_items": ["private" * 1024]}, indent=2),
        encoding="utf-8",
    )

    result = await diagnostics.execute_diagnostic_tool("read_reasoning_record", {"record_path": record_path})
    assert result["record"]["model"] == "test-model"
    assert result["record"]["partial"] is True
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["本轮没有注入内容。", "PRIVATE_MEMORY_CONTENT"])
async def test_context_injection_reports_presence_without_copying_memory(tmp_path, monkeypatch, content):
    from src.webui.routers import reasoning_process

    monkeypatch.setattr(reasoning_process, "PROMPT_LOG_ROOT", tmp_path)
    record_path = "memory_image/session/123.json"
    file_path = tmp_path / record_path
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        json.dumps(
            {
                "request": {"kind": "memory_image"},
                "metadata": {"request_source": "context_injection"},
                "output_items": [{"item_type": "UserMessageItem", "parts": [{"type": "text", "text": content}]}],
            }
        ),
        encoding="utf-8",
    )

    result = await diagnostics.execute_diagnostic_tool("read_reasoning_record", {"record_path": record_path})
    assert result["record"]["source"] == "context_injection"
    assert result["record"]["request_kind"] == "memory_image"
    assert result["record"]["has_injected_context"] is (content == "PRIVATE_MEMORY_CONTENT")
    assert content not in json.dumps(result, ensure_ascii=False)

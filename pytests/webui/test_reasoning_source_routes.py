from urllib.parse import quote
import json

import pytest

from src.webui.routers import reasoning_process as routes


@pytest.mark.parametrize("source_session,exists", [("chat", True), ("other-chat", True), ("chat", False)])
def test_passive_reply_source_uses_explicit_planner_record(tmp_path, monkeypatch, source_session, exists):
    monkeypatch.setattr(routes, "PROMPT_LOG_ROOT", tmp_path)
    source_path = f"planner/{source_session}/100.json"
    if exists:
        source_file = tmp_path / source_path
        source_file.parent.mkdir(parents=True)
        source_file.write_text(json.dumps({"output_items": []}), encoding="utf-8")
    reply_file = tmp_path / "replyer/chat/200.json"
    reply_file.parent.mkdir(parents=True)
    reply_file.write_text(json.dumps({"metadata": {
        "source_planner_uri": f"/api/webui/config/maisaka-prompt-preview?path={quote(source_path, safe='')}",
    }}), encoding="utf-8")
    result = routes.find_reasoning_tool_call_source(session="chat", stage="replyer", stem="200", call_id="execution")
    if source_session == "chat" and exists:
        assert result.record is not None
        assert (result.record.stage, result.record.session, result.record.stem) == ("planner", "chat", "100")
    else:
        assert result.record is None


def test_explicit_tool_call_source_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "PROMPT_LOG_ROOT", tmp_path)
    source_file = tmp_path / "planner/chat/100.json"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(json.dumps({"output_items": [{
        "item_type": "FunctionCallItem", "tool_call": {"call_id": "call-1"},
    }]}), encoding="utf-8")
    result = routes.find_reasoning_tool_call_source(session="chat", stage="replyer", stem="200", call_id="call-1")
    assert result.record is not None
    assert result.record.stem == "100"

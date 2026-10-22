"""验证终端关闭后仍保留完整推理记录及 WebUI 入口。"""

from types import SimpleNamespace

import json

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemMeta, ContextTextPart, UserMessageItem
from src.maisaka.chat_loop_service import MaisakaChatLoopService
from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger
from src.maisaka.display.runtime_mixin import MaisakaRuntimeDisplayMixin
from src.maisaka.memory.mid_term import _save_mid_term_memory_prompt_preview
from src.maisaka.monitor.events import _serialize_tool_results
from src.maisaka.reasoning_engine import CycleEnd, CycleRuntimeState, MaisakaReasoningEngine


@pytest.fixture
def saved_records(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr(PromptPreviewLogger, "_BASE_DIR", tmp_path)

    def save_preview_file(chat_id, category, content):
        path = tmp_path / category / chat_id / f"{len(records)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        records.append(path)
        return path

    monkeypatch.setattr(PromptPreviewLogger, "save_preview_file", save_preview_file)
    return records


@pytest.mark.parametrize("show", [False, True])
@pytest.mark.parametrize("save", [False, True])
def test_tool_records_and_webui_links_are_independent_of_console(monkeypatch, saved_records, show, save):
    monkeypatch.setattr(global_config.debug, "show_maisaka_thinking", show)
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", save)
    printed = []
    monkeypatch.setattr("src.maisaka.display.runtime_mixin.console.print", printed.append)
    runtime = MaisakaRuntimeDisplayMixin()
    runtime.session_id = "test-session"
    runtime.session_name = "测试群"
    monkeypatch.setattr(runtime, "_get_effective_reply_frequency", lambda: 1.0, raising=False)
    monkeypatch.setattr(runtime, "_format_reply_frequency_for_display", str, raising=False)
    result = LLMResponseResult.from_portable_output(response="完整回复", reasoning="完整推理", model_name="test")
    detail = {
        "prompt_text": "完整提示词",
        "output_items": list(result.output_items),
        "output_text": "完整回复",
        "metrics": {"model_name": "test", "prompt_tokens": 123},
        "generation_attempts": [{"attempt_id": "attempt-1", "status": "succeeded"}],
        "additional_prompt_records": [{"prompt_text": "附加请求", "output_text": "附加输出"}],
    }
    tools = [{"tool_name": "reply", "tool_call_id": "call-1", "detail": detail}]
    panels = runtime._prepare_tool_prompt_records(tools)
    runtime._render_context_usage_panel(planner_tool_detail_results=tools, tool_prompt_panels=panels)
    assert bool(printed) is show
    assert len(saved_records) == (2 if save else 0)
    serialized = _serialize_tool_results(tools)[0]
    if save:
        payload = json.loads(saved_records[0].read_text(encoding="utf-8"))
        assert payload["output_items"][0]["text_parts"] == ["完整推理"]
        assert payload["generation_attempts"][0]["attempt_id"] == "attempt-1"
        assert payload["metadata"]["prompt_tokens"] == 123
        assert serialized["prompt_html_uri"].startswith("/api/webui/config/maisaka-prompt-preview?")
        assert len(serialized["detail"]["additional_prompt_html_uris"]) == 1
    else:
        assert "prompt_html_uri" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("show", [False, True])
@pytest.mark.parametrize("save", [False, True])
async def test_planner_and_memory_records_follow_save_switch(monkeypatch, saved_records, show, save):
    monkeypatch.setattr(global_config.debug, "show_maisaka_thinking", show)
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", save)
    result = LLMResponseResult.from_portable_output(response="回复正文", reasoning="推理正文", model_name="test")

    class Client:
        async def generate_response_with_context(self, context_factory, options):
            context_factory(None)
            return result

    class Hooks:
        async def invoke_hook(self, hook_name, **kwargs):
            return SimpleNamespace(kwargs=kwargs)

    service = MaisakaChatLoopService(chat_system_prompt="测试系统提示词")
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda request_kind: Client())
    monkeypatch.setattr(MaisakaChatLoopService, "_get_runtime_manager", staticmethod(lambda: Hooks()))
    response = await service.chat_loop_step([])
    assert bool(response.prompt_html_uri) is save
    assert (response.prompt_section is not None) is (show and save)
    _save_mid_term_memory_prompt_preview(
        [UserMessageItem(meta=ContextItemMeta.create(), parts=(ContextTextPart("回想输入"),))],
        result=result, session_id="test-session", time_range="测试时段", participants=["测试用户"], log_prefix="测试",
    )
    assert len(saved_records) == (2 if save else 0)
    if save:
        for path in saved_records:
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["output_items"][0]["text_parts"] == ["推理正文"]


@pytest.mark.asyncio
async def test_cycle_saves_tool_records_before_webui_broadcast_with_console_off(monkeypatch, saved_records):
    monkeypatch.setattr(global_config.debug, "show_maisaka_thinking", False)
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", True)
    monkeypatch.setattr(
        "src.maisaka.display.runtime_mixin.console.print",
        lambda *args: pytest.fail("关闭终端显示后不应打印大框"),
    )
    runtime = MaisakaRuntimeDisplayMixin()
    runtime.session_id = "test-session"
    runtime._max_internal_rounds = 10
    runtime._monitor_run_id = "test-run"
    runtime._agent_state = "idle"
    runtime._idle_backoff = SimpleNamespace(record_cycle_result=lambda reason: None)
    monkeypatch.setattr(runtime, "record_idle_cycle_result", lambda reason: None, raising=False)
    engine = MaisakaReasoningEngine(runtime)
    cycle = SimpleNamespace(cycle_id=1, time_records={})

    async def end_cycle(cycle_detail):
        return cycle

    broadcasts = []

    async def broadcast(**kwargs):
        broadcasts.append(_serialize_tool_results(kwargs["tools"]))

    monkeypatch.setattr(engine, "_end_cycle", end_cycle)
    monkeypatch.setattr("src.maisaka.reasoning_engine.emit_planner_snapshot", broadcast)
    state = CycleRuntimeState(
        cycle_end=CycleEnd("wait", "等待消息"),
        tool_monitor_results=[{
            "tool_name": "reply", "tool_call_id": "call-1",
            "detail": {"prompt_text": "完整请求", "output_text": "完整输出"},
        }],
    )
    await engine._finalize_cycle(cycle_detail=cycle, round_index=0, state=state)
    assert len(saved_records) == 1
    assert broadcasts[0][0]["prompt_html_uri"].startswith("/api/webui/config/maisaka-prompt-preview?")

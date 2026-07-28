"""Maisaka 回复文本后处理策略测试。"""

from types import SimpleNamespace

import pytest

from src.maisaka.builtin_tool import context as context_module
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.reply import _invoke_before_post_process_hook
from src.maisaka.chat_loop_service import register_maisaka_hook_specs
from src.plugin_runtime.host.hook_spec_registry import HookSpecRegistry


def test_skip_post_process_returns_original_reply(monkeypatch) -> None:
    def fail_process(*args, **kwargs) -> list[str]:
        raise AssertionError("跳过后处理时不应调用 process_llm_response")

    monkeypatch.setattr(context_module, "process_llm_response", fail_process)

    segments = BuiltinToolRuntimeContext.post_process_reply_text(
        "第一句。第二句。",
        skip_post_process=True,
    )

    assert segments == ["第一句。第二句。"]


def test_post_process_options_are_independent(monkeypatch) -> None:
    calls: list[tuple[str, bool, bool]] = []

    def fake_process(text: str, enable_splitter: bool, enable_chinese_typo: bool) -> list[str]:
        calls.append((text, enable_splitter, enable_chinese_typo))
        return [text]

    monkeypatch.setattr(context_module, "process_llm_response", fake_process)

    segments = BuiltinToolRuntimeContext.post_process_reply_text(
        "保留完整回复",
        enable_splitter=False,
        enable_chinese_typo=True,
    )

    assert segments == ["保留完整回复"]
    assert calls == [("保留完整回复", False, True)]


def test_before_post_process_hook_exposes_per_reply_options() -> None:
    registry = HookSpecRegistry()
    register_maisaka_hook_specs(registry)

    spec = registry.get_hook_spec("maisaka.reply.before_post_process")

    assert spec is not None
    properties = spec.parameters_schema["properties"]
    assert properties["skip_post_process"]["type"] == "boolean"
    assert properties["enable_splitter"]["type"] == "boolean"
    assert properties["enable_chinese_typo"]["type"] == "boolean"


@pytest.mark.asyncio
async def test_before_post_process_hook_controls_current_reply_only() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class RuntimeManager:
        async def invoke_hook(self, hook_name: str, **kwargs: object) -> SimpleNamespace:
            calls.append((hook_name, kwargs))
            return SimpleNamespace(
                kwargs={
                    **kwargs,
                    "skip_post_process": kwargs["response"] == "本次保持原文",
                }
            )

    tool_ctx = SimpleNamespace(
        runtime=SimpleNamespace(session_id="session-1"),
        get_runtime_manager=lambda: RuntimeManager(),
    )

    reply_text, options = await _invoke_before_post_process_hook(
        tool_ctx,
        "本次保持原文",
        "message-1",
        {"reply_guide": "测试"},
    )

    assert reply_text == "本次保持原文"
    assert options == {
        "skip_post_process": True,
        "enable_splitter": True,
        "enable_chinese_typo": True,
    }
    assert calls[0][0] == "maisaka.reply.before_post_process"
    assert calls[0][1]["session_id"] == "session-1"
    assert calls[0][1]["reply_message_id"] == "message-1"

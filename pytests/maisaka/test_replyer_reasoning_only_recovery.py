"""replyer 仅返回推理内容时的恢复路径回归测试（#2188）。

openai_responses 通过 ReasoningItem 回放续写一次；chat_completions 等
非 Responses 协议无法回放 ReasoningItem，改为带约束重生成一次。
"""
from types import SimpleNamespace
from typing import List, Optional

import pytest

from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.model_client.base_client import GenerationTrace
from src.llm_models.payload_content.context_item import build_portable_output_items, get_item_text

REASONING_TEXT = "The user wants me to continue the conversation naturally. I should think about tone first."
VISIBLE_TEXT = "来了来了，刚才在想怎么回你才自然。"


def _make_result(*, reasoning: str = "", content: str = "") -> LLMResponseResult:
    items = build_portable_output_items(
        content=content or None,
        reasoning=reasoning or None,
        logical_turn_id="turn-2188",
    )
    trace = GenerationTrace(
        provider="FakeProvider",
        endpoint="https://example.invalid",
        model="MiniMax-M3",
        response_id="resp-2188",
        status="completed",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=0,
        output_item_ids=tuple(item.meta.item_id for item in items),
    )
    return LLMResponseResult(
        output_items=tuple(items),
        generation_trace=trace,
        model_name="MiniMax-M3",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )


class _FakeRuntime:
    after_response_queue: List[dict] = []

    async def invoke_hook(self, *args, **kwargs):
        hook_name = args[0] if args else ""
        if hook_name == "maisaka.replyer.after_response" and _FakeRuntime.after_response_queue:
            return SimpleNamespace(kwargs=_FakeRuntime.after_response_queue.pop(0))
        return SimpleNamespace(kwargs={})


class _FakeLLMClient:
    script: List[LLMResponseResult] = []
    captured: List[str] = []
    client_type: Optional[str] = "openai"

    def __init__(self, task_name: str = "replyer", request_type: str = "maisaka.replyer", session_id: str = "") -> None:
        self.task_name = task_name
        self.request_type = request_type
        self.session_id = session_id
        # client_type=None 表示 provider 根本没有 client_type 属性（不是空字符串）。
        self.api_provider = (
            SimpleNamespace()
            if _FakeLLMClient.client_type is None
            else SimpleNamespace(client_type=_FakeLLMClient.client_type)
        )

    async def generate_response_with_context(self, context_factory=None, options=None) -> LLMResponseResult:
        items = await context_factory(self) if context_factory is not None else []
        _FakeLLMClient.captured.append("\n".join(get_item_text(item) for item in items))
        assert _FakeLLMClient.script, "模型调用次数超过脚本预期"
        return _FakeLLMClient.script.pop(0)


def _build_generator() -> BaseMaisakaReplyGenerator:
    generator = object.__new__(BaseMaisakaReplyGenerator)
    generator.chat_stream = None
    generator.request_type = "maisaka.replyer"
    generator._llm_client_cls = _FakeLLMClient
    generator._load_prompt = load_prompt
    generator.express_model = _FakeLLMClient()
    generator._get_runtime_manager = lambda: _FakeRuntime()
    return generator


async def _run(client_type: Optional[str], script: List[LLMResponseResult], after_response_queue: List[dict] = ()):
    _FakeLLMClient.client_type = client_type
    _FakeLLMClient.script = list(script)
    _FakeLLMClient.captured = []
    _FakeRuntime.after_response_queue = list(after_response_queue)
    generator = _build_generator()
    success, result = await generator.generate_reply_with_context(
        stream_id="test-session-2188",
        chat_history=[],
        reply_message=None,
        reply_reason="test",
        sub_agent_runner=None,
    )
    return success, result, _FakeLLMClient.captured


@pytest.fixture(autouse=True)
def _disable_reasoning_preview(monkeypatch) -> None:
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", False)


@pytest.mark.asyncio
async def test_chat_completions_reasoning_only_recovers_via_regeneration() -> None:
    success, result, captured = await _run(
        "openai",
        [_make_result(reasoning=REASONING_TEXT), _make_result(content=VISIBLE_TEXT)],
    )

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 2
    # 第二次请求必须带上「直接输出正文」的重生成约束，而不是原样重发。
    assert "只输出了思考内容" in captured[1]
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 1


@pytest.mark.asyncio
async def test_chat_completions_visible_first_try_not_regenerated() -> None:
    success, result, captured = await _run("openai", [_make_result(content=VISIBLE_TEXT)])

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 1
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 0


@pytest.mark.asyncio
async def test_chat_completions_reasoning_only_twice_regenerates_once_then_fails() -> None:
    success, result, captured = await _run(
        "openai",
        [_make_result(reasoning=REASONING_TEXT), _make_result(reasoning=REASONING_TEXT)],
    )

    assert success is False
    assert result.error_message == "回复器返回了空内容"
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_openai_responses_reasoning_only_still_uses_continuation() -> None:
    success, result, captured = await _run(
        "openai_responses",
        [_make_result(reasoning=REASONING_TEXT), _make_result(content=VISIBLE_TEXT)],
    )

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 2
    # Responses 路径是回放续写：第二次请求包含第一次的推理内容，且不带重生成约束句。
    assert REASONING_TEXT in captured[1]
    assert "只输出了思考内容" not in captured[1]


@pytest.mark.asyncio
async def test_hook_rewrite_to_visible_text_does_not_trigger_regeneration() -> None:
    # after_response 钩子已把 reasoning-only 改写为可见正文时，不应再触发恢复。
    success, result, captured = await _run(
        "openai",
        [_make_result(reasoning=REASONING_TEXT)],
        after_response_queue=[{"response": VISIBLE_TEXT}],
    )

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 1
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 0
    assert result.metrics.extra["replyer_retry_count"] == 0


@pytest.mark.asyncio
async def test_hook_retry_request_is_not_overwritten_by_reasoning_recovery() -> None:
    # 钩子已对 reasoning-only 请求重试时，同轮走钩子的 reason，新分支不叠加、不置门闩。
    success, result, captured = await _run(
        "openai",
        [_make_result(reasoning=REASONING_TEXT), _make_result(content=VISIBLE_TEXT)],
        after_response_queue=[{"retry": True, "retry_reason": "钩子要求重生成"}],
    )

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 2
    assert "钩子要求重生成" in captured[1]
    assert "只输出了思考内容" not in captured[1]
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 0
    assert result.metrics.extra["replyer_retry_count"] == 1


@pytest.mark.asyncio
async def test_empty_response_without_reasoning_does_not_trigger_recovery() -> None:
    # 纯空响应（无 ReasoningItem、无正文）不是 reasoning-only，不触发任何恢复。
    success, result, captured = await _run("openai", [_make_result()])

    assert success is False
    assert result.error_message == "回复器返回了空内容"
    assert len(captured) == 1
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 0


@pytest.mark.asyncio
async def test_recovery_warning_log_excludes_reasoning_content(monkeypatch) -> None:
    # 隐私回归（CodeRabbit Major）：恢复 warning 不得记录推理原文——
    # 推理文本可能含用户私密信息，且 save_maisaka_reasoning=False 也关不掉这条日志。
    import src.chat.replyer.maisaka_generator_base as base_mod

    captured_warnings = []
    real_warning = base_mod.logger.warning

    def _capture(msg, *args, **kwargs):
        text = msg % args if args else msg
        captured_warnings.append(text)
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(base_mod.logger, "warning", _capture)
    success, result, _ = await _run(
        "openai",
        [_make_result(reasoning=REASONING_TEXT), _make_result(content=VISIBLE_TEXT)],
    )

    assert success is True
    recovery_warnings = [w for w in captured_warnings if "将重生成一次" in w]
    assert recovery_warnings, "恢复 warning 未触发，测试前置失效"
    assert all(REASONING_TEXT not in w for w in recovery_warnings)
    assert all("reasoning=" not in w for w in recovery_warnings)


@pytest.mark.asyncio
async def test_missing_client_type_reasoning_only_recovers_via_regeneration() -> None:
    # provider 缺少 client_type 属性时，生产代码经 getattr 归一化为空字符串，
    # 按非 Responses 处理：带约束重生成一次，而不是走 Responses 回放续写。
    success, result, captured = await _run(
        None,
        [_make_result(reasoning=REASONING_TEXT), _make_result(content=VISIBLE_TEXT)],
    )

    assert success is True
    assert result.completion.response_text.strip() == VISIBLE_TEXT
    assert len(captured) == 2
    assert "只输出了思考内容" in captured[1]
    # 第二次请求不含原推理文本，证明走的是重生成而非 ReasoningItem 回放续写。
    assert REASONING_TEXT not in captured[1]
    assert result.metrics.extra["replyer_reasoning_continuation_count"] == 1

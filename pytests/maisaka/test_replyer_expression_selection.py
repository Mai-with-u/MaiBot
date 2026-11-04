from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.replyer.maisaka_expression_selector import (
    MaisakaExpressionSelectionResult,
    MaisakaExpressionSelector,
    expression_vector_index,
)
from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator, maisaka_expression_selector
from src.config.config import global_config


@pytest.mark.asyncio
@pytest.mark.parametrize("retro", [False, True], ids=["normal", "retro"])
async def test_reply_context_preserves_expression_selection_in_creative_mode(monkeypatch, retro: bool) -> None:
    generator = object.__new__(BaseMaisakaReplyGenerator)
    monkeypatch.setattr(global_config.chat, "creative_mode", retro)
    monkeypatch.setattr(generator, "_resolve_session_id", lambda stream_id: stream_id)
    selection = MaisakaExpressionSelectionResult(
        expression_habits="测试表达", selected_expression_ids=[1],
    )
    select_for_reply = AsyncMock(return_value=selection)
    monkeypatch.setattr(maisaka_expression_selector, "select_for_reply", select_for_reply)
    runner = AsyncMock(return_value='{"selected_ids":[1]}')

    result = await generator._build_reply_context(
        chat_history=[], reply_message=None, reply_reason="测试回复",
        stream_id="test-session", sub_agent_runner=runner,
    )

    select_for_reply.assert_awaited_once()
    assert select_for_reply.call_args.kwargs["sub_agent_runner"] is runner
    assert result.expression_habits == "测试表达"
    assert result.selected_expression_ids == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fine", [False, True], ids=["fast", "fine"])
async def test_expression_mode_controls_vector_and_llm_calls(monkeypatch, fine: bool) -> None:
    selector = MaisakaExpressionSelector()
    candidates = [
        {"id": index, "situation": f"情景{index}", "style": f"表达{index}", "count": index + 2}
        for index in range(12)
    ]
    monkeypatch.setattr(global_config.expression, "use_vector_expression", fine)
    monkeypatch.setattr(selector, "_can_use_expressions", lambda session_id: True)
    monkeypatch.setattr(selector, "_load_all_expression_candidates", lambda session_id: candidates)
    monkeypatch.setattr(selector, "_has_embedding_model_configured", lambda: True)
    update_activity = Mock()
    monkeypatch.setattr(selector, "_update_last_active_time", update_activity)
    vector_select = AsyncMock(return_value=candidates[:8])
    monkeypatch.setattr(expression_vector_index, "select_candidates", vector_select)

    async def invoke_hook(_name: str, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(aborted=False, kwargs=kwargs)

    monkeypatch.setattr(selector, "_get_runtime_manager", lambda: SimpleNamespace(invoke_hook=invoke_hook))
    runner = AsyncMock(return_value='{"selected_ids":[1]}')

    result = await selector.select_for_reply(
        session_id="test-session", chat_history=[], reply_message=None,
        reply_reason="测试回复", sub_agent_runner=runner,
    )

    if fine:
        vector_select.assert_awaited_once()
        runner.assert_awaited_once()
        assert result.selected_expression_ids == [1]
    else:
        vector_select.assert_not_awaited()
        runner.assert_not_awaited()
        assert 5 <= len(result.selected_expression_ids) <= 10
        assert len(set(result.selected_expression_ids)) == len(result.selected_expression_ids)
    assert result.expression_habits
    assert update_activity.call_args.args[0] == result.selected_expression_ids


@pytest.mark.asyncio
async def test_fast_reply_context_does_not_require_selector_runner(monkeypatch) -> None:
    generator = object.__new__(BaseMaisakaReplyGenerator)
    monkeypatch.setattr(global_config.expression, "use_vector_expression", False)
    monkeypatch.setattr(generator, "_resolve_session_id", lambda stream_id: stream_id)
    select_for_reply = AsyncMock(return_value=MaisakaExpressionSelectionResult(
        expression_habits="快速表达", selected_expression_ids=[1],
    ))
    monkeypatch.setattr(maisaka_expression_selector, "select_for_reply", select_for_reply)

    result = await generator._build_reply_context(
        chat_history=[], reply_message=None, reply_reason="测试回复",
        stream_id="test-session", sub_agent_runner=None,
    )

    select_for_reply.assert_awaited_once()
    assert result.expression_habits == "快速表达"

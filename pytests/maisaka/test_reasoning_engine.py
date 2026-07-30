"""Maisaka 推理引擎测试。"""

from datetime import datetime
from typing import Optional

import pytest

from src.maisaka.chat_loop_service import ChatResponse
from src.maisaka.context.messages import AssistantMessage
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


def _build_chat_response(content: Optional[str], reasoning: str) -> ChatResponse:
    """构造仅包含 Planner 思考字段的响应。"""

    return ChatResponse(
        content=content,
        tool_calls=[],
        request_messages=[],
        raw_message=AssistantMessage(
            content=content or "",
            timestamp=datetime.now(),
            tool_calls=[],
        ),
        selected_history_count=0,
        tool_count=0,
        prompt_tokens=0,
        built_message_count=0,
        completion_tokens=0,
        total_tokens=0,
        reasoning=reasoning,
    )


@pytest.mark.parametrize(
    ("content", "reasoning", "expected"),
    [
        (" Planner 工具正文 ", " Provider 原生推理 ", "Planner 工具正文"),
        ("", " Provider 原生推理 ", "Provider 原生推理"),
        (None, " Provider 原生推理 ", "Provider 原生推理"),
        ("   ", "   ", ""),
    ],
)
def test_effective_planner_thought_prefers_content(
    content: Optional[str],
    reasoning: str,
    expected: str,
) -> None:
    response = _build_chat_response(content, reasoning)

    result = MaisakaReasoningEngine._get_effective_planner_thought(response)

    assert result == expected

"""Maisaka 推理引擎测试。"""

from datetime import datetime
from types import SimpleNamespace
from typing import Optional

import pytest

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.maisaka.chat_loop_service import ChatResponse, MaisakaChatLoopService
from src.maisaka.context.messages import AssistantMessage
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
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
        ("", " Provider 原生推理 ", ""),
        (None, " Provider 原生推理 ", ""),
        ("   ", "   ", ""),
    ],
)
def test_planner_content_does_not_fall_back_to_reasoning(
    content: Optional[str],
    reasoning: str,
    expected: str,
) -> None:
    response = _build_chat_response(content, reasoning)

    result = MaisakaReasoningEngine._get_planner_content(response)

    assert result == expected


@pytest.mark.asyncio
async def test_chat_loop_keeps_reasoning_separate_from_content(monkeypatch) -> None:
    """Provider 仅返回 reasoning 时，不应将其回填为 Planner 正文。"""

    class FakeLLMClient:
        async def generate_response_with_messages(self, message_factory, options) -> LLMResponseResult:
            del message_factory, options
            return LLMResponseResult(
                response="",
                reasoning="Provider 原生推理",
                model_name="test-model",
            )

    class PassthroughRuntimeManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def invoke_hook(self, hook_name: str, **kwargs: object) -> SimpleNamespace:
            self.calls.append((hook_name, kwargs))
            return SimpleNamespace(kwargs=kwargs)

    runtime_manager = PassthroughRuntimeManager()
    service = MaisakaChatLoopService(chat_system_prompt="测试系统提示词")
    monkeypatch.setattr(service, "_get_llm_chat_client", lambda request_kind: FakeLLMClient())
    monkeypatch.setattr(
        MaisakaChatLoopService,
        "_get_runtime_manager",
        staticmethod(lambda: runtime_manager),
    )
    monkeypatch.setattr(
        PromptCLIVisualizer,
        "build_prompt_section_result",
        lambda *args, **kwargs: SimpleNamespace(
            panel=None,
            preview_access=SimpleNamespace(preview_web_uri=""),
        ),
    )

    response = await service.chat_loop_step([])

    after_response_kwargs = next(
        kwargs for hook_name, kwargs in runtime_manager.calls if hook_name == "maisaka.planner.after_response"
    )
    assert after_response_kwargs["response"] == ""
    assert response.content is None
    assert response.raw_message.content == ""
    assert response.reasoning == "Provider 原生推理"

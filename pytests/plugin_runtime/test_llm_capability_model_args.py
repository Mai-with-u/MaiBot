"""插件 LLM 能力的任务与模型参数路由回归测试。"""

from typing import Any, Dict, List

import pytest

from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin
from src.services import llm_service, service_task_resolver

AVAILABLE_TASKS = {
    "embedding": object(),
    "planner": object(),
    "replyer": object(),
    "utils": object(),
}
AVAILABLE_MODEL_NAMES = {"deepseek-v4-flash", "glm-5.2", "replyer"}


class _StubGenerateResult:
    """模拟服务层返回对象。"""

    def to_capability_payload(self) -> Dict[str, Any]:
        return {"success": True, "response": "ok"}


@pytest.fixture
def captured_requests(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """固定任务集合并捕获能力层构造出的请求。"""
    monkeypatch.setattr(llm_service, "get_available_models", lambda: AVAILABLE_TASKS)
    monkeypatch.setattr(service_task_resolver, "get_available_models", lambda: AVAILABLE_TASKS)
    requests: List[Any] = []

    async def _fake_generate(request: Any) -> _StubGenerateResult:
        requested_model_name = str(request.model_name or "").strip()
        if requested_model_name and requested_model_name not in AVAILABLE_MODEL_NAMES:
            raise ValueError(f"未找到模型 '{requested_model_name}' 的配置")
        requests.append(request)
        return _StubGenerateResult()

    monkeypatch.setattr(llm_service, "generate", _fake_generate)
    return requests


@pytest.mark.asyncio
async def test_default_generation_uses_explicit_utils_task(captured_requests: List[Any]) -> None:
    """默认文本生成不应受任务枚举顺序影响而落入 embedding。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate("demo.plugin", "llm.generate", {"prompt": "hi"})

    assert result["success"] is True
    assert captured_requests[0].task_name == "utils"
    assert captured_requests[0].model_name is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["model", "model_name"])
async def test_explicit_model_reaches_service_layer(field_name: str, captured_requests: List[Any]) -> None:
    """新旧模型字段都应支持直选具体模型。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", field_name: "deepseek-v4-flash"},
    )

    assert result["success"] is True
    assert captured_requests[0].task_name == "utils"
    assert captured_requests[0].model_name == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_legacy_model_task_alias_remains_compatible(captured_requests: List[Any]) -> None:
    """旧 SDK 未发送 task_name 时，model 命中任务名仍按任务路由。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "replyer"},
    )

    assert result["success"] is True
    assert captured_requests[0].task_name == "replyer"
    assert captured_requests[0].model_name is None


@pytest.mark.asyncio
async def test_explicit_task_disambiguates_colliding_model_name(captured_requests: List[Any]) -> None:
    """新版协议中即使模型与任务同名，model 也应表示具体模型。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "task_name": "utils", "model": "replyer"},
    )

    assert result["success"] is True
    assert captured_requests[0].task_name == "utils"
    assert captured_requests[0].model_name == "replyer"


@pytest.mark.asyncio
async def test_explicit_task_uses_task_selection_strategy(captured_requests: List[Any]) -> None:
    """只传 task_name 时应使用对应任务的模型选择策略。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "task_name": "planner"},
    )

    assert result["success"] is True
    assert captured_requests[0].task_name == "planner"
    assert captured_requests[0].model_name is None


@pytest.mark.asyncio
async def test_conflicting_model_fields_are_rejected(captured_requests: List[Any]) -> None:
    """两个模型字段不一致时应暴露调用错误。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "deepseek-v4-flash", "model_name": "glm-5.2"},
    )

    assert result["success"] is False
    assert "不能指定不同的模型" in result["error"]
    assert captured_requests == []


@pytest.mark.asyncio
async def test_unknown_explicit_task_is_rejected(captured_requests: List[Any]) -> None:
    """显式任务名拼写错误时不应静默回退。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "task_name": "unknown"},
    )

    assert result["success"] is False
    assert "未找到名为 `unknown` 的模型配置" in result["error"]
    assert captured_requests == []


@pytest.mark.asyncio
async def test_generate_with_tools_uses_same_route(captured_requests: List[Any]) -> None:
    """带工具生成应复用相同的任务和模型语义。"""
    manager = RuntimeCoreCapabilityMixin()
    tool = {"type": "function", "function": {"name": "ping"}}

    result = await manager._cap_llm_generate_with_tools(
        "demo.plugin",
        "llm.generate_with_tools",
        {"prompt": "hi", "task_name": "planner", "model_name": "glm-5.2", "tools": [tool]},
    )

    assert result["success"] is True
    assert captured_requests[0].task_name == "planner"
    assert captured_requests[0].model_name == "glm-5.2"
    assert captured_requests[0].tool_options == [tool]

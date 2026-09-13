"""插件 LLM 能力的 ``model`` / ``model_name`` 参数分流回归测试。

覆盖三种调用写法在 host 侧的最终归属：
- ``model="<已注册任务名>"`` → 按任务解析（插件沿用任务路由，向后兼容）；
- ``model="<模型名>"``       → 未命中任务名，按模型名直选；
- ``model_name="<模型名>"``  → 按模型名直选。
"""

from typing import Any, Dict, List

import pytest

from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin
from src.services import llm_service, service_task_resolver

# 固定的已注册任务名与已注册模型名，模拟线上两个命名空间互斥的情形
REGISTERED_TASK_NAMES = {"replyer": object()}
REGISTERED_MODEL_NAMES = {"deepseek-v4-flash", "glm-5.2"}


class _StubGenerateResult:
    """替身：模拟服务层返回对象，仅提供能力层需要的载荷转换。"""

    def to_capability_payload(self) -> Dict[str, Any]:
        return {"success": True, "response": "ok"}


@pytest.fixture
def captured_requests(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """固定任务名集合，并捕获能力层构造出的 ``LLMServiceRequest``。

    同时打桩 ``llm_service`` 与 ``service_task_resolver`` 两处的
    ``get_available_models``：前者是能力层直接调用的入口，后者是
    ``resolve_task_name`` 内部使用的同名函数（模块加载时已绑定，必须分别打桩）。
    """
    monkeypatch.setattr(llm_service, "get_available_models", lambda: REGISTERED_TASK_NAMES)
    monkeypatch.setattr(service_task_resolver, "get_available_models", lambda: REGISTERED_TASK_NAMES)

    requests: List[Any] = []

    async def _fake_generate(request: Any) -> _StubGenerateResult:
        # 对齐 utils_model._select_model：未注册的模型名会在服务层被拒绝
        requested_model_name = str(getattr(request, "model_name", None) or "").strip()
        if requested_model_name and requested_model_name not in REGISTERED_MODEL_NAMES:
            raise ValueError(f"未找到模型 '{requested_model_name}' 的配置")
        requests.append(request)
        return _StubGenerateResult()

    monkeypatch.setattr(llm_service, "generate", _fake_generate)
    return requests


@pytest.mark.asyncio
async def test_model_name_reaches_service_layer_without_task_resolution(captured_requests: List[Any]) -> None:
    """``model_name`` 透传给服务层直选，不再被喂给任务名解析。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model_name": "deepseek-v4-flash"},
    )

    assert result == {"success": True, "response": "ok"}
    assert len(captured_requests) == 1
    assert captured_requests[0].model_name == "deepseek-v4-flash"
    # 未指定任务名时回落到默认任务；修复前模型名会被喂给 resolve_task_name 并抛 ValueError
    assert captured_requests[0].task_name == "replyer"


@pytest.mark.asyncio
async def test_model_task_name_still_resolves_as_task(captured_requests: List[Any]) -> None:
    """``model`` 命中已注册任务名时仍按任务解析，保持既有插件的兼容行为。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "replyer"},
    )

    assert result == {"success": True, "response": "ok"}
    assert captured_requests[0].task_name == "replyer"
    assert captured_requests[0].model_name is None


@pytest.mark.asyncio
async def test_explicit_model_name_via_model_arg_reaches_service_layer(
    captured_requests: List[Any],
) -> None:
    """``model`` 传模型名（issue 正文的复现写法）同样直达，不再抛任务解析异常。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "deepseek-v4-flash"},
    )

    assert result == {"success": True, "response": "ok"}
    assert captured_requests[0].model_name == "deepseek-v4-flash"
    assert captured_requests[0].task_name == "replyer"


@pytest.mark.asyncio
async def test_blank_model_falls_back_to_selection_strategy(captured_requests: List[Any]) -> None:
    """``model`` / ``model_name`` 为空白时不算指定模型，行为回落到模型选择策略。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "", "model_name": "   "},
    )

    assert result == {"success": True, "response": "ok"}
    assert captured_requests[0].model_name is None
    assert captured_requests[0].task_name == "replyer"


@pytest.mark.asyncio
async def test_generate_with_tools_passes_model_name(captured_requests: List[Any]) -> None:
    """带工具生成同样按模型名直选。"""
    manager = RuntimeCoreCapabilityMixin()
    tool_definition = {"type": "function", "function": {"name": "ping"}}

    result = await manager._cap_llm_generate_with_tools(
        "demo.plugin",
        "llm.generate_with_tools",
        {"prompt": "hi", "model_name": "glm-5.2", "tools": [tool_definition]},
    )

    assert result == {"success": True, "response": "ok"}
    assert captured_requests[0].model_name == "glm-5.2"
    assert captured_requests[0].task_name == "replyer"
    assert captured_requests[0].tool_options == [tool_definition]


@pytest.mark.asyncio
async def test_unknown_name_is_rejected_downstream(captured_requests: List[Any]) -> None:
    """未命中的名字按模型名下传，由服务层如实报错，不静默回退到默认模型。"""
    manager = RuntimeCoreCapabilityMixin()

    result = await manager._cap_llm_generate(
        "demo.plugin",
        "llm.generate",
        {"prompt": "hi", "model": "不存在的名字"},
    )

    assert result["success"] is False
    assert "未找到模型" in result["error"]
    assert captured_requests == []

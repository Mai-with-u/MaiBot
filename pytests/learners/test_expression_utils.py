from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import json
import sys

import pytest


@pytest.fixture
def expression_utils(monkeypatch: pytest.MonkeyPatch):
    """加载真实审核模块，只隔离日志、提示词和模型服务的初始化及 IO。"""
    prompt_manager = SimpleNamespace(
        get_prompt=Mock(return_value=Mock()),
        render_prompt=AsyncMock(return_value="synthetic prompt"),
    )
    judge = SimpleNamespace(generate_response=AsyncMock())
    dependencies = {
        "src.common.logger": {"get_logger": Mock(return_value=Mock())},
        "src.prompt.prompt_manager": {"prompt_manager": prompt_manager},
        "src.services.llm_service": {"LLMServiceClient": Mock(return_value=judge)},
    }
    for name, attributes in dependencies.items():
        dependency = ModuleType(name)
        dependency.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, dependency)

    path = Path(__file__).resolve().parents[2] / "src/learners/expression_utils.py"
    module_name = "src.learners._expression_utils_type_test"
    spec = spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("suitable", [True, False], ids=["true", "false"])
async def test_expression_review_preserves_json_booleans(expression_utils, suitable: bool) -> None:
    expression_utils.judge_llm.generate_response.return_value = SimpleNamespace(
        response=json.dumps({"suitable": suitable, "reason": "synthetic"})
    )

    result = await expression_utils.check_expression_suitability("scene", "style", session_id="synthetic-session")

    assert result == (suitable, "synthetic", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suitable",
    ["false", "true", "", None, 0, 1, [], [False], {}, {"value": False}],
    ids=[
        "string-false",
        "string-true",
        "empty-string",
        "null",
        "zero",
        "one",
        "empty-list",
        "list",
        "empty-object",
        "object",
    ],
)
async def test_expression_review_rejects_non_boolean_results(expression_utils, suitable) -> None:
    expression_utils.judge_llm.generate_response.return_value = SimpleNamespace(
        response=json.dumps({"suitable": suitable, "reason": "synthetic"})
    )

    accepted, reason, error = await expression_utils.check_expression_suitability("scene", "style")

    assert accepted is False
    assert error is not None and "suitable" in error
    assert "格式错误" in reason


@pytest.mark.asyncio
async def test_expression_review_rejects_missing_boolean_result(expression_utils) -> None:
    expression_utils.judge_llm.generate_response.return_value = SimpleNamespace(response='{"reason": "synthetic"}')

    accepted, reason, error = await expression_utils.check_expression_suitability("scene", "style")

    assert accepted is False
    assert error is not None and "suitable" in error
    assert "格式错误" in reason

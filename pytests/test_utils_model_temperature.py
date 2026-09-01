from typing import Any

from src.config.model_configs import ModelInfo, TaskConfig
from src.llm_models.utils_model import LLMOrchestrator


def _build_orchestrator(task_temperature: float = 0.8) -> LLMOrchestrator:
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.model_for_task = TaskConfig(
        model_list=["test-model"],
        temperature=task_temperature,
    )
    return orchestrator


def _build_model(**kwargs: Any) -> ModelInfo:
    return ModelInfo(
        model_identifier="gpt-test",
        name="test-model",
        api_provider="test-provider",
        **kwargs,
    )


def test_temperature_is_omitted_when_model_disables_it() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model(send_temperature=False, temperature=0.4)

    assert orchestrator._resolve_effective_temperature(model_info, temperature=0.2) is None


def test_temperature_resolution_remains_backward_compatible_by_default() -> None:
    orchestrator = _build_orchestrator()

    assert orchestrator._resolve_effective_temperature(_build_model(), temperature=None) == 0.8
    assert orchestrator._resolve_effective_temperature(_build_model(temperature=0.4), temperature=None) == 0.4
    assert orchestrator._resolve_effective_temperature(_build_model(temperature=0.4), temperature=0.2) == 0.2

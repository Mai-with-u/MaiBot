from src.config.model_configs import ModelInfo, TaskConfig
from src.llm_models.utils_model import LLMOrchestrator


class _FixedTaskOrchestrator(LLMOrchestrator):
    """使用固定任务配置的调度器，避免测试依赖全局配置。"""

    def __init__(self, task_config: TaskConfig) -> None:
        self._task_config = task_config
        super().__init__(task_name="temperature_test", request_type="temperature_test")

    def _get_task_config_or_raise(self) -> TaskConfig:
        return self._task_config


def _build_orchestrator() -> LLMOrchestrator:
    return _FixedTaskOrchestrator(TaskConfig(model_list=["demo-model"], temperature=0.3))


def _build_model_info(**overrides) -> ModelInfo:
    return ModelInfo(
        api_provider="test-provider",
        model_identifier="demo-model",
        name="demo-model",
        **overrides,
    )


def test_model_level_temperature_overrides_explicit_temperature() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model_info(temperature=0.6)

    assert orchestrator._resolve_effective_temperature(model_info, 0.1) == 0.6


def test_explicit_temperature_used_when_model_level_absent() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model_info()

    assert orchestrator._resolve_effective_temperature(model_info, 0.1) == 0.1


def test_explicit_temperature_overrides_extra_params_default() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model_info(extra_params={"temperature": 0.5})

    assert orchestrator._resolve_effective_temperature(model_info, 0.1) == 0.1


def test_extra_params_temperature_used_as_model_default() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model_info(extra_params={"temperature": 0.5})

    assert orchestrator._resolve_effective_temperature(model_info, None) == 0.5


def test_task_config_temperature_used_as_final_fallback() -> None:
    orchestrator = _build_orchestrator()
    model_info = _build_model_info()

    assert orchestrator._resolve_effective_temperature(model_info, None) == 0.3

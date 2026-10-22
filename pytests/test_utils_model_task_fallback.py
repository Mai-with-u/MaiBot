from types import SimpleNamespace

import pytest

from src.config.model_configs import TaskConfig
from src.llm_models import utils_model
from src.llm_models.utils_model import LLMOrchestrator


@pytest.mark.parametrize(
    ("mid_models", "fast_models", "expected_task"),
    [
        (["mid-model"], ["fast-model"], "mid_memory"),
        ([], ["fast-model"], "fast_model"),
        ([], [], "utils"),
    ],
)
def test_mid_memory_resolves_configured_task_chain(monkeypatch, mid_models, fast_models, expected_task) -> None:
    tasks = SimpleNamespace(
        mid_memory=TaskConfig(model_list=mid_models, hard_timeout=180.0),
        fast_model=TaskConfig(model_list=fast_models, hard_timeout=120.0),
        utils=TaskConfig(model_list=["utils-model"], hard_timeout=240.0),
        planner=TaskConfig(model_list=["planner-model"]),
    )
    monkeypatch.setattr(
        utils_model.config_manager, "get_model_config", lambda: SimpleNamespace(model_task_config=tasks)
    )

    orchestrator = LLMOrchestrator(task_name="mid_memory")

    assert orchestrator.model_for_task is getattr(tasks, expected_task)


@pytest.mark.parametrize("computer_models", [[], ["computer-model"]])
def test_computer_uses_dedicated_task_or_complete_planner_config(monkeypatch, computer_models) -> None:
    tasks = SimpleNamespace(
        computer=TaskConfig(model_list=computer_models, temperature=0.2, max_tokens=2048),
        planner=TaskConfig(model_list=["planner-model"], temperature=0.9, max_tokens=8192),
    )
    monkeypatch.setattr(utils_model.config_manager, "get_model_config", lambda: SimpleNamespace(model_task_config=tasks))
    orchestrator = LLMOrchestrator(task_name="computer")
    assert orchestrator.model_for_task is (tasks.computer if computer_models else tasks.planner)
    # 配置热更新后，沿用的客户端也必须重新解析任务归属。
    tasks.computer = TaskConfig(model_list=["new-computer-model"])
    assert orchestrator._refresh_task_config() is tasks.computer


def _resolve_task_config(
    monkeypatch,
    *,
    embedding: TaskConfig,
    image_embedding: TaskConfig,
) -> TaskConfig:
    model_config = SimpleNamespace(
        model_task_config=SimpleNamespace(
            embedding=embedding,
            image_embedding=image_embedding,
        )
    )
    monkeypatch.setattr(utils_model.config_manager, "get_model_config", lambda: model_config)

    orchestrator = LLMOrchestrator(task_name="image_embedding")
    return orchestrator.model_for_task


def test_image_embedding_does_not_reuse_text_embedding_when_dedicated_task_is_empty(monkeypatch) -> None:
    embedding = TaskConfig(model_list=["shared-embedding"], hard_timeout=120.0)

    with pytest.raises(ValueError, match="图片嵌入任务未配置模型"):
        _resolve_task_config(
            monkeypatch,
            embedding=embedding,
            image_embedding=TaskConfig(),
        )


def test_image_embedding_prefers_dedicated_task_when_configured(monkeypatch) -> None:
    image_embedding = TaskConfig(model_list=["image-embedding"], hard_timeout=180.0)

    resolved = _resolve_task_config(
        monkeypatch,
        embedding=TaskConfig(model_list=["shared-embedding"]),
        image_embedding=image_embedding,
    )

    assert resolved is image_embedding

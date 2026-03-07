from types import SimpleNamespace
from importlib import util
from pathlib import Path

from src.config.config import config_manager
from src.config.model_configs import TaskConfig
from src.llm_models.utils_model import LLMRequest


def _load_llm_api_module():
    file_path = Path(__file__).parent.parent.parent / "src" / "plugin_system" / "apis" / "llm_api.py"
    spec = util.spec_from_file_location("test_llm_api_module", file_path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_model_config(task_config: TaskConfig, attr_name: str = "utils"):
    model_task_config = SimpleNamespace(**{attr_name: task_config})
    return SimpleNamespace(model_task_config=model_task_config, models=[], api_providers=[])


def test_llm_request_resolve_task_config_by_signature(monkeypatch):
    old_task = TaskConfig(model_list=["gpt-a"], max_tokens=512, temperature=0.3, slow_threshold=15.0)
    current_task = TaskConfig(model_list=["gpt-a"], max_tokens=512, temperature=0.3, slow_threshold=15.0)

    monkeypatch.setattr(config_manager, "get_model_config", lambda: _make_model_config(current_task, "utils"))

    req = LLMRequest(model_set=old_task, request_type="test")

    assert req._task_config_name == "utils"


def test_llm_request_refresh_task_config_updates_runtime_state(monkeypatch):
    old_task = TaskConfig(model_list=["gpt-a"], max_tokens=512, temperature=0.3, slow_threshold=15.0)
    initial_task = TaskConfig(model_list=["gpt-a"], max_tokens=512, temperature=0.3, slow_threshold=15.0)
    updated_task = TaskConfig(model_list=["gpt-b", "gpt-c"], max_tokens=1024, temperature=0.5, slow_threshold=20.0)

    current = {"task": initial_task}

    def get_model_config_stub():
        return _make_model_config(current["task"], "replyer")

    monkeypatch.setattr(config_manager, "get_model_config", get_model_config_stub)

    req = LLMRequest(model_set=old_task, request_type="test")
    assert req._task_config_name == "replyer"

    current["task"] = updated_task
    req._refresh_task_config()

    assert req.model_for_task.model_list == ["gpt-b", "gpt-c"]
    assert list(req.model_usage.keys()) == ["gpt-b", "gpt-c"]


def test_llm_api_get_available_models_reads_latest_config(monkeypatch):
    llm_api = _load_llm_api_module()

    first_utils = TaskConfig(model_list=["gpt-a"])
    second_utils = TaskConfig(model_list=["gpt-z"])

    state = {"task": first_utils}

    def get_model_config_stub():
        model_task_config = SimpleNamespace(utils=state["task"], planner=TaskConfig(model_list=["gpt-p"]))
        return SimpleNamespace(model_task_config=model_task_config)

    monkeypatch.setattr(config_manager, "get_model_config", get_model_config_stub)

    first = llm_api.get_available_models()
    assert first["utils"].model_list == ["gpt-a"]

    state["task"] = second_utils
    second = llm_api.get_available_models()
    assert second["utils"].model_list == ["gpt-z"]

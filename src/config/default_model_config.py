from typing import Any, TypeVar

from .config_base import ConfigBase
from .model_configs import APIProvider, ModelInfo, ModelTaskConfig, OpenAICompatibleAuthType, TaskConfig

T = TypeVar("T", bound=ConfigBase)

DEFAULT_PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "your-api-key",
        "auth_type": OpenAICompatibleAuthType.BEARER.value,
        "max_retry": 3,
        "timeout": 100,
        "retry_interval": 8,
    }
]

DEFAULT_TASK_CONFIG_TEMPLATES: dict[str, dict[str, Any]] = {
    "utils": {
        "model_list": ["deepseek-v4-flash"],
        "max_tokens": 4096,
        "temperature": 0.5,
        "slow_threshold": 15.0,
        "selection_strategy": "random",
        "hard_timeout": 120.0,
    },
    "memory": {
        "model_list": [],
        "max_tokens": 8192,
        "temperature": 0.5,
        "slow_threshold": 30.0,
        "selection_strategy": "random",
        "hard_timeout": 240.0,
    },
    "mid_memory": {
        "model_list": [],
        "max_tokens": 8000,
        "temperature": 0.7,
        "slow_threshold": 12.0,
        "selection_strategy": "random",
        "hard_timeout": 180.0,
    },
    "replyer": {
        "model_list": ["deepseek-v4-pro-think", "deepseek-v4-pro-nonthink"],
        "max_tokens": 4096,
        "temperature": 1,
        "slow_threshold": 120.0,
        "selection_strategy": "random",
        "hard_timeout": 240.0,
    },
    "planner": {
        "model_list": ["deepseek-v4-flash"],
        "max_tokens": 8000,
        "temperature": 0.7,
        "slow_threshold": 12.0,
        "selection_strategy": "random",
        "hard_timeout": 180.0,
    },
    "learner": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0},
    "expression_use": {"model_list": [], "max_tokens": 1024, "temperature": 0.3, "hard_timeout": 120.0},
    "emoji": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0},
    "vlm": {"model_list": [], "max_tokens": 4096, "hard_timeout": 240.0},
    "voice": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0},
    "embedding": {"model_list": [], "max_tokens": 4096, "hard_timeout": 60.0},
}

DEFAULT_MODEL_TEMPLATES: list[dict[str, Any]] = [
    {
        "model_identifier": "deepseek-v4-pro",
        "name": "deepseek-v4-pro-think",
        "api_provider": "DeepSeek",
        "price_in": 12.0,
        "price_out": 24.0,
        "send_temperature": True,
        "visual": False,
        "extra_params": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    },
    {
        "model_identifier": "deepseek-v4-pro",
        "name": "deepseek-v4-pro-nonthink",
        "api_provider": "DeepSeek",
        "price_in": 12.0,
        "price_out": 24.0,
        "send_temperature": True,
        "visual": False,
        "extra_params": {"thinking": {"type": "disabled"}},
    },
    {
        "model_identifier": "deepseek-v4-flash",
        "name": "deepseek-v4-flash",
        "api_provider": "DeepSeek",
        "price_in": 1.0,
        "price_out": 2.0,
        "send_temperature": True,
        "visual": False,
        "extra_params": {"thinking": {"type": "disabled"}},
    },
]


def build_default_model_templates() -> list[dict[str, Any]]:
    """筛选任务分配中实际用到的模型模板。"""

    used_model_names = {
        model_name
        for task_template in DEFAULT_TASK_CONFIG_TEMPLATES.values()
        for model_name in task_template["model_list"]
    }
    return [model_template for model_template in DEFAULT_MODEL_TEMPLATES if model_template["name"] in used_model_names]


def create_default_model_config(config_class: type[T]) -> T:
    """根据预置模板创建可通过校验的默认模型配置。"""

    task_config_fields = {}
    for field_name, field_info in ModelTaskConfig.model_fields.items():
        if field_info.annotation is not TaskConfig:
            continue

        task_template = DEFAULT_TASK_CONFIG_TEMPLATES.get(field_name, {})
        task_config_fields[field_name] = TaskConfig(**task_template)

    return config_class(
        models=[ModelInfo(**model_template) for model_template in build_default_model_templates()],
        model_task_config=ModelTaskConfig(**task_config_fields),
        api_providers=[APIProvider(**provider_template) for provider_template in DEFAULT_PROVIDER_TEMPLATES],
    )

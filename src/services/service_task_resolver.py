"""服务层模型任务解析工具。"""

from typing import Dict

from src.common.logger import get_logger
from src.config.config import config_manager
from src.config.model_configs import TaskConfig

logger = get_logger("service_task_resolver")


def get_available_models() -> Dict[str, TaskConfig]:
    """获取当前所有可用的模型任务配置。

    Returns:
        Dict[str, TaskConfig]: 以任务名为键的可用任务配置映射。
    """
    try:
        models = config_manager.get_model_config().model_task_config
        available_models: Dict[str, TaskConfig] = {}
        for attr_name in dir(models):
            if attr_name.startswith("__"):
                continue
            try:
                attr_value = getattr(models, attr_name)
            except Exception as exc:
                logger.debug(f"获取模型任务配置属性 {attr_name} 失败: {exc}")
                continue
            if not callable(attr_value) and isinstance(attr_value, TaskConfig):
                available_models[attr_name] = attr_value
        return available_models
    except Exception as exc:
        logger.error(f"获取可用模型配置失败: {exc}")
        return {}


def resolve_task_name(task_name: str = "") -> str:
    """根据任务名解析实际可用的模型任务名称。

    Args:
        task_name: 目标任务名；为空时返回首个可用任务。

    Returns:
        str: 解析后的模型任务名。

    Raises:
        RuntimeError: 当前没有任何可用模型配置时抛出。
        ValueError: 指定任务名不存在时抛出。
    """
    models = get_available_models()
    if not models:
        raise RuntimeError("没有可用的模型配置")

    normalized_task_name = task_name.strip()
    if not normalized_task_name:
        return next(iter(models.keys()))
    if normalized_task_name not in models:
        raise ValueError(f"未找到名为 `{normalized_task_name}` 的模型配置")
    return normalized_task_name

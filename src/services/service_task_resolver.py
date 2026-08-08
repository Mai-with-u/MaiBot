"""服务层模型任务解析工具。"""

from typing import Any, Dict

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


def resolve_task_name_from_model_config(model_config: Any, preferred_task_name: str = "") -> str:
    """根据旧版模型配置对象解析任务名。

    Args:
        model_config: 旧调用方持有的任务配置对象。
        preferred_task_name: 候选任务名。

    Returns:
        str: 解析后的模型任务名。

    Raises:
        RuntimeError: 当前没有任何可用模型配置时抛出。
        ValueError: 无法解析任何可用任务名时抛出。
    """
    models = get_available_models()
    if not models:
        raise RuntimeError("没有可用的模型配置")

    normalized_preferred = str(preferred_task_name or "").strip()
    if normalized_preferred and normalized_preferred in models:
        return normalized_preferred

    for task_name, task_cfg in models.items():
        if task_cfg is model_config:
            return task_name

    requested_model_list_raw = getattr(model_config, "model_list", [])
    requested_model_list = [str(item).strip() for item in (requested_model_list_raw or []) if str(item).strip()]
    if requested_model_list:
        for task_name, task_cfg in models.items():
            candidate_list = [str(item).strip() for item in getattr(task_cfg, "model_list", []) if str(item).strip()]
            if candidate_list == requested_model_list:
                return task_name

        for requested_model in requested_model_list:
            for task_name, task_cfg in models.items():
                candidate_list = [str(item).strip() for item in getattr(task_cfg, "model_list", []) if str(item).strip()]
                if requested_model in candidate_list:
                    logger.info(
                        "旧版 model_config 未命中任务配置，"
                        f"按模型 `{requested_model}` 近似映射到任务 `{task_name}`"
                    )
                    return task_name

    if normalized_preferred:
        logger.warning(f"无法映射旧版 model_config，回退默认任务: preferred={normalized_preferred}")
    return resolve_task_name("")

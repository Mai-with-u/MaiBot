"""图片记忆在宿主侧的启用条件。"""

from .config import config_manager, global_config
from .model_configs import TaskConfig


def is_image_embedding_configured(task_name: str) -> bool:
    """只检查指定任务是否分配模型，不探测模型服务是否在线。"""
    tasks = config_manager.get_model_config().model_task_config
    task = vars(tasks).get(task_name)
    return isinstance(task, TaskConfig) and any(name.strip() for name in task.model_list)


def is_image_memory_enabled() -> bool:
    """图片记忆需要总开关、图片开关及图片嵌入模型同时满足。"""
    config = global_config.a_memorix
    return (
        config.plugin.enabled
        and config.image_memory.enabled
        and is_image_embedding_configured(config.image_memory.task_name)
    )

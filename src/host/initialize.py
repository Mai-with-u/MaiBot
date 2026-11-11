"""在独立进程中直接生成新 Bot 的最终配置，不经过旧配置迁移。"""
from pathlib import Path

import os
import sys
import tomlkit

from src.common.paths import get_instance_root
from src.common.process_lease import ProcessLease
from src.config.shared_models import CATALOG_FILENAME, SHARED_MODELS_ENV, extract_catalog, read_catalog
from src.config.shared_settings import RETENTION_DEFAULTS, WEBUI_DEFAULTS

from .config_transaction import write_private
from .model_migration import _config_values


def initialize(host: Path) -> None:
    os.environ["MAIBOT_INITIALIZE_ONLY"] = "1"
    with ProcessLease(host / "model-config.lock"):
        # 只为全新工作区生成公共默认值；后续实例不改写已有公共定义。
        host_path = host / "config.toml"
        document = tomlkit.parse(host_path.read_text(encoding="utf-8"))
        if "retention" not in document:
            document["webui"].update(WEBUI_DEFAULTS)
            document["retention"] = RETENTION_DEFAULTS
            write_private(host_path, tomlkit.dumps(document))
        catalog_path = host / CATALOG_FILENAME
        os.environ[SHARED_MODELS_ENV] = str(catalog_path)
        from src.config.config import CONFIG_VERSION, MODEL_CONFIG_VERSION, Config, ModelConfig, write_config_to_file
        from src.config.default_model_config import create_default_model_config
        from src.config.model_configs import TaskConfig

        model = create_default_model_config(ModelConfig)
        if not catalog_path.exists():
            write_private(catalog_path, tomlkit.dumps(extract_catalog(_config_values(model))))
        catalog = read_catalog(catalog_path)
        names = {entry["name"] for entry in catalog["models"]}
        services = catalog.get("web", {}).get("services", {})
        for name in type(model.model_task_config).model_fields:
            task = model.model_task_config.__dict__[name]
            # 配置元信息不是任务；只调整具有模型选择的任务对象。
            if not isinstance(task, TaskConfig):
                continue
            available = services if name == "web_search" else names
            task.model_list = [value for value in task.model_list if value in available]
        directory = get_instance_root()
        config = directory / "config"
        config.mkdir(parents=True, exist_ok=True)
        write_config_to_file(Config(), config / "bot_config.toml", CONFIG_VERSION)
        write_config_to_file(model, config / "model_config.toml", MODEL_CONFIG_VERSION, True)
        for name in ("data", "logs", "plugins", "temp"):
            (directory / name).mkdir(exist_ok=True)


if __name__ == "__main__":
    initialize(Path(sys.argv[1]))

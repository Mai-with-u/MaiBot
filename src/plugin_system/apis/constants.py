from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class _SystemConstants:
    PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.parent.absolute().resolve()
    CONFIG_DIR: Path = PROJECT_ROOT / "config"
    BOT_CONFIG_PATH: Path = (CONFIG_DIR / "bot_config.toml").resolve().absolute()
    MODEL_CONFIG_PATH: Path = (CONFIG_DIR / "model_config.toml").resolve().absolute()
    PLUGINS_DIR: Path = (PROJECT_ROOT / "plugins").resolve().absolute()
    INTERNAL_PLUGINS_DIR: Path = (PROJECT_ROOT / "src" / "plugins").resolve().absolute()


_system_constants = _SystemConstants()

PROJECT_ROOT: Path = _system_constants.PROJECT_ROOT
CONFIG_DIR: Path = _system_constants.CONFIG_DIR
BOT_CONFIG_PATH: Path = _system_constants.BOT_CONFIG_PATH
MODEL_CONFIG_PATH: Path = _system_constants.MODEL_CONFIG_PATH
PLUGINS_DIR: Path = _system_constants.PLUGINS_DIR
INTERNAL_PLUGINS_DIR: Path = _system_constants.INTERNAL_PLUGINS_DIR

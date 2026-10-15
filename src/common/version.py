from functools import lru_cache
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any

import tomllib

from .paths import PACKAGED_INSTALL, RESOURCE_ROOT, get_instance_root


PROJECT_ROOT: Path = RESOURCE_ROOT


@lru_cache(maxsize=None)
def read_project_version(project_root: Path | None = None) -> str:
    """读取 pyproject.toml 中声明的主程序版本号。"""

    if PACKAGED_INSTALL:
        return installed_version("MaiBot")
    root = RESOURCE_ROOT if project_root is None or project_root == get_instance_root() else project_root
    pyproject_path = root / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        pyproject_data: dict[str, Any] = tomllib.load(pyproject_file)

    project_data = pyproject_data.get("project")
    if not isinstance(project_data, dict):
        raise ValueError("pyproject.toml 缺少 [project] 配置节，无法读取主程序版本号")

    version = project_data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("pyproject.toml 缺少 project.version，无法读取主程序版本号")

    return version.strip()

"""共享程序的运行登记：源码锁放在仓库内，安装版沿用工具环境锁。"""

from contextlib import nullcontext
from pathlib import Path

import sys

from src.common.package_update import FileLock, ToolEnvironment, instance_lock_path, register_instance
from src.common.paths import PACKAGED_INSTALL, SOURCE_ROOT


def update_environment() -> ToolEnvironment:
    # 源码可能使用系统 Python；不能要求它有权限写入系统解释器的父目录。
    prefix = Path(sys.prefix) if PACKAGED_INSTALL else SOURCE_ROOT / ".host-runtime"
    return ToolEnvironment(prefix, Path(sys.executable), Path(sys.executable), "")


def register_host_instance(root: Path):
    if PACKAGED_INSTALL:
        if (Path(sys.prefix) / "uv-receipt.toml").is_file():
            return register_instance(root)
        return nullcontext()  # 非 uv tool 安装本来就不支持自更新，但仍可正常运行。
    environment = update_environment()
    with FileLock(environment.locks / "update.guard"):
        return FileLock(instance_lock_path(environment, root))

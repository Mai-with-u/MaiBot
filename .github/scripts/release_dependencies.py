"""发布依赖的可见性检查：每个任务都刷新索引，只重试刚发布版本暂不可见的情况。"""

from pathlib import Path
from typing import Dict, List, Optional

from packaging.utils import canonicalize_name

import os
import re
import subprocess
import time


PYPI_INDEX = "https://pypi.org/simple"
CHECK_INTERVAL_SECONDS = 15
MAX_CHECK_ATTEMPTS = 40


def run_with_published_package(
    command: List[str],
    package_name: str,
    version: str,
    *,
    cwd: Path,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> None:
    """在实际使用依赖的任务内重新确认版本，保留其他依赖的下载缓存。"""
    package_name = canonicalize_name(package_name)
    command = [
        *command,
        "--no-config",
        "--default-index",
        PYPI_INDEX,
        "--refresh-package",
        package_name,
    ]
    environment = dict(os.environ if env is None else env)
    # 发布验证固定使用官方源，避免运行器或项目的镜像配置改变探测和安装的含义。
    for key in ("UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_FIND_LINKS"):
        environment.pop(key, None)
    missing_version = re.compile(
        rf"\bno version of {re.escape(package_name)}=={re.escape(version)}(?=[\s,;)]|$)",
        re.IGNORECASE,
    )
    for attempt in range(1, MAX_CHECK_ATTEMPTS + 1):
        print(
            f"从 {PYPI_INDEX} 刷新并解析 {package_name}=={version}（第 {attempt}/{MAX_CHECK_ATTEMPTS} 次）", flush=True
        )
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", flush=True)
        if result.returncode == 0:
            return
        # 不吞掉网络、认证、Python 不兼容或其他依赖冲突，也不无限重试。
        if result.returncode != 1 or not missing_version.search(result.stderr + "\n" + result.stdout):
            result.check_returncode()
        if attempt == MAX_CHECK_ATTEMPTS:
            result.check_returncode()
        time.sleep(CHECK_INTERVAL_SECONDS)

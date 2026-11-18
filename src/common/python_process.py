"""直接启动当前环境的 Python，确保受管 PID 就是实际解释器。"""

from typing import Dict, Mapping

import os
import sys


def python_executable() -> str:
    return sys._base_executable if os.name == "nt" else sys.executable


def python_environment(env: Mapping[str, str]) -> Dict[str, str]:
    environment = dict(env)
    if os.name == "nt":
        # CPython 的 venv redirector 用此协议传递虚拟环境；绕过启动器后
        # terminate/kill 会命中实际解释器，同时保留虚拟环境的包搜索路径。
        environment["__PYVENV_LAUNCHER__"] = sys.executable
    return environment

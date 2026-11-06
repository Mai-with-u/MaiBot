"""承接 1.3.19 Runner 在线升级后对 src.runtime 的启动调用。"""

import os

from src.common.paths import PACKAGED_INSTALL, get_instance_root

from .launcher import main as run_host


LEGACY_PACKAGE_INSTANCE_ENV = "MAIBOT_LEGACY_PACKAGE_INSTANCE"


def main() -> int:
    root = get_instance_root().resolve()
    if PACKAGED_INSTALL and os.environ.get("MAIBOT_PACKAGE_RUNNER") == "1":
        # 旧 CLI 在整个守护周期持有实例锁，宿主与更新器沿用它，直至最外层进程退出。
        os.environ[LEGACY_PACKAGE_INSTANCE_ENV] = str(root)
    os.environ.pop("MAIBOT_RUNNER_SUPERVISED", None)
    return run_host(root)

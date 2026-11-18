"""宿主完全退出后的更新阶段；只加载标准库及标准库实现的更新器。"""

from pathlib import Path

import logging
import os
import sys

from src.common.package_update import FileLock, ensure_other_instances_stopped
from src.common.paths import INSTANCE_DIRECTORY_ENV, PACKAGED_INSTALL, SOURCE_ROOT
from src.common.process_lease import ProcessLease

from .update_locks import register_host_instance, update_environment


def main(root: Path) -> int:
    root = root.resolve()
    os.environ[INSTANCE_DIRECTORY_ENV] = str(root)
    os.environ["MAIBOT_PACKAGE_RUNNER"] = "1"
    from src.common.self_update import apply_pending_update

    logging.basicConfig(level=logging.INFO)
    environment = update_environment()
    with register_host_instance(root), ProcessLease(root / "host" / "run.lock"):
        if PACKAGED_INSTALL:
            # 包更新器会在环境外启动 Python，并自行持有 update.guard。
            result = apply_pending_update(logging.getLogger("host.update"), root)
        else:
            with FileLock(environment.locks / "update.guard"):
                ensure_other_instances_stopped(environment, root)
                result = apply_pending_update(logging.getLogger("host.update"), SOURCE_ROOT)
        if result is None:
            raise RuntimeError("没有待执行的宿主更新请求")
        if result.rollback_error:
            raise RuntimeError(f"更新回滚未完成，未启动 Bot：{result.rollback_error}")
        if not result.success and result.stage in {"merge", "dependencies"} and not result.rolled_back:
            raise RuntimeError(f"更新失败且未确认回滚，未启动 Bot：{result.error}")
    # 成功或已安全回滚才通知启动器恢复宿主，由新解释器加载更新后的代码。
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

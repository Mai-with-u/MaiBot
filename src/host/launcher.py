"""稳定的宿主启动进程：更新前后始终保留供外部进程管理器跟踪的 PID。"""

from pathlib import Path
from typing import List

import os
import signal
import subprocess
import sys

from src.common.paths import PACKAGED_INSTALL, SOURCE_ROOT


UPDATE_REQUESTED = 75


def _command(stage: str, root: Path) -> List[str]:
    # 包部署沿用隔离模式；各阶段均由新解释器加载，更新器不持有宿主模块。
    flags = ["-I"] if sys.flags.isolated else []
    if sys.flags.utf8_mode:
        flags.extend(["-X", "utf8"])
    return [sys.executable, *flags, "-m", f"src.host.{stage}", str(root)]


def main(root: Path) -> int:
    stage = "bootstrap"
    interrupted = False
    child = None

    def stop(signum, _frame) -> None:
        nonlocal interrupted
        interrupted = True
        # Ctrl+C 会由终端同时送达子进程；SIGTERM 需要转发。
        if signum == signal.SIGTERM and child is not None and child.poll() is None:
            child.terminate()

    signals = [signal.SIGINT, signal.SIGTERM]
    if os.name == "nt":
        signals.append(signal.SIGBREAK)  # 旧 Runner 在 Windows 下通过 CTRL_BREAK 请求停止。
    previous = {sig: signal.signal(sig, stop) for sig in signals}
    try:
        while True:
            # 继承终端与标准流；外部管理器只在整个运行周期结束后收到退出。
            # 源码入口可以从仓库外启动；程序目录用于模块查找，实例目录仍单独传入。
            child = subprocess.Popen(
                _command(stage, root.resolve()),
                cwd=None if PACKAGED_INSTALL else SOURCE_ROOT,
            )
            code = child.wait()
            if interrupted:
                return code if code else 130
            if stage == "bootstrap" and code == UPDATE_REQUESTED:
                stage = "updater"
            elif stage == "updater" and code == 0:
                stage = "bootstrap"
            else:
                return code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

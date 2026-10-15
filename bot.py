"""源码部署和 OneKey 的兼容启动入口。"""

import os
import runpy

from src.common.paths import get_instance_root

if __name__ == "__main__":
    os.chdir(get_instance_root())
    runpy.run_module("src.runtime", run_name="__main__")

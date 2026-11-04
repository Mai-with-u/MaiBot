"""源码部署和 OneKey 的启动入口：以仓库目录为工作区运行宿主。"""

from src.common.paths import get_instance_root
from src.host.launcher import main

if __name__ == "__main__":
    raise SystemExit(main(get_instance_root()))

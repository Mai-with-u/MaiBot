"""把首页资讯服务挂载到插件统计服务上（幂等，可重复执行）。

线上新端口外网不可达，因此资讯接口与插件统计服务共用 10059。
本脚本只做两处最小改动：
1. 导入块补 logging / sys；
2. ``app = make_app()`` 之后挂载 /root/news_server 下的资讯路由（失败不影响插件统计）。
"""

from __future__ import annotations

from pathlib import Path

import sys

APP_PATH = Path("/root/plugin_stats_server/app.py")

IMPORT_ANCHOR = "import hashlib\nimport os\nimport sqlite3\nimport time\n"
IMPORT_PATCHED = "import hashlib\nimport logging\nimport os\nimport sqlite3\nimport sys\nimport time\n"

MOUNT_ANCHOR = "\n\napp = make_app()\n"
MOUNT_BLOCK = '''

# 首页资讯服务：代码与内容放在独立目录，挂载失败不影响插件统计功能
_NEWS_SERVER_DIR = Path(os.getenv("MAIBOT_NEWS_DIR", "/root/news_server"))
if _NEWS_SERVER_DIR.is_dir():
    if str(_NEWS_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_NEWS_SERVER_DIR))
    try:
        from news_app import router as news_router

        app.include_router(news_router)
    except Exception as exc:  # 部署兜底：资讯不可用不应影响插件统计
        logging.getLogger("maibot.news").warning(f"资讯服务挂载失败，已跳过: {exc}")
'''


def main() -> int:
    source = APP_PATH.read_text(encoding="utf-8")

    if "_NEWS_SERVER_DIR" in source:
        print("SKIP: 资讯服务已挂载，无需重复修改")
        return 0

    if IMPORT_ANCHOR not in source:
        print("FAIL: 未找到导入块锚点，请人工确认 app.py 结构")
        return 1

    if MOUNT_ANCHOR not in source:
        print("FAIL: 未找到 app = make_app() 锚点，请人工确认 app.py 结构")
        return 1

    patched = source.replace(IMPORT_ANCHOR, IMPORT_PATCHED, 1)
    patched = patched.replace(MOUNT_ANCHOR, f"\n\napp = make_app(){MOUNT_BLOCK}", 1)

    if patched == source:
        print("FAIL: 替换后内容无变化")
        return 1

    APP_PATH.write_text(patched, encoding="utf-8")
    print("OK: 已挂载资讯服务")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""在一个 Bot 的实例目录中读取全部已启用插件的 Python 依赖；只读，不安装也不加载插件。

由宿主在独立进程中调用：路径与插件配置模块在导入时固定实例目录，不能在宿主进程里逐个分析。
"""

from pathlib import Path

import argparse
import json
import tomllib

from src.common.paths import builtin_plugin_root, get_instance_root, third_party_plugin_root
from src.plugin_runtime.dependency_pipeline import PluginDependencyPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="输出当前实例插件的完整 Python 依赖约束")
    parser.add_argument("--output", type=Path, required=True, help="结果 JSON 文件；日志输出不能混入结果")
    args = parser.parse_args()
    plugin_dirs = [path for path in (builtin_plugin_root(), third_party_plugin_root()) if path.is_dir()]
    config_path = get_instance_root() / "config" / "bot_config.toml"
    force_compatibility = False
    if config_path.exists():
        with config_path.open("rb") as stream:
            force_compatibility = tomllib.load(stream).get("debug", {}).get("force_plugin_compatibility", False)
    # 只读原始配置，不导入会初始化、迁移或启动监视器的全局配置模块。
    result = PluginDependencyPipeline(force_plugin_compatibility=force_compatibility).collect_declared_requirements(plugin_dirs)
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

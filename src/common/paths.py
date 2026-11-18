"""程序资源与实例目录；不导入配置、日志或数据库。"""

from pathlib import Path

import os

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT.parent
# wheel 的构建步骤把仓库级只读资源放到包内，源码部署仍读取原目录。
RESOURCE_ROOT = PACKAGE_ROOT / "_resources" if (PACKAGE_ROOT / "_resources").is_dir() else SOURCE_ROOT
PACKAGED_INSTALL = RESOURCE_ROOT != SOURCE_ROOT
INSTANCE_DIRECTORY_ENV = "MAIBOT_INSTANCE_DIR"


def get_instance_root() -> Path:
    """源码默认使用仓库目录；CLI 在业务导入前指定独立实例目录。"""
    directory = os.environ.get(INSTANCE_DIRECTORY_ENV)
    if directory:
        return Path(directory).expanduser().resolve()
    if PACKAGED_INSTALL:
        raise RuntimeError("请通过 maibot run --directory <实例目录> 启动安装版 MaiBot")
    return SOURCE_ROOT


def resource_path(*parts: str) -> Path:
    return RESOURCE_ROOT.joinpath(*parts)


def project_metadata_path(project_root: Path) -> Path:
    """实例不拥有程序元数据；显式传入的源码/测试项目仍读取自身声明。"""
    root = RESOURCE_ROOT if PACKAGED_INSTALL or project_root == get_instance_root() else project_root
    return root / "pyproject.toml"


def builtin_plugin_root() -> Path:
    """内置插件代码目录，所有 Bot 共用。"""
    return (PACKAGE_ROOT / "plugins" / "built_in").resolve()


def third_party_plugin_root() -> Path:
    """第三方插件目录，归各 Bot 所有。"""
    return (get_instance_root() / "plugins").resolve()


def builtin_plugin_config_root() -> Path:
    return get_instance_root() / "config" / "builtin_plugins"


def resolve_plugin_config_path(plugin_dir: Path) -> Path:
    """内置插件只共享代码，配置归各 Bot 所有；第三方插件的配置留在插件目录内。"""
    builtin_root = builtin_plugin_root()
    resolved_plugin = plugin_dir.resolve()
    if not resolved_plugin.is_relative_to(builtin_root):
        return plugin_dir / "config.toml"
    instance_root = get_instance_root().resolve()
    config_root = builtin_plugin_config_root().resolve()
    if not config_root.is_relative_to(instance_root):
        raise ValueError(f"内置插件配置目录超出 Bot 实例: {config_root}")
    candidate = config_root / resolved_plugin.relative_to(builtin_root) / "config.toml"
    if candidate.is_symlink():
        raise ValueError(f"插件配置文件不能是符号链接: {candidate}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(config_root):
        raise ValueError(f"插件配置路径超出实例配置目录: {candidate}")
    return resolved

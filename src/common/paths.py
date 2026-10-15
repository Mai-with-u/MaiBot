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

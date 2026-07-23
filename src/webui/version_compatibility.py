"""主程序与 WebUI 的版本兼容性检查。"""

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as read_installed_version
from pathlib import Path
from typing import Literal

import json
import re
import tomllib

from src.common.version import PROJECT_ROOT, read_project_version

DASHBOARD_PACKAGE_NAME = "maibot-dashboard"

WebUICompatibilityStatus = Literal["compatible", "webui_outdated", "main_program_outdated"]

_DASHBOARD_REQUIREMENT_PATTERN = re.compile(
    rf"^{re.escape(DASHBOARD_PACKAGE_NAME)}\s*>=\s*(?P<version>[^,;\s]+)",
    re.IGNORECASE,
)
_VERSION_PATTERN = re.compile(
    r"^v?"
    r"(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>\d+))?"
    r"(?:\.?dev(?P<dev_number>\d+))?"
    r"(?:\.?post(?P<post_number>\d+))?"
    r"(?:\+[0-9a-z.-]+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WebUIVersionCompatibility:
    """主程序声明的 WebUI 版本与实际 WebUI 版本的比较结果。"""

    status: WebUICompatibilityStatus
    main_program_version: str
    webui_version: str
    required_webui_version: str


@dataclass(frozen=True)
class _ParsedVersion:
    release: tuple[int, ...]
    phase: tuple[int, int, int, int]


def _parse_version(version: str) -> _ParsedVersion:
    normalized_version = version.strip()
    match = _VERSION_PATTERN.fullmatch(normalized_version)
    if match is None:
        raise ValueError(f"不支持的版本号格式: {version}")

    release = tuple(int(part) for part in match.group("release").split("."))
    pre = match.group("pre")
    pre_number = int(match.group("pre_number") or 0)
    dev_number = match.group("dev_number")
    post_number = match.group("post_number")

    if pre is not None:
        phase_rank = {"a": 1, "b": 2, "rc": 3}[pre.lower()]
        phase = (
            phase_rank,
            pre_number,
            0 if dev_number is not None else 1,
            int(dev_number or 0),
        )
    elif dev_number is not None:
        phase = (0, int(dev_number), 0, 0)
    elif post_number is not None:
        phase = (5, int(post_number), 0, 0)
    else:
        phase = (4, 0, 0, 0)

    return _ParsedVersion(release=release, phase=phase)


def compare_versions(left: str, right: str) -> int:
    """比较两个项目版本号，返回 -1、0 或 1。"""

    parsed_left = _parse_version(left)
    parsed_right = _parse_version(right)
    release_length = max(len(parsed_left.release), len(parsed_right.release))
    left_release = parsed_left.release + (0,) * (release_length - len(parsed_left.release))
    right_release = parsed_right.release + (0,) * (release_length - len(parsed_right.release))

    left_key = (left_release, parsed_left.phase)
    right_key = (right_release, parsed_right.phase)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def _has_same_release(left: str, right: str) -> bool:
    """忽略末尾的零，判断两个版本是否属于同一个正式版本序列。"""

    left_release = _parse_version(left).release
    right_release = _parse_version(right).release
    release_length = max(len(left_release), len(right_release))
    return left_release + (0,) * (release_length - len(left_release)) == (
        right_release + (0,) * (release_length - len(right_release))
    )


def read_required_webui_version(project_root: Path | None = None) -> str:
    """从 pyproject.toml 读取主程序要求的最低 WebUI 版本。"""

    root = project_root or PROJECT_ROOT
    with (root / "pyproject.toml").open("rb") as pyproject_file:
        pyproject_data = tomllib.load(pyproject_file)

    project_data = pyproject_data.get("project")
    if not isinstance(project_data, dict):
        raise ValueError("pyproject.toml 缺少 [project] 配置节，无法读取 WebUI 版本要求")

    dependencies = project_data.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("pyproject.toml 缺少 project.dependencies，无法读取 WebUI 版本要求")

    for dependency in dependencies:
        if not isinstance(dependency, str):
            continue
        match = _DASHBOARD_REQUIREMENT_PATTERN.match(dependency.strip())
        if match is not None:
            return match.group("version")

    raise ValueError(f"pyproject.toml 未声明 {DASHBOARD_PACKAGE_NAME}>=<version>")


def read_local_webui_version(project_root: Path | None = None) -> str:
    """读取本地 Dashboard 源码声明的版本。"""

    root = project_root or PROJECT_ROOT
    package_json_path = root / "dashboard" / "package.json"
    with package_json_path.open("r", encoding="utf-8") as package_json_file:
        package_data = json.load(package_json_file)

    webui_version = package_data.get("version")
    if not isinstance(webui_version, str) or not webui_version.strip():
        raise ValueError("dashboard/package.json 缺少有效的 version 字段")
    return webui_version.strip()


def read_installed_webui_version() -> str:
    """读取当前安装的 Dashboard 静态资源包版本。"""

    try:
        return read_installed_version(DASHBOARD_PACKAGE_NAME)
    except PackageNotFoundError as exc:
        raise RuntimeError(f"未安装 {DASHBOARD_PACKAGE_NAME}，无法检查 WebUI 版本") from exc


def get_webui_version_compatibility(
    webui_version: str,
    project_root: Path | None = None,
) -> WebUIVersionCompatibility:
    """比较实际 WebUI 版本和主程序在 pyproject.toml 中声明的版本。"""

    root = project_root or PROJECT_ROOT
    required_webui_version = read_required_webui_version(root)
    comparison = compare_versions(webui_version, required_webui_version)
    if comparison < 0:
        status: WebUICompatibilityStatus = "webui_outdated"
    elif not _has_same_release(webui_version, required_webui_version):
        status = "main_program_outdated"
    else:
        status = "compatible"

    return WebUIVersionCompatibility(
        status=status,
        main_program_version=read_project_version(root),
        webui_version=webui_version,
        required_webui_version=required_webui_version,
    )

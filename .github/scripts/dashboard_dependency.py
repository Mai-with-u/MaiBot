from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
import os
import sys

import tomlkit

from release_dependencies import run_with_published_package


PACKAGE_NAME = os.environ.get("DASHBOARD_PACKAGE_NAME", "maibot-dashboard")
PYPROJECT_PATH = Path("pyproject.toml")
REQUIREMENTS_PATH = Path("requirements.txt")


def find_dashboard_requirement(requirements: Iterable[str]) -> Requirement:
    normalized_package_name = canonicalize_name(PACKAGE_NAME)

    for dependency in requirements:
        parsed_requirement = Requirement(dependency)
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            return parsed_requirement

    raise RuntimeError(f"未在依赖列表中找到 {PACKAGE_NAME}")


def wait_for_published_version(target_version: Version) -> None:
    # JSON/Simple 索引可见不等于 uv 能解析；与最终安装统一从官方源刷新并解析。
    # 临时项目只探测 Dashboard，不修改真实依赖声明、锁文件或虚拟环境。
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    probe = tomlkit.document()
    probe["project"] = {
        "name": "maibot-dashboard-release-probe",
        "version": "0.0.0",
        "requires-python": document["project"]["requires-python"],
        "dependencies": [f"{PACKAGE_NAME}=={target_version}"],
    }
    with TemporaryDirectory(prefix="maibot-dashboard-release-") as probe_dir:
        (Path(probe_dir) / "pyproject.toml").write_text(tomlkit.dumps(probe), encoding="utf-8")
        command = [
            "uv", "lock", "--project", probe_dir, "--python", sys.executable,
            "--upgrade-package", PACKAGE_NAME,
        ]
        run_with_published_package(command, PACKAGE_NAME, str(target_version), cwd=Path(probe_dir))


def update_pyproject(target_version: Version) -> bool:
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = document["project"]["dependencies"]
    current_requirement = find_dashboard_requirement(str(item) for item in dependencies)
    updated_dependency = f"{PACKAGE_NAME}=={target_version}"

    if str(current_requirement) == updated_dependency:
        print(f"pyproject.toml 已锁定到目标版本: {target_version}")
        return False

    normalized_package_name = canonicalize_name(PACKAGE_NAME)
    for index, dependency in enumerate(dependencies):
        parsed_requirement = Requirement(str(dependency))
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            dependencies[index] = updated_dependency
            break

    PYPROJECT_PATH.write_text(tomlkit.dumps(document), encoding="utf-8")
    print(f"pyproject.toml: {current_requirement} -> {updated_dependency}")
    return True


def update_requirements(target_version: Version) -> bool:
    lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    current_requirement = find_dashboard_requirement(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )
    updated_requirement = f"{PACKAGE_NAME}=={target_version}"

    if str(current_requirement) == updated_requirement:
        print(f"requirements.txt 已锁定到目标版本: {target_version}")
        return False

    normalized_package_name = canonicalize_name(PACKAGE_NAME)
    for index, line in enumerate(lines):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue

        parsed_requirement = Requirement(stripped_line)
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            if line.endswith("\r\n"):
                newline = "\r\n"
            elif line.endswith("\n"):
                newline = "\n"
            else:
                newline = ""
            lines[index] = f"{updated_requirement}{newline}"
            break

    REQUIREMENTS_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"requirements.txt: {current_requirement} -> {updated_requirement}")
    return True


def update_dependencies(target_version: Version) -> None:
    wait_for_published_version(target_version)
    update_pyproject(target_version)
    update_requirements(target_version)

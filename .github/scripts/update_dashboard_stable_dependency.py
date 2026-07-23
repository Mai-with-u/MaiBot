from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.request import urlopen
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
import json
import os

import tomlkit


PACKAGE_NAME = os.environ.get("DASHBOARD_PACKAGE_NAME", "maibot-dashboard")
PYPROJECT_PATH = Path("pyproject.toml")
REQUIREMENTS_PATH = Path("requirements.txt")
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


def find_dashboard_requirement(requirements: Iterable[str]) -> Requirement:
    normalized_package_name = canonicalize_name(PACKAGE_NAME)

    for dependency in requirements:
        parsed_requirement = Requirement(dependency)
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            return parsed_requirement

    raise RuntimeError(f"未在依赖列表中找到 {PACKAGE_NAME}")


def get_latest_stable_version() -> Version:
    with urlopen(PYPI_JSON_URL, timeout=30) as response:
        pypi_data = json.load(response)

    stable_versions: list[Version] = []
    for release_version, release_files in pypi_data["releases"].items():
        if not release_files or all(release_file.get("yanked", False) for release_file in release_files):
            continue

        try:
            parsed_version = Version(release_version)
        except InvalidVersion:
            continue

        if not parsed_version.is_prerelease:
            stable_versions.append(parsed_version)

    if not stable_versions:
        raise RuntimeError(f"PyPI 上没有找到 {PACKAGE_NAME} 的正式版本")

    return max(stable_versions)


def update_pyproject(latest_version: Version) -> bool:
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = document["project"]["dependencies"]
    current_requirement = find_dashboard_requirement(str(item) for item in dependencies)
    updated_dependency = f"{PACKAGE_NAME}=={latest_version}"

    if str(current_requirement) == updated_dependency:
        print(f"pyproject.toml 已锁定到最新正式版本: {latest_version}")
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


def update_requirements(latest_version: Version) -> bool:
    lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    current_requirement = find_dashboard_requirement(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )
    updated_requirement = f"{PACKAGE_NAME}=={latest_version}"

    if str(current_requirement) == updated_requirement:
        print(f"requirements.txt 已锁定到最新正式版本: {latest_version}")
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


def main() -> None:
    latest_version = get_latest_stable_version()
    print(f"PyPI 最新 dashboard 正式版本: {latest_version}")

    pyproject_updated = update_pyproject(latest_version)
    requirements_updated = update_requirements(latest_version)

    if pyproject_updated != requirements_updated:
        raise RuntimeError("pyproject.toml 与 requirements.txt 的 dashboard 依赖更新状态不一致")


if __name__ == "__main__":
    main()

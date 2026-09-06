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
DASHBOARD_PACKAGE_PATH = Path("dashboard/package.json")
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


def extract_pinned_min_version(requirement: Requirement) -> Version:
    matched_versions = [
        Version(specifier.version) for specifier in requirement.specifier if specifier.operator in {">=", "=="}
    ]

    if len(matched_versions) != 1:
        raise RuntimeError(f"{requirement} 的版本约束无法唯一定位当前 dashboard 版本")

    return matched_versions[0]


def get_target_base_version() -> Version:
    package_data = json.loads(DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8"))
    target_version = Version(str(package_data["version"]))
    if target_version.is_prerelease:
        raise RuntimeError(f"dashboard/package.json 中的 {target_version} 不是正式版本序列")
    return target_version


def get_latest_dev_version(target_base_version: Version) -> Version:
    with urlopen(PYPI_JSON_URL, timeout=30) as response:
        pypi_data = json.load(response)

    dev_versions: list[Version] = []
    for release_version, release_files in pypi_data["releases"].items():
        if not release_files or all(release_file.get("yanked", False) for release_file in release_files):
            continue

        try:
            parsed_version = Version(release_version)
        except InvalidVersion:
            continue

        if parsed_version.is_devrelease and parsed_version.release == target_base_version.release:
            dev_versions.append(parsed_version)

    if not dev_versions:
        raise RuntimeError(f"PyPI 上没有找到 {PACKAGE_NAME} {target_base_version} 序列的 dev 版本")

    return max(dev_versions)


def update_pyproject(latest_version: Version) -> bool:
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = document["project"]["dependencies"]
    current_requirement = find_dashboard_requirement(str(item) for item in dependencies)
    current_version = extract_pinned_min_version(current_requirement)

    if latest_version == current_version:
        print(f"pyproject.toml 已是最新 dev 版本: {current_version}")
        return False

    normalized_package_name = canonicalize_name(PACKAGE_NAME)
    updated_dependency = f"{PACKAGE_NAME}=={latest_version}"

    for index, dependency in enumerate(dependencies):
        parsed_requirement = Requirement(str(dependency))
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            dependencies[index] = updated_dependency
            break

    PYPROJECT_PATH.write_text(tomlkit.dumps(document), encoding="utf-8")
    print(f"pyproject.toml: {current_version} -> {latest_version}")
    return True


def update_requirements(latest_version: Version) -> bool:
    lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    current_requirement = find_dashboard_requirement(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )
    current_version = extract_pinned_min_version(current_requirement)

    if latest_version == current_version:
        print(f"requirements.txt 已是最新 dev 版本: {current_version}")
        return False

    normalized_package_name = canonicalize_name(PACKAGE_NAME)
    updated_requirement = f"{PACKAGE_NAME}=={latest_version}"

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
    print(f"requirements.txt: {current_version} -> {latest_version}")
    return True


def main() -> None:
    target_base_version = get_target_base_version()
    latest_version = get_latest_dev_version(target_base_version)
    print(f"PyPI 最新 dashboard {target_base_version} 序列 dev 版本: {latest_version}")

    pyproject_updated = update_pyproject(latest_version)
    requirements_updated = update_requirements(latest_version)

    if pyproject_updated != requirements_updated:
        raise RuntimeError("pyproject.toml 与 requirements.txt 的 dashboard 依赖更新状态不一致")


if __name__ == "__main__":
    main()

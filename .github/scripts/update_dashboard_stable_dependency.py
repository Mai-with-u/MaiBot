from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.request import urlopen
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
import json
import os
import time

import tomlkit


PACKAGE_NAME = os.environ.get("DASHBOARD_PACKAGE_NAME", "maibot-dashboard")
DASHBOARD_PACKAGE_PATH = Path("dashboard/package.json")
PYPROJECT_PATH = Path("pyproject.toml")
REQUIREMENTS_PATH = Path("requirements.txt")
PYPI_VERSION_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/{{version}}/json"
CHECK_INTERVAL_SECONDS = 180
MAX_CHECK_ATTEMPTS = 3


def find_dashboard_requirement(requirements: Iterable[str]) -> Requirement:
    normalized_package_name = canonicalize_name(PACKAGE_NAME)

    for dependency in requirements:
        parsed_requirement = Requirement(dependency)
        if canonicalize_name(parsed_requirement.name) == normalized_package_name:
            return parsed_requirement

    raise RuntimeError(f"未在依赖列表中找到 {PACKAGE_NAME}")


def dependencies_are_current(target_version: Version) -> bool:
    expected_requirement = f"{PACKAGE_NAME}=={target_version}"

    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    pyproject_requirement = find_dashboard_requirement(
        str(item) for item in document["project"]["dependencies"]
    )

    requirement_lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    requirements_requirement = find_dashboard_requirement(
        line.strip()
        for line in requirement_lines
        if line.strip() and not line.strip().startswith("#")
    )

    return (
        str(pyproject_requirement) == expected_requirement
        and str(requirements_requirement) == expected_requirement
    )


def get_target_stable_version() -> Version:
    package_data = json.loads(DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8"))
    target_version = Version(str(package_data["version"]))
    if target_version.is_prerelease:
        raise RuntimeError(f"dashboard/package.json 中的 {target_version} 不是正式版本")
    return target_version


def wait_for_published_version(target_version: Version) -> None:
    version_url = PYPI_VERSION_JSON_URL.format(version=target_version)

    for attempt in range(1, MAX_CHECK_ATTEMPTS + 1):
        print(
            f"等待 {CHECK_INTERVAL_SECONDS} 秒后检查 PyPI 上的 "
            f"{PACKAGE_NAME}=={target_version}（第 {attempt}/{MAX_CHECK_ATTEMPTS} 次）"
        )
        time.sleep(CHECK_INTERVAL_SECONDS)

        try:
            with urlopen(version_url, timeout=30) as response:
                pypi_data = json.load(response)
        except HTTPError as error:
            if error.code != 404:
                raise
            print(f"PyPI 上暂未找到 {PACKAGE_NAME}=={target_version}")
            continue

        release_files = pypi_data["urls"]
        if release_files and any(not release_file.get("yanked", False) for release_file in release_files):
            print(f"PyPI 已发布 dashboard 正式版本: {target_version}")
            return

        print(f"PyPI 上的 {PACKAGE_NAME}=={target_version} 没有可用的未撤回文件")

    raise RuntimeError(
        f"等待 {MAX_CHECK_ATTEMPTS * CHECK_INTERVAL_SECONDS} 秒后，"
        f"PyPI 上仍未找到可用的 {PACKAGE_NAME}=={target_version}"
    )


def update_pyproject(target_version: Version) -> bool:
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = document["project"]["dependencies"]
    current_requirement = find_dashboard_requirement(str(item) for item in dependencies)
    updated_dependency = f"{PACKAGE_NAME}=={target_version}"

    if str(current_requirement) == updated_dependency:
        print(f"pyproject.toml 已锁定到目标正式版本: {target_version}")
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
        print(f"requirements.txt 已锁定到目标正式版本: {target_version}")
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
    target_version = get_target_stable_version()
    print(f"main 分支 dashboard 目标正式版本: {target_version}")

    if dependencies_are_current(target_version):
        print(f"依赖文件已锁定到 {PACKAGE_NAME}=={target_version}，无需等待 PyPI")
        return

    wait_for_published_version(target_version)

    pyproject_updated = update_pyproject(target_version)
    requirements_updated = update_requirements(target_version)

    if pyproject_updated != requirements_updated:
        raise RuntimeError("pyproject.toml 与 requirements.txt 的 dashboard 依赖更新状态不一致")


if __name__ == "__main__":
    main()

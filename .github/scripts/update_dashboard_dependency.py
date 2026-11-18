from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version
import json
import os

from dashboard_dependency import PACKAGE_NAME, update_dependencies


PUBLISHED_DEV_VERSION = os.environ.get("DASHBOARD_DEV_VERSION")
DASHBOARD_PACKAGE_PATH = Path("dashboard/package.json")
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


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


def get_target_dev_version(target_base_version: Version) -> Version:
    if PUBLISHED_DEV_VERSION is None:
        return get_latest_dev_version(target_base_version)

    published_version = Version(PUBLISHED_DEV_VERSION)
    if not published_version.is_devrelease:
        raise RuntimeError(f"工作流发布版本 {published_version} 不是 dev 版本")
    if published_version.release != target_base_version.release:
        raise RuntimeError(
            f"工作流发布版本 {published_version} 不属于 dashboard {target_base_version} 序列"
        )
    return published_version


def main() -> None:
    target_base_version = get_target_base_version()
    latest_version = get_target_dev_version(target_base_version)
    print(f"Dashboard {target_base_version} 序列目标 dev 版本: {latest_version}")
    update_dependencies(latest_version)


if __name__ == "__main__":
    main()

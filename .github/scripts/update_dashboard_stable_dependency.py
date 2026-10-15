from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
import json
import os
import subprocess
import sys
import time

import tomlkit


PACKAGE_NAME = os.environ.get("DASHBOARD_PACKAGE_NAME", "maibot-dashboard")
DASHBOARD_PACKAGE_PATH = Path("dashboard/package.json")
PYPROJECT_PATH = Path("pyproject.toml")
REQUIREMENTS_PATH = Path("requirements.txt")
CHECK_INTERVAL_SECONDS = 15
MAX_CHECK_ATTEMPTS = 40


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
    pyproject_requirement = find_dashboard_requirement(str(item) for item in document["project"]["dependencies"])

    requirement_lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    requirements_requirement = find_dashboard_requirement(
        line.strip() for line in requirement_lines if line.strip() and not line.strip().startswith("#")
    )

    return str(pyproject_requirement) == expected_requirement and str(requirements_requirement) == expected_requirement


def get_target_stable_version() -> Version:
    package_data = json.loads(DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8"))
    target_version = Version(str(package_data["version"]))
    if target_version.is_prerelease:
        raise RuntimeError(f"dashboard/package.json 中的 {target_version} 不是正式版本")
    return target_version


def wait_for_published_version(target_version: Version) -> None:
    # JSON/Simple 索引可见不等于 uv 能解析；使用同一解析器、索引配置和 Python 范围确认。
    # 临时项目只探测 Dashboard，不修改真实依赖声明、锁文件或虚拟环境。
    document = tomlkit.parse(PYPROJECT_PATH.read_text(encoding="utf-8"))
    probe = tomlkit.document()
    probe["project"] = {
        "name": "maibot-dashboard-release-probe",
        "version": "0.0.0",
        "requires-python": document["project"]["requires-python"],
        "dependencies": [f"{PACKAGE_NAME}=={target_version}"],
    }
    if "tool" in document and "uv" in document["tool"]:
        probe["tool"] = {"uv": document["tool"]["uv"].unwrap()}

    with TemporaryDirectory(prefix="maibot-dashboard-release-") as probe_dir:
        (Path(probe_dir) / "pyproject.toml").write_text(tomlkit.dumps(probe), encoding="utf-8")
        command = [
            "uv", "lock", "--project", probe_dir, "--python", sys.executable,
            "--upgrade-package", PACKAGE_NAME, "--refresh-package", PACKAGE_NAME,
        ]
        for attempt in range(1, MAX_CHECK_ATTEMPTS + 1):
            print(f"确认 uv 可解析 {PACKAGE_NAME}=={target_version}（第 {attempt}/{MAX_CHECK_ATTEMPTS} 次）", flush=True)
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                print(f"uv 已成功解析 Dashboard 正式版本: {target_version}", flush=True)
                return
            details = (result.stderr + "\n" + result.stdout).strip()
            # 仅等待目标版本缺失这一发布同步状态；配置、网络和其他依赖错误立即暴露。
            missing_version = f"no version of {canonicalize_name(PACKAGE_NAME)}=={target_version}" in details.lower()
            if result.returncode != 1 or not missing_version:
                raise RuntimeError(f"uv 检查失败，不能作为发布同步延迟重试:\n{details}")
            print(details, flush=True)
            if attempt < MAX_CHECK_ATTEMPTS:
                time.sleep(CHECK_INTERVAL_SECONDS)
        raise RuntimeError(f"经过 {MAX_CHECK_ATTEMPTS} 次检查，uv 仍无法解析 {PACKAGE_NAME}=={target_version}:\n{details}")


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
    print(f"Dashboard 目标正式版本: {target_version}")

    # 即使依赖文件已经写入目标版本，也必须确认 PyPI 上的发布物真实可用。
    wait_for_published_version(target_version)

    if dependencies_are_current(target_version):
        print(f"依赖文件已锁定到 {PACKAGE_NAME}=={target_version}")
        return

    pyproject_updated = update_pyproject(target_version)
    requirements_updated = update_requirements(target_version)

    if pyproject_updated != requirements_updated:
        raise RuntimeError("pyproject.toml 与 requirements.txt 的 dashboard 依赖更新状态不一致")


if __name__ == "__main__":
    main()

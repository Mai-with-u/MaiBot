from __future__ import annotations

from pathlib import Path

from packaging.version import Version
import json

from dashboard_dependency import update_dependencies


DASHBOARD_PACKAGE_PATH = Path("dashboard/package.json")


def get_target_stable_version() -> Version:
    package_data = json.loads(DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8"))
    target_version = Version(str(package_data["version"]))
    if target_version.is_prerelease:
        raise RuntimeError(f"dashboard/package.json 中的 {target_version} 不是正式版本")
    return target_version


def main() -> None:
    target_version = get_target_stable_version()
    print(f"Dashboard 目标正式版本: {target_version}")
    # 即使依赖文件已是目标版本，也确认已发布的包真实可用。
    update_dependencies(target_version)


if __name__ == "__main__":
    main()

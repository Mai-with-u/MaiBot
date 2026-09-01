from __future__ import annotations

from pathlib import Path

import argparse
import re
import subprocess
import sys
import tarfile
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ERROR_LINE_PATTERN = re.compile(r"^  - (.+)$")


def _run_validator(project_root: Path) -> tuple[set[str], str]:
    result = subprocess.run(
        [sys.executable, "scripts/i18n_validate.py"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"i18n 校验器执行失败：\n{output}")

    errors: set[str] = set()
    reading_errors = False
    for line in output.splitlines():
        if line == "i18n validation failed:":
            reading_errors = True
            continue
        if line.startswith("warnings ("):
            reading_errors = False
            continue
        if reading_errors and (match := ERROR_LINE_PATTERN.match(line)):
            errors.add(match.group(1))

    if result.returncode == 1 and not errors:
        raise RuntimeError(f"无法解析 i18n 校验结果：\n{output}")
    return errors, output


def _extract_revision(revision: str, destination: Path) -> None:
    archive_path = destination.parent / "baseline.tar"
    with archive_path.open("wb") as archive_file:
        result = subprocess.run(
            ["git", "archive", "--format=tar", revision],
            cwd=PROJECT_ROOT,
            check=False,
            stdout=archive_file,
            stderr=subprocess.PIPE,
            text=False,
        )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法读取基线提交 {revision}：{error}")

    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if member.isfile() or member.isdir():
                archive.extract(member, destination, filter="data")


def main() -> int:
    parser = argparse.ArgumentParser(description="只阻止相对目标分支新增的 i18n 校验问题")
    parser.add_argument("--baseline-ref", required=True, help="用于比较的 Git 提交")
    args = parser.parse_args()

    current_errors, _current_output = _run_validator(PROJECT_ROOT)
    with tempfile.TemporaryDirectory(prefix="maibot-i18n-baseline-") as temporary_directory:
        baseline_root = Path(temporary_directory) / "repository"
        baseline_root.mkdir()
        _extract_revision(args.baseline_ref, baseline_root)
        baseline_errors, _baseline_output = _run_validator(baseline_root)

    new_errors = sorted(current_errors - baseline_errors)
    if new_errors:
        print("i18n validation failed with new errors:")
        for error in new_errors:
            print(f"  - {error}")
        return 1

    if current_errors:
        print(f"i18n validation passed; {len(current_errors)} 个既有问题与目标分支一致。")
        for error in sorted(current_errors):
            print(f"  - {error}")
    else:
        print("i18n validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

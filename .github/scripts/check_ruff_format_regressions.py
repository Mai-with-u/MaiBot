from __future__ import annotations

from pathlib import Path

import argparse
import re
import subprocess
import tarfile
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UNFORMATTED_FILE_PATTERN = re.compile(r"^Would reformat:\s+(.+)$", re.MULTILINE)


def _find_unformatted_files(project_root: Path) -> set[str]:
    result = subprocess.run(
        ["ruff", "format", "--check"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = ANSI_ESCAPE_PATTERN.sub("", f"{result.stdout}\n{result.stderr}")
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Ruff format 执行失败：\n{output.strip()}")

    unformatted_files = {Path(match).as_posix() for match in UNFORMATTED_FILE_PATTERN.findall(output)}
    if result.returncode == 1 and not unformatted_files:
        raise RuntimeError(f"无法解析 Ruff format 结果：\n{output.strip()}")
    return unformatted_files


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
    parser = argparse.ArgumentParser(description="只阻止相对目标分支新增的 Ruff 格式问题")
    parser.add_argument("--baseline-ref", required=True, help="用于比较的 Git 提交")
    args = parser.parse_args()

    current_files = _find_unformatted_files(PROJECT_ROOT)
    with tempfile.TemporaryDirectory(prefix="maibot-ruff-baseline-") as temporary_directory:
        baseline_root = Path(temporary_directory) / "repository"
        baseline_root.mkdir()
        _extract_revision(args.baseline_ref, baseline_root)
        baseline_files = _find_unformatted_files(baseline_root)

    new_files = sorted(current_files - baseline_files)
    if new_files:
        print("Ruff format 检测到相对目标分支新增的不合规文件：")
        for file_path in new_files:
            print(f"  - {file_path}")
        return 1

    print(f"Ruff format 增量检查通过；目标分支已有 {len(baseline_files)} 个待格式化文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""插件强制更新与代码、配置备份；运行数据不参与代码替换。"""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Set

import json
import shutil
import subprocess


RUNTIME_DIRECTORIES = frozenset({
    "data", "logs", "log", "temp", "tmp", "cache", "config_back", ".git",
    ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".update_backups", ".update_tmp",
})
CONFIG_NAMES = frozenset({"config.toml", "bot_config.toml", "model_config.toml", ".env"})


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True, timeout=120,
    )
    if result.returncode:
        error = (result.stderr.strip() or result.stdout.strip()).decode("utf-8")
        raise RuntimeError(f"Git 操作失败：{error}")
    return result.stdout


def git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8")


def is_code_path(name: str) -> bool:
    path = Path(name)
    parts = [part.casefold() for part in path.parts]
    if not parts or any(part in RUNTIME_DIRECTORIES for part in parts):
        return False
    if parts[0] == "config":
        return False
    return path.name.casefold() not in CONFIG_NAMES and path.suffix.casefold() not in {
        ".db", ".sqlite", ".sqlite3", ".log", ".pyc",
    }


def _path(root: Path, name: str) -> Path:
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"代码路径越界：{name}")
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise ValueError(f"代码路径包含链接：{name}")
        current = current.parent
    return path


def code_paths(root: Path, target: str) -> List[str]:
    names = set(git(root, "ls-files", "-z").split("\0"))
    names.update(git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").split("\0"))
    names.update(git(root, "ls-tree", "-r", "--name-only", "-z", target).split("\0"))
    return sorted(name for name in names if name and is_code_path(name))


def update_conflict(root: Path, target: str) -> Dict[str, Any]:
    paths = code_paths(root, target)
    changed = git(root, "diff", "HEAD", "--name-only", "-z").split("\0")
    files = [name for name in changed if name and is_code_path(name)]
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", "HEAD", target], capture_output=True, timeout=120,
    )
    if ancestor.returncode not in (0, 1):
        raise RuntimeError(ancestor.stderr.decode("utf-8"))
    tracked = set(git(root, "ls-files", "-z").split("\0"))
    tracked.update(git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").split("\0"))
    files = sorted(set(files) | {name for name in paths if name not in tracked and (root / name).exists()})
    if not files and ancestor.returncode == 0:
        return {}
    return {"code": "local_changes", "message": "存在本地代码修改或提交分叉，可备份后强制更新",
            "files": files, "diverged": ancestor.returncode == 1, "target_commit": git(root, "rev-parse", target).strip()}


def backup_code(root: Path, backup: Path, target: str) -> Path:
    """保存当前代码文件、删除状态和暂存差异；插件额外保存受管配置。"""
    paths: Set[str] = set(code_paths(root, target))
    paths.update(name for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
                 if name and is_code_path(name))
    _path(Path(backup.absolute().anchor), str(backup.absolute().relative_to(backup.absolute().anchor)))
    backup.mkdir(parents=True, exist_ok=False)
    present = []
    for name in sorted(paths):
        source = _path(root, name)
        if source.is_file():
            destination = backup / "files" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            present.append(name)
        elif source.exists():
            raise ValueError(f"代码文件与本地目录冲突：{name}")
    for name in (*CONFIG_NAMES, "config", "config_back"):
        source = _path(root, name)
        if source.is_dir():
            for child in source.rglob("*"):
                _path(root, str(child.relative_to(root)))
            shutil.copytree(source, backup / "configuration" / name)
        elif source.is_file():
            destination = backup / "configuration" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    metadata = {"commit": git(root, "rev-parse", "HEAD").strip(),
                "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                "paths": sorted(paths), "present": present}
    (backup / "snapshot.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    # 只保存代码的暂存差异，避免把配置或数据写入本体备份。
    # diff 不支持 pathspec-from-file；逐个使用 literal pathspec，分批避免 Windows 命令长度限制。
    patches = [_git_bytes(root, "diff", "--cached", "--binary", "HEAD", "--", *sorted(paths)[i:i + 50])
               for i in range(0, len(paths), 50)]
    (backup / "staged.patch").write_bytes(b"".join(patches))
    return backup


def replace_code(root: Path, target: str) -> None:
    """只检出代码路径，随后移动 HEAD；不使用 reset --hard 或 git clean。"""
    paths = code_paths(root, target)
    known = set(git(root, "ls-files", "-z").split("\0"))
    known.update(git(root, "ls-tree", "-r", "--name-only", "-z", target).split("\0"))
    for name in paths:
        path = _path(root, name)
        if path.exists() and not path.is_file():
            raise ValueError(f"代码文件与本地目录冲突：{name}")
    restore_paths = [name for name in paths if name in known]
    if restore_paths:
        with NamedTemporaryFile(delete=False) as spec:
            spec.write(b"".join(name.encode("utf-8") + b"\0" for name in restore_paths))
        try:
            git(root, "restore", f"--source={target}", "--staged", "--worktree",
                f"--pathspec-from-file={spec.name}", "--pathspec-file-nul")
        finally:
            Path(spec.name).unlink()
    for name in paths:
        if name not in known and _path(root, name).is_file():
            _path(root, name).unlink()
    git(root, "reset", "--mixed", target)


def restore_code(root: Path, backup: Path) -> None:
    metadata = json.loads((backup / "snapshot.json").read_text(encoding="utf-8"))
    replace_code(root, metadata["commit"])
    present = set(metadata["present"])
    for name in metadata["paths"]:
        destination = _path(root, name)
        if name in present:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "files" / name, destination)
        elif destination.is_file():
            destination.unlink()
    patch = backup / "staged.patch"
    if patch.stat().st_size:
        git(root, "apply", "--cached", str(patch.resolve()))

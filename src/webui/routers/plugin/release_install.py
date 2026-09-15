"""按索引中固定的 commit 安装插件，校验失败不改动现有目录。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from packaging.version import Version
import asyncio
import json
import shutil
import subprocess

from src.plugin_runtime.runner.manifest_validator import ManifestValidator, PluginDependencyDefinition
from src.webui.services.git_mirror_service import get_git_mirror_service

from .progress import update_progress
from .releases import PluginRelease, PluginReleaseEntry
from .support import (
    get_plugin_candidate_paths,
    iter_plugin_directories,
    load_manifest_json,
    parse_repository_url,
    remove_tree,
    resolve_installed_plugin_path,
    resolve_plugin_file_path,
)

RECEIPT_NAME = ".maibot-release.json"


def _work_directory(plugin_root: Path, name: str) -> Path:
    directory = plugin_root / name
    if directory.is_symlink() or directory.is_junction() or not directory.resolve().is_relative_to(plugin_root.resolve()):
        raise HTTPException(status_code=400, detail=f"插件工作目录越界或包含链接：{name}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_release_receipt(plugin_path: Path) -> Optional[Dict[str, Any]]:
    receipt_path = resolve_plugin_file_path(plugin_path, RECEIPT_NAME)
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or not isinstance(receipt.get("pinned"), bool):
        raise HTTPException(status_code=409, detail="插件安装记录损坏，请检查 .maibot-release.json")
    return receipt


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, timeout=120)
    if result.returncode:
        raise HTTPException(status_code=400, detail=f"Git 操作失败：{result.stderr.decode('utf-8', errors='replace')}")
    return result.stdout.decode("utf-8")


def _check_tree(path: Path) -> None:
    """拒绝链接，确保后续复制、清理只访问当前工作目录内的真实文件。"""
    for child in path.rglob("*"):
        if child.is_symlink() or child.is_junction():
            raise HTTPException(status_code=400, detail=f"插件目录不能包含链接：{child.relative_to(path)}")


def _preserve_user_files(source: Path, target: Path) -> None:
    _check_tree(source)
    names = {"config.toml", "config_back", "data"}
    if (source / ".git").is_dir():
        modified = _git(source, "diff", "HEAD", "--name-only", "-z").split("\0")
        changed_code = [name for name in modified if name and name not in {"config.toml", RECEIPT_NAME}]
        if changed_code:
            raise HTTPException(status_code=409, detail=f"插件存在本地代码修改，请先处理：{', '.join(changed_code)}")
        # 同时保留未跟踪和被忽略的数据文件；运行缓存和虚拟环境不复制到新版本。
        for name in _git(source, "ls-files", "--others", "-z").split("\0"):
            if not name or name == RECEIPT_NAME:
                continue
            if any(part in {".venv", "venv", "__pycache__", ".pytest_cache"} for part in Path(name).parts):
                continue
            names.add(name)
    copied: List[Path] = []
    for name in sorted(names, key=lambda value: (len(Path(value).parts), value)):
        relative = Path(name)
        if any(relative.is_relative_to(parent) for parent in copied):
            continue
        original = resolve_plugin_file_path(source, name)
        if not original.exists():
            continue
        destination = resolve_plugin_file_path(target, name)
        if destination.exists():
            if name != "config.toml":
                raise HTTPException(status_code=409, detail=f"新版本代码与本地数据路径冲突：{name}")
            if not destination.is_file():
                raise HTTPException(status_code=409, detail="新版本 config.toml 不是文件")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if original.is_dir():
            shutil.copytree(original, destination)
        else:
            shutil.copy2(original, destination)
        copied.append(relative)


def _validate_candidate(candidate: Path, entry: PluginReleaseEntry, release: PluginRelease) -> None:
    _check_tree(candidate)
    if _git(candidate, "rev-parse", "HEAD").strip() != release.commit:
        raise HTTPException(status_code=409, detail="Tag 当前指向的 commit 与版本索引不一致，已停止安装")
    # 下载来源与索引必须对应同一份 manifest，不能只比较版本字符串。
    manifest = load_manifest_json(resolve_plugin_file_path(candidate, "_manifest.json"))
    if manifest != release.manifest:
        raise HTTPException(status_code=409, detail="下载的 manifest 与版本索引不一致")
    validator = ManifestValidator(log_errors=False)
    parsed = validator.load_from_plugin_path(candidate)
    if parsed is None:
        raise HTTPException(status_code=400, detail="；".join(validator.errors))
    if parsed.id != entry.manifest_id:
        raise HTTPException(status_code=400, detail="下载的插件 ID 与索引不一致")
    available: Dict[str, str] = {}
    for directory in iter_plugin_directories():
        installed = load_manifest_json(directory / "_manifest.json")
        if installed and isinstance(installed.get("id"), str) and isinstance(installed.get("version"), str):
            available[installed["id"]] = installed["version"]
    missing = validator.get_unsatisfied_plugin_dependencies(parsed, available)
    if missing:
        raise HTTPException(status_code=400, detail=f"插件依赖不满足：{'；'.join(missing)}")
    # 切换基础插件的版本也不能破坏其他已安装插件的版本约束。
    for directory in iter_plugin_directories():
        installed = load_manifest_json(directory / "_manifest.json")
        if not installed or installed.get("manifest_version") != 2 or installed.get("id") == parsed.id:
            continue
        for declaration in installed.get("dependencies", []):
            if not isinstance(declaration, dict) or declaration.get("type") != "plugin" or declaration.get("id") != parsed.id:
                continue
            dependency = PluginDependencyDefinition.model_validate(declaration)
            if not validator.is_plugin_dependency_satisfied(dependency, parsed.version):
                raise HTTPException(
                    status_code=409,
                    detail=f"该版本不满足已安装插件 {installed['id']} 的依赖要求：{dependency.version_spec}",
                )


async def _stop_runtime(plugin_id: str) -> List[str]:
    from src.common.runtime_loop import run_on_main_loop
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    async def stop() -> List[str]:
        manager = get_plugin_runtime_manager()
        stopped: List[str] = []
        for supervisor in manager.supervisors:
            if plugin_id in supervisor.get_loaded_plugin_ids():
                result = await supervisor.unload_plugins([plugin_id], reason="release_update")
                if not result.success:
                    raise HTTPException(status_code=409, detail=f"停止插件失败：{result.failed_plugins}")
                stopped.extend(result.unloaded_plugins)
        return stopped

    return await run_on_main_loop(stop())


async def _resume_runtime(plugin_ids: List[str]) -> None:
    if not plugin_ids:
        return
    from src.common.runtime_loop import run_on_main_loop
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    success = await run_on_main_loop(
        get_plugin_runtime_manager().reload_plugins_globally(plugin_ids, reason="release_update")
    )
    if not success:
        raise HTTPException(status_code=409, detail="版本文件已替换，但插件重新加载失败，请检查插件运行日志")


async def _install_release(
    plugin_id: str,
    entry: PluginReleaseEntry,
    release: PluginRelease,
    *,
    updating: bool,
    automatic: bool,
    pinned: bool,
    mirror_id: Optional[str],
) -> Dict[str, Any]:
    operation = "update" if updating else "install"
    canonical_id = entry.manifest_id or plugin_id
    existing = resolve_installed_plugin_path(canonical_id)
    if updating and existing is None:
        raise HTTPException(status_code=404, detail="插件未安装")
    if not updating and existing is not None:
        raise HTTPException(status_code=409, detail="插件已安装")
    target, old_format = get_plugin_candidate_paths(canonical_id)
    if existing is not None:
        target = existing
    elif target.exists() or old_format.exists():
        raise HTTPException(status_code=409, detail="插件目标目录已存在，请先处理现有目录")
    old_manifest = load_manifest_json(target / "_manifest.json") if existing else None
    old_version = old_manifest["version"] if old_manifest else None
    receipt = read_release_receipt(target) if existing else None
    if automatic and receipt and receipt["pinned"]:
        raise HTTPException(status_code=409, detail="该插件已锁定版本，请在插件详情中选择版本并解除锁定后更新")
    if automatic and old_version and Version(release.version) <= Version(old_version):
        raise HTTPException(status_code=409, detail="当前已是最新兼容版本，不会自动降级或重装")
    candidate = _work_directory(target.parent, ".update_tmp") / f"{target.name}.{uuid4().hex}"
    backup = _work_directory(target.parent, ".update_backups") / f"{target.name}.{uuid4().hex}"
    stopped: List[str] = []
    swapped = False
    failure: Optional[Exception] = None
    try:
        await update_progress(stage="loading", progress=15, message=f"下载发布版本 {release.version}", operation=operation, plugin_id=plugin_id)
        _, owner, repo = parse_repository_url(entry.repositoryUrl)
        result = await get_git_mirror_service().clone_repository(
            owner=owner, repo=repo, target_path=candidate, branch=release.tag,
            depth=1, mirror_id=mirror_id, operation=operation, plugin_id=plugin_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=502, detail=result.get("error", "下载发布版本失败"))
        await asyncio.to_thread(_validate_candidate, candidate, entry, release)
        if existing:
            # 先检查目录边界；停止运行时之后再检查并复制用户文件，避免复制过程中继续写入。
            _check_tree(target)
            stopped = await _stop_runtime(canonical_id)
            await asyncio.to_thread(_preserve_user_files, target, candidate)
        receipt_data = {
            "plugin_id": canonical_id, "version": release.version, "tag": release.tag,
            "commit": release.commit, "repository_url": entry.repositoryUrl, "pinned": pinned,
        }
        (candidate / RECEIPT_NAME).write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if existing:
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            candidate.rename(target)
        except Exception:
            if existing:
                backup.rename(target)
            raise
        swapped = True
    except Exception as exc:
        failure = exc
        raise
    finally:
        if candidate.exists():
            # candidate 来自插件根目录下固定的临时目录，且下载文件已拒绝链接。
            remove_tree(candidate)
        if stopped:
            try:
                await _resume_runtime(stopped)
            except Exception as resume_error:
                if failure is not None:
                    detail = failure.detail if isinstance(failure, HTTPException) else str(failure)
                    raise HTTPException(status_code=500, detail=f"版本替换失败：{detail}；恢复插件运行也失败：{resume_error}") from failure
                raise
    await update_progress(stage="success", progress=100, message=f"已安装发布版本 {release.version}", operation=operation, plugin_id=plugin_id)
    return {
        "success": swapped, "message": "插件版本安装成功", "plugin_id": canonical_id,
        "plugin_name": release.manifest["name"], "version": release.version,
        "old_version": old_version, "new_version": release.version, "commit": release.commit,
        "pinned": pinned, "update_mode": "release", "backup_path": str(backup) if existing else None,
    }


async def install_release(
    plugin_id: str,
    entry: PluginReleaseEntry,
    release: PluginRelease,
    *,
    updating: bool,
    automatic: bool,
    pinned: bool,
    mirror_id: Optional[str],
) -> Dict[str, Any]:
    try:
        return await _install_release(
            plugin_id, entry, release, updating=updating, automatic=automatic, pinned=pinned, mirror_id=mirror_id
        )
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        await update_progress(
            stage="error", progress=0, message="插件版本安装失败", error=message,
            operation="update" if updating else "install", plugin_id=plugin_id,
        )
        raise

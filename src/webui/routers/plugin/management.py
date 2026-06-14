from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import shutil

from fastapi import APIRouter, Cookie, HTTPException
import tomlkit

from src.common.logger import get_logger
from src.webui.services.git_mirror_service import get_git_mirror_service

from .progress import update_progress
from .schemas import InstallPluginRequest, UninstallPluginRequest, UpdatePluginRequest
from .support import (
    find_plugin_path_by_id,
    get_plugin_candidate_paths,
    get_plugin_config_path,
    is_plugin_install_residue,
    iter_plugin_directories,
    load_manifest_json,
    parse_repository_url,
    remove_tree,
    require_plugin_token,
    resolve_installed_plugin_path,
    resolve_plugin_file_path,
    validate_plugin_id,
)

logger = get_logger("webui.plugin_routes")

router = APIRouter()


def _infer_plugin_id(folder_name: str, manifest: Dict[str, Any], manifest_path: Path) -> str:
    if "id" in manifest:
        return str(manifest["id"])

    author_name: Optional[str] = None
    repo_name: Optional[str] = None
    if "author" in manifest:
        author_data = manifest["author"]
        if isinstance(author_data, dict) and "name" in author_data:
            author_name = str(author_data["name"])
        elif isinstance(author_data, str):
            author_name = author_data

    if "repository_url" in manifest:
        repo_url = str(manifest["repository_url"]).rstrip("/").removesuffix(".git")
        repo_name = repo_url.split("/")[-1]

    if author_name and repo_name:
        plugin_id = f"{author_name}.{repo_name}"
    elif author_name:
        plugin_id = f"{author_name}.{folder_name}"
    elif "_" in folder_name and "." not in folder_name:
        plugin_id = folder_name.replace("_", ".", 1)
    else:
        plugin_id = folder_name

    logger.info(f"为插件 {folder_name} 自动生成 ID: {plugin_id}")
    manifest["id"] = plugin_id
    try:
        safe_manifest_path = resolve_plugin_file_path(manifest_path.parent, "_manifest.json")
        with open(safe_manifest_path, "w", encoding="utf-8") as file_obj:
            json.dump(manifest, file_obj, ensure_ascii=False, indent=2)
    except Exception as write_error:
        logger.warning(f"无法写入 ID 到 manifest: {write_error}")
    return plugin_id


def _coerce_enabled_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", "disabled"}
    return bool(value)


def _read_plugin_enabled(plugin_id: str, plugin_path: Path) -> bool:
    try:
        config_path = get_plugin_config_path(plugin_id, plugin_path)
        if not config_path.exists():
            return True
        with open(config_path, "r", encoding="utf-8") as file_obj:
            config = tomlkit.load(file_obj).unwrap()
    except Exception as exc:
        logger.warning(f"读取插件 {plugin_id} 启用状态失败，将按启用处理: {exc}")
        return True

    plugin_config = config.get("plugin") if isinstance(config, dict) else None
    if not isinstance(plugin_config, dict):
        return True
    return _coerce_enabled_value(plugin_config.get("enabled", True))


def _get_runtime_plugin_load_statuses() -> Dict[str, str]:
    try:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager().get_plugin_load_statuses()
    except Exception as exc:
        logger.warning(f"获取插件运行时加载状态失败: {exc}")
        return {}


def _get_runtime_plugin_circuit_statuses() -> Dict[str, Dict[str, Any]]:
    try:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager().get_plugin_circuit_statuses()
    except Exception as exc:
        logger.warning(f"获取插件熔断状态失败: {exc}")
        return {}


def _is_runtime_loading() -> bool:
    try:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return bool(get_plugin_runtime_manager().is_loading)
    except Exception as exc:
        logger.warning(f"获取插件运行时加载中状态失败: {exc}")
        return False


def _build_update_work_path(plugin_path: Path, plugin_id: str, directory_name: str) -> Path:
    safe_plugin_id = "".join(char if char.isalnum() or char in "._-" else "_" for char in plugin_id)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return plugin_path.parent / directory_name / f"{safe_plugin_id}.{timestamp}"


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"拒绝删除符号链接路径: {path}")
    if not path.exists():
        return
    if path.is_dir():
        remove_tree(path)
    else:
        path.unlink()


def _restore_known_user_files(source_plugin_path: Path, target_plugin_path: Path) -> None:
    """只恢复 WebUI 明确管理的用户文件，不自动混入未知文件。"""
    config_path = source_plugin_path / "config.toml"
    if config_path.is_symlink():
        raise HTTPException(status_code=400, detail="插件配置文件不能是符号链接")
    if config_path.is_file():
        target_config_path = target_plugin_path / "config.toml"
        _remove_path(target_config_path)
        shutil.copy2(config_path, target_config_path)

    config_backup_dir = source_plugin_path / "config_back"
    if config_backup_dir.is_dir() and not config_backup_dir.is_symlink():
        target_backup_dir = target_plugin_path / "config_back"
        _remove_path(target_backup_dir)
        shutil.copytree(config_backup_dir, target_backup_dir)


def _read_required_manifest(plugin_path: Path) -> Dict[str, Any]:
    manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
    if manifest is None:
        raise HTTPException(status_code=400, detail="无效的插件：_manifest.json 不存在或无法读取")
    return manifest


def _validate_updated_manifest(plugin_path: Path, expected_plugin_id: str) -> Dict[str, Any]:
    manifest = _read_required_manifest(plugin_path)
    new_plugin_id = str(manifest.get("id", "")).strip()
    if not new_plugin_id:
        raise HTTPException(status_code=400, detail="无效的插件：_manifest.json 缺少 id")
    if new_plugin_id != expected_plugin_id:
        raise HTTPException(
            status_code=400,
            detail=f"新版本插件 ID 不匹配：期望 {expected_plugin_id}，实际 {new_plugin_id}",
        )
    return manifest


async def _clone_plugin_repository_for_update(
    request: UpdatePluginRequest,
    target_path: Path,
) -> Dict[str, Any]:
    repo_url, owner, repo = parse_repository_url(request.repository_url)
    service = get_git_mirror_service()
    if "github.com" in repo_url:
        return await service.clone_repository(
            owner=owner,
            repo=repo,
            target_path=target_path,
            branch=request.branch,
            mirror_id=request.mirror_id,
            depth=1,
            operation="update",
            plugin_id=request.plugin_id,
        )

    return await service.clone_repository(
        owner=owner,
        repo=repo,
        target_path=target_path,
        branch=request.branch,
        custom_url=repo_url,
        depth=1,
        operation="update",
        plugin_id=request.plugin_id,
    )


async def _update_non_git_plugin(
    plugin_id: str,
    plugin_path: Path,
    old_manifest: Dict[str, Any],
    request: UpdatePluginRequest,
) -> Dict[str, Any]:
    old_version = str(old_manifest.get("version", "unknown"))
    old_manifest_id = str(old_manifest.get("id") or plugin_id).strip()
    candidate_path = _build_update_work_path(plugin_path, plugin_id, ".update_tmp")
    backup_path = _build_update_work_path(plugin_path, plugin_id, ".update_backups")
    old_moved = False

    try:
        await update_progress(
            stage="loading",
            progress=30,
            message="当前插件不是 Git 仓库，正在重新克隆新版本...",
            operation="update",
            plugin_id=plugin_id,
        )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        result = await _clone_plugin_repository_for_update(request, candidate_path)
        if not result.get("success"):
            error_msg = str(result.get("error", "克隆失败"))
            raise HTTPException(status_code=int(result.get("status_code", 500)), detail=error_msg)

        await update_progress(
            stage="loading",
            progress=70,
            message="正在校验新版本插件身份...",
            operation="update",
            plugin_id=plugin_id,
        )
        new_manifest = _validate_updated_manifest(candidate_path, old_manifest_id)
        _restore_known_user_files(plugin_path, candidate_path)

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.rename(backup_path)
        old_moved = True
        candidate_path.rename(plugin_path)

        new_version = str(new_manifest.get("version", "unknown"))
        new_name = str(new_manifest.get("name", plugin_id))
        return {
            "success": True,
            "message": "插件更新成功",
            "plugin_id": plugin_id,
            "plugin_name": new_name,
            "old_version": old_version,
            "new_version": new_version,
            "update_mode": "reinstall_from_backup",
            "backup_path": str(backup_path),
        }
    except Exception:
        if candidate_path.exists():
            _remove_path(candidate_path)
        if old_moved and backup_path.exists() and not plugin_path.exists():
            backup_path.rename(plugin_path)
        raise


def _write_plugin_disabled_for_uninstall(plugin_path: Path) -> None:
    config_path = resolve_plugin_file_path(plugin_path, "config.toml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as file_obj:
            config_doc = tomlkit.load(file_obj)
    else:
        config_doc = tomlkit.document()

    plugin_section = config_doc.get("plugin")
    if not isinstance(plugin_section, dict):
        plugin_section = tomlkit.table()
        config_doc["plugin"] = plugin_section
    plugin_section["enabled"] = False

    with open(config_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(tomlkit.dumps(config_doc))


async def _release_plugin_runtime_before_delete(plugin_id: str, plugin_path: Path) -> bool:
    try:
        _write_plugin_disabled_for_uninstall(plugin_path)

        from src.common.runtime_loop import run_on_main_loop
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return await run_on_main_loop(get_plugin_runtime_manager().reload_plugins_globally([plugin_id], reason="uninstall"))
    except Exception as exc:
        logger.warning(f"插件 {plugin_id} 删除前运行时卸载失败，将继续尝试删除文件: {exc}")
        return False


@router.post("/install")
async def install_plugin(request: InstallPluginRequest, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到安装插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(
            stage="loading", progress=5, message=f"开始安装插件: {plugin_id}", operation="install", plugin_id=plugin_id
        )

        repo_url, owner, repo = parse_repository_url(request.repository_url)
        await update_progress(
            stage="loading",
            progress=10,
            message=f"解析仓库信息: {owner}/{repo}",
            operation="install",
            plugin_id=plugin_id,
        )

        target_path, old_format_path = get_plugin_candidate_paths(plugin_id)
        for candidate_path in (target_path, old_format_path):
            if is_plugin_install_residue(candidate_path):
                logger.warning(f"检测到插件安装残留目录，安装前自动清理: {candidate_path}")
                remove_tree(candidate_path)
        if target_path.exists() or old_format_path.exists():
            await update_progress(
                stage="error",
                progress=0,
                message="插件已存在",
                operation="install",
                plugin_id=plugin_id,
                error="插件已安装，请先卸载",
            )
            raise HTTPException(status_code=400, detail="插件已安装")

        await update_progress(
            stage="loading", progress=15, message=f"准备克隆到: {target_path}", operation="install", plugin_id=plugin_id
        )
        service = get_git_mirror_service()
        if "github.com" in repo_url:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=target_path,
                branch=request.branch,
                mirror_id=request.mirror_id,
                depth=1,
                plugin_id=plugin_id,
            )
        else:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=target_path,
                branch=request.branch,
                custom_url=repo_url,
                depth=1,
                plugin_id=plugin_id,
            )

        if not result.get("success"):
            error_msg = str(result.get("error", "克隆失败"))
            await update_progress(
                stage="error",
                progress=0,
                message="克隆仓库失败",
                operation="install",
                plugin_id=plugin_id,
                error=error_msg,
            )
            raise HTTPException(status_code=int(result.get("status_code", 500)), detail=error_msg)

        await update_progress(
            stage="loading", progress=85, message="验证插件文件...", operation="install", plugin_id=plugin_id
        )
        manifest_path = resolve_plugin_file_path(target_path, "_manifest.json")
        if not manifest_path.exists():
            remove_tree(target_path)
            await update_progress(
                stage="error",
                progress=0,
                message="插件缺少 _manifest.json",
                operation="install",
                plugin_id=plugin_id,
                error="无效的插件格式",
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        await update_progress(
            stage="loading", progress=90, message="读取插件配置...", operation="install", plugin_id=plugin_id
        )
        try:
            with open(manifest_path, "r", encoding="utf-8") as file_obj:
                manifest = json.load(file_obj)
            for field in ["manifest_version", "name", "version", "author"]:
                if field not in manifest:
                    raise ValueError(f"缺少必需字段: {field}")
            if not str(manifest.get("id", "")).strip():
                manifest["id"] = plugin_id
                with open(manifest_path, "w", encoding="utf-8") as file_obj:
                    json.dump(manifest, file_obj, ensure_ascii=False, indent=2)
        except Exception as e:
            remove_tree(target_path)
            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="install",
                plugin_id=plugin_id,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e

        await update_progress(
            stage="success",
            progress=100,
            message=f"成功安装插件: {manifest['name']} v{manifest['version']}",
            operation="install",
            plugin_id=plugin_id,
        )
        return {
            "success": True,
            "message": "插件安装成功",
            "plugin_id": plugin_id,
            "plugin_name": manifest["name"],
            "version": manifest["version"],
            "path": str(target_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装插件失败: {e}", exc_info=True)
        await update_progress(
            stage="error", progress=0, message="安装失败", operation="install", plugin_id=plugin_id, error=str(e)
        )
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/uninstall")
async def uninstall_plugin(
    request: UninstallPluginRequest, maibot_session: Optional[str] = Cookie(None)
) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到卸载插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(
            stage="loading",
            progress=10,
            message=f"开始卸载插件: {plugin_id}",
            operation="uninstall",
            plugin_id=plugin_id,
        )
        plugin_path = resolve_installed_plugin_path(plugin_id)
        if plugin_path is None:
            await update_progress(
                stage="error",
                progress=0,
                message="插件不存在",
                operation="uninstall",
                plugin_id=plugin_id,
                error="插件未安装或已被删除",
            )
            raise HTTPException(status_code=404, detail="插件未安装")

        manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
        plugin_name = str(manifest.get("name", plugin_id)) if manifest is not None else plugin_id
        runtime_plugin_id = str(manifest.get("id", plugin_id)) if manifest is not None else plugin_id
        await update_progress(
            stage="loading",
            progress=30,
            message=f"正在卸载运行中的插件: {plugin_name}",
            operation="uninstall",
            plugin_id=plugin_id,
        )
        await _release_plugin_runtime_before_delete(runtime_plugin_id, plugin_path)
        await update_progress(
            stage="loading",
            progress=45,
            message=f"正在删除插件文件: {plugin_path}",
            operation="uninstall",
            plugin_id=plugin_id,
        )
        await update_progress(
            stage="loading",
            progress=50,
            message=f"正在删除 {plugin_name}...",
            operation="uninstall",
            plugin_id=plugin_id,
        )
        remove_tree(plugin_path)
        logger.info(f"成功卸载插件: {plugin_id} ({plugin_name})")
        await update_progress(
            stage="success",
            progress=100,
            message=f"成功卸载插件: {plugin_name}",
            operation="uninstall",
            plugin_id=plugin_id,
        )
        return {"success": True, "message": "插件卸载成功", "plugin_id": plugin_id, "plugin_name": plugin_name}
    except HTTPException:
        raise
    except PermissionError as e:
        logger.error(f"卸载插件失败（权限错误）: {e}")
        await update_progress(
            stage="error",
            progress=0,
            message="卸载失败",
            operation="uninstall",
            plugin_id=plugin_id,
            error="权限不足，无法删除插件文件",
        )
        raise HTTPException(status_code=500, detail="权限不足，无法删除插件文件") from e
    except Exception as e:
        logger.error(f"卸载插件失败: {e}", exc_info=True)
        await update_progress(
            stage="error", progress=0, message="卸载失败", operation="uninstall", plugin_id=plugin_id, error=str(e)
        )
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/update")
async def update_plugin(request: UpdatePluginRequest, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到更新插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(
            stage="loading", progress=5, message=f"开始更新插件: {plugin_id}", operation="update", plugin_id=plugin_id
        )
        plugin_path = resolve_installed_plugin_path(plugin_id)
        if plugin_path is None:
            await update_progress(
                stage="error",
                progress=0,
                message="插件不存在",
                operation="update",
                plugin_id=plugin_id,
                error="插件未安装，请先安装",
            )
            raise HTTPException(status_code=404, detail="插件未安装")

        manifest = _read_required_manifest(plugin_path)
        old_version = str(manifest.get("version", "unknown"))
        old_manifest_id = str(manifest.get("id") or plugin_id).strip()
        await update_progress(
            stage="loading",
            progress=10,
            message=f"当前版本: {old_version}，准备更新...",
            operation="update",
            plugin_id=plugin_id,
        )

        if not (plugin_path / ".git").is_dir():
            result = await _update_non_git_plugin(plugin_id, plugin_path, manifest, request)
            await update_progress(
                stage="success",
                progress=100,
                message=f"成功更新 {result['plugin_name']}: {old_version} → {result['new_version']}",
                operation="update",
                plugin_id=plugin_id,
            )
            return result

        await update_progress(
            stage="loading", progress=30, message="正在通过 Git 拉取新版本...", operation="update", plugin_id=plugin_id
        )
        service = get_git_mirror_service()
        result = await service.pull_repository(
            repository_path=plugin_path,
            branch=request.branch,
            remote_url=request.repository_url,
        )

        if not result.get("success"):
            error_msg = str(result.get("error", "Git 更新失败"))
            await update_progress(
                stage="error",
                progress=0,
                message="Git 拉取新版本失败",
                operation="update",
                plugin_id=plugin_id,
                error=error_msg,
            )
            raise HTTPException(status_code=int(result.get("status_code", 500)), detail=error_msg)

        await update_progress(
            stage="loading", progress=90, message="验证新版本...", operation="update", plugin_id=plugin_id
        )
        new_manifest_path = resolve_plugin_file_path(plugin_path, "_manifest.json")
        if not new_manifest_path.exists():
            await update_progress(
                stage="error",
                progress=0,
                message="新版本缺少 _manifest.json",
                operation="update",
                plugin_id=plugin_id,
                error="无效的插件格式",
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        try:
            new_manifest = _validate_updated_manifest(plugin_path, old_manifest_id)
            new_version = str(new_manifest.get("version", "unknown"))
            new_name = str(new_manifest.get("name", plugin_id))
            logger.info(f"成功更新插件: {plugin_id} {old_version} → {new_version}")
            await update_progress(
                stage="success",
                progress=100,
                message=f"成功更新 {new_name}: {old_version} → {new_version}",
                operation="update",
                plugin_id=plugin_id,
            )
            return {
                "success": True,
                "message": "插件更新成功",
                "plugin_id": plugin_id,
                "plugin_name": new_name,
                "old_version": old_version,
                "new_version": new_version,
                "update_mode": "git_pull",
            }
        except HTTPException as e:
            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="update",
                plugin_id=plugin_id,
                error=str(e.detail),
            )
            raise
        except Exception as e:
            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="update",
                plugin_id=plugin_id,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件失败: {e}", exc_info=True)
        await update_progress(
            stage="error", progress=0, message="更新失败", operation="update", plugin_id=plugin_id, error=str(e)
        )
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/installed")
async def get_installed_plugins(maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info("收到获取已安装插件列表请求")

    try:
        installed_plugins: List[Dict[str, Any]] = []
        runtime_statuses = _get_runtime_plugin_load_statuses()
        circuit_statuses = _get_runtime_plugin_circuit_statuses()
        runtime_loading = _is_runtime_loading()
        for plugin_path in iter_plugin_directories():
            folder_name = plugin_path.name
            if folder_name.startswith(".") or folder_name.startswith("__"):
                continue

            manifest_path = resolve_plugin_file_path(plugin_path, "_manifest.json")
            if not manifest_path.exists():
                logger.warning(f"插件文件夹 {folder_name} 缺少 _manifest.json，跳过")
                continue

            try:
                manifest = load_manifest_json(manifest_path)
                if manifest is None:
                    logger.warning(f"插件文件夹 {folder_name} 的 _manifest.json 不安全或无效，跳过")
                    continue
                if "name" not in manifest or "version" not in manifest:
                    logger.warning(f"插件文件夹 {folder_name} 的 _manifest.json 格式无效，跳过")
                    continue
                plugin_id = _infer_plugin_id(folder_name, manifest, manifest_path)
                enabled = _read_plugin_enabled(plugin_id, plugin_path)
                load_status = runtime_statuses.get(plugin_id, "unknown")
                if enabled and load_status == "unknown" and runtime_loading:
                    load_status = "loading"
                circuit_status = circuit_statuses.get(plugin_id)
                installed_plugins.append(
                    {
                        "id": plugin_id,
                        "manifest": manifest,
                        "path": str(plugin_path.absolute()),
                        "enabled": enabled,
                        "disabled": not enabled,
                        "loaded": load_status == "success",
                        "load_status": "disabled" if not enabled else load_status,
                        "circuit_status": circuit_status,
                    }
                )
            except json.JSONDecodeError as e:
                logger.warning(f"插件 {folder_name} 的 _manifest.json 解析失败: {e}")
            except Exception as e:
                logger.error(f"读取插件 {folder_name} 信息时出错: {e}")

        seen_ids: Dict[str, str] = {}
        unique_plugins: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []
        for plugin in installed_plugins:
            plugin_id = str(plugin["id"])
            plugin_path = str(plugin["path"])
            if plugin_id not in seen_ids:
                seen_ids[plugin_id] = plugin_path
                unique_plugins.append(plugin)
            else:
                duplicates.append(plugin)
                logger.warning(f"重复插件 {plugin_id}: 保留 {seen_ids[plugin_id]}, 跳过 {plugin_path}")

        if duplicates:
            logger.warning(f"共检测到 {len(duplicates)} 个重复插件已去重")

        logger.info(f"找到 {len(unique_plugins)} 个已安装插件")
        return {"success": True, "plugins": unique_plugins, "total": len(unique_plugins)}
    except Exception as e:
        logger.error(f"获取已安装插件列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/local-readme/{plugin_id}")
async def get_local_plugin_readme(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"获取本地插件 README: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            return {"success": False, "error": "插件未安装"}

        for readme_name in ["README.md", "readme.md", "Readme.md", "README.MD"]:
            readme_path = resolve_plugin_file_path(plugin_path, readme_name)
            if readme_path.exists():
                try:
                    with open(readme_path, "r", encoding="utf-8") as file_obj:
                        readme_content = file_obj.read()
                    logger.info(f"成功读取本地 README: {readme_path}")
                    return {"success": True, "data": readme_content}
                except Exception as e:
                    logger.warning(f"读取 {readme_path} 失败: {e}")

        return {"success": False, "error": "本地未找到 README 文件"}
    except Exception as e:
        logger.error(f"获取本地 README 失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

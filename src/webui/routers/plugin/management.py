from pathlib import Path
from typing import Any, Optional

import json

from fastapi import APIRouter, Cookie, HTTPException

from src.common.logger import get_logger
from src.webui.services.git_mirror_service import get_git_mirror_service

from .progress import update_progress
from .schemas import InstallPluginRequest, UninstallPluginRequest, UpdatePluginRequest
from .support import (
    find_plugin_path_by_id,
    get_plugin_candidate_paths,
    iter_plugin_directories,
    load_manifest_json,
    parse_repository_url,
    remove_tree,
    require_plugin_token,
    resolve_plugin_file_path,
    resolve_installed_plugin_path,
    validate_plugin_id,
)

logger = get_logger("webui.plugin_routes")

router = APIRouter()


def _infer_plugin_id(folder_name: str, manifest: dict[str, Any], manifest_path: Path) -> str:
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


@router.post("/install")
async def install_plugin(request: InstallPluginRequest, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到安装插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(stage="loading", progress=5, message=f"开始安装插件: {plugin_id}", operation="install", plugin_id=plugin_id)

        repo_url, owner, repo = parse_repository_url(request.repository_url)
        await update_progress(stage="loading", progress=10, message=f"解析仓库信息: {owner}/{repo}", operation="install", plugin_id=plugin_id)

        target_path, old_format_path = get_plugin_candidate_paths(plugin_id)
        if target_path.exists() or old_format_path.exists():
            await update_progress(stage="error", progress=0, message="插件已存在", operation="install", plugin_id=plugin_id, error="插件已安装，请先卸载")
            raise HTTPException(status_code=400, detail="插件已安装")

        await update_progress(stage="loading", progress=15, message=f"准备克隆到: {target_path}", operation="install", plugin_id=plugin_id)
        service = get_git_mirror_service()
        if "github.com" in repo_url:
            result = await service.clone_repository(owner=owner, repo=repo, target_path=target_path, branch=request.branch, mirror_id=request.mirror_id, depth=1)
        else:
            result = await service.clone_repository(owner=owner, repo=repo, target_path=target_path, branch=request.branch, custom_url=repo_url, depth=1)

        if not result.get("success"):
            error_msg = str(result.get("error", "克隆失败"))
            await update_progress(stage="error", progress=0, message="克隆仓库失败", operation="install", plugin_id=plugin_id, error=error_msg)
            raise HTTPException(status_code=int(result.get("status_code", 500)), detail=error_msg)

        await update_progress(stage="loading", progress=85, message="验证插件文件...", operation="install", plugin_id=plugin_id)
        manifest_path = resolve_plugin_file_path(target_path, "_manifest.json")
        if not manifest_path.exists():
            remove_tree(target_path)
            await update_progress(stage="error", progress=0, message="插件缺少 _manifest.json", operation="install", plugin_id=plugin_id, error="无效的插件格式")
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        await update_progress(stage="loading", progress=90, message="读取插件配置...", operation="install", plugin_id=plugin_id)
        try:
            with open(manifest_path, "r", encoding="utf-8") as file_obj:
                manifest = json.load(file_obj)
            for field in ["manifest_version", "name", "version", "author"]:
                if field not in manifest:
                    raise ValueError(f"缺少必需字段: {field}")
            manifest["id"] = plugin_id
            with open(manifest_path, "w", encoding="utf-8") as file_obj:
                json.dump(manifest, file_obj, ensure_ascii=False, indent=2)
        except Exception as e:
            remove_tree(target_path)
            await update_progress(stage="error", progress=0, message="_manifest.json 无效", operation="install", plugin_id=plugin_id, error=str(e))
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e

        await update_progress(stage="success", progress=100, message=f"成功安装插件: {manifest['name']} v{manifest['version']}", operation="install", plugin_id=plugin_id)
        return {"success": True, "message": "插件安装成功", "plugin_id": plugin_id, "plugin_name": manifest["name"], "version": manifest["version"], "path": str(target_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装插件失败: {e}", exc_info=True)
        await update_progress(stage="error", progress=0, message="安装失败", operation="install", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/uninstall")
async def uninstall_plugin(request: UninstallPluginRequest, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到卸载插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(stage="loading", progress=10, message=f"开始卸载插件: {plugin_id}", operation="uninstall", plugin_id=plugin_id)
        plugin_path = resolve_installed_plugin_path(plugin_id)
        if plugin_path is None:
            await update_progress(stage="error", progress=0, message="插件不存在", operation="uninstall", plugin_id=plugin_id, error="插件未安装或已被删除")
            raise HTTPException(status_code=404, detail="插件未安装")

        await update_progress(stage="loading", progress=30, message=f"正在删除插件文件: {plugin_path}", operation="uninstall", plugin_id=plugin_id)
        manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
        plugin_name = str(manifest.get("name", plugin_id)) if manifest is not None else plugin_id
        await update_progress(stage="loading", progress=50, message=f"正在删除 {plugin_name}...", operation="uninstall", plugin_id=plugin_id)
        remove_tree(plugin_path)
        logger.info(f"成功卸载插件: {plugin_id} ({plugin_name})")
        await update_progress(stage="success", progress=100, message=f"成功卸载插件: {plugin_name}", operation="uninstall", plugin_id=plugin_id)
        return {"success": True, "message": "插件卸载成功", "plugin_id": plugin_id, "plugin_name": plugin_name}
    except HTTPException:
        raise
    except PermissionError as e:
        logger.error(f"卸载插件失败（权限错误）: {e}")
        await update_progress(stage="error", progress=0, message="卸载失败", operation="uninstall", plugin_id=plugin_id, error="权限不足，无法删除插件文件")
        raise HTTPException(status_code=500, detail="权限不足，无法删除插件文件") from e
    except Exception as e:
        logger.error(f"卸载插件失败: {e}", exc_info=True)
        await update_progress(stage="error", progress=0, message="卸载失败", operation="uninstall", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/update")
async def update_plugin(request: UpdatePluginRequest, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"收到更新插件请求: {request.plugin_id}")
    plugin_id = request.plugin_id

    try:
        plugin_id = validate_plugin_id(request.plugin_id)
        await update_progress(stage="loading", progress=5, message=f"开始更新插件: {plugin_id}", operation="update", plugin_id=plugin_id)
        plugin_path = resolve_installed_plugin_path(plugin_id)
        if plugin_path is None:
            await update_progress(stage="error", progress=0, message="插件不存在", operation="update", plugin_id=plugin_id, error="插件未安装，请先安装")
            raise HTTPException(status_code=404, detail="插件未安装")

        manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
        old_version = str(manifest.get("version", "unknown")) if manifest is not None else "unknown"
        await update_progress(stage="loading", progress=10, message=f"当前版本: {old_version}，准备更新...", operation="update", plugin_id=plugin_id)
        await update_progress(stage="loading", progress=20, message="正在删除旧版本...", operation="update", plugin_id=plugin_id)
        remove_tree(plugin_path)

        await update_progress(stage="loading", progress=30, message="正在准备下载新版本...", operation="update", plugin_id=plugin_id)
        repo_url, owner, repo = parse_repository_url(request.repository_url)
        service = get_git_mirror_service()
        if "github.com" in repo_url:
            result = await service.clone_repository(owner=owner, repo=repo, target_path=plugin_path, branch=request.branch, mirror_id=request.mirror_id, depth=1)
        else:
            result = await service.clone_repository(owner=owner, repo=repo, target_path=plugin_path, branch=request.branch, custom_url=repo_url, depth=1)

        if not result.get("success"):
            error_msg = str(result.get("error", "克隆失败"))
            await update_progress(stage="error", progress=0, message="下载新版本失败", operation="update", plugin_id=plugin_id, error=error_msg)
            raise HTTPException(status_code=int(result.get("status_code", 500)), detail=error_msg)

        await update_progress(stage="loading", progress=90, message="验证新版本...", operation="update", plugin_id=plugin_id)
        new_manifest_path = resolve_plugin_file_path(plugin_path, "_manifest.json")
        if not new_manifest_path.exists():
            remove_tree(plugin_path)
            await update_progress(stage="error", progress=0, message="新版本缺少 _manifest.json", operation="update", plugin_id=plugin_id, error="无效的插件格式")
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        try:
            with open(new_manifest_path, "r", encoding="utf-8") as file_obj:
                new_manifest = json.load(file_obj)
            new_version = str(new_manifest.get("version", "unknown"))
            new_name = str(new_manifest.get("name", plugin_id))
            logger.info(f"成功更新插件: {plugin_id} {old_version} → {new_version}")
            await update_progress(stage="success", progress=100, message=f"成功更新 {new_name}: {old_version} → {new_version}", operation="update", plugin_id=plugin_id)
            return {"success": True, "message": "插件更新成功", "plugin_id": plugin_id, "plugin_name": new_name, "old_version": old_version, "new_version": new_version}
        except Exception as e:
            remove_tree(plugin_path)
            await update_progress(stage="error", progress=0, message="_manifest.json 无效", operation="update", plugin_id=plugin_id, error=str(e))
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件失败: {e}", exc_info=True)
        await update_progress(stage="error", progress=0, message="更新失败", operation="update", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/installed")
async def get_installed_plugins(maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info("收到获取已安装插件列表请求")

    try:
        installed_plugins: list[dict[str, Any]] = []
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
                installed_plugins.append({"id": plugin_id, "manifest": manifest, "path": str(plugin_path.absolute())})
            except json.JSONDecodeError as e:
                logger.warning(f"插件 {folder_name} 的 _manifest.json 解析失败: {e}")
            except Exception as e:
                logger.error(f"读取插件 {folder_name} 信息时出错: {e}")

        seen_ids: dict[str, str] = {}
        unique_plugins: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
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
async def get_local_plugin_readme(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
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
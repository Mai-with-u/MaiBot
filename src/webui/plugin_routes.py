from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from pathlib import Path
import json
import os
import toml
from src.plugin_system.base.config_types import ConfigField
from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.plugin_system.core.dependency_manager import plugin_dependency_manager
from src.plugin_system.core.plugin_manager import plugin_manager
from .git_mirror_service import get_git_mirror_service, set_update_progress_callback
from .token_manager import get_token_manager
from .plugin_progress_ws import update_progress

logger = get_logger("webui.plugin_routes")

# 创建路由器
router = APIRouter(prefix="/plugins", tags=["插件管理"])

# 设置进度更新回调
set_update_progress_callback(update_progress)


def parse_version(version_str: str) -> tuple[int, int, int]:
    """
    解析版本号字符串
    
    支持格式:
    - 0.11.2 -> (0, 11, 2)
    - 0.11.2.snapshot.2 -> (0, 11, 2)
    
    Returns:
        (major, minor, patch) 三元组
    """
    # 移除 snapshot 等后缀
    base_version = version_str.split('.snapshot')[0].split('.dev')[0].split('.alpha')[0].split('.beta')[0]

    parts = base_version.split('.')
    if len(parts) < 3:
        # 补齐到 3 位
        parts.extend(['0'] * (3 - len(parts)))

    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])
        return (major, minor, patch)
    except (ValueError, IndexError):
        logger.warning(f"无法解析版本号: {version_str}，返回默认值 (0, 0, 0)")
        return (0, 0, 0)


# ============ 请求/响应模型 ============

class FetchRawFileRequest(BaseModel):
    """获取 Raw 文件请求"""
    owner: str = Field(..., description="仓库所有者", example="MaiM-with-u")
    repo: str = Field(..., description="仓库名称", example="plugin-repo")
    branch: str = Field(..., description="分支名称", example="main")
    file_path: str = Field(..., description="文件路径", example="plugin_details.json")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    custom_url: Optional[str] = Field(None, description="自定义完整 URL")


class FetchRawFileResponse(BaseModel):
    """获取 Raw 文件响应"""
    success: bool = Field(..., description="是否成功")
    data: Optional[str] = Field(None, description="文件内容")
    error: Optional[str] = Field(None, description="错误信息")
    mirror_used: Optional[str] = Field(None, description="使用的镜像源")
    attempts: int = Field(..., description="尝试次数")
    url: Optional[str] = Field(None, description="实际请求的 URL")


class CloneRepositoryRequest(BaseModel):
    """克隆仓库请求"""
    owner: str = Field(..., description="仓库所有者", example="MaiM-with-u")
    repo: str = Field(..., description="仓库名称", example="plugin-repo")
    target_path: str = Field(..., description="目标路径（相对于插件目录）")
    branch: Optional[str] = Field(None, description="分支名称", example="main")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    custom_url: Optional[str] = Field(None, description="自定义克隆 URL")
    depth: Optional[int] = Field(None, description="克隆深度（浅克隆）", ge=1)


class CloneRepositoryResponse(BaseModel):
    """克隆仓库响应"""
    success: bool = Field(..., description="是否成功")
    path: Optional[str] = Field(None, description="克隆路径")
    error: Optional[str] = Field(None, description="错误信息")
    mirror_used: Optional[str] = Field(None, description="使用的镜像源")
    attempts: int = Field(..., description="尝试次数")
    url: Optional[str] = Field(None, description="实际克隆的 URL")
    message: Optional[str] = Field(None, description="附加信息")


class MirrorConfigResponse(BaseModel):
    """镜像源配置响应"""
    id: str = Field(..., description="镜像源 ID")
    name: str = Field(..., description="镜像源名称")
    raw_prefix: str = Field(..., description="Raw 文件前缀")
    clone_prefix: str = Field(..., description="克隆前缀")
    enabled: bool = Field(..., description="是否启用")
    priority: int = Field(..., description="优先级（数字越小优先级越高）")


class AvailableMirrorsResponse(BaseModel):
    """可用镜像源列表响应"""
    mirrors: List[MirrorConfigResponse] = Field(..., description="镜像源列表")
    default_priority: List[str] = Field(..., description="默认优先级顺序（ID 列表）")


class AddMirrorRequest(BaseModel):
    """添加镜像源请求"""
    id: str = Field(..., description="镜像源 ID", example="custom-mirror")
    name: str = Field(..., description="镜像源名称", example="自定义镜像源")
    raw_prefix: str = Field(..., description="Raw 文件前缀", example="https://example.com/raw")
    clone_prefix: str = Field(..., description="克隆前缀", example="https://example.com/clone")
    enabled: bool = Field(True, description="是否启用")
    priority: Optional[int] = Field(None, description="优先级")


class UpdateMirrorRequest(BaseModel):
    """更新镜像源请求"""
    name: Optional[str] = Field(None, description="镜像源名称")
    raw_prefix: Optional[str] = Field(None, description="Raw 文件前缀")
    clone_prefix: Optional[str] = Field(None, description="克隆前缀")
    enabled: Optional[bool] = Field(None, description="是否启用")
    priority: Optional[int] = Field(None, description="优先级")


class GitStatusResponse(BaseModel):
    """Git 安装状态响应"""
    installed: bool = Field(..., description="是否已安装 Git")
    version: Optional[str] = Field(None, description="Git 版本号")
    path: Optional[str] = Field(None, description="Git 可执行文件路径")
    error: Optional[str] = Field(None, description="错误信息")


class InstallPluginRequest(BaseModel):
    """安装插件请求"""
    plugin_id: str = Field(..., description="插件 ID")
    repository_url: str = Field(..., description="插件仓库 URL")
    branch: Optional[str] = Field("main", description="分支名称")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")


class VersionResponse(BaseModel):
    """麦麦版本响应"""
    version: str = Field(..., description="麦麦版本号")
    version_major: int = Field(..., description="主版本号")
    version_minor: int = Field(..., description="次版本号")
    version_patch: int = Field(..., description="补丁版本号")


class UninstallPluginRequest(BaseModel):
    """卸载插件请求"""
    plugin_id: str = Field(..., description="插件 ID")


class UpdatePluginRequest(BaseModel):
    """更新插件请求"""
    plugin_id: str = Field(..., description="插件 ID")
    repository_url: str = Field(..., description="插件仓库 URL")
    branch: Optional[str] = Field("main", description="分支名称")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")


class PluginConfigItem(BaseModel):
    """插件配置项"""
    key: str = Field(..., description="配置键（section.key）")
    label: str = Field(..., description="显示标签")
    value: Any = Field(..., description="当前值")
    description: str = Field(..., description="描述")
    section: str = Field(..., description="所属节")
    required: bool = Field(..., description="是否必需")
    default: Any = Field(..., description="默认值")
    type: str = Field(..., description="字段类型: input, number, switch, select, array")
    options: Optional[List[Any]] = Field(None, description="可选值列表")


class PluginConfigResponse(BaseModel):
    """插件配置响应"""
    success: bool = Field(..., description="是否成功")
    data: List[PluginConfigItem] = Field(..., description="配置项列表")
    error: Optional[str] = Field(None, description="错误信息")


class UpdatePluginConfigRequest(BaseModel):
    """更新插件配置请求"""
    configs: Dict[str, Any] = Field(..., description="配置键值对")


# ============ API 路由 ============

@router.get("/version", response_model=VersionResponse)
async def get_maimai_version() -> VersionResponse:
    """
    获取麦麦版本信息
    
    此接口无需认证，用于前端检查插件兼容性
    """
    major, minor, patch = parse_version(MMC_VERSION)

    return VersionResponse(
        version=MMC_VERSION,
        version_major=major,
        version_minor=minor,
        version_patch=patch
    )


@router.get("/git-status", response_model=GitStatusResponse)
async def check_git_status() -> GitStatusResponse:
    """
    检查本机 Git 安装状态
    
    此接口无需认证，用于前端快速检测是否可以使用插件安装功能
    """
    service = get_git_mirror_service()
    result = service.check_git_installed()

    return GitStatusResponse(**result)


@router.get("/mirrors", response_model=AvailableMirrorsResponse)
async def get_available_mirrors(
    authorization: Optional[str] = Header(None)
) -> AvailableMirrorsResponse:
    """
    获取所有可用的镜像源配置
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    service = get_git_mirror_service()
    config = service.get_mirror_config()

    all_mirrors = config.get_all_mirrors()
    mirrors = [
        MirrorConfigResponse(
            id=m["id"],
            name=m["name"],
            raw_prefix=m["raw_prefix"],
            clone_prefix=m["clone_prefix"],
            enabled=m["enabled"],
            priority=m["priority"]
        )
        for m in all_mirrors
    ]

    return AvailableMirrorsResponse(
        mirrors=mirrors,
        default_priority=config.get_default_priority_list()
    )


@router.post("/mirrors", response_model=MirrorConfigResponse)
async def add_mirror(
    request: AddMirrorRequest,
    authorization: Optional[str] = Header(None)
) -> MirrorConfigResponse:
    """
    添加新的镜像源
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    try:
        service = get_git_mirror_service()
        config = service.get_mirror_config()

        mirror = config.add_mirror(
            mirror_id=request.id,
            name=request.name,
            raw_prefix=request.raw_prefix,
            clone_prefix=request.clone_prefix,
            enabled=request.enabled,
            priority=request.priority
        )

        return MirrorConfigResponse(
            id=mirror["id"],
            name=mirror["name"],
            raw_prefix=mirror["raw_prefix"],
            clone_prefix=mirror["clone_prefix"],
            enabled=mirror["enabled"],
            priority=mirror["priority"]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"添加镜像源失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/mirrors/{mirror_id}", response_model=MirrorConfigResponse)
async def update_mirror(
    mirror_id: str,
    request: UpdateMirrorRequest,
    authorization: Optional[str] = Header(None)
) -> MirrorConfigResponse:
    """
    更新镜像源配置
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    try:
        service = get_git_mirror_service()
        config = service.get_mirror_config()

        mirror = config.update_mirror(
            mirror_id=mirror_id,
            name=request.name,
            raw_prefix=request.raw_prefix,
            clone_prefix=request.clone_prefix,
            enabled=request.enabled,
            priority=request.priority
        )

        if not mirror:
            raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")

        return MirrorConfigResponse(
            id=mirror["id"],
            name=mirror["name"],
            raw_prefix=mirror["raw_prefix"],
            clone_prefix=mirror["clone_prefix"],
            enabled=mirror["enabled"],
            priority=mirror["priority"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新镜像源失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.delete("/mirrors/{mirror_id}")
async def delete_mirror(
    mirror_id: str,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    删除镜像源
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    service = get_git_mirror_service()
    config = service.get_mirror_config()

    success = config.delete_mirror(mirror_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")

    return {
        "success": True,
        "message": f"已删除镜像源: {mirror_id}"
    }


@router.post("/fetch-raw", response_model=FetchRawFileResponse)
async def fetch_raw_file(
    request: FetchRawFileRequest,
    authorization: Optional[str] = Header(None)
) -> FetchRawFileResponse:
    """
    获取 GitHub 仓库的 Raw 文件内容
    
    支持多镜像源自动切换和错误重试
    
    注意：此接口可公开访问，用于获取插件仓库等公开资源
    """
    # Token 验证（可选，用于日志记录）
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    is_authenticated = token and token_manager.verify_token(token)

    # 对于公开仓库的访问，不强制要求认证
    # 只在日志中记录是否认证
    logger.info(
        f"收到获取 Raw 文件请求 (认证: {is_authenticated}): "
        f"{request.owner}/{request.repo}/{request.branch}/{request.file_path}"
    )

    # 发送开始加载进度
    await update_progress(
        stage="loading",
        progress=10,
        message=f"正在获取插件列表: {request.file_path}",
        total_plugins=0,
        loaded_plugins=0
    )

    try:
        service = get_git_mirror_service()

        # git_mirror_service 会自动推送 30%-70% 的详细镜像源尝试进度
        result = await service.fetch_raw_file(
            owner=request.owner,
            repo=request.repo,
            branch=request.branch,
            file_path=request.file_path,
            mirror_id=request.mirror_id,
            custom_url=request.custom_url
        )

        if result.get("success"):
            # 更新进度：成功获取
            await update_progress(
                stage="loading",
                progress=70,
                message="正在解析插件数据...",
                total_plugins=0,
                loaded_plugins=0
            )

            # 尝试解析插件数量
            try:
                import json
                data = json.loads(result.get("data", "[]"))
                total = len(data) if isinstance(data, list) else 0

                # 发送成功状态
                await update_progress(
                    stage="success",
                    progress=100,
                    message=f"成功加载 {total} 个插件",
                    total_plugins=total,
                    loaded_plugins=total
                )
            except Exception:
                # 如果解析失败，仍然发送成功状态
                await update_progress(
                    stage="success",
                    progress=100,
                    message="加载完成",
                    total_plugins=0,
                    loaded_plugins=0
                )

        return FetchRawFileResponse(**result)

    except Exception as e:
        logger.error(f"获取 Raw 文件失败: {e}")

        # 发送错误进度
        await update_progress(
            stage="error",
            progress=0,
            message="加载失败",
            error=str(e),
            total_plugins=0,
            loaded_plugins=0
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/clone", response_model=CloneRepositoryResponse)
async def clone_repository(
    request: CloneRepositoryRequest,
    authorization: Optional[str] = Header(None)
) -> CloneRepositoryResponse:
    """
    克隆 GitHub 仓库到本地
    
    支持多镜像源自动切换和错误重试
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(
        f"收到克隆仓库请求: {request.owner}/{request.repo} -> {request.target_path}"
    )

    try:
        # TODO: 验证 target_path 的安全性，防止路径遍历攻击
        # TODO: 确定实际的插件目录基路径
        base_plugin_path = Path("./plugins")  # 临时路径
        target_path = base_plugin_path / request.target_path

        service = get_git_mirror_service()
        result = await service.clone_repository(
            owner=request.owner,
            repo=request.repo,
            target_path=target_path,
            branch=request.branch,
            mirror_id=request.mirror_id,
            custom_url=request.custom_url,
            depth=request.depth
        )

        return CloneRepositoryResponse(**result)

    except Exception as e:
        logger.error(f"克隆仓库失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/install")
async def install_plugin(
    request: InstallPluginRequest,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    安装插件
    
    从 Git 仓库克隆插件到本地插件目录
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到安装插件请求: {request.plugin_id}")

    try:
        # 推送进度：开始安装
        await update_progress(
            stage="loading",
            progress=5,
            message=f"开始安装插件: {request.plugin_id}",
            operation="install",
            plugin_id=request.plugin_id
        )

        # 1. 解析仓库 URL
        # repository_url 格式: https://github.com/owner/repo
        repo_url = request.repository_url.rstrip('/')
        if repo_url.endswith('.git'):
            repo_url = repo_url[:-4]

        parts = repo_url.split('/')
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="无效的仓库 URL")

        owner = parts[-2]
        repo = parts[-1]

        await update_progress(
            stage="loading",
            progress=10,
            message=f"解析仓库信息: {owner}/{repo}",
            operation="install",
            plugin_id=request.plugin_id
        )

        # 2. 确定插件安装路径
        plugins_dir = Path("plugins")
        plugins_dir.mkdir(exist_ok=True)

        target_path = plugins_dir / request.plugin_id

        # 检查插件是否已安装
        if target_path.exists():
            await update_progress(
                stage="error",
                progress=0,
                message="插件已存在",
                operation="install",
                plugin_id=request.plugin_id,
                error="插件已安装，请先卸载"
            )
            raise HTTPException(status_code=400, detail="插件已安装")

        await update_progress(
            stage="loading",
            progress=15,
            message=f"准备克隆到: {target_path}",
            operation="install",
            plugin_id=request.plugin_id
        )

        # 3. 克隆仓库（这里会自动推送 20%-80% 的进度）
        service = get_git_mirror_service()

        # 如果是 GitHub 仓库，使用镜像源
        if 'github.com' in repo_url:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=target_path,
                branch=request.branch,
                mirror_id=request.mirror_id,
                depth=1  # 浅克隆，节省时间和空间
            )
        else:
            # 自定义仓库，直接使用 URL
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=target_path,
                branch=request.branch,
                custom_url=repo_url,
                depth=1
            )

        if not result.get("success"):
            error_msg = result.get("error", "克隆失败")
            await update_progress(
                stage="error",
                progress=0,
                message="克隆仓库失败",
                operation="install",
                plugin_id=request.plugin_id,
                error=error_msg
            )
            raise HTTPException(status_code=500, detail=error_msg)

        # 4. 验证插件完整性
        await update_progress(
            stage="loading",
            progress=85,
            message="验证插件文件...",
            operation="install",
            plugin_id=request.plugin_id
        )

        manifest_path = target_path / "_manifest.json"
        if not manifest_path.exists():
            # 清理失败的安装
            import shutil
            shutil.rmtree(target_path, ignore_errors=True)

            await update_progress(
                stage="error",
                progress=0,
                message="插件缺少 _manifest.json",
                operation="install",
                plugin_id=request.plugin_id,
                error="无效的插件格式"
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        # 5. 读取并验证 manifest
        await update_progress(
            stage="loading",
            progress=90,
            message="读取插件配置...",
            operation="install",
            plugin_id=request.plugin_id
        )

        try:
            import json as json_module
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json_module.load(f)

            # 基本验证
            required_fields = ['manifest_version', 'name', 'version', 'author']
            for field in required_fields:
                if field not in manifest:
                    raise ValueError(f"缺少必需字段: {field}")

        except Exception as e:
            # 清理失败的安装
            import shutil
            shutil.rmtree(target_path, ignore_errors=True)

            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="install",
                plugin_id=request.plugin_id,
                error=str(e)
            )
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e

        # 5.5 安装依赖
        await update_progress(
            stage="loading",
            progress=95,
            message="正在检查并安装依赖...",
            operation="install",
            plugin_id=request.plugin_id
        )

        try:
            # 自动检测并安装 requirements.txt 或 pyproject.toml
            await plugin_dependency_manager.install_auto_from_directory(str(target_path))
        except Exception as e:
            logger.error(f"依赖安装过程出错: {e}")
            # 不中断安装流程，但记录日志

        # 6. 安装成功
        await update_progress(
            stage="success",
            progress=100,
            message=f"成功安装插件: {manifest['name']} v{manifest['version']}",
            operation="install",
            plugin_id=request.plugin_id
        )

        return {
            "success": True,
            "message": "插件安装成功",
            "plugin_id": request.plugin_id,
            "plugin_name": manifest['name'],
            "version": manifest['version'],
            "path": str(target_path)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装插件失败: {e}", exc_info=True)

        await update_progress(
            stage="error",
            progress=0,
            message="安装失败",
            operation="install",
            plugin_id=request.plugin_id,
            error=str(e)
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/uninstall")
async def uninstall_plugin(
    request: UninstallPluginRequest,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    卸载插件
    
    删除插件目录及其所有文件
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到卸载插件请求: {request.plugin_id}")

    try:
        # 推送进度：开始卸载
        await update_progress(
            stage="loading",
            progress=10,
            message=f"开始卸载插件: {request.plugin_id}",
            operation="uninstall",
            plugin_id=request.plugin_id
        )

        # 1. 检查插件是否存在
        plugins_dir = Path("plugins")
        plugin_path = plugins_dir / request.plugin_id

        if not plugin_path.exists():
            await update_progress(
                stage="error",
                progress=0,
                message="插件不存在",
                operation="uninstall",
                plugin_id=request.plugin_id,
                error="插件未安装或已被删除"
            )
            raise HTTPException(status_code=404, detail="插件未安装")

        await update_progress(
            stage="loading",
            progress=30,
            message=f"正在删除插件文件: {plugin_path}",
            operation="uninstall",
            plugin_id=request.plugin_id
        )

        # 2. 读取插件信息（用于日志）
        manifest_path = plugin_path / "_manifest.json"
        plugin_name = request.plugin_id

        if manifest_path.exists():
            try:
                import json as json_module
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json_module.load(f)
                plugin_name = manifest.get("name", request.plugin_id)
            except Exception:
                pass  # 如果读取失败，使用插件 ID 作为名称

        await update_progress(
            stage="loading",
            progress=50,
            message=f"正在删除 {plugin_name}...",
            operation="uninstall",
            plugin_id=request.plugin_id
        )

        # 3. 删除插件目录
        import shutil
        import stat

        def remove_readonly(func, path, _):
            """清除只读属性并删除文件"""
            import os
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(plugin_path, onerror=remove_readonly)

        logger.info(f"成功卸载插件: {request.plugin_id} ({plugin_name})")

        # 4. 推送成功状态
        await update_progress(
            stage="success",
            progress=100,
            message=f"成功卸载插件: {plugin_name}",
            operation="uninstall",
            plugin_id=request.plugin_id
        )

        return {
            "success": True,
            "message": "插件卸载成功",
            "plugin_id": request.plugin_id,
            "plugin_name": plugin_name
        }

    except HTTPException:
        raise
    except PermissionError as e:
        logger.error(f"卸载插件失败（权限错误）: {e}")

        await update_progress(
            stage="error",
            progress=0,
            message="卸载失败",
            operation="uninstall",
            plugin_id=request.plugin_id,
            error="权限不足，无法删除插件文件"
        )

        raise HTTPException(status_code=500, detail="权限不足，无法删除插件文件") from e
    except Exception as e:
        logger.error(f"卸载插件失败: {e}", exc_info=True)

        await update_progress(
            stage="error",
            progress=0,
            message="卸载失败",
            operation="uninstall",
            plugin_id=request.plugin_id,
            error=str(e)
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/update")
async def update_plugin(
    request: UpdatePluginRequest,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    更新插件
    
    删除旧版本，重新克隆新版本
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到更新插件请求: {request.plugin_id}")

    try:
        # 推送进度：开始更新
        await update_progress(
            stage="loading",
            progress=5,
            message=f"开始更新插件: {request.plugin_id}",
            operation="update",
            plugin_id=request.plugin_id
        )

        # 1. 检查插件是否已安装
        plugins_dir = Path("plugins")
        plugin_path = plugins_dir / request.plugin_id

        if not plugin_path.exists():
            await update_progress(
                stage="error",
                progress=0,
                message="插件不存在",
                operation="update",
                plugin_id=request.plugin_id,
                error="插件未安装，请先安装"
            )
            raise HTTPException(status_code=404, detail="插件未安装")

        # 2. 读取旧版本信息
        manifest_path = plugin_path / "_manifest.json"
        old_version = "unknown"

        if manifest_path.exists():
            try:
                import json as json_module
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json_module.load(f)
                old_version = manifest.get("version", "unknown")
                # plugin_name = manifest.get("name", request.plugin_id)
            except Exception:
                pass

        await update_progress(
            stage="loading",
            progress=10,
            message=f"当前版本: {old_version}，准备更新...",
            operation="update",
            plugin_id=request.plugin_id
        )

        # 3. 删除旧版本
        await update_progress(
            stage="loading",
            progress=20,
            message="正在删除旧版本...",
            operation="update",
            plugin_id=request.plugin_id
        )

        import shutil
        import stat

        def remove_readonly(func, path, _):
            """清除只读属性并删除文件"""
            import os
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(plugin_path, onerror=remove_readonly)

        logger.info(f"已删除旧版本: {request.plugin_id} v{old_version}")

        # 4. 解析仓库 URL
        await update_progress(
            stage="loading",
            progress=30,
            message="正在准备下载新版本...",
            operation="update",
            plugin_id=request.plugin_id
        )

        repo_url = request.repository_url.rstrip('/')
        if repo_url.endswith('.git'):
            repo_url = repo_url[:-4]

        parts = repo_url.split('/')
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="无效的仓库 URL")

        owner = parts[-2]
        repo = parts[-1]

        # 5. 克隆新版本（这里会推送 35%-85% 的进度）
        service = get_git_mirror_service()

        if 'github.com' in repo_url:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=plugin_path,
                branch=request.branch,
                mirror_id=request.mirror_id,
                depth=1
            )
        else:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=plugin_path,
                branch=request.branch,
                custom_url=repo_url,
                depth=1
            )

        if not result.get("success"):
            error_msg = result.get("error", "克隆失败")
            await update_progress(
                stage="error",
                progress=0,
                message="下载新版本失败",
                operation="update",
                plugin_id=request.plugin_id,
                error=error_msg
            )
            raise HTTPException(status_code=500, detail=error_msg)

        # 6. 验证新版本
        await update_progress(
            stage="loading",
            progress=90,
            message="验证新版本...",
            operation="update",
            plugin_id=request.plugin_id
        )

        new_manifest_path = plugin_path / "_manifest.json"
        if not new_manifest_path.exists():
            # 清理失败的更新
            def remove_readonly(func, path, _):
                """清除只读属性并删除文件"""
                import os
                os.chmod(path, stat.S_IWRITE)
                func(path)

            shutil.rmtree(plugin_path, onerror=remove_readonly)

            await update_progress(
                stage="error",
                progress=0,
                message="新版本缺少 _manifest.json",
                operation="update",
                plugin_id=request.plugin_id,
                error="无效的插件格式"
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        # 7. 读取新版本信息
        try:
            with open(new_manifest_path, 'r', encoding='utf-8') as f:
                new_manifest = json_module.load(f)

            new_version = new_manifest.get("version", "unknown")
            new_name = new_manifest.get("name", request.plugin_id)

            # 7.5 安装依赖
            await update_progress(
                stage="loading",
                progress=95,
                message="正在更新依赖...",
                operation="update",
                plugin_id=request.plugin_id
            )
            try:
                await plugin_dependency_manager.install_auto_from_directory(str(plugin_path))
            except Exception as e:
                logger.error(f"依赖更新过程出错: {e}")

            logger.info(f"成功更新插件: {request.plugin_id} {old_version} → {new_version}")

            # 8. 推送成功状态
            await update_progress(
                stage="success",
                progress=100,
                message=f"成功更新 {new_name}: {old_version} → {new_version}",
                operation="update",
                plugin_id=request.plugin_id
            )

            return {
                "success": True,
                "message": "插件更新成功",
                "plugin_id": request.plugin_id,
                "plugin_name": new_name,
                "old_version": old_version,
                "new_version": new_version
            }

        except Exception as e:
            # 清理失败的更新
            shutil.rmtree(plugin_path, ignore_errors=True)

            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="update",
                plugin_id=request.plugin_id,
                error=str(e)
            )
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件失败: {e}", exc_info=True)

        await update_progress(
            stage="error",
            progress=0,
            message="更新失败",
            operation="update",
            plugin_id=request.plugin_id,
            error=str(e)
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/installed")
async def get_installed_plugins(
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    获取已安装的插件列表
    
    扫描 plugins 目录，返回所有已安装插件的 ID 和基本信息
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info("收到获取已安装插件列表请求")

    try:
        plugins_dir = Path("plugins")

        # 如果插件目录不存在，返回空列表
        if not plugins_dir.exists():
            logger.info("插件目录不存在，创建目录")
            plugins_dir.mkdir(exist_ok=True)
            return {
                "success": True,
                "plugins": []
            }

        installed_plugins = []

        # 遍历插件目录
        for plugin_path in plugins_dir.iterdir():
            # 只处理目录
            if not plugin_path.is_dir():
                continue

            # 目录名即为插件 ID
            plugin_id = plugin_path.name

            # 跳过隐藏目录和特殊目录
            if plugin_id.startswith('.') or plugin_id.startswith('__'):
                continue

            # 读取 _manifest.json
            manifest_path = plugin_path / "_manifest.json"

            if not manifest_path.exists():
                logger.warning(f"插件 {plugin_id} 缺少 _manifest.json，跳过")
                continue

            try:
                import json as json_module
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json_module.load(f)

                # 基本验证
                if 'name' not in manifest or 'version' not in manifest:
                    logger.warning(f"插件 {plugin_id} 的 _manifest.json 格式无效，跳过")
                    continue

                # 读取 config.toml 获取启用状态
                config_path = plugin_path / "config.toml"
                is_enabled = False
                try:
                    if config_path.exists():
                        import toml
                        config = toml.load(config_path)
                        is_enabled = config.get("plugin", {}).get("enabled", False)
                except Exception:  # pylint: disable=broad-except
                    pass
                
                is_running = plugin_manager.get_plugin_instance(plugin_id) is not None

                # 添加到已安装列表（返回完整的 manifest 信息）
                installed_plugins.append({
                    "id": plugin_id,
                    "manifest": manifest,  # 返回完整的 manifest 对象
                    "path": str(plugin_path.absolute()),
                    "enabled": is_enabled,
                    "running": is_running
                })

            except json.JSONDecodeError as e:
                logger.warning(f"插件 {plugin_id} 的 _manifest.json 解析失败: {e}")
                continue
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f"读取插件 {plugin_id} 信息时出错: {e}")
                continue

        logger.info(f"找到 {len(installed_plugins)} 个已安装插件")

        return {
            "success": True,
            "plugins": installed_plugins,
            "total": len(installed_plugins)
        }

    except Exception as e:
        logger.error(f"获取已安装插件列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/{plugin_id}/config", response_model=PluginConfigResponse)
async def get_plugin_config(
    plugin_id: str,
    authorization: Optional[str] = Header(None)
) -> PluginConfigResponse:
    """
    获取插件配置表单定义和当前值
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    plugin = plugin_manager.get_plugin_instance(plugin_id)

    schema = None
    current_config = None

    # 1. 尝试从已加载的插件获取
    if plugin:
        schema = plugin.config_schema
        current_config = plugin.config
    else:
        # 2. 尝试从已注册的类中加载（针对已禁用或加载失败的插件）
        logger.info(f"插件 {plugin_id} 未运行，尝试静态加载配置")

        plugin_class = plugin_manager.plugin_classes.get(plugin_id)
        plugin_path = plugin_manager.plugin_paths.get(plugin_id)

        # 如果没有路径信息，尝试推断标准路径
        if not plugin_path:
            possible_path = Path("plugins") / plugin_id
            if possible_path.exists():
                plugin_path = str(possible_path)

        if plugin_class and plugin_path:
            try:
                # 临时实例化，仅用于获取 schema 和 config
                # 注意：这里可能会触发 __init__ 中的逻辑
                temp_plugin = plugin_class(plugin_dir=plugin_path)
                schema = temp_plugin.config_schema
                current_config = temp_plugin.config
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f"临时实例化插件 {plugin_id} 失败: {e}")

        # 3. 如果还是没有 schema，尝试直接读取 config.toml 并推断
        if schema is None and plugin_path:
            config_path = Path(plugin_path) / "config.toml"
            if config_path.exists():
                try:

                    current_config = toml.load(config_path)
                    # 根据 config 生成简单的 schema
                    schema = {}
                    for section, values in current_config.items():
                        if isinstance(values, dict):
                            schema[section] = {}
                            for key, value in values.items():
                                # 构造一个伪造的 ConfigField
                                schema[section][key] = ConfigField(
                                    type=type(value),
                                    default=value,
                                    description=f"自动推断字段: {key}",
                                    required=False
                                )
                    logger.info(f"已从 config.toml 推断出插件 {plugin_id} 的配置结构")
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f"直接读取配置文件失败: {e}")

    if schema is None:
        raise HTTPException(status_code=404, detail=f"无法加载插件 {plugin_id} 的配置：插件未运行且无法读取配置文件")

    try:
        items = []
        for section, fields in schema.items():
            if not isinstance(fields, dict):
                continue

            for field_name, field_info in fields.items():
                # 获取当前值
                # 注意：current_config 结构是 {section: {key: value}}
                section_config = current_config.get(section, {}) if current_config else {}
                value = section_config.get(field_name, field_info.default)

                # 确定前端控件类型
                field_type = "input"
                if field_info.choices:
                    field_type = "select"
                elif field_info.type is bool:
                    field_type = "switch"
                elif field_info.type in (int, float):
                    field_type = "number"
                elif field_info.type is list:
                    field_type = "array"

                items.append(PluginConfigItem(
                    key=f"{section}.{field_name}",
                    label=field_name,
                    value=value,
                    description=field_info.description,
                    section=section,
                    required=field_info.required,
                    default=field_info.default,
                    type=field_type,
                    options=field_info.choices
                ))

        return PluginConfigResponse(success=True, data=items)

    except Exception as e:
        logger.error(f"获取插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}") from e


@router.post("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    request: UpdatePluginConfigRequest,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    更新插件配置
    """
    # Token 验证
    token = authorization.replace("Bearer ", "") if authorization else None
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    plugin = plugin_manager.get_plugin_instance(plugin_id)

    # 准备配置更新逻辑
    config_file_name = "config.toml" # 默认值
    plugin_dir = None
    current_config = {}

    if plugin:
        plugin_dir = plugin.plugin_dir
        config_file_name = plugin.config_file_name or "config.toml"
        current_config = plugin.config
    else:
        # 插件未加载，尝试查找路径
        plugin_path = plugin_manager.plugin_paths.get(plugin_id)
        if not plugin_path:
            possible_path = Path("plugins") / plugin_id
            if possible_path.exists():
                plugin_path = str(possible_path)

        if not plugin_path:
             raise HTTPException(status_code=404, detail=f"找不到插件 {plugin_id} 的目录")

        plugin_dir = plugin_path

        # 尝试读取现有配置以进行合并
        try:

            config_path = os.path.join(plugin_dir, config_file_name)
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    current_config = toml.load(f)
        except Exception:  # pylint: disable=broad-except
            pass

    try:


        # 1. 解析扁平化的配置键值对到嵌套字典
        # 前端传来的格式: {"section.key": value}
        # 目标格式: {"section": {"key": value}}
        new_config = current_config.copy()

        for key, value in request.configs.items():
            if "." in key:
                section, field_name = key.split(".", 1)
                if section not in new_config:
                    new_config[section] = {}

                # 类型转换（如果需要）
                # 这里假设前端传来的类型已经是正确的，或者在保存时会自动处理
                new_config[section][field_name] = value

        # 2. 保存到文件
        config_path = os.path.join(plugin_dir, config_file_name)

        # 如果插件已加载，使用插件的方法保存
        if plugin:
            plugin._save_config_to_file(new_config, config_path)
            # 更新内存中的配置
            plugin.config = new_config
        else:
            # 否则直接使用 toml 库保存
            with open(config_path, "w", encoding="utf-8") as f:
                toml.dump(new_config, f)

        return {"success": True, "message": "配置已更新"}

    except Exception as e:
        logger.error(f"更新插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}") from e

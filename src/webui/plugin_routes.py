from fastapi import APIRouter, HTTPException, Header, Cookie
from collections.abc import Mapping
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, get_origin
from pathlib import Path
import json
from src.common.logger import get_logger
from src.common.toml_utils import save_toml_with_format
from src.config.config import MMC_VERSION
from src.plugin_system.base.config_types import ConfigField
from .git_mirror_service import get_git_mirror_service, set_update_progress_callback
from .token_manager import get_token_manager
from .plugin_progress_ws import update_progress

logger = get_logger("webui.plugin_routes")

# 创建路由器
router = APIRouter(prefix="/plugins", tags=["插件管理"])

# 设置进度更新回调
set_update_progress_callback(update_progress)


def get_token_from_cookie_or_header(
    maibot_session: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Optional[str]:
    """从 Cookie 或 Header 获取 token"""
    # 优先从 Cookie 获取
    if maibot_session:
        return maibot_session
    # 其次从 Header 获取
    if authorization and authorization.startswith("Bearer "):
        return authorization.replace("Bearer ", "")
    return None


def validate_safe_path(user_path: str, base_path: Path) -> Path:
    """
    验证用户提供的路径是否安全，防止路径遍历攻击

    Args:
        user_path: 用户输入的路径（相对路径）
        base_path: 允许的基础目录

    Returns:
        安全的绝对路径

    Raises:
        HTTPException: 如果检测到路径遍历攻击
    """
    # 规范化基础路径
    base_resolved = base_path.resolve()

    # 检查用户路径是否包含可疑字符
    # 禁止: .., 绝对路径开头, 空字节等
    if any(pattern in user_path for pattern in ["..", "\x00"]):
        logger.warning(f"检测到可疑路径: {user_path}")
        raise HTTPException(status_code=400, detail="路径包含非法字符")

    # 检查是否为绝对路径（Windows 和 Unix）
    if user_path.startswith("/") or user_path.startswith("\\") or (len(user_path) > 1 and user_path[1] == ":"):
        logger.warning(f"检测到绝对路径: {user_path}")
        raise HTTPException(status_code=400, detail="不允许使用绝对路径")

    # 构建目标路径并解析
    target_path = (base_path / user_path).resolve()

    # 验证解析后的路径仍在基础目录内
    try:
        target_path.relative_to(base_resolved)
    except ValueError as e:
        logger.warning(f"路径遍历攻击检测: {user_path} -> {target_path}")
        raise HTTPException(status_code=400, detail="路径超出允许范围") from e

    return target_path


def validate_plugin_id(plugin_id: str) -> str:
    """
    验证插件 ID 格式是否安全

    Args:
        plugin_id: 插件 ID (支持 author.name 格式，允许中文)

    Returns:
        验证通过的插件 ID

    Raises:
        HTTPException: 如果插件 ID 格式不安全
    """
    # 禁止空字符串
    if not plugin_id or not plugin_id.strip():
        logger.warning("非法插件 ID: 空字符串")
        raise HTTPException(status_code=400, detail="插件 ID 不能为空")

    # 禁止危险字符: 路径分隔符、空字节、控制字符等
    dangerous_patterns = ["/", "\\", "\x00", "..", "\n", "\r", "\t"]
    for pattern in dangerous_patterns:
        if pattern in plugin_id:
            logger.warning(f"非法插件 ID 格式: {plugin_id} (包含危险字符)")
            raise HTTPException(status_code=400, detail="插件 ID 包含非法字符")

    # 禁止以点开头或结尾（防止隐藏文件和路径问题）
    if plugin_id.startswith(".") or plugin_id.endswith("."):
        logger.warning(f"非法插件 ID: {plugin_id}")
        raise HTTPException(status_code=400, detail="插件 ID 不能以点开头或结尾")

    # 禁止特殊名称
    if plugin_id in (".", ".."):
        logger.warning(f"非法插件 ID: {plugin_id}")
        raise HTTPException(status_code=400, detail="插件 ID 不能为特殊目录名")

    return plugin_id


def parse_version(version_str: str) -> tuple[int, int, int]:
    """
    解析版本号字符串

    支持格式:
    - 0.11.2 -> (0, 11, 2)
    - 0.11.2.snapshot.2 -> (0, 11, 2)

    Returns:
        (major, minor, patch) 三元组
    """
    # 移除 snapshot、dev、alpha、beta 等后缀（支持 - 和 . 分隔符）
    import re

    # 匹配 -snapshot.X, .snapshot, -dev, .dev, -alpha, .alpha, -beta, .beta 等后缀
    base_version = re.split(r"[-.](?:snapshot|dev|alpha|beta|rc)", version_str, flags=re.IGNORECASE)[0]

    parts = base_version.split(".")
    if len(parts) < 3:
        # 补齐到 3 位
        parts.extend(["0"] * (3 - len(parts)))

    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])
        return (major, minor, patch)
    except (ValueError, IndexError):
        logger.warning(f"无法解析版本号: {version_str}，返回默认值 (0, 0, 0)")
        return (0, 0, 0)


# ============ 工具函数（避免在请求内重复定义） ============


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """深度合并两个字典，src 的值会覆盖或合并到 dst 中。"""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def normalize_dotted_keys(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    将形如 {'a.b': 1} 的键展开为嵌套结构 {'a': {'b': 1}}。
    若遇到中间节点已存在且非字典，记录日志并覆盖为字典。
    """
    result: Dict[str, Any] = {}
    dotted_items = []

    # 先处理非点号键，避免后续展开覆盖已有结构
    for k, v in obj.items():
        if "." in k:
            dotted_items.append((k, v))
        else:
            result[k] = normalize_dotted_keys(v) if isinstance(v, dict) else v

    # 再处理点号键
    for dotted_key, v in dotted_items:
        value = normalize_dotted_keys(v) if isinstance(v, dict) else v
        parts = dotted_key.split(".")
        if "" in parts:
            logger.warning(f"键路径包含空段: '{dotted_key}'")
            parts = [p for p in parts if p]
        if not parts:
            logger.warning(f"忽略空键路径: '{dotted_key}'")
            continue
        current = result
        # 中间层
        for idx, part in enumerate(parts[:-1]):
            if part in current and not isinstance(current[part], dict):
                path_ctx = ".".join(parts[: idx + 1])
                logger.warning(f"键冲突：{part} 已存在且非字典，覆盖为字典以展开 {dotted_key} (路径 {path_ctx})")
                current[part] = {}
            current = current.setdefault(part, {})
        # 最后一层
        last_part = parts[-1]
        if last_part in current and isinstance(current[last_part], dict) and isinstance(value, dict):
            _deep_merge(current[last_part], value)
        else:
            current[last_part] = value

    return result


def coerce_types(schema_part: Dict[str, Any], config_part: Dict[str, Any]) -> None:
    """
    根据 schema 将配置中的类型纠正（目前只纠正 list-from-str）。
    """

    def _is_list_type(tp: Any) -> bool:
        origin = get_origin(tp)
        return tp is list or origin is list

    for key, schema_val in schema_part.items():
        if key not in config_part:
            continue
        value = config_part[key]
        if isinstance(schema_val, ConfigField):
            if _is_list_type(schema_val.type) and isinstance(value, str):
                config_part[key] = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(schema_val, dict) and isinstance(value, dict):
            coerce_types(schema_val, value)


def find_plugin_instance(plugin_id: str) -> Optional[Any]:
    """
    按 plugin_id 或 plugin_name 查找已加载的插件实例。
    局部导入 plugin_manager 以规避循环依赖。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    for loaded_plugin_name in plugin_manager.list_loaded_plugins():
        instance = plugin_manager.get_plugin_instance(loaded_plugin_name)
        if instance and (instance.plugin_name == plugin_id or instance.get_manifest_info("id", "") == plugin_id):
            return instance
    return None


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


# ============ API 路由 ============


@router.get("/version", response_model=VersionResponse)
async def get_maimai_version() -> VersionResponse:
    """
    获取麦麦版本信息

    此接口无需认证，用于前端检查插件兼容性
    """
    major, minor, patch = parse_version(MMC_VERSION)

    return VersionResponse(version=MMC_VERSION, version_major=major, version_minor=minor, version_patch=patch)


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
    maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> AvailableMirrorsResponse:
    """
    获取所有可用的镜像源配置
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
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
            priority=m["priority"],
        )
        for m in all_mirrors
    ]

    return AvailableMirrorsResponse(mirrors=mirrors, default_priority=config.get_default_priority_list())


@router.post("/mirrors", response_model=MirrorConfigResponse)
async def add_mirror(
    request: AddMirrorRequest, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> MirrorConfigResponse:
    """
    添加新的镜像源
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
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
            priority=request.priority,
        )

        return MirrorConfigResponse(
            id=mirror["id"],
            name=mirror["name"],
            raw_prefix=mirror["raw_prefix"],
            clone_prefix=mirror["clone_prefix"],
            enabled=mirror["enabled"],
            priority=mirror["priority"],
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
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> MirrorConfigResponse:
    """
    更新镜像源配置
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
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
            priority=request.priority,
        )

        if not mirror:
            raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")

        return MirrorConfigResponse(
            id=mirror["id"],
            name=mirror["name"],
            raw_prefix=mirror["raw_prefix"],
            clone_prefix=mirror["clone_prefix"],
            enabled=mirror["enabled"],
            priority=mirror["priority"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新镜像源失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.delete("/mirrors/{mirror_id}")
async def delete_mirror(
    mirror_id: str, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    删除镜像源
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    service = get_git_mirror_service()
    config = service.get_mirror_config()

    success = config.delete_mirror(mirror_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"未找到镜像源: {mirror_id}")

    return {"success": True, "message": f"已删除镜像源: {mirror_id}"}


@router.post("/fetch-raw", response_model=FetchRawFileResponse)
async def fetch_raw_file(
    request: FetchRawFileRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> FetchRawFileResponse:
    """
    获取 GitHub 仓库的 Raw 文件内容

    支持多镜像源自动切换和错误重试

    需要认证才能访问，防止被滥用作为 SSRF 跳板
    """
    # Token 验证（强制）
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到获取 Raw 文件请求: {request.owner}/{request.repo}/{request.branch}/{request.file_path}")

    # 发送开始加载进度
    await update_progress(
        stage="loading",
        progress=10,
        message=f"正在获取插件列表: {request.file_path}",
        total_plugins=0,
        loaded_plugins=0,
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
            custom_url=request.custom_url,
        )

        if result.get("success"):
            # 更新进度：成功获取
            await update_progress(
                stage="loading", progress=70, message="正在解析插件数据...", total_plugins=0, loaded_plugins=0
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
                    loaded_plugins=total,
                )
            except Exception:
                # 如果解析失败，仍然发送成功状态
                await update_progress(
                    stage="success", progress=100, message="加载完成", total_plugins=0, loaded_plugins=0
                )

        return FetchRawFileResponse(**result)

    except Exception as e:
        logger.error(f"获取 Raw 文件失败: {e}")

        # 发送错误进度
        await update_progress(
            stage="error", progress=0, message="加载失败", error=str(e), total_plugins=0, loaded_plugins=0
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/clone", response_model=CloneRepositoryResponse)
async def clone_repository(
    request: CloneRepositoryRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> CloneRepositoryResponse:
    """
    克隆 GitHub 仓库到本地

    支持多镜像源自动切换和错误重试
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到克隆仓库请求: {request.owner}/{request.repo} -> {request.target_path}")

    try:
        # 验证 target_path 的安全性，防止路径遍历攻击
        base_plugin_path = Path("./plugins").resolve()
        base_plugin_path.mkdir(exist_ok=True)
        target_path = validate_safe_path(request.target_path, base_plugin_path)

        service = get_git_mirror_service()
        result = await service.clone_repository(
            owner=request.owner,
            repo=request.repo,
            target_path=target_path,
            branch=request.branch,
            mirror_id=request.mirror_id,
            custom_url=request.custom_url,
            depth=request.depth,
        )

        return CloneRepositoryResponse(**result)

    except Exception as e:
        logger.error(f"克隆仓库失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/install")
async def install_plugin(
    request: InstallPluginRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    安装插件

    从 Git 仓库克隆插件到本地插件目录
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到安装插件请求: {request.plugin_id}")

    try:
        # 验证插件 ID 格式安全性
        plugin_id = validate_plugin_id(request.plugin_id)

        # 推送进度：开始安装
        await update_progress(
            stage="loading",
            progress=5,
            message=f"开始安装插件: {plugin_id}",
            operation="install",
            plugin_id=plugin_id,
        )

        # 1. 解析仓库 URL
        # repository_url 格式: https://github.com/owner/repo
        repo_url = request.repository_url.rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]

        parts = repo_url.split("/")
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="无效的仓库 URL")

        owner = parts[-2]
        repo = parts[-1]

        await update_progress(
            stage="loading",
            progress=10,
            message=f"解析仓库信息: {owner}/{repo}",
            operation="install",
            plugin_id=plugin_id,
        )

        # 2. 确定插件安装路径
        plugins_dir = Path("plugins").resolve()
        plugins_dir.mkdir(exist_ok=True)

        # 将插件 ID 中的点替换为下划线作为文件夹名称（避免文件系统问题）
        # 例如: SengokuCola.Mute-Plugin -> SengokuCola_Mute-Plugin
        folder_name = plugin_id.replace(".", "_")
        # 使用安全路径验证，防止路径遍历
        target_path = validate_safe_path(folder_name, plugins_dir)

        # 检查插件是否已安装（需要检查两种格式：新格式下划线和旧格式点）
        old_format_path = plugins_dir / plugin_id
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
            stage="loading",
            progress=15,
            message=f"准备克隆到: {target_path}",
            operation="install",
            plugin_id=plugin_id,
        )

        # 3. 克隆仓库（这里会自动推送 20%-80% 的进度）
        service = get_git_mirror_service()

        # 如果是 GitHub 仓库，使用镜像源
        if "github.com" in repo_url:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=target_path,
                branch=request.branch,
                mirror_id=request.mirror_id,
                depth=1,  # 浅克隆，节省时间和空间
            )
        else:
            # 自定义仓库，直接使用 URL
            result = await service.clone_repository(
                owner=owner, repo=repo, target_path=target_path, branch=request.branch, custom_url=repo_url, depth=1
            )

        if not result.get("success"):
            error_msg = result.get("error", "克隆失败")
            await update_progress(
                stage="error",
                progress=0,
                message="克隆仓库失败",
                operation="install",
                plugin_id=plugin_id,
                error=error_msg,
            )
            raise HTTPException(status_code=500, detail=error_msg)

        # 4. 验证插件完整性
        await update_progress(
            stage="loading", progress=85, message="验证插件文件...", operation="install", plugin_id=plugin_id
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
                plugin_id=plugin_id,
                error="无效的插件格式",
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        # 5. 读取并验证 manifest
        await update_progress(
            stage="loading", progress=90, message="读取插件配置...", operation="install", plugin_id=plugin_id
        )

        try:
            import json as json_module

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json_module.load(f)

            # 基本验证
            required_fields = ["manifest_version", "name", "version", "author"]
            for field in required_fields:
                if field not in manifest:
                    raise ValueError(f"缺少必需字段: {field}")

            # 将插件 ID 写入 manifest（用于后续准确识别）
            # 这样即使文件夹名称改变，也能通过 manifest 准确识别插件
            manifest["id"] = plugin_id
            with open(manifest_path, "w", encoding="utf-8") as f:
                json_module.dump(manifest, f, ensure_ascii=False, indent=2)

        except Exception as e:
            # 清理失败的安装
            import shutil

            shutil.rmtree(target_path, ignore_errors=True)

            await update_progress(
                stage="error",
                progress=0,
                message="_manifest.json 无效",
                operation="install",
                plugin_id=plugin_id,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail=f"无效的 _manifest.json: {e}") from e

        # 6. 安装成功
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
            stage="error",
            progress=0,
            message="安装失败",
            operation="install",
            plugin_id=plugin_id,
            error=str(e),
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/uninstall")
async def uninstall_plugin(
    request: UninstallPluginRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    卸载插件

    删除插件目录及其所有文件
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到卸载插件请求: {request.plugin_id}")

    try:
        # 验证插件 ID 格式安全性
        plugin_id = validate_plugin_id(request.plugin_id)

        # 推送进度：开始卸载
        await update_progress(
            stage="loading",
            progress=10,
            message=f"开始卸载插件: {plugin_id}",
            operation="uninstall",
            plugin_id=plugin_id,
        )

        # 1. 检查插件是否存在（支持新旧两种格式）
        plugins_dir = Path("plugins").resolve()
        # 新格式：下划线
        folder_name = plugin_id.replace(".", "_")
        # 使用安全路径验证
        plugin_path = validate_safe_path(folder_name, plugins_dir)
        # 旧格式：点
        old_format_path = validate_safe_path(plugin_id, plugins_dir)

        # 优先使用新格式，如果不存在则尝试旧格式
        if not plugin_path.exists():
            if old_format_path.exists():
                plugin_path = old_format_path
            else:
                await update_progress(
                    stage="error",
                    progress=0,
                    message="插件不存在",
                    operation="uninstall",
                    plugin_id=plugin_id,
                    error="插件未安装或已被删除",
                )
                raise HTTPException(status_code=404, detail="插件未安装")

        await update_progress(
            stage="loading",
            progress=30,
            message=f"正在删除插件文件: {plugin_path}",
            operation="uninstall",
            plugin_id=plugin_id,
        )

        # 2. 读取插件信息（用于日志）
        manifest_path = plugin_path / "_manifest.json"
        plugin_name = plugin_id

        if manifest_path.exists():
            try:
                import json as json_module

                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json_module.load(f)
                plugin_name = manifest.get("name", plugin_id)
            except Exception:
                pass  # 如果读取失败，使用插件 ID 作为名称

        await update_progress(
            stage="loading",
            progress=50,
            message=f"正在删除 {plugin_name}...",
            operation="uninstall",
            plugin_id=plugin_id,
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

        logger.info(f"成功卸载插件: {plugin_id} ({plugin_name})")

        # 4. 推送成功状态
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
            stage="error",
            progress=0,
            message="卸载失败",
            operation="uninstall",
            plugin_id=plugin_id,
            error=str(e),
        )

        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/update")
async def update_plugin(
    request: UpdatePluginRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    更新插件

    删除旧版本，重新克隆新版本
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"收到更新插件请求: {request.plugin_id}")

    try:
        # 验证插件 ID 格式安全性
        plugin_id = validate_plugin_id(request.plugin_id)

        # 推送进度：开始更新
        await update_progress(
            stage="loading",
            progress=5,
            message=f"开始更新插件: {plugin_id}",
            operation="update",
            plugin_id=plugin_id,
        )

        # 1. 检查插件是否已安装（支持新旧两种格式）
        plugins_dir = Path("plugins").resolve()
        # 新格式：下划线
        folder_name = plugin_id.replace(".", "_")
        # 使用安全路径验证
        plugin_path = validate_safe_path(folder_name, plugins_dir)
        # 旧格式：点
        old_format_path = validate_safe_path(plugin_id, plugins_dir)

        # 优先使用新格式，如果不存在则尝试旧格式
        if not plugin_path.exists():
            if old_format_path.exists():
                plugin_path = old_format_path
            else:
                await update_progress(
                    stage="error",
                    progress=0,
                    message="插件不存在",
                    operation="update",
                    plugin_id=plugin_id,
                    error="插件未安装，请先安装",
                )
                raise HTTPException(status_code=404, detail="插件未安装")

        # 2. 读取旧版本信息
        manifest_path = plugin_path / "_manifest.json"
        old_version = "unknown"

        if manifest_path.exists():
            try:
                import json as json_module

                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json_module.load(f)
                old_version = manifest.get("version", "unknown")
            except Exception:
                pass

        await update_progress(
            stage="loading",
            progress=10,
            message=f"当前版本: {old_version}，准备更新...",
            operation="update",
            plugin_id=plugin_id,
        )

        # 3. 删除旧版本
        await update_progress(
            stage="loading", progress=20, message="正在删除旧版本...", operation="update", plugin_id=plugin_id
        )

        import shutil
        import stat

        def remove_readonly(func, path, _):
            """清除只读属性并删除文件"""
            import os

            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(plugin_path, onerror=remove_readonly)

        logger.info(f"已删除旧版本: {plugin_id} v{old_version}")

        # 4. 解析仓库 URL
        await update_progress(
            stage="loading",
            progress=30,
            message="正在准备下载新版本...",
            operation="update",
            plugin_id=plugin_id,
        )

        repo_url = request.repository_url.rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]

        parts = repo_url.split("/")
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="无效的仓库 URL")

        owner = parts[-2]
        repo = parts[-1]

        # 5. 克隆新版本（这里会推送 35%-85% 的进度）
        service = get_git_mirror_service()

        if "github.com" in repo_url:
            result = await service.clone_repository(
                owner=owner,
                repo=repo,
                target_path=plugin_path,
                branch=request.branch,
                mirror_id=request.mirror_id,
                depth=1,
            )
        else:
            result = await service.clone_repository(
                owner=owner, repo=repo, target_path=plugin_path, branch=request.branch, custom_url=repo_url, depth=1
            )

        if not result.get("success"):
            error_msg = result.get("error", "克隆失败")
            await update_progress(
                stage="error",
                progress=0,
                message="下载新版本失败",
                operation="update",
                plugin_id=plugin_id,
                error=error_msg,
            )
            raise HTTPException(status_code=500, detail=error_msg)

        # 6. 验证新版本
        await update_progress(
            stage="loading", progress=90, message="验证新版本...", operation="update", plugin_id=plugin_id
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
                plugin_id=plugin_id,
                error="无效的插件格式",
            )
            raise HTTPException(status_code=400, detail="无效的插件：缺少 _manifest.json")

        # 7. 读取新版本信息
        try:
            with open(new_manifest_path, "r", encoding="utf-8") as f:
                new_manifest = json_module.load(f)

            new_version = new_manifest.get("version", "unknown")
            new_name = new_manifest.get("name", plugin_id)

            logger.info(f"成功更新插件: {plugin_id} {old_version} → {new_version}")

            # 8. 推送成功状态
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
            }

        except Exception as e:
            # 清理失败的更新
            shutil.rmtree(plugin_path, ignore_errors=True)

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
async def get_installed_plugins(
    maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    获取已安装的插件列表

    扫描 plugins 目录，返回所有已安装插件的 ID 和基本信息
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
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
            return {"success": True, "plugins": []}

        installed_plugins = []

        # 遍历插件目录
        for plugin_path in plugins_dir.iterdir():
            # 只处理目录
            if not plugin_path.is_dir():
                continue

            # 目录名（可能是下划线格式、点格式或其他格式）
            folder_name = plugin_path.name

            # 跳过隐藏目录和特殊目录
            if folder_name.startswith(".") or folder_name.startswith("__"):
                continue

            # 读取 _manifest.json
            manifest_path = plugin_path / "_manifest.json"

            if not manifest_path.exists():
                logger.warning(f"插件文件夹 {folder_name} 缺少 _manifest.json，跳过")
                continue

            try:
                import json as json_module

                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json_module.load(f)

                # 基本验证
                if "name" not in manifest or "version" not in manifest:
                    logger.warning(f"插件文件夹 {folder_name} 的 _manifest.json 格式无效，跳过")
                    continue

                # 获取插件 ID（优先从 manifest，否则从文件夹名推断）
                if "id" in manifest:
                    # 优先使用 manifest 中的 id（最准确）
                    plugin_id = manifest["id"]
                else:
                    # 从 manifest 信息构建 ID
                    # 尝试从 author.name 和 repository_url 构建标准 ID
                    author_name = None
                    repo_name = None

                    # 获取作者名
                    if "author" in manifest:
                        if isinstance(manifest["author"], dict) and "name" in manifest["author"]:
                            author_name = manifest["author"]["name"]
                        elif isinstance(manifest["author"], str):
                            author_name = manifest["author"]

                    # 从 repository_url 获取仓库名
                    if "repository_url" in manifest:
                        repo_url = manifest["repository_url"].rstrip("/")
                        if repo_url.endswith(".git"):
                            repo_url = repo_url[:-4]
                        repo_name = repo_url.split("/")[-1]

                    # 构建 ID
                    if author_name and repo_name:
                        # 标准格式: Author.RepoName
                        plugin_id = f"{author_name}.{repo_name}"
                    elif author_name:
                        # 如果只有作者，使用 Author.FolderName
                        plugin_id = f"{author_name}.{folder_name}"
                    else:
                        # 从文件夹名推断
                        if "_" in folder_name and "." not in folder_name:
                            # 假设格式为 Author_PluginName，转换为 Author.PluginName
                            plugin_id = folder_name.replace("_", ".", 1)
                        else:
                            # 直接使用文件夹名
                            plugin_id = folder_name

                    # 将推断的 ID 写入 manifest（方便下次识别）
                    logger.info(f"为插件 {folder_name} 自动生成 ID: {plugin_id}")
                    manifest["id"] = plugin_id
                    try:
                        with open(manifest_path, "w", encoding="utf-8") as f:
                            json_module.dump(manifest, f, ensure_ascii=False, indent=2)
                    except Exception as write_error:
                        logger.warning(f"无法写入 ID 到 manifest: {write_error}")

                # 添加到已安装列表（返回完整的 manifest 信息）
                installed_plugins.append(
                    {
                        "id": plugin_id,
                        "manifest": manifest,  # 返回完整的 manifest 对象
                        "path": str(plugin_path.absolute()),
                    }
                )

            except json.JSONDecodeError as e:
                logger.warning(f"插件 {folder_name} 的 _manifest.json 解析失败: {e}")
                continue
            except Exception as e:
                logger.error(f"读取插件 {folder_name} 信息时出错: {e}")
                continue

        # 去重：如果有重复的 plugin_id，只保留第一个（按路径）
        seen_ids = {}  # 记录 ID -> 路径的映射
        unique_plugins = []
        duplicates = []
        
        for plugin in installed_plugins:
            plugin_id = plugin["id"]
            plugin_path = plugin["path"]
            
            if plugin_id not in seen_ids:
                seen_ids[plugin_id] = plugin_path
                unique_plugins.append(plugin)
            else:
                duplicates.append(plugin)
                first_path = seen_ids[plugin_id]
                logger.warning(
                    f"重复插件 {plugin_id}: 保留 {first_path}, 跳过 {plugin_path}"
                )
        
        if duplicates:
            logger.warning(f"共检测到 {len(duplicates)} 个重复插件已去重")

        logger.info(f"找到 {len(unique_plugins)} 个已安装插件")

        return {"success": True, "plugins": unique_plugins, "total": len(unique_plugins)}

    except Exception as e:
        logger.error(f"获取已安装插件列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


# ============ 插件配置管理 API ============


class UpdatePluginConfigRequest(BaseModel):
    """更新插件配置请求"""

    config: Dict[str, Any] = Field(..., description="配置数据")


@router.get("/config/{plugin_id}/schema")
async def get_plugin_config_schema(
    plugin_id: str, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    获取插件配置 Schema

    返回插件的完整配置 schema，包含所有 section、字段定义和布局信息。
    用于前端动态生成配置表单。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"获取插件配置 Schema: {plugin_id}")

    try:
        # 尝试从已加载的插件中获取
        from src.plugin_system.core.plugin_manager import plugin_manager

        # 查找插件实例
        plugin_instance = None

        # 遍历所有已加载的插件
        for loaded_plugin_name in plugin_manager.list_loaded_plugins():
            instance = plugin_manager.get_plugin_instance(loaded_plugin_name)
            if instance:
                # 匹配 plugin_name 或 manifest 中的 id
                if instance.plugin_name == plugin_id:
                    plugin_instance = instance
                    break
                # 也尝试匹配 manifest 中的 id
                manifest_id = instance.get_manifest_info("id", "")
                if manifest_id == plugin_id:
                    plugin_instance = instance
                    break

        if plugin_instance and hasattr(plugin_instance, "get_webui_config_schema"):
            # 从插件实例获取 schema
            schema = plugin_instance.get_webui_config_schema()
            return {"success": True, "schema": schema}

        # 如果插件未加载，尝试从文件系统读取
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        # 读取配置文件获取当前配置
        config_path = plugin_path / "config.toml"
        current_config = {}
        if config_path.exists():
            import tomlkit

            with open(config_path, "r", encoding="utf-8") as f:
                current_config = tomlkit.load(f)

        # 构建基础 schema（无法获取完整的 ConfigField 信息）
        schema = {
            "plugin_id": plugin_id,
            "plugin_info": {
                "name": plugin_id,
                "version": "",
                "description": "",
                "author": "",
            },
            "sections": {},
            "layout": {"type": "auto", "tabs": []},
            "_note": "插件未加载，仅返回当前配置结构",
        }

        # 从当前配置推断 schema
        for section_name, section_data in current_config.items():
            if isinstance(section_data, dict):
                schema["sections"][section_name] = {
                    "name": section_name,
                    "title": section_name,
                    "description": None,
                    "icon": None,
                    "collapsed": False,
                    "order": 0,
                    "fields": {},
                }
                for field_name, field_value in section_data.items():
                    # 推断字段类型
                    field_type = type(field_value).__name__
                    ui_type = "text"
                    item_type = None
                    item_fields = None

                    if isinstance(field_value, bool):
                        ui_type = "switch"
                    elif isinstance(field_value, (int, float)):
                        ui_type = "number"
                    elif isinstance(field_value, list):
                        ui_type = "list"
                        # 推断数组元素类型
                        if field_value:
                            first_item = field_value[0]
                            if isinstance(first_item, dict):
                                item_type = "object"
                                # 从第一个元素推断字段结构
                                item_fields = {}
                                for k, v in first_item.items():
                                    item_fields[k] = {
                                        "type": "number" if isinstance(v, (int, float)) else "string",
                                        "label": k,
                                        "default": "" if isinstance(v, str) else 0,
                                    }
                            elif isinstance(first_item, (int, float)):
                                item_type = "number"
                            else:
                                item_type = "string"
                        else:
                            item_type = "string"
                    elif isinstance(field_value, dict):
                        ui_type = "json"

                    schema["sections"][section_name]["fields"][field_name] = {
                        "name": field_name,
                        "type": field_type,
                        "default": field_value,
                        "description": field_name,
                        "label": field_name,
                        "ui_type": ui_type,
                        "required": False,
                        "hidden": False,
                        "disabled": False,
                        "order": 0,
                        "item_type": item_type,
                        "item_fields": item_fields,
                        "min_items": None,
                        "max_items": None,
                        # 补充缺失的字段
                        "placeholder": None,
                        "hint": None,
                        "icon": None,
                        "example": None,
                        "choices": None,
                        "min": None,
                        "max": None,
                        "step": None,
                        "pattern": None,
                        "max_length": None,
                        "input_type": None,
                        "rows": 3,
                        "group": None,
                        "depends_on": None,
                        "depends_value": None,
                    }

        return {"success": True, "schema": schema}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置 Schema 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}/raw")
async def get_plugin_config_raw(
    plugin_id: str,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    获取插件原始 TOML 配置文件内容
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"获取插件原始配置: {plugin_id}")

    try:
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        # 读取配置文件
        config_path = plugin_path / "config.toml"
        if not config_path.exists():
            return {"success": True, "config": "", "message": "配置文件不存在"}

        with open(config_path, "r", encoding="utf-8") as f:
            config_content = f.read()

        return {"success": True, "config": config_content}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件原始配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/config/{plugin_id}/raw")
async def update_plugin_config_raw(
    plugin_id: str,
    request: UpdatePluginConfigRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    更新插件原始 TOML 配置文件

    直接保存 TOML 字符串到配置文件。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"更新插件原始配置: {plugin_id}")

    try:
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = plugin_path / "config.toml"

        # 验证 TOML 格式
        import tomlkit
        
        if not isinstance(request.config, str):
            raise HTTPException(status_code=400, detail="配置必须是字符串格式的 TOML 内容")
        
        try:
            tomlkit.loads(request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}")

        # 备份旧配置
        import shutil
        import datetime

        if config_path.exists():
            backup_name = f"config.toml.backup.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            backup_path = plugin_path / backup_name
            shutil.copy(config_path, backup_path)
            logger.info(f"已备份配置文件: {backup_path}")

        # 写入新配置
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(request.config)

        logger.info(f"已更新插件原始配置: {plugin_id}")

        return {"success": True, "message": "配置已保存", "note": "配置更改将在插件重新加载后生效"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件原始配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}")
async def get_plugin_config(
    plugin_id: str, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    获取插件当前配置值

    返回插件的当前配置值。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"获取插件配置: {plugin_id}")

    try:
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        # 读取配置文件
        config_path = plugin_path / "config.toml"
        if not config_path.exists():
            return {"success": True, "config": {}, "message": "配置文件不存在"}

        import tomlkit
        from tomlkit.items import Array as TomlArray

        def to_builtin(value):
            if isinstance(value, Mapping):
                return {str(k): to_builtin(v) for k, v in value.items()}
            if isinstance(value, (list, tuple, TomlArray)):
                return [to_builtin(v) for v in value]
            return value

        with open(config_path, "r", encoding="utf-8") as f:
            config = tomlkit.load(f)

        return {"success": True, "config": to_builtin(config)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/config/{plugin_id}")
async def update_plugin_config(
    plugin_id: str,
    request: UpdatePluginConfigRequest,
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    更新插件配置

    保存新的配置值到插件的配置文件。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"更新插件配置: {plugin_id}")

    try:
        plugin_instance = find_plugin_instance(plugin_id)

        # 纠正 WebUI 提交的数据结构（扁平键与字符串列表）
        if plugin_instance and isinstance(request.config, dict):
            request.config = normalize_dotted_keys(request.config)
            if isinstance(plugin_instance.config_schema, dict):
                coerce_types(plugin_instance.config_schema, request.config)

        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = plugin_path / "config.toml"

        # 备份旧配置
        import shutil
        import datetime

        if config_path.exists():
            backup_name = f"config.toml.backup.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            backup_path = plugin_path / backup_name
            shutil.copy(config_path, backup_path)
            logger.info(f"已备份配置文件: {backup_path}")

        # 写入新配置（自动保留注释和格式）
        save_toml_with_format(request.config, str(config_path))

        logger.info(f"已更新插件配置: {plugin_id}")

        return {"success": True, "message": "配置已保存", "note": "配置更改将在插件重新加载后生效"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/reset")
async def reset_plugin_config(
    plugin_id: str, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    重置插件配置为默认值

    删除当前配置文件，下次加载插件时将使用默认配置。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"重置插件配置: {plugin_id}")

    try:
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = plugin_path / "config.toml"

        if not config_path.exists():
            return {"success": True, "message": "配置文件不存在，无需重置"}

        # 备份并删除
        import shutil
        import datetime

        backup_name = f"config.toml.reset.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        backup_path = plugin_path / backup_name
        shutil.move(config_path, backup_path)

        logger.info(f"已重置插件配置: {plugin_id}，备份: {backup_path}")

        return {"success": True, "message": "配置已重置，下次加载插件时将使用默认配置", "backup": str(backup_path)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/toggle")
async def toggle_plugin(
    plugin_id: str, maibot_session: Optional[str] = Cookie(None), authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """
    切换插件启用状态

    切换插件配置中的 enabled 字段。
    """
    # Token 验证
    token = get_token_from_cookie_or_header(maibot_session, authorization)
    token_manager = get_token_manager()
    if not token or not token_manager.verify_token(token):
        raise HTTPException(status_code=401, detail="未授权：无效的访问令牌")

    logger.info(f"切换插件状态: {plugin_id}")

    try:
        # 查找插件目录
        plugins_dir = Path("plugins")
        plugin_path = None

        for p in plugins_dir.iterdir():
            if p.is_dir():
                manifest_path = p / "_manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if manifest.get("id") == plugin_id or p.name == plugin_id:
                            plugin_path = p
                            break
                    except Exception:
                        continue

        if not plugin_path:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = plugin_path / "config.toml"

        import tomlkit

        # 读取当前配置（保留注释和格式）
        config = tomlkit.document()
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = tomlkit.load(f)

        # 切换 enabled 状态
        if "plugin" not in config:
            config["plugin"] = tomlkit.table()

        current_enabled = config["plugin"].get("enabled", True)
        new_enabled = not current_enabled
        config["plugin"]["enabled"] = new_enabled

        # 写入配置（保留注释，格式化数组）
        save_toml_with_format(config, str(config_path))

        status = "启用" if new_enabled else "禁用"
        logger.info(f"已{status}插件: {plugin_id}")

        return {
            "success": True,
            "enabled": new_enabled,
            "message": f"插件已{status}",
            "note": "状态更改将在下次加载插件时生效",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换插件状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e

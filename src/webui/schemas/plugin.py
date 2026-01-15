from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


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


class UpdatePluginConfigRequest(BaseModel):
    """更新插件配置请求"""

    config: Dict[str, Any] = Field(..., description="配置数据")

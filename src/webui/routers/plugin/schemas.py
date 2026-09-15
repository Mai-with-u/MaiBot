from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FetchRawFileRequest(BaseModel):
    owner: str = Field(..., description="仓库所有者", examples=["MaiM-with-u"])
    repo: str = Field(..., description="仓库名称", examples=["plugin-repo"])
    branch: str = Field(..., description="分支名称", examples=["main"])
    file_path: str = Field(..., description="文件路径", examples=["plugin_details.json"])
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    custom_url: Optional[str] = Field(None, description="自定义完整 URL")


class FetchRawFileResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    data: Optional[str] = Field(None, description="文件内容")
    error: Optional[str] = Field(None, description="错误信息")
    mirror_used: Optional[str] = Field(None, description="使用的镜像源")
    attempts: int = Field(..., description="尝试次数")
    url: Optional[str] = Field(None, description="实际请求的 URL")


class CloneRepositoryRequest(BaseModel):
    owner: str = Field(..., description="仓库所有者", examples=["MaiM-with-u"])
    repo: str = Field(..., description="仓库名称", examples=["plugin-repo"])
    target_path: str = Field(..., description="目标路径（相对于插件目录）")
    branch: Optional[str] = Field(None, description="分支名称", examples=["main"])
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    custom_url: Optional[str] = Field(None, description="自定义克隆 URL")
    depth: Optional[int] = Field(None, description="克隆深度（浅克隆）", ge=1)


class CloneRepositoryResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    path: Optional[str] = Field(None, description="克隆路径")
    error: Optional[str] = Field(None, description="错误信息")
    mirror_used: Optional[str] = Field(None, description="使用的镜像源")
    attempts: int = Field(..., description="尝试次数")
    url: Optional[str] = Field(None, description="实际克隆的 URL")
    message: Optional[str] = Field(None, description="附加信息")


class MirrorConfigResponse(BaseModel):
    id: str = Field(..., description="镜像源 ID")
    name: str = Field(..., description="镜像源名称")
    raw_prefix: str = Field(..., description="Raw 文件前缀")
    clone_prefix: str = Field(..., description="克隆前缀")
    enabled: bool = Field(..., description="是否启用")
    priority: int = Field(..., description="优先级（数字越小优先级越高）")


class AvailableMirrorsResponse(BaseModel):
    mirrors: List[MirrorConfigResponse] = Field(..., description="镜像源列表")
    default_priority: List[str] = Field(..., description="默认优先级顺序（ID 列表）")


class AddMirrorRequest(BaseModel):
    id: str = Field(..., description="镜像源 ID", examples=["custom-mirror"])
    name: str = Field(..., description="镜像源名称", examples=["自定义镜像源"])
    raw_prefix: str = Field(..., description="Raw 文件前缀", examples=["https://example.com/raw"])
    clone_prefix: str = Field(..., description="克隆前缀", examples=["https://example.com/clone"])
    enabled: bool = Field(True, description="是否启用")
    priority: Optional[int] = Field(None, description="优先级")


class UpdateMirrorRequest(BaseModel):
    name: Optional[str] = Field(None, description="镜像源名称")
    raw_prefix: Optional[str] = Field(None, description="Raw 文件前缀")
    clone_prefix: Optional[str] = Field(None, description="克隆前缀")
    enabled: Optional[bool] = Field(None, description="是否启用")
    priority: Optional[int] = Field(None, description="优先级")


class GitStatusResponse(BaseModel):
    installed: bool = Field(..., description="是否已安装 Git")
    version: Optional[str] = Field(None, description="Git 版本号")
    path: Optional[str] = Field(None, description="Git 可执行文件路径")
    error: Optional[str] = Field(None, description="错误信息")


class InstallPluginRequest(BaseModel):
    plugin_id: str = Field(..., description="插件 ID")
    repository_url: str = Field("", description="分支安装的仓库 URL；发布版本由索引解析")
    branch: Optional[str] = Field("main", description="分支名称")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    version: Optional[str] = Field(None, description="发布版本；latest 表示最新兼容稳定版本")
    pinned: bool = Field(False, description="锁定所选发布版本，阻止自动更新")


class VersionResponse(BaseModel):
    version: str = Field(..., description="麦麦版本号")
    version_major: int = Field(..., description="主版本号")
    version_minor: int = Field(..., description="次版本号")
    version_patch: int = Field(..., description="补丁版本号")


class UninstallPluginRequest(BaseModel):
    plugin_id: str = Field(..., description="插件 ID")


class UpdatePluginRequest(BaseModel):
    plugin_id: str = Field(..., description="插件 ID")
    repository_url: str = Field("", description="分支安装的仓库 URL；发布版本由索引解析")
    branch: Optional[str] = Field("main", description="分支名称")
    mirror_id: Optional[str] = Field(None, description="指定镜像源 ID")
    version: Optional[str] = Field(None, description="发布版本；latest 表示最新兼容稳定版本")
    pinned: bool = Field(False, description="锁定所选发布版本，阻止自动更新")


class UpdatePluginConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class UpdatePluginRawConfigRequest(BaseModel):
    config: str = Field(..., description="原始 TOML 配置内容")


class HookSpecResponse(BaseModel):
    name: str = Field(..., description="Hook 名称")
    description: str = Field("", description="Hook 描述")
    parameters_schema: Dict[str, Any] = Field(default_factory=dict, description="Hook 参数模型")
    default_timeout_ms: int = Field(..., description="默认超时毫秒数")
    allow_blocking: bool = Field(..., description="是否允许 blocking 处理器")
    allow_observe: bool = Field(..., description="是否允许 observe 处理器")
    allow_abort: bool = Field(..., description="是否允许 abort")
    allow_kwargs_mutation: bool = Field(..., description="是否允许修改 kwargs")


class HookSpecListResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    hooks: List[HookSpecResponse] = Field(default_factory=list, description="Hook 规格列表")

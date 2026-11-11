"""WebUI AI 搜索共享的数据模型。"""

from typing import Any, Awaitable, Callable, List, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .ai_help_artifacts import HelpArtifact

AI_SEARCH_MAX_CANDIDATES = 2000


class PluginReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=500, description="插件 ID、名称或市场登记的仓库地址")


class AISearchOutputError(ValueError):
    """模型返回的内容无法解析为 AI 搜索结果结构。"""


class AISearchCandidate(BaseModel):
    """由 WebUI 真实搜索索引提供的候选项。"""

    id: str = Field(..., min_length=1, max_length=180)
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=240)
    category: str = Field(default="", max_length=80)
    document: str = Field(default="", max_length=2000)


class AISearchRequest(BaseModel):
    """AI 搜索请求。"""

    query: str = Field(..., min_length=1, max_length=500)
    language: str = Field(default="zh-CN", max_length=16)
    candidates: List[AISearchCandidate] = Field(..., min_length=1, max_length=AI_SEARCH_MAX_CANDIDATES)
    help_session_id: UUID | Literal["new"] | None = None
    allow_plugin_config_write: bool = False


class AISearchModelResult(BaseModel):
    """模型选择的单个搜索候选。"""

    id: str = Field(..., min_length=1)
    reason: str = ""


class AISearchModelOutput(BaseModel):
    """模型必须返回的结构化搜索结果，不限制任何字段的长度与数量。"""

    answer: str = ""
    suggestions: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    expanded_terms: List[str] = Field(default_factory=list)
    results: List[AISearchModelResult] = Field(default_factory=list)
    plugin_ids: List[str] = Field(default_factory=list)


class AISearchSource(BaseModel):
    """Agent 实际查询并引用的文档或插件资料。"""

    title: str
    url: str


class AISearchPlugin(BaseModel):
    """由市场服务查询结果生成的插件卡片，兼容性只采用详情接口的结论。"""

    id: str
    name: str
    description: str = ""
    version: str = ""
    compatibility: Literal["unknown", "compatible", "incompatible"] = "unknown"
    recommended_version: str | None = None
    reasons: List[str] = Field(default_factory=list)


class AISearchInstallRequest(BaseModel):
    """仅供界面展示和手动确认的安装请求，不代表已经执行安装。"""

    id: str
    plugin_id: str
    name: str
    version: str
    repository_url: str
    branch: str = "main"
    mode: Literal["releases", "branch"]


class AISearchConfigChange(BaseModel):
    path: List[str]
    before: Any
    after: Any


class AISearchConfigUpdate(BaseModel):
    """已提交到插件配置服务的实际改动记录。"""

    id: str
    plugin_id: str
    name: str
    changes: List[AISearchConfigChange]
    message: str


class AISearchResponse(BaseModel):
    """经过候选 ID、插件 ID 与引用来源校验后的 AI 搜索响应。"""

    cached: bool = False
    model_name: str = ""
    answer: str = ""
    suggestions: List[str] = Field(default_factory=list)
    sources: List[AISearchSource] = Field(default_factory=list)
    expanded_terms: List[str] = Field(default_factory=list)
    results: List[AISearchModelResult] = Field(default_factory=list)
    plugins: List[AISearchPlugin] = Field(default_factory=list)
    install_requests: List[AISearchInstallRequest] = Field(default_factory=list)
    config_updates: List[AISearchConfigUpdate] = Field(default_factory=list)
    artifacts: List[HelpArtifact] = Field(default_factory=list)
    total_tokens: int = 0
    # 回答正文未通过证据校验而被丢弃时的原因；此时仅保留导航结果
    grounding_error: str = ""
    # 回答是否依据了本地配置的当前值；配置随时可能被修改，这类回答不能缓存
    used_local_config: bool = False
    # 插件资料、配置和操作记录可能随时改变；复用市场自身缓存，不缓存整份答疑结果。
    used_plugin_market: bool = False


class AISearchPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=100)
    status: Literal["pending", "in_progress", "completed"]


class AISearchProgressEvent(BaseModel):
    """AI 搜索流式返回的可核验过程事件。"""

    type: Literal["progress"] = "progress"
    stage: Literal[
        "start",
        "planning",
        "skill",
        "plan",
        "tool",
        "finalizing",
        "correcting",
        "cache_hit",
        "completed",
        "failed",
    ]
    status: Literal["started", "completed", "failed"] | None = None
    round: int | None = None
    skill: Literal["setup", "troubleshoot", "plugin", "develop"] | None = None
    plan: List[AISearchPlanStep] = Field(default_factory=list)
    tool: str = ""
    query: str = ""
    targets: List[str] = Field(default_factory=list)
    titles: List[str] = Field(default_factory=list)
    count: int | None = None
    error: str = ""
    install_request: AISearchInstallRequest | None = None
    config_update: AISearchConfigUpdate | None = None


AISearchProgressCallback = Callable[[AISearchProgressEvent], Awaitable[None]]

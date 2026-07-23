"""WebUI AI 搜索共享的数据模型。"""

from typing import Awaitable, Callable, List, Literal

from pydantic import BaseModel, Field


AI_SEARCH_MAX_CANDIDATES = 600
AI_SEARCH_MAX_RESULTS = 6


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


class AISearchModelResult(BaseModel):
    """模型选择的单个搜索候选。"""

    id: str = Field(..., min_length=1, max_length=180)
    score: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=160)


class AISearchModelOutput(BaseModel):
    """模型必须返回的结构化搜索结果。"""

    answer: str = Field(default="", max_length=2000)
    suggestions: List[str] = Field(default_factory=list, max_length=6)
    source_ids: List[str] = Field(default_factory=list, max_length=6)
    expanded_terms: List[str] = Field(default_factory=list, max_length=10)
    results: List[AISearchModelResult] = Field(default_factory=list, max_length=AI_SEARCH_MAX_RESULTS)


class AISearchSource(BaseModel):
    """Agent 阅读并引用的官方文档。"""

    title: str
    url: str


class AISearchResponse(BaseModel):
    """经过候选 ID 与官方文档来源校验后的 AI 搜索响应。"""

    success: bool = True
    cached: bool = False
    model_name: str = ""
    answer: str = ""
    suggestions: List[str] = Field(default_factory=list)
    sources: List[AISearchSource] = Field(default_factory=list)
    expanded_terms: List[str] = Field(default_factory=list)
    results: List[AISearchModelResult] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class AISearchProgressEvent(BaseModel):
    """AI 搜索流式返回的可核验过程事件。"""

    type: Literal["progress"] = "progress"
    stage: Literal[
        "start",
        "planning",
        "tool",
        "finalizing",
        "correcting",
        "cache_hit",
        "completed",
        "failed",
    ]
    status: Literal["started", "completed", "failed"] | None = None
    round: int | None = None
    tool: str = ""
    query: str = ""
    targets: List[str] = Field(default_factory=list)
    titles: List[str] = Field(default_factory=list)
    count: int | None = None
    error: str = ""


AISearchProgressCallback = Callable[[AISearchProgressEvent], Awaitable[None]]

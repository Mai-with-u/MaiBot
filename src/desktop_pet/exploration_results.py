"""探索产物的精简结构；工具声明与持久化校验共用同一份定义。"""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExplorationResource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "image", "audio", "video", "file"]
    summary: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=1200, description="文本信息的精简正文；媒体资源可留空")
    source: str = Field(default="", max_length=2000, description="实际来源 URL、文件路径或消息引用；未知时留空")
    location: str = Field(default="", max_length=2000, description="实际可访问的资源 URL 或文件路径；不要编造")

    @model_validator(mode="after")
    def validate_content(self) -> "ExplorationResource":
        if self.kind == "text" and not self.content:
            raise ValueError("文本资源必须提供精简正文 content")
        if self.kind != "text" and not self.location:
            raise ValueError("媒体或文件资源必须提供实际地址 location")
        return self


class ExplorationExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)
    conditions: str = Field(min_length=1, max_length=240, description="适用前提与限制")
    path: List[str] = Field(min_length=1, max_length=6, description="实际执行路径，最多六步，每步最多160字")
    outcome: Literal["success", "partial", "failed"]
    result: str = Field(min_length=1, max_length=300, description="实际结果及关键证据，不复述过程")
    feedback: str = Field(min_length=1, max_length=300, description="观察到的反馈、失败教训或改进建议；不得虚构用户评价")
    feedback_source: Literal["observation", "user", "unverified"]

    @model_validator(mode="after")
    def validate_path(self) -> "ExplorationExperience":
        self.path = [step.strip() for step in self.path]
        if any(not step or len(step) > 160 for step in self.path):
            raise ValueError("经验路径每步必须为 1 至 160 字")
        return self


class ExplorationProducts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resources: List[ExplorationResource] = Field(default_factory=list, max_length=12)
    experiences: List[ExplorationExperience] = Field(default_factory=list, max_length=8)


class ExperienceReuse(ExplorationExperience):
    """复用反馈同样保存实际路径和条件，额外要求可执行的改进或失败原因。"""

    improvement: str = Field(default="", max_length=300, description="本次验证的条件或步骤改进；成功时必填")
    failure_reason: str = Field(default="", max_length=300, description="失败或部分成功的原因；未查明时明确写明未知")

    @model_validator(mode="after")
    def validate_learning(self) -> "ExperienceReuse":
        if self.outcome == "success" and not self.improvement:
            raise ValueError("成功复用必须记录验证到的适用条件或步骤改进")
        if self.outcome in {"partial", "failed"} and not self.failure_reason:
            raise ValueError("失败或部分成功必须记录原因，未查明时明确说明")
        return self

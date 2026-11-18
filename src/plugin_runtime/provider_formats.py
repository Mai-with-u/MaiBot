"""插件通过 @LLMProvider(formats=...) 发布给模型库的协议声明。"""

from typing import Annotated, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


FormatName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SpeechPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: FormatName
    voices: Dict[FormatName, FormatName] = Field(default_factory=dict)


class ProviderFormats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asr: Dict[FormatName, SpeechPreset] = Field(default_factory=dict)
    tts: Dict[FormatName, SpeechPreset] = Field(default_factory=dict)
    web: List[Literal["web_search", "web_fetch"]] = Field(default_factory=list)

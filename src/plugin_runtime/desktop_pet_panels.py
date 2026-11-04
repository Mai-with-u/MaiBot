"""声明式桌宠插件面板；只接受数据，不执行插件提供的 HTML 或脚本。"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
import base64


class DesktopPetPanelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    label: str = Field(min_length=1, max_length=32)
    disabled: StrictBool = False
    selected: StrictBool = False


class DesktopPetPanel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=4000)
    image: Optional[str] = Field(default=None, max_length=1000000)
    copy_text: str = Field(default="", max_length=16000)
    actions: List[DesktopPetPanelAction] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_content(self) -> "DesktopPetPanel":
        if len({action.id for action in self.actions}) != len(self.actions):
            raise ValueError("面板操作 ID 重复")
        if self.image is not None:
            raw = base64.b64decode(self.image, validate=True)
            if len(raw) < 33 or not raw.startswith(b"\x89PNG\r\n\x1a\n") or raw[12:16] != b"IHDR":
                raise ValueError("面板图片必须为 base64 PNG")
            width, height = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
            if not width or not height or width * height > 4000000:
                raise ValueError("面板图片不能超过 400 万像素")
        return self

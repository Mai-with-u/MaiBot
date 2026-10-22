"""外接 Agent 的输入队列、会话状态和接口提示词。"""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence
from uuid import uuid4

import asyncio
import base64

from src.common.async_work import run_sync_work
from src.common.data_models.message_component_data_model import ImageComponent
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType

from .history import AgentHistory
from .persistence import read_json, write_json

if TYPE_CHECKING:
    from .service import AgentService


class AgentSession:
    backend = "acp"

    def __init__(self, service: "AgentService", root: Path) -> None:
        self.service = service
        self.history = AgentHistory(root)
        self.state = "idle"
        self.inbox: List[Dict[str, Any]] = []
        self.pending: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self.backend_state: Dict[str, Any] = {}
        self._backends: Dict[str, Any] = {}

    @property
    def state_key(self) -> str:
        return self.backend

    async def load(self) -> None:
        await asyncio.to_thread(self.history.load)
        path = self.history.root / "state.json"
        if await asyncio.to_thread(path.exists):
            state = await asyncio.to_thread(read_json, path)
            self.inbox, self._backends = state["inbox"], state["backends"]
            if self.state_key in self._backends:
                current = self._backends[self.state_key]
                self.pending = current["pending"]
                self.backend_state = current["backend_state"]

    async def save(self) -> None:
        async with self._lock:
            self._backends[self.state_key] = {"pending": deepcopy(self.pending), "backend_state": deepcopy(self.backend_state)}
            state = {"inbox": deepcopy(self.inbox), "backends": deepcopy(self._backends)}
            await run_sync_work(write_json, self.history.root / "state.json", state)

    async def enqueue(self, text: str, images: Sequence[ImageComponent] = (), *, kind: Literal["user", "system"] = "user") -> str:
        builder = ContextItemBuilder().add_text_content(text)
        for image in images:
            # 接口中的图片已校验、存入工作区；上下文直接带实际图像。
            from io import BytesIO
            from PIL import Image

            def image_format(data: bytes) -> str:
                with Image.open(BytesIO(data)) as decoded:
                    return decoded.format.lower()
            fmt = await asyncio.to_thread(image_format, image.binary_data)
            builder.add_image_content(fmt, base64.b64encode(image.binary_data).decode())
        record = await self.record(kind, [builder.build()])
        self.inbox.append(record)
        await self.save()
        self.service._wake.set()
        return record["id"]

    async def record(self, kind: str, items: Sequence[ContextItem]) -> Dict[str, Any]:
        return {"id": uuid4().hex, "time": datetime.now().astimezone().isoformat(), "kind": kind,
                "items": await asyncio.to_thread(self.history.pack, items)}

    def system_prompt(self) -> ContextItem:
        policy = self.service.policy()
        text = "\n\n".join([global_config.bot.nickname, global_config.personality.personality,
                                policy])
        return ContextItemBuilder().set_role(RoleType.System).add_text_content(text).build()


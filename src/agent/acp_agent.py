"""独立 ACP Agent：会话由外部 harness 持久化，麦麦提供图文传话和可选能力。"""

from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence
from uuid import uuid4

import asyncio
import base64
import hashlib
import json
import os

from src.common.async_work import run_sync_work
from src.common.data_models.message_component_data_model import ImageComponent
from src.common.json_storage import read_json, write_json
from src.files.provider import FileToolProvider

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem, ContextImagePart, ContextItem, ContextItemBuilder, ContextItemMeta, ContextTextPart,
    ProviderActivityItem, ReasoningItem, ReasoningRepresentation, RoleType,
)

from .acp import ACPConnection, ACPRequestError, desktop_manifest, session_cwd
from .history import AgentHistory
from .mcp_server import ToolMCPServer

if TYPE_CHECKING:
    from .service import AgentService


class AgentTurnError(RuntimeError):
    """已结束的单轮失败，保留原始错误及可信传话来源用于回报。"""

    def __init__(self, message: str, batch: Sequence[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.reply_targets: Dict[str, List[str]] = {}
        for record in batch:
            if record.get("reply_to"):
                self.reply_targets.setdefault(record["reply_to"], []).append(record["id"])


class AcpAgent:
    backend = "acp"

    def __init__(self, service: "AgentService") -> None:
        self.service = service
        self.history = AgentHistory(service.root)
        self.state = "idle"
        self.inbox: List[Dict[str, Any]] = []
        self.pending: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self.backend_state: Dict[str, Any] = {}
        self._backends: Dict[str, Any] = {}
        self.config = service.config.model_copy(deep=True)
        self.connection: Optional[ACPConnection] = None
        self.mcp: Optional[ToolMCPServer] = None
        self.capabilities: Dict[str, Any] = {}
        self._connected_session_id: Optional[str] = None

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

    async def enqueue(
        self, text: str, images: Sequence[ImageComponent] = (), *,
        kind: Literal["user", "system"] = "user", reply_to: str = "",
    ) -> str:
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
        if reply_to:
            record["reply_to"] = reply_to
        self.inbox.append(record)
        await self.save()
        if kind == "user":
            await self.service.resume_after_error()
        self.service._wake.set()
        return record["id"]

    async def record(self, kind: str, items: Sequence[ContextItem]) -> Dict[str, Any]:
        return {"id": uuid4().hex, "time": datetime.now().astimezone().isoformat(), "kind": kind,
                "items": await asyncio.to_thread(self.history.pack, items)}

    @property
    def desktop_manifest(self) -> Path:
        return desktop_manifest(self.config)

    @property
    def state_key(self) -> str:
        # 每个 Desktop 桥接文件对应独立 Host，测试实例和正式实例不能共用会话 ID。
        return ("acp_desktop:" + os.path.normcase(str(self.desktop_manifest))
                if self.config.connection == "dsh_desktop" else self.backend)

    async def connect(self) -> None:
        if self.connection is not None:
            if self.connection.failure is None:
                return
            await self.close()
        cwd = await asyncio.to_thread(Path(self.config.acp_cwd).expanduser().resolve) if self.config.acp_cwd else self.history.root / "workspace"
        remote_cwd = session_cwd(self.config, cwd)
        await asyncio.to_thread(cwd.mkdir, parents=True, exist_ok=True)
        if self.backend_state.get("cwd", remote_cwd) != remote_cwd:
            raise ValueError("ACP 会话的主机工作目录已改变，请恢复原工作目录再连接。")
        from .provider import AgentToolProvider

        self.mcp = ToolMCPServer(
            [AgentToolProvider(self.service), FileToolProvider()],
            self.service.agent_id, self.config.acp_timeout, port=self.config.mcp_port,
        )
        self.connection = ACPConnection(self._update)
        try:
            mount = await self.mcp.start()
            self.capabilities = await self.connection.initialize(self.config, cwd, "maibot-agent", mcp_port=self.mcp.port)
            session_id = self.backend_state.get("session_id")
            if session_id and "resume" not in self.capabilities.get("sessionCapabilities", {}):
                raise ValueError("外部 Agent 未声明 session/resume 能力，不能恢复已记录的会话")
            session = await self.connection.open_session(remote_cwd, mount, session_id, self.config.acp_timeout)
            self._connected_session_id = session_id if session_id else session["sessionId"]
            if not session_id:
                self.backend_state.update(session_id=self._connected_session_id, cwd=remote_cwd)
                await self.save()
            await self.connection.select_model(self.config, session, self._connected_session_id)
        except BaseException:
            await self.close()
            raise

    async def _update(self, params: Dict[str, Any]) -> None:
        if params["sessionId"] != self.backend_state.get("session_id"):
            return
        update = params["update"]
        kind = update["sessionUpdate"]
        if kind in {"agent_message_chunk", "agent_thought_chunk"}:
            content = update["content"]
            if content["type"] == "text" and content["text"]:
                meta = ContextItemMeta.create(logical_turn_id=str(update.get("messageId", params["sessionId"])))
                # 流式片段必须原样保存；完整响应的归一化会吞掉片段间的空格和换行。
                items = [ReasoningItem(meta=meta, text_parts=(content["text"],),
                                       representation=ReasoningRepresentation.RAW_TEXT)
                         if kind == "agent_thought_chunk" else
                         AssistantMessageItem(meta=meta, parts=(ContextTextPart(content["text"]),))]
            elif content["type"] == "image":
                items = [ContextItemBuilder().set_role(RoleType.Assistant).add_image_content(
                    content["mimeType"].split("/")[-1], content["data"],
                ).build()]
            else:
                return
            await self.history.append(await self.record("step", items))
        elif kind in {"tool_call", "tool_call_update"}:
            status = update.get("status", "pending")
            self.state = "tool" if status in {"pending", "in_progress"} else "thinking" if self.pending else "idle"
            title = update.get("title", update["toolCallId"])
            item = ProviderActivityItem(meta=ContextItemMeta.create(), action_type="tool", call_id=update["toolCallId"],
                                        display_summary=title, provider_type="acp", status=status)
            record = await self.record("step", [item])
            raw = record["items"][0]
            if kind == "tool_call":
                raw["tool_call"] = {"func_name": title, "args": update.get("rawInput", {})}
            else:
                raw.update(tool_name=title, success=status != "failed",
                           output=json.dumps(update.get("rawOutput", update.get("content", update)), ensure_ascii=False))
                # 使用现有 WebUI 的结果卡片，但不把外部协议事件误当成本体函数调用。
                raw["display_kind"] = "tool_result"
            await self.history.append(record)

    async def _cancel_prompt(self, prompt: asyncio.Task[Any]) -> None:
        await self.connection.notify("session/cancel", {"sessionId": self.backend_state["session_id"]})
        grace = min(self.config.acp_timeout, 2) if self.service._closing else self.config.acp_timeout
        try:
            await asyncio.wait_for(asyncio.shield(prompt), grace)
        except asyncio.TimeoutError:
            await self.connection.close(1)
            raise RuntimeError("ACP 未确认取消，连接已关闭；不会重发原任务") from None

    async def run_turn(self) -> None:
        try:
            await self.connect()
            if self.pending is not None:
                uncertain = set(self.pending["input_ids"])
                self.inbox = [record for record in self.inbox if record["id"] not in uncertain]
                warning = ContextItemBuilder().add_text_content(
                    "上次 ACP 回合在进程中断前未确认结束，执行结果未知，未重发原任务。请检查外部会话及原桌面操作 ID。",
                ).build()
                await self.history.append(await self.record("step", [warning]))
                self.pending = None
                await self.save()
            batch = list(self.inbox)
            if not batch:
                return
            items = await asyncio.to_thread(self.history.unpack, [item for record in batch for item in record["items"]])
            prompt = []
            # ACP prompt 传递接入说明，不冒充外部 harness 的系统消息。
            # 按内容同步，旧会话及能力/人格资料变更后也能收到最新说明。
            policy = await asyncio.to_thread(self.service.policy)
            policy_key = hashlib.sha256(policy.encode()).hexdigest()
            if self.backend_state.get("policy") != policy_key:
                prompt.append({"type": "text", "text": policy})
            prompt.append({"type": "text", "text": await self.service.context()})
            for item in items:
                for part in item.parts:
                    if isinstance(part, ContextImagePart):
                        if not self.capabilities.get("promptCapabilities", {}).get("image", False):
                            raise ValueError("ACP 当前模型路由未声明图片输入能力，任务保留在队列中")
                        prompt.append({"type": "image", "data": part.image_base64, "mimeType": "image/" + part.image_format})
                    elif isinstance(part, ContextTextPart):
                        prompt.append({"type": "text", "text": part.text})
            self.pending = {"input_ids": [record["id"] for record in batch]}
            await self.save()
            for record in batch:
                await self.history.append(record)
                self.inbox.remove(record)
            await self.save()
            self.state = "thinking"
            task = asyncio.create_task(self.connection.request("session/prompt", {
                "sessionId": self.backend_state["session_id"], "prompt": prompt,
            }), name="agent.acp.prompt")
            try:
                result = await asyncio.shield(task)
            except ACPRequestError as exc:
                # 对端已明确结束此请求；保留部分输出，不把失败回合当作在途任务重放。
                self.backend_state["stop_reason"] = "error"
                self.pending = None
                await self.save()
                raise AgentTurnError(str(exc), batch) from exc
            except asyncio.CancelledError:
                await self._cancel_prompt(task)
                self.backend_state.update(policy=policy_key, stop_reason="cancelled")
                self.pending = None
                await self.save()
                raise
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.backend_state["policy"] = policy_key
            self.backend_state["stop_reason"] = result["stopReason"]
            self.pending = None
            await self.save()
            if result["stopReason"] != "end_turn":
                # 已收到最终回执，保留输出并清除在途标记；报告单轮失败，不重发原任务。
                raise AgentTurnError(f"外接 Agent 未正常完成：{result['stopReason']}，已有输出已保留，原任务不会自动重发", batch)
        finally:
            self.state = "idle"

    async def close(self) -> None:
        connection, mcp = self.connection, self.mcp
        session_id, self._connected_session_id = self._connected_session_id, None
        self.connection, self.mcp = None, None
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(self.save)
            if mcp is not None:
                cleanup.push_async_callback(mcp.close)
            if connection is not None:
                cleanup.push_async_callback(connection.close, 1)
                if connection.failure is None and session_id is not None and "close" in self.capabilities.get("sessionCapabilities", {}):
                    await connection.request("session/close", {"sessionId": session_id}, min(self.config.acp_timeout, 2))

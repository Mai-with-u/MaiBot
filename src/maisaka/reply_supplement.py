"""与正文回复并行生成富文本编排，复用实际 Planner 请求前缀。"""

from dataclasses import dataclass
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from jsonschema import validate
from pydantic import BaseModel, ConfigDict, Field, model_validator

import asyncio
import json

from src.agent.planner import ACPPlannerSnapshot, generate_planner_response
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import (
    AtComponent, BaseMessageComponentModel, EmojiComponent, ImageComponent, MessageSequence, TextComponent,
)
from src.common.llm_json import validate_llm_json
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.maisaka.scenes.factory import get_scene
from src.core.tooling import ToolAvailabilityContext
from src.plugin_runtime.host.reply_extensions import build_reply_extensions_schema
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.llm_models.request_snapshot import serialize_tool_options_snapshot
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext, PostProcessedReplyMessage
from src.maisaka.context.compaction import PlannerRequestSnapshot
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import ContentContextMessage, SessionBackedMessage
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.services.llm_service import LLMServiceClient

logger = get_logger("reply_supplement")


class SupplementNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReplyNode(SupplementNode):
    type: Literal["reply"]


class AtNode(SupplementNode):
    type: Literal["at"]
    msg_id: str = Field(min_length=1)


class PictureNode(SupplementNode):
    type: Literal["pic"]
    source: str = Field(min_length=1)
    index: int = Field(ge=0)


class EmojiNode(SupplementNode):
    type: Literal["emoji"]
    index: Optional[int] = Field(default=None, ge=0)
    source: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, min_length=1)
    emotion: Optional[str] = Field(default=None, min_length=1)
    content: Optional[str] = Field(default=None, min_length=1)

    @property
    def is_semantic(self) -> bool:
        return any(value is not None for value in (self.description, self.emotion, self.content))

    @model_validator(mode="after")
    def validate_reference(self) -> "EmojiNode":
        if self.is_semantic:
            if self.source is not None or self.index is not None:
                raise ValueError("情绪描述不能与表情来源或编号同时填写。")
            if self.description is not None and (self.emotion is not None or self.content is not None):
                raise ValueError("旧版 description 不能与 emotion 或 content 同时填写。")
            if any(value is not None and not value.strip() for value in (self.description, self.emotion, self.content)):
                raise ValueError("表情语义查询文本不能为空。")
        elif self.source is None or not self.source.strip():
            raise ValueError("上下文表情需要消息来源。")
        return self


class ReplySupplementPlan(SupplementNode):
    """一份发送编排；reply 只能出现一次，不能携带或改写正文。"""

    plugin_options: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    msg_id: Optional[str] = Field(min_length=1)
    parts: List[Annotated[Union[ReplyNode, AtNode, PictureNode, EmojiNode], Field(discriminator="type")]] = Field(
        min_length=1, max_length=16,
    )

    @model_validator(mode="after")
    def require_one_reply(self) -> "ReplySupplementPlan":
        if sum(isinstance(part, ReplyNode) for part in self.parts) != 1:
            raise ValueError("回复补充编排必须且只能包含一个 reply 占位符")
        return self


@dataclass
class SupplementResources:
    """只允许引用本轮 Planner 已看到的资源，不受生成期间新消息影响。"""

    messages: Dict[str, SessionMessage]
    pictures: Dict[str, List[ImageComponent]]
    allow_at: bool
    allow_quote: bool
    context_emojis: Dict[str, List[EmojiComponent]]
    session_id: str

    @classmethod
    def capture(cls, snapshot: PlannerRequestSnapshot, session_id: str, is_group: bool) -> "SupplementResources":
        messages: Dict[str, SessionMessage] = {}
        pictures: Dict[str, List[ImageComponent]] = {}
        context_emojis: Dict[str, List[EmojiComponent]] = {}
        for message in snapshot.history:
            if isinstance(message, SessionBackedMessage):
                original = message.original_message
                if original.session_id != session_id or original.is_notify:
                    continue
                messages[original.message_id] = original
            if isinstance(message, ContentContextMessage) and message.message_id:
                images = [item for item in message.raw_message.components if isinstance(item, ImageComponent)]
                if images:
                    pictures[message.message_id] = images
                stickers = [item for item in message.raw_message.components if isinstance(item, EmojiComponent)]
                if stickers:
                    context_emojis[message.message_id] = stickers
        scene = get_scene(is_group)
        return cls(messages, pictures, scene.allow_reply_at, scene.quote_frequency != "never",
                   context_emojis, session_id)

    @staticmethod
    def resolve_id(value: str, candidates: Dict[str, Any]) -> str:
        matches = [key for key in candidates if value == key or value == to_display_message_id(key)]
        if len(matches) != 1:
            raise ValueError(f"回复补充器引用的资源不存在或编号有歧义：{value}")
        return matches[0]

    def inventory(self) -> Dict[str, Any]:
        return {
            "message_ids": [to_display_message_id(key) for key in self.messages],
            "pictures": [{"source": key if key.startswith("tool_result:") else to_display_message_id(key),
                          "indices": list(range(len(images)))} for key, images in self.pictures.items()],
            "context_emojis": [{"source": to_display_message_id(key), "indices": list(range(len(emojis)))}
                               for key, emojis in self.context_emojis.items()],
            "allow_at": self.allow_at,
        }


@dataclass
class ReplySupplement:
    plan: ReplySupplementPlan
    target_message: Optional[SessionMessage]
    attachments: asyncio.Task[List[Optional[BaseMessageComponentModel]]]
    metadata: Dict[str, Any]

    async def compose(self, body: List[PostProcessedReplyMessage]) -> List[PostProcessedReplyMessage]:
        """按顺序展开正文；相邻 @ 拼入正文，图片和表情作为独立消息发送。"""
        if not body:
            raise ValueError("回复补充器不能替代空正文发送消息")
        result: List[PostProcessedReplyMessage] = []
        pending: List[BaseMessageComponentModel] = []
        for component in await self.attachments:
            if component is None:
                body[0].sequence.components = pending + body[0].sequence.components
                pending = []
                result.extend(body)
            elif isinstance(component, AtComponent):
                if result and any(isinstance(item, TextComponent) for item in result[-1].sequence.components):
                    result[-1].sequence.components.extend([TextComponent(" "), component])
                else:
                    pending.extend([component, TextComponent(" ")])
            else:
                result.append(PostProcessedReplyMessage(MessageSequence([*pending, component])))
                pending = []
        if pending:
            result.append(PostProcessedReplyMessage(MessageSequence(pending)))
        return result


def resolve_supplement(
    plan: ReplySupplementPlan, resources: SupplementResources, metadata: Dict[str, Any],
) -> ReplySupplement:
    """先确定正文依赖的回复目标，再启动独立的附件任务。"""
    quote = None
    if plan.msg_id is not None:
        quote = resources.messages[resources.resolve_id(plan.msg_id, resources.messages)]
    parts: List[Union[BaseMessageComponentModel, EmojiNode, None]] = []
    for part in plan.parts:
        if isinstance(part, ReplyNode):
            parts.append(None)
        elif isinstance(part, AtNode):
            if not resources.allow_at:
                raise ValueError("回复时 @ 用户已关闭")
            message = resources.messages[resources.resolve_id(part.msg_id, resources.messages)]
            user = message.message_info.user_info
            parts.append(AtComponent(target_user_id=user.user_id, target_user_nickname=user.user_nickname,
                                     target_user_cardname=user.user_cardname))
        elif isinstance(part, PictureNode):
            images = resources.pictures[resources.resolve_id(part.source, resources.pictures)]
            if part.index >= len(images):
                raise ValueError(f"回复补充器图片序号超出范围：{part.source}:{part.index}")
            parts.append(images[part.index])
        else:
            parts.append(part)
    attachments = asyncio.create_task(_resolve_supplement_attachments(parts, resources))
    return ReplySupplement(plan, quote, attachments, metadata)


async def _resolve_supplement_attachments(
    parts: List[Union[BaseMessageComponentModel, EmojiNode, None]], resources: SupplementResources,
) -> List[Optional[BaseMessageComponentModel]]:
    components: List[Optional[BaseMessageComponentModel]] = []
    for part in parts:
        if isinstance(part, ImageComponent):
            await part.load_image_binary()
            if not part.binary_data:
                raise ValueError(f"回复补充器图片无法读取：{part.binary_hash}")
            components.append(ImageComponent(binary_hash=part.binary_hash, content=part.content,
                                             binary_data=part.binary_data))
        elif isinstance(part, EmojiNode):
            source = "semantic" if part.is_semantic else "context"
            try:
                if part.is_semantic:
                    emoji = await BuiltinToolRuntimeContext.load_emoji_by_description(
                        part.emotion or part.description or "", resources.session_id, content=part.content or "",
                    )
                else:
                    source_id = resources.resolve_id(part.source or "", resources.context_emojis)
                    emoji = await BuiltinToolRuntimeContext.load_context_emoji(
                        resources.context_emojis[source_id], part.index if part.index is not None else 0,
                    )
            except Exception as exc:
                logger.warning(
                    f"attach_emoji resolution=failed source={source} path=supplement error={type(exc).__name__}",
                    exc_info=True,
                )
            else:
                components.append(emoji)
                logger.info(f"attach_emoji resolution=resolved source={source} path=supplement")
        else:
            components.append(part)
    return components


async def generate_reply_supplement(
    snapshot: PlannerRequestSnapshot, guidance: str, session_id: str, is_group: bool,
) -> ReplySupplement:
    """补充生成失败时携带错误重生成一次，再失败则终止本次回复。"""
    try:
        return await _generate_reply_supplement_once(snapshot, guidance, session_id, is_group)
    except Exception as exc:
        logger.warning(f"回复补充器生成失败，将重生成一次: session={session_id}", exc_info=True)
        retry_error = f"{type(exc).__name__}: {exc}"
    return await _generate_reply_supplement_once(snapshot, guidance, session_id, is_group, retry_error=retry_error)


async def _generate_reply_supplement_once(
    snapshot: PlannerRequestSnapshot, guidance: str, session_id: str, is_group: bool, *, retry_error: str = "",
) -> ReplySupplement:
    """保留 Planner 的模型、工具及消息前缀，只在末尾追加编排任务。"""
    resources = SupplementResources.capture(snapshot, session_id, is_group)
    context = ToolAvailabilityContext(session_id=session_id, stream_id=session_id, is_group_chat=is_group)
    if resources.messages:
        message = next(iter(resources.messages.values()))
        context.platform = message.platform
        context.group_id = message.message_info.group_info.group_id if message.message_info.group_info else ""
        context.user_id = message.message_info.user_info.user_id
    extension_schema = build_reply_extensions_schema(context) or {"type": "object", "properties": {}, "additionalProperties": False}

    def build_request() -> Union[ResponseRequest, ACPPlannerSnapshot]:
        request = snapshot.request.detached_snapshot()
        schema = ReplySupplementPlan.model_json_schema()
        schema["properties"]["plugin_options"] = extension_schema
        if not resources.allow_at:
            # 在请求声明中移除不可用的 @ 节点，私聊不向模型提供该选项。
            node_schema = schema["properties"]["parts"]["items"]
            at_ref = node_schema["discriminator"]["mapping"].pop("at")
            node_schema["oneOf"] = [node for node in node_schema["oneOf"] if node["$ref"] != at_ref]
            del schema["$defs"]["AtNode"]
        instruction = load_prompt(
            "reply_supplement", guidance=guidance,
            resources=json.dumps(resources.inventory(), ensure_ascii=False),
            schema=json.dumps(schema, ensure_ascii=False),
        )
        request.context_items.append(
            ContextItemBuilder().set_role(RoleType.User).add_text_content(instruction).build()
        )
        if retry_error:
            request.context_items.append(
                ContextItemBuilder().set_role(RoleType.User).add_text_content(
                    load_prompt("reply_supplement_retry", error=retry_error),
                ).build()
            )
        return request

    request = await asyncio.to_thread(build_request)
    if isinstance(request, ACPPlannerSnapshot):
        result = await generate_planner_response(
            request.config, session_id, request.context_items, [], history_item_ids=request.history_item_ids,
        )
    else:
        client = LLMServiceClient(task_name="planner", request_type="maisaka.reply_supplement", session_id=session_id)
        result = await client.generate_response_from_snapshot(request)
    metadata = {
        "model_name": result.model_name, "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
        "prompt_cache_hit_tokens": result.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": result.prompt_cache_miss_tokens,
        "prompt_cache_reported": result.prompt_cache_reported,
    }
    if global_config.debug.save_maisaka_reasoning:
        await asyncio.to_thread(
            PromptCLIVisualizer.build_prompt_preview_access, list(request.context_items),
            category="reply_supplement", chat_id=session_id, request_kind="reply_supplement",
            selection_reason="与 Replyer 并行的富文本编排（Planner 请求快照）",
            tool_definitions=serialize_tool_options_snapshot(request.tool_options)
            if not isinstance(request, ACPPlannerSnapshot) else [],
            output_items=list(result.output_items), metadata=metadata, generation_attempts=result.generation_attempts,
        )
    logger.info(f"回复补充器缓存: session={session_id} {metadata}")
    if result.tool_calls or result.native_tool_calls:
        raise ValueError("回复补充器不得调用工具")
    plan = validate_llm_json(result.response, ReplySupplementPlan)
    validate(plan.plugin_options, extension_schema)
    metadata["plan"] = plan.model_dump()
    return resolve_supplement(plan, resources, metadata)

"""send_emoji 内置工具。"""

from datetime import datetime
from io import BytesIO
from json import dumps
from random import sample
from typing import Any, Dict, Optional

import asyncio
import math

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from pydantic import BaseModel, Field as PydanticField

from src.emoji_system.emoji_manager import emoji_manager
from src.emoji_system.maisaka_tool import send_emoji_for_maisaka
from src.common.data_models.image_data_model import MaiEmoji
from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence, TextComponent
from src.common.logger import get_logger
from src.config.config import config_manager, global_config
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.payload_content.message import MessageBuilder, RoleType
from src.maisaka.context_messages import (
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
)
from src.plugin_runtime.hook_payloads import serialize_prompt_messages

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_send_emoji")

_EMOJI_SUB_AGENT_CONTEXT_LIMIT = 12
_EMOJI_SUB_AGENT_MAX_TOKENS = 240
_EMOJI_MAX_CANDIDATE_COUNT = 64
_EMOJI_CANDIDATE_TILE_SIZE = 256
_EMOJI_SUCCESS_MESSAGE = "表情包发送成功"


class EmojiSelectionResult(BaseModel):
    """表情包子代理的结构化选择结果。"""

    emoji_index: int = PydanticField(default=1, description="选中的表情包序号，从 1 开始计数。")
    reason: str = PydanticField(default="", description="选择这张表情包的简短理由。")


def get_tool_spec() -> ToolSpec:
    """获取 send_emoji 工具声明。"""

    return ToolSpec(
        name="send_emoji",
        brief_description="发送一个合适的表情包来辅助表达情绪。",
        detailed_description="无需参数，直接发送一个合适的表情包。",
        parameters_schema={
            "type": "object",
            "properties": {},
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def _load_emoji_bytes(emoji: MaiEmoji) -> bytes:
    """读取单个表情包图片字节。"""

    return await asyncio.to_thread(emoji.full_path.read_bytes)


def _get_emoji_candidate_count() -> int:
    """获取本次表情包候选数量配置。"""

    configured_count = int(getattr(global_config.emoji, "emoji_send_num", 25))
    return max(1, min(configured_count, _EMOJI_MAX_CANDIDATE_COUNT))


def _calculate_grid_shape(candidate_count: int) -> tuple[int, int]:
    """根据候选数量计算尽量接近矩形的拼图行列数。"""

    if candidate_count <= 0:
        return 1, 1

    best_columns = candidate_count
    best_rows = 1
    best_score: tuple[int, int] | None = None

    for columns in range(1, candidate_count + 1):
        rows = math.ceil(candidate_count / columns)
        empty_slots = rows * columns - candidate_count
        aspect_gap = abs(columns - rows)
        score = (aspect_gap, empty_slots)
        if best_score is None or score < best_score:
            best_score = score
            best_columns = columns
            best_rows = rows

    return best_rows, best_columns


def _build_placeholder_tile(label: str, tile_size: int) -> PILImage.Image:
    """构建图片读取失败时使用的占位图。"""

    tile = PILImage.new("RGB", (tile_size, tile_size), color=(245, 245, 245))
    draw = ImageDraw.Draw(tile)
    font = ImageFont.load_default()
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    draw.text(
        ((tile_size - text_width) / 2, (tile_size - text_height) / 2),
        label,
        fill=(80, 80, 80),
        font=font,
    )
    return tile


def _build_labeled_tile(image_bytes: bytes, index: int, tile_size: int) -> PILImage.Image:
    """构建带序号角标的候选图片块。"""

    try:
        with PILImage.open(BytesIO(image_bytes)) as raw_image:
            image = raw_image.convert("RGBA")
    except Exception:
        return _build_placeholder_tile(str(index), tile_size)

    image.thumbnail((tile_size, tile_size))
    tile = PILImage.new("RGBA", (tile_size, tile_size), color=(255, 255, 255, 255))
    offset_x = (tile_size - image.width) // 2
    offset_y = (tile_size - image.height) // 2
    tile.paste(image, (offset_x, offset_y), image)

    draw = ImageDraw.Draw(tile)
    font = ImageFont.load_default()
    badge_size = 56
    badge_margin = 14
    draw.rounded_rectangle(
        (
            badge_margin,
            badge_margin,
            badge_margin + badge_size,
            badge_margin + badge_size,
        ),
        radius=8,
        fill=(0, 0, 0, 180),
    )
    label = str(index)
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    draw.text(
        (
            badge_margin + (badge_size - text_width) / 2,
            badge_margin + (badge_size - text_height) / 2 - 1,
        ),
        label,
        fill=(255, 255, 255, 255),
        font=font,
    )
    return tile


def _merge_emoji_tiles(image_bytes_list: list[bytes]) -> bytes:
    """将候选表情图拼接成一张尽量接近矩形的网格图片。"""

    tile_size = _EMOJI_CANDIDATE_TILE_SIZE
    gap = 12
    candidate_count = len(image_bytes_list)
    grid_rows, grid_columns = _calculate_grid_shape(candidate_count)
    tiles = [
        _build_labeled_tile(image_bytes=image_bytes, index=index, tile_size=tile_size)
        for index, image_bytes in enumerate(image_bytes_list, start=1)
    ]
    canvas_width = tile_size * grid_columns + gap * (grid_columns - 1)
    canvas_height = tile_size * grid_rows + gap * (grid_rows - 1)
    canvas = PILImage.new("RGBA", (canvas_width, canvas_height), color=(255, 255, 255, 255))

    for index, tile in enumerate(tiles):
        row = index // grid_columns
        column = index % grid_columns
        offset_x = column * (tile_size + gap)
        offset_y = row * (tile_size + gap)
        canvas.paste(tile, (offset_x, offset_y), tile)

    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG")
    return output.getvalue()


async def _build_emoji_candidate_message(emojis: list[MaiEmoji]) -> SessionBackedMessage:
    """构建供子代理挑选的拼图候选消息。"""

    image_bytes_list = await asyncio.gather(*[_load_emoji_bytes(emoji) for emoji in emojis])
    merged_image_bytes = await asyncio.to_thread(_merge_emoji_tiles, list(image_bytes_list))
    raw_message = MessageSequence(
        [
            TextComponent("请从这张 5x5 拼图中选择一个序号。"),
            ImageComponent(binary_hash="", binary_data=merged_image_bytes),
        ]
    )
    return SessionBackedMessage(
        raw_message=raw_message,
        visible_text="[表情包拼图候选]",
        timestamp=datetime.now(),
        source_kind="emoji_candidate",
    )


def _build_send_emoji_monitor_detail(
    *,
    request_messages: Optional[list[dict[str, Any]]] = None,
    reasoning_text: str = "",
    output_text: str = "",
    metrics: Optional[Dict[str, Any]] = None,
    extra_sections: Optional[list[dict[str, str]]] = None,
) -> Dict[str, Any]:
    """构建 send_emoji 工具统一监控详情。"""

    detail: Dict[str, Any] = {}
    if isinstance(request_messages, list) and request_messages:
        detail["request_messages"] = request_messages
        detail["prompt_text"] = dumps(request_messages, ensure_ascii=False, indent=2)
    if reasoning_text.strip():
        detail["reasoning_text"] = reasoning_text.strip()
    if output_text.strip():
        detail["output_text"] = output_text.strip()
    if isinstance(metrics, dict) and metrics:
        detail["metrics"] = dict(metrics)
    normalized_sections = [
        {
            "title": str(section.get("title") or "").strip(),
            "content": str(section.get("content") or "").strip(),
        }
        for section in extra_sections or []
        if isinstance(section, dict)
        and str(section.get("title") or "").strip()
        and str(section.get("content") or "").strip()
    ]
    if normalized_sections:
        detail["extra_sections"] = normalized_sections
    return detail


def _build_send_emoji_monitor_metadata(
    selection_metadata: Dict[str, Any],
    *,
    send_result: Optional[Any] = None,
    error_message: str = "",
) -> Dict[str, Any]:
    """根据表情选择与发送结果构建统一监控 metadata。"""

    raw_detail = selection_metadata.get("monitor_detail")
    detail = dict(raw_detail) if isinstance(raw_detail, dict) else {}
    extra_sections = list(detail.get("extra_sections", [])) if isinstance(detail.get("extra_sections"), list) else []

    if send_result is not None:
        result_lines = [
            f"命中情绪：{send_result.matched_emotion or '未命中'}",
            f"表情描述：{send_result.description or '无描述'}",
            f"情绪标签：{'、'.join(send_result.emotions) if send_result.emotions else '无'}",
            f"发送结果：{send_result.message or ('成功' if send_result.success else '失败')}",
        ]
        extra_sections.append({
            "title": "表情发送结果",
            "content": "\n".join(result_lines),
        })
    elif error_message.strip():
        extra_sections.append({
            "title": "表情发送结果",
            "content": f"发送结果：{error_message.strip()}",
        })

    if extra_sections:
        detail["extra_sections"] = extra_sections

    if detail:
        return {"monitor_detail": detail}
    return {}


def _resolve_emoji_selector_model_task_name() -> str:
    """返回表情选择子代理的模型任务名，始终使用组件模型（utils）。"""
    return "utils"


async def _select_emoji_with_sub_agent(
    tool_ctx: BuiltinToolRuntimeContext,
    reasoning: str,
    context_texts: list[str],
    sample_size: int,
    selection_metadata: Optional[Dict[str, Any]] = None,
) -> tuple[MaiEmoji | None, str]:
    """通过临时子代理从候选表情包中选出一个结果。"""

    del reasoning, context_texts, sample_size

    available_emojis = list(emoji_manager.emojis)
    if not available_emojis:
        return None, ""

    total_candidate_count = min(len(available_emojis), _get_emoji_candidate_count())
    sampled_emojis = sample(available_emojis, total_candidate_count)
    candidate_message = await _build_emoji_candidate_message(sampled_emojis)
    grid_rows, grid_columns = _calculate_grid_shape(len(sampled_emojis))

    system_prompt = (
        "你是 Maisaka 的临时表情包选择子代理。\n"
        f"你会收到群聊上下文，以及 1 条额外候选消息，其中包含一张 {grid_rows}x{grid_columns} 的表情包拼图，"
        f"一共 {len(sampled_emojis)} 个位置。\n"
        f"每张小图左上角都有一个较大的序号，范围是 1 到 {len(sampled_emojis)}。\n"
        f"你的任务是根据上下文和当前语气，从这 {len(sampled_emojis)} 张图里选出最合适的一张表情包。\n"
        "你必须返回一个 JSON 对象（json object），不要输出任何 JSON 之外的内容。\n"
        '返回格式固定为：{"emoji_index":1,"reason":"简短理由"}'
    )
    prompt_message = ReferenceMessage(
        content=(
            f"[选择任务]\n"
            f"候选总数: {len(sampled_emojis)}\n"
            f"拼图布局: {grid_rows}x{grid_columns}\n"
            "请只输出 JSON。"
        ),
        timestamp=datetime.now(),
        reference_type=ReferenceMessageType.TOOL_HINT,
        remaining_uses_value=1,
        display_prefix="[表情包选择任务]",
    )
    request_messages = [
        MessageBuilder().set_role(RoleType.System).add_text_content(system_prompt).build(),
    ]
    prompt_llm_message = prompt_message.to_llm_message()
    if prompt_llm_message is not None:
        request_messages.append(prompt_llm_message)
    candidate_to_llm_message = getattr(candidate_message, "to_llm_message", None)
    candidate_llm_message = candidate_to_llm_message() if callable(candidate_to_llm_message) else None
    if candidate_llm_message is not None:
        request_messages.append(candidate_llm_message)
    serialized_request_messages = serialize_prompt_messages(request_messages)

    selection_started_at = datetime.now()
    response = await tool_ctx.runtime.run_sub_agent(
        context_message_limit=_EMOJI_SUB_AGENT_CONTEXT_LIMIT,
        system_prompt=system_prompt,
        extra_messages=[prompt_message, candidate_message],
        max_tokens=_EMOJI_SUB_AGENT_MAX_TOKENS,
        model_task_name=_resolve_emoji_selector_model_task_name(),
    )
    selection_duration_ms = round((datetime.now() - selection_started_at).total_seconds() * 1000, 2)

    selection_metrics: Dict[str, Any] = {
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "total_tokens": response.total_tokens,
        "overall_ms": selection_duration_ms,
    }

    try:
        selection = EmojiSelectionResult.model_validate_json(response.content or "")
    except Exception as exc:
        logger.warning(f"{tool_ctx.runtime.log_prefix} 表情包子代理结果解析失败，将回退到候选首项: {exc}")
        if selection_metadata is not None:
            selection_metadata["monitor_detail"] = _build_send_emoji_monitor_detail(
                request_messages=serialized_request_messages,
                output_text=response.content or "",
                metrics=selection_metrics,
                extra_sections=[{
                    "title": "解析异常",
                    "content": str(exc),
                }],
            )
        fallback_emoji = sampled_emojis[0] if sampled_emojis else None
        return fallback_emoji, ""

    if selection_metadata is not None:
        selection_metadata["reason"] = selection.reason.strip()
        selection_metadata["monitor_detail"] = _build_send_emoji_monitor_detail(
            request_messages=serialized_request_messages,
            reasoning_text=selection.reason,
            output_text=response.content or "",
            metrics=selection_metrics,
        )

    emoji_index = int(selection.emoji_index)
    if emoji_index < 1 or emoji_index > len(sampled_emojis):
        logger.warning(
            f"{tool_ctx.runtime.log_prefix} 表情包子代理返回了无效序号: {emoji_index!r}，将回退到第 1 张"
        )
        emoji_index = 1

    return sampled_emojis[emoji_index - 1], ""


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 send_emoji 内置工具。"""

    del context
    context_texts = [
        message.processed_plain_text.strip()
        for message in tool_ctx.runtime._chat_history[-5:]
        if isinstance(message, LLMContextMessage) and message.processed_plain_text.strip()
    ]
    structured_result: Dict[str, Any] = {
        "success": False,
        "message": "",
        "description": "",
        "emotion": [],
        "matched_emotion": "",
        "reason": "",
    }
    selection_metadata: Dict[str, Any] = {"reason": "", "monitor_detail": {}}
    requested_emotion = ""
    if isinstance(invocation.arguments, dict):
        requested_emotion = str(invocation.arguments.get("emotion") or "").strip()

    logger.info(f"{tool_ctx.runtime.log_prefix} 触发表情包发送工具")

    try:
        send_result = await send_emoji_for_maisaka(
            stream_id=tool_ctx.runtime.session_id,
            requested_emotion=requested_emotion,
            reasoning=tool_ctx.engine.last_reasoning_content,
            context_texts=context_texts,
            emoji_selector=lambda _requested_emotion, reasoning, context_texts, sample_size: _select_emoji_with_sub_agent(
                tool_ctx,
                reasoning,
                list(context_texts or []),
                sample_size,
                selection_metadata,
            ),
        )
    except Exception as exc:
        logger.exception(f"{tool_ctx.runtime.log_prefix} 发送表情包时发生异常: {exc}")
        structured_result["message"] = f"发送表情包时发生异常：{exc}"
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            structured_result["message"],
            structured_content=structured_result,
            metadata=_build_send_emoji_monitor_metadata(
                selection_metadata,
                error_message=structured_result["message"],
            ),
        )

    if send_result.success:
        structured_result["message"] = _EMOJI_SUCCESS_MESSAGE
        structured_result["reason"] = selection_metadata["reason"]
        logger.info(
            f"{tool_ctx.runtime.log_prefix} 表情包发送成功 "
            f"描述={send_result.description!r} 情绪标签={send_result.emotions} "
            f"命中情绪={send_result.matched_emotion!r}"
        )
        if send_result.sent_message is None:
            tool_ctx.append_sent_emoji_to_chat_history(
                emoji_base64=send_result.emoji_base64,
                success_message=_EMOJI_SUCCESS_MESSAGE,
            )
        structured_result["success"] = True
        return tool_ctx.build_success_result(
            invocation.tool_name,
            selection_metadata["reason"] or _EMOJI_SUCCESS_MESSAGE,
            structured_content=structured_result,
            metadata=_build_send_emoji_monitor_metadata(
                selection_metadata,
                send_result=send_result,
            ),
        )

    structured_result["description"] = send_result.description
    structured_result["emotion"] = list(send_result.emotions)
    structured_result["matched_emotion"] = send_result.matched_emotion
    structured_result["message"] = send_result.message

    logger.warning(
        f"{tool_ctx.runtime.log_prefix} 表情包发送失败 "
        f"错误信息={send_result.message}"
    )
    return tool_ctx.build_failure_result(
        invocation.tool_name,
        structured_result["message"],
        structured_content=structured_result,
        metadata=_build_send_emoji_monitor_metadata(
            selection_metadata,
            send_result=send_result,
        ),
    )

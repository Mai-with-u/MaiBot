"""展示供 Planner 直接选择的表情包拼图。"""

from base64 import b64encode
from datetime import datetime
from itertools import count
from random import sample
from typing import Optional

import asyncio

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.emoji_system.emoji_manager import emoji_manager
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextItemMeta,
    ContextTextPart,
    UserMessageItem,
)
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

from .context import BuiltinToolRuntimeContext
from .send_emoji import _get_emoji_candidate_count, _load_emoji_bytes, _merge_emoji_tiles

# 历史只存在于进程内；使用进程级单调序号，聊天切换、历史复制或裁剪后也不复用旧编号。
_emoji_indices = count(1)


def get_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="show_emoji_list",
        description=(
            "查看随机抽取的表情包选择拼图。图片会进入你的上下文，不会发送给聊天对象。"
            "调用 reply 时将选中图片的序号填入 attach_emoji；历史拼图的序号也可使用。"
        ),
        parameters_schema={"type": "object", "properties": {}},
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """生成一次拼图快照，由工具执行器在工具结果之后追加到历史。"""

    if not resolve_enable_visual_planner():
        return tool_ctx.build_failure_result(invocation.tool_name, "show_emoji_list 需要启用 Planner 视觉能力。")
    available = list(emoji_manager.emojis)
    if not available:
        return tool_ctx.build_failure_result(invocation.tool_name, "当前表情包库中没有可用表情。")
    candidates = sample(available, min(len(available), _get_emoji_candidate_count()))
    # 在任何 await 之前一次性分配连续编号，并发调用也不会分配到重复编号。
    indices = [next(_emoji_indices) for _ in candidates]
    try:
        images = await asyncio.gather(*[_load_emoji_bytes(emoji) for emoji in candidates])
        collage = await asyncio.to_thread(_merge_emoji_tiles, list(images), indices[0])
    except Exception as exc:
        return tool_ctx.build_failure_result(invocation.tool_name, f"生成表情包拼图失败：{exc}")
    text = (
        f"表情包选择图，序号 {indices[0]}–{indices[-1]}。"
        "请在 reply.attach_emoji 中填写选中的整数序号；表情包将于文字回复后单独发送。"
    )
    timestamp = datetime.now()
    message = EmojiCandidateMessage(
        item=UserMessageItem(
            meta=ContextItemMeta.create(timestamp=timestamp),
            parts=(ContextTextPart(text), ContextImagePart("png", b64encode(collage).decode("ascii"))),
        ),
        emoji_hashes={index: emoji.file_hash for index, emoji in zip(indices, candidates, strict=True)},
        visible_text=text,
        timestamp=timestamp,
    )
    return tool_ctx.build_success_result(invocation.tool_name, text, post_history_messages=[message])

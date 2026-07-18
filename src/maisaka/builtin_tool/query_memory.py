"""query_memory 内置工具。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.utils.tool_post_execution import with_memory_feedback_task
from src.person_info.person_info import resolve_person_id_for_memory
from src.services.memory_service import MemorySearchResult, memory_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_query_memory")

_ALLOWED_QUERY_MODES = {"search", "time", "hybrid", "episode", "aggregate"}
REPLYER_MEMORY_REFERENCE_MARKER = "【长期记忆检索结果-内部参考】"


def get_tool_spec(*, enabled: bool = True) -> ToolSpec:
    """获取 query_memory 工具声明。"""

    return ToolSpec(
        name="query_memory",
        description="检索长期记忆。",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词或问题；非纯时间检索必填。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数。",
                },
                "mode": {
                    "type": "string",
                    "description": "search事实偏好，time时间段，episode经历，aggregate整体，hybrid不确定。",
                    "enum": sorted(_ALLOWED_QUERY_MODES),
                    "default": "search",
                },
                "person_name": {
                    "type": "string",
                    "description": "人物名；用于定向过滤。",
                },
                "time_start": {
                    "type": "string",
                    "description": "起始时间。",
                },
                "time_end": {
                    "type": "string",
                    "description": "结束时间。",
                },
                "respect_filter": {
                    "type": "boolean",
                    "description": "是否遵守记忆过滤规则；默认true，模糊来源或整体印象可false。",
                    "default": True,
                },
            },
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
        enabled=enabled,
    )


def _normalize_optional_time(raw_value: Any) -> str | float | None:
    """归一化可选时间参数。"""

    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        time_text = raw_value.strip()
        if not time_text:
            return None
        return time_text
    if isinstance(raw_value, (float, int)):
        return float(raw_value)

    time_text = str(raw_value).strip()
    if not time_text:
        return None
    return time_text


def _resolve_person_id(
    *,
    person_name: str,
    platform: str,
    user_id: str,
    group_id: str,
) -> Tuple[str, str]:
    """按约定顺序解析长期记忆检索使用的 person_id。"""

    clean_person_name = str(person_name or "").strip()
    if clean_person_name:
        person_id = resolve_person_id_for_memory(
            person_name=clean_person_name,
            platform=platform,
            user_id=user_id,
        )
        if person_id:
            return person_id, clean_person_name

    if not group_id and platform and user_id:
        person_id = resolve_person_id_for_memory(
            platform=platform,
            user_id=user_id,
        )
        if person_id:
            return person_id, clean_person_name

    return "", clean_person_name


def _build_success_content(result: MemorySearchResult, *, limit: int) -> str:
    """构造工具成功时的可读内容。"""

    summary = str(result.summary or "").strip()
    snippet = result.to_text(limit=max(1, int(limit)), truncate_content=False)

    if result.hits:
        if snippet:
            return snippet
        if summary:
            return summary
        return "已找到匹配的长期记忆。"

    if result.filtered:
        return "当前请求被聊天过滤策略跳过，未执行长期记忆检索。"
    return "未找到匹配的长期记忆。"


def _build_replyer_memory_reference(structured_content: Dict[str, Any]) -> str:
    """构造自动透传给 replyer 的长期记忆参考。"""

    raw_hits = structured_content.get("hits")
    if not isinstance(raw_hits, list):
        return ""

    lines = [REPLYER_MEMORY_REFERENCE_MARKER]
    query = str(structured_content.get("query") or "").strip()
    mode = str(structured_content.get("mode") or "").strip()
    effective_mode = str(structured_content.get("effective_mode") or "").strip()
    if query:
        lines.append(f"查询：{query}")
    if mode:
        mode_text = mode
        if effective_mode and effective_mode != mode:
            mode_text = f"{mode} -> {effective_mode}"
        lines.append(f"模式：{mode_text}")

    hit_lines: list[str] = []
    for index, raw_hit in enumerate(raw_hits, start=1):
        if not isinstance(raw_hit, dict):
            continue
        content = str(raw_hit.get("content") or "").strip()
        if not content:
            continue
        hit_type = str(raw_hit.get("type") or "").strip()
        title = str(raw_hit.get("title") or "").strip()
        label_parts = [part for part in (title, hit_type) if part]
        label = f"（{' / '.join(label_parts)}）" if label_parts else ""
        normalized_content = " ".join(content.split())
        hit_lines.append(f"{index}. {label}{normalized_content}")

    if not hit_lines:
        return ""

    lines.append(f"命中：{len(hit_lines)} 条")
    lines.extend(hit_lines)
    return "\n".join(lines)


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 query_memory 内置工具。"""

    del context
    runtime = tool_ctx.runtime
    chat_stream = runtime.chat_stream

    clean_query = str(invocation.arguments.get("query") or "").strip()
    mode = str(invocation.arguments.get("mode") or "search").strip().lower() or "search"
    if mode not in _ALLOWED_QUERY_MODES:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"不支持的检索模式：{mode}。可选值：search/time/hybrid/episode/aggregate。",
        )

    default_limit = max(1, global_config.a_memorix.integration.memory_query_default_limit)
    try:
        limit = int(invocation.arguments.get("limit", default_limit) or default_limit)
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(limit, 20))

    time_start = _normalize_optional_time(invocation.arguments.get("time_start"))
    time_end = _normalize_optional_time(invocation.arguments.get("time_end"))
    if not clean_query and time_start is None and time_end is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "query_memory 需要提供 query，或至少提供 time_start/time_end 中的一个。",
        )

    session_id = str(runtime.session_id or "").strip()
    platform = str(chat_stream.platform or "").strip()
    user_id = str(chat_stream.user_id or "").strip()
    group_id = str(chat_stream.group_id or "").strip()
    person_id, person_name = _resolve_person_id(
        person_name=str(invocation.arguments.get("person_name") or ""),
        platform=platform,
        user_id=user_id,
        group_id=group_id,
    )
    respect_filter = bool(invocation.arguments.get("respect_filter", True))
    fallback_applied = False
    fallback_reason = ""
    fallback_query = ""
    effective_mode = mode
    primary_hit_count = 0

    logger.info(
        f"{runtime.log_prefix} 触发长期记忆检索工具: "
        f"mode={mode} query={clean_query!r} person_name={person_name!r} person_id={person_id!r}"
    )
    try:
        result = await memory_service.search(
            clean_query,
            limit=limit,
            mode=mode,
            chat_id=session_id,
            person_id=person_id,
            time_start=time_start,
            time_end=time_end,
            respect_filter=respect_filter,
            user_id=user_id,
            group_id=group_id,
        )
    except Exception as exc:
        logger.exception(f"{runtime.log_prefix} 长期记忆检索执行异常: {exc}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"长期记忆检索失败：{exc}",
        )
    primary_hit_count = len(result.hits)

    # 方案2：人物过滤未命中时，降级到关键词检索，避免直接“空结果”。
    if result.success and person_id and not result.filtered and not result.hits and clean_query:
        fallback_applied = True
        fallback_reason = "person_filter_miss"
        fallback_query = clean_query
        effective_mode = "search"
        logger.info(
            f"{runtime.log_prefix} 人物过滤未命中，降级为关键词检索: "
            f"query={fallback_query!r} original_mode={mode} person_id={person_id!r}"
        )
        try:
            fallback_result = await memory_service.search(
                fallback_query,
                limit=limit,
                mode="search",
                chat_id=session_id,
                person_id="",
                time_start=None,
                time_end=None,
                respect_filter=respect_filter,
                user_id=user_id,
                group_id=group_id,
            )
            if fallback_result.success:
                result = fallback_result
            else:
                logger.warning(f"{runtime.log_prefix} 关键词降级检索失败，回退原结果: error={fallback_result.error}")
        except Exception as exc:
            logger.warning(f"{runtime.log_prefix} 关键词降级检索异常，回退原结果: {exc}")

    structured_content: Dict[str, Any] = result.to_dict()
    structured_content.update(
        {
            "query": clean_query,
            "mode": mode,
            "effective_mode": effective_mode,
            "limit": limit,
            "chat_id": session_id,
            "person_name": person_name,
            "person_id": person_id,
            "time_start": time_start,
            "time_end": time_end,
            "respect_filter": respect_filter,
            "user_id": user_id,
            "group_id": group_id,
            "fallback_applied": fallback_applied,
            "fallback_reason": fallback_reason,
            "fallback_query": fallback_query,
            "primary_hit_count": primary_hit_count,
        }
    )

    if not result.success:
        error_message = str(result.error or "").strip() or "长期记忆检索失败。"
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            error_message,
            structured_content=structured_content,
        )

    content = _build_success_content(result, limit=limit)
    if fallback_applied:
        content = f"提示：人物定向检索未命中，已自动降级为关键词检索。\n{content}"
    metadata: Dict[str, Any] = with_memory_feedback_task()
    replyer_memory_reference = _build_replyer_memory_reference(structured_content)
    if replyer_memory_reference:
        metadata["replyer_memory_reference"] = replyer_memory_reference

    return tool_ctx.build_success_result(
        invocation.tool_name,
        content,
        structured_content=structured_content,
        metadata=metadata,
    )

"""query_person_profile 内置工具。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.services.memory_service import memory_service

from .context import BuiltinToolRuntimeContext

DEFAULT_PROFILE_LIMIT = 8


def get_tool_spec(*, enabled: bool = True) -> ToolSpec:
    """获取 query_person_profile 工具声明。"""

    return ToolSpec(
        name="query_person_profile",
        description="查询人物画像。当需要查询与某人的关系时使用。",
        parameters_schema={
            "type": "object",
            "properties": {
                "person_id": {
                    "type": "string",
                    "description": "内部人物ID；明确给出时填。",
                },
                "person_name": {
                    "type": "string",
                    "description": "名称/昵称/关键词；通常填这个。",
                },
                "limit": {
                    "type": "integer",
                    "description": "证据上限。",
                    "default": DEFAULT_PROFILE_LIMIT,
                },
            },
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
        enabled=enabled,
    )


def _normalize_limit(raw_limit: Any) -> int:
    try:
        limit = int(raw_limit or DEFAULT_PROFILE_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_PROFILE_LIMIT
    return max(1, min(limit, 20))


def _extract_profile_text(payload: Dict[str, Any]) -> str:
    return str(payload.get("profile_text") or payload.get("summary") or "").strip()


def _extract_traits(profile_text: str) -> list[str]:
    traits: list[str] = []
    for line in profile_text.splitlines():
        clean_line = line.strip().strip("-").strip()
        if clean_line:
            traits.append(clean_line)
        if len(traits) >= 8:
            break
    return traits


def _build_structured_content(
    *,
    payload: Dict[str, Any],
    requested_person_id: str,
    requested_person_name: str,
    limit: int,
) -> Dict[str, Any]:
    profile_text = _extract_profile_text(payload)
    return {
        "success": bool(payload.get("success")),
        "summary": profile_text,
        "traits": _extract_traits(profile_text),
        "person_id": str(payload.get("person_id") or requested_person_id or "").strip(),
        "person_name": str(payload.get("person_name") or requested_person_name or "").strip(),
        "profile_source": str(payload.get("profile_source") or "").strip(),
        "has_manual_override": bool(payload.get("has_manual_override", False)),
        "from_cache": bool(payload.get("from_cache", False)),
        "limit": limit,
    }


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 query_person_profile 内置工具。"""

    del context
    person_id = str(invocation.arguments.get("person_id") or "").strip()
    person_name = str(invocation.arguments.get("person_name") or "").strip()
    if not person_id and not person_name:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "query_person_profile 需要提供 person_id 或 person_name 中的一个。",
        )

    limit = _normalize_limit(invocation.arguments.get("limit"))
    try:
        if person_id:
            payload = await memory_service.profile_admin(
                action="query",
                person_id=person_id,
                limit=limit,
            )
        else:
            payload = await memory_service.profile_admin(
                action="query",
                person_keyword=person_name,
                limit=limit,
            )
    except Exception as exc:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"人物画像查询失败：{exc}",
        )

    if not isinstance(payload, dict):
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "人物画像查询失败：invalid_payload",
        )

    structured_content = _build_structured_content(
        payload=payload,
        requested_person_id=person_id,
        requested_person_name=person_name,
        limit=limit,
    )
    if not bool(payload.get("success")):
        error_message = str(payload.get("error") or "未找到人物画像。").strip()
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            error_message,
            structured_content=structured_content,
        )

    profile_text = structured_content["summary"]
    if not profile_text:
        profile_text = "未找到可用的人物画像。"
    return tool_ctx.build_success_result(
        invocation.tool_name,
        profile_text,
        structured_content=structured_content,
    )

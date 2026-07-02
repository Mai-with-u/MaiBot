"""插件运行时相关 WebUI 路由。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, HTTPException

from src.plugin_runtime.component_query import component_query_service
from src.plugin_runtime.host.component_registry import CommandEntry, ComponentEntry, ComponentTypes, HomeCardEntry, ToolEntry

from .schemas import HookSpecListResponse, HookSpecResponse
from .support import find_plugin_path_by_id, require_plugin_token, validate_plugin_id

router = APIRouter()

MAX_HOME_CARD_TEXT_LENGTH = 4000
MAX_HOME_CARD_SHORT_TEXT_LENGTH = 120
MAX_HOME_CARD_BLOCKS = 20
ALLOWED_HOME_CARD_WIDTHS = {"small", "medium", "large", "wide", "full"}
ALLOWED_HOME_CARD_BLOCK_TYPES = {"markdown", "text", "stat", "key_value", "list", "actions"}
ALLOWED_HOME_CARD_LINK_PREFIXES = ("http://", "https://", "mailto:", "/")


def _ensure_installed_plugin(plugin_id: str) -> None:
    """确认插件 ID 合法且本地已安装。"""

    validate_plugin_id(plugin_id)
    if find_plugin_path_by_id(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")


def _serialize_component_entry(component: ComponentEntry) -> Dict[str, Any]:
    """将插件原始注册组件条目转换为前端展示结构。"""

    component_type = component.component_type.value.lower()
    if isinstance(component, ToolEntry) and component.legacy_component_type.upper() == ComponentTypes.ACTION.value:
        component_type = "action"

    description = str(component.metadata.get("description", "") or component.metadata.get("brief_description", "") or "")
    if isinstance(component, ToolEntry):
        description = component.description

    data: Dict[str, Any] = {
        "name": component.name,
        "description": description,
        "enabled": component.enabled,
        "plugin_name": component.plugin_id,
        "component_type": component_type,
    }

    if component_type == "action":
        data.update(
            {
                "action_parameters": dict(component.metadata.get("action_parameters") or {}),
                "action_require": list(component.metadata.get("action_require") or []),
                "associated_types": list(component.metadata.get("associated_types") or []),
                "activation_type": str(component.metadata.get("activation_type", "") or ""),
                "random_activation_probability": float(component.metadata.get("activation_probability") or 0.0),
                "activation_keywords": list(component.metadata.get("activation_keywords") or []),
                "parallel_action": bool(component.metadata.get("parallel_action", False)),
            }
        )
    elif isinstance(component, ToolEntry):
        data["parameters_schema"] = dict(component._get_parameters_schema() or {})
    elif isinstance(component, CommandEntry):
        data["pattern"] = str(component.metadata.get("command_pattern", "") or "")
        data["aliases"] = list(component.aliases)

    return data


def _truncate_text(value: Any, max_length: int = MAX_HOME_CARD_TEXT_LENGTH) -> str:
    """将插件文本字段裁剪为 WebUI 可安全展示的长度。"""

    return str(value or "").strip()[:max_length]


def _sanitize_home_card_link(value: Any) -> str:
    """仅允许 WebUI 内部路径和常见安全外链协议。"""

    normalized_value = _truncate_text(value, 500)
    if not normalized_value:
        return ""
    lowered_value = normalized_value.lower()
    if lowered_value.startswith("//"):
        return ""
    if any(lowered_value.startswith(prefix) for prefix in ALLOWED_HOME_CARD_LINK_PREFIXES):
        return normalized_value
    return ""


def _sanitize_home_card_content(value: Any) -> Any:
    """裁剪首页卡片内容，避免插件传入过大的任意 JSON。"""

    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, list):
        return [_sanitize_home_card_block(item) for item in value[:MAX_HOME_CARD_BLOCKS] if isinstance(item, dict)]
    if isinstance(value, dict):
        return _sanitize_home_card_block(value)
    return ""


def _sanitize_home_card_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """规范化单个首页卡片内容块。"""

    block_type = _truncate_text(block.get("type"), MAX_HOME_CARD_SHORT_TEXT_LENGTH).lower()
    if block_type not in ALLOWED_HOME_CARD_BLOCK_TYPES:
        block_type = "text"

    sanitized_block: Dict[str, Any] = {"type": block_type}
    for key, value in block.items():
        normalized_key = _truncate_text(key, 40)
        if normalized_key in {"type", ""}:
            continue
        if normalized_key in {"url", "href", "link_url"}:
            sanitized_block[normalized_key] = _sanitize_home_card_link(value)
        elif isinstance(value, str):
            sanitized_block[normalized_key] = _truncate_text(value)
        elif isinstance(value, (int, float, bool)):
            sanitized_block[normalized_key] = value
        elif isinstance(value, list):
            sanitized_block[normalized_key] = [
                _truncate_text(item, MAX_HOME_CARD_SHORT_TEXT_LENGTH)
                if not isinstance(item, dict)
                else _sanitize_home_card_block(item)
                for item in value[:MAX_HOME_CARD_BLOCKS]
            ]
        elif isinstance(value, dict):
            sanitized_block[normalized_key] = {
                _truncate_text(child_key, 40): _truncate_text(child_value, MAX_HOME_CARD_SHORT_TEXT_LENGTH)
                for child_key, child_value in list(value.items())[:MAX_HOME_CARD_BLOCKS]
            }
    return sanitized_block


def _serialize_home_card_entry(component: HomeCardEntry) -> Dict[str, Any]:
    """将首页卡片组件转换为前端展示结构。"""

    metadata = component.metadata
    width = _truncate_text(metadata.get("width", component.width), 20).lower()
    if width not in ALLOWED_HOME_CARD_WIDTHS:
        width = "medium"

    return {
        "id": f"plugin:{component.plugin_id}:{component.name}",
        "name": component.name,
        "plugin_id": component.plugin_id,
        "title": _truncate_text(metadata.get("title", component.title), MAX_HOME_CARD_SHORT_TEXT_LENGTH),
        "description": _truncate_text(metadata.get("description", component.description), 300),
        "content": _sanitize_home_card_content(metadata.get("content", "")),
        "link_url": _sanitize_home_card_link(metadata.get("link_url", "")),
        "link_label": _truncate_text(metadata.get("link_label", ""), 40),
        "icon": _truncate_text(metadata.get("icon", ""), 40),
        "width": width,
        "order": component.order,
        "enabled": component.enabled,
    }


@router.get("/runtime/plugins/{plugin_id}/components")
async def list_plugin_components(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """返回指定插件当前注册的全部组件。"""

    require_plugin_token(maibot_session)
    _ensure_installed_plugin(plugin_id)

    components = []
    for supervisor in component_query_service._iter_supervisors():
        for component in supervisor.component_registry.get_components_by_plugin(plugin_id, enabled_only=False):
            if component.component_type not in {ComponentTypes.COMMAND, ComponentTypes.TOOL}:
                continue
            components.append(_serialize_component_entry(component))

    return {"success": True, "components": components}


@router.get("/runtime/home-cards")
async def list_runtime_home_cards(maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """返回当前已启用插件注册的 WebUI 首页卡片。"""

    require_plugin_token(maibot_session)

    cards: List[Dict[str, Any]] = []
    for supervisor in component_query_service._iter_supervisors():
        for component in supervisor.component_registry.get_components_by_type(
            ComponentTypes.HOME_CARD.value,
            enabled_only=True,
        ):
            if isinstance(component, HomeCardEntry):
                cards.append(_serialize_home_card_entry(component))

    cards.sort(key=lambda card: (int(card.get("order", 1000)), str(card.get("plugin_id", "")), str(card.get("name", ""))))
    return {"success": True, "cards": cards}


@router.get("/runtime/hooks", response_model=HookSpecListResponse)
async def list_runtime_hook_specs(maibot_session: Optional[str] = Cookie(None)) -> HookSpecListResponse:
    """返回当前插件运行时公开的 Hook 规格清单。

    Args:
        maibot_session: 当前 WebUI 会话令牌。

    Returns:
        HookSpecListResponse: Hook 规格列表响应。
    """

    require_plugin_token(maibot_session)
    hooks = [HookSpecResponse(**hook_data) for hook_data in component_query_service.list_hook_specs()]
    return HookSpecListResponse(success=True, hooks=hooks)

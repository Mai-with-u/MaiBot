"""按真实私聊选择轻量模式，并收窄 Planner 的工具协议。"""

from copy import deepcopy
from typing import Any, Dict, List, Sequence

from src.chat.message_receive.chat_manager import chat_manager
from src.config.config import global_config
from src.core.tooling import ToolSpec


LIGHTWEIGHT_TOOL_PARAMETERS = {
    "query_memory": ("query",),
    "query_person_profile": ("person_name",),
    "view_forward_message": ("msg_id", "path"),
    "tool_search": ("query",),
    "wait": (),
    "schedule": ("action", "text", "delay_seconds", "due_at", "todo_id", "include_completed"),
}


def is_lightweight_private_chat(session_id: str) -> bool:
    """只匹配已注册私聊，不计算或查询替代会话 ID。"""

    targets = global_config.experimental.lightweight_private_chats
    if not targets:
        return False
    session = chat_manager.get_session_by_session_id(session_id)
    if session is None or session.is_group_session:
        return False
    return any(
        target.rule_type == "private"
        and target.platform.strip() in {"*", session.platform}
        and target.item_id.strip() in {"*", session.user_id}
        for target in targets
    )


def build_lightweight_tools(specs: Sequence[ToolSpec]) -> List[Dict[str, Any]]:
    """复用已通过可用性检查的内置工具，隐藏高级检索参数。"""

    definitions: List[Dict[str, Any]] = []
    for spec in specs:
        if not spec.enabled or spec.provider_name != "maisaka_builtin" or spec.name not in LIGHTWEIGHT_TOOL_PARAMETERS:
            continue
        definition = deepcopy(spec.to_llm_definition())
        schema = definition["parameters_schema"]
        schema["properties"] = {
            name: schema["properties"][name] for name in LIGHTWEIGHT_TOOL_PARAMETERS[spec.name]
        }
        schema["additionalProperties"] = False
        if spec.name == "wait":
            definition["description"] = "不发送消息，静默结束本轮思考并等待新消息；无需回复时单独调用，不填参数。"
        if spec.name == "query_memory":
            schema["required"] = ["query"]
        elif spec.name == "query_person_profile":
            schema["required"] = ["person_name"]
        definitions.append(definition)
    return definitions

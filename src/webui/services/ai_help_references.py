"""答疑组合检索中的少量维护者核实参考，补足官方文档未覆盖的具体接入与运行时行为。"""

from typing import Dict, List, Tuple

from .ai_search_models import AISearchSource


# 每组至少命中一个词才返回该条参考，避免把通用排障资料塞进无关问答。
_REFERENCES: Dict[str, Tuple[Tuple[Tuple[str, ...], ...], AISearchSource, str]] = {
    "help:ollama": (
        (("ollama",),),
        AISearchSource(title="Ollama · OpenAI compatibility", url="https://docs.ollama.com/api/openai-compatibility"),
        "For a local Ollama service on the same host as the MaiBot process, the OpenAI-compatible Base URL "
        "is http://localhost:11434/v1/ (also http://127.0.0.1:11434/v1/). If MaiBot runs in a separate container "
        "or on another host, use an Ollama address reachable from MaiBot; localhost refers to MaiBot's own network "
        "namespace. Ollama's SDK examples use a dummy API key, which the local Ollama service ignores. "
        "In MaiBot WebUI → 模型配置 / Model Management, add the provider with client_type=openai, auth_type=none "
        "and an empty api_key. Add a model with the actual Ollama model_identifier and set its api_provider to "
        "that provider. Use the provider connection test, then send a model test request. Assign the model to a "
        "task only after confirming that the request succeeds.",
    ),
    "help:nginx-websocket": (
        (("nginx", "反代", "reverse proxy", "リバースプロキシ"), ("websocket", "ws://", "wss", "握手", "https")),
        AISearchSource(title="NGINX · WebSocket proxying", url="https://nginx.org/en/docs/http/websocket.html"),
        "If an HTTPS WebUI tries ws:// and the browser blocks mixed content, first inspect the actual URL in "
        "Developer Tools → Network → WS. The browser-facing connection must use wss://; fix the client or public "
        "URL before debugging the upstream handshake. When wss:// reaches NGINX, check that the same proxy route "
        "forwards Upgrade and Connection headers. The linked NGINX documentation supports this proxy step; "
        "the mixed-content diagnosis follows from the reported browser URL. A successful upgrade returns HTTP 101 Switching "
        "Protocols. Verify a live log update or chat event after the handshake.",
    ),
    "help:group-reply": (
        (("群聊", "群消息", "group chat", "グループ"), ("不回复", "不回", "禁用", "暂停", "no reply", "返信しない")),
        AISearchSource(
            title="MaiBot · 群聊静默处理",
            url="https://github.com/Mai-with-u/MaiBot/blob/5553542622d1a8d96a9ab73484ce3530dca202d4/src/maisaka/reasoning_engine.py#L1127",
        ),
        "MaiBot can keep receiving group messages even when the effective group reply frequency is 0. "
        "In this case it consumes messages and maintains history without entering Planner. In WebUI, open the "
        "actual group conversation settings and check its effective reply frequency before changing adapter rules. "
        "Adapter filtering is a separate stage and can prevent messages reaching the bot at all. If the frequency "
        "is above 0, reproduce once and correlate the group message with Planner and Replyer requests. A recorded "
        "timeout or provider error on a request establishes a model failure; an absent request alone does not.",
    ),
}


def match_help_references(query: str) -> List[Dict[str, str]]:
    normalized_query = query.casefold()
    return [
        {"source_id": source_id, "title": source.title, "url": source.url, "content": content}
        for source_id, (groups, source, content) in _REFERENCES.items()
        if all(any(term in normalized_query for term in group) for group in groups)
    ]


def get_help_reference(source_id: str) -> AISearchSource | None:
    reference = _REFERENCES.get(source_id)
    return reference[1] if reference is not None else None

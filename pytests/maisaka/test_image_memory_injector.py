from types import SimpleNamespace

import asyncio
import pytest

from src.maisaka.memory import image_injector as image_injector_module


@pytest.mark.asyncio
async def test_image_memory_injector_uses_current_occurrence_and_expands_memory(monkeypatch) -> None:
    captured: dict[str, object] = {}
    component = SimpleNamespace(binary_hash="sha256-current")
    message = SimpleNamespace(
        message_id="message-1",
        raw_message=SimpleNamespace(components=[component]),
    )

    monkeypatch.setattr(
        image_injector_module,
        "iter_message_image_components",
        lambda components: iter([("0", components[0])]),
    )

    async def fake_image_memory(*, action: str, **kwargs):
        captured["action"] = action
        captured.update(kwargs)
        # 秒级远程嵌入不应被注入层原有的180毫秒预算提前截断。
        await asyncio.sleep(0.25)
        return {
            "success": True,
            "hits": [
                {
                    "match_kind": "visual_similar",
                    "similarity": 0.91,
                    "observations": [{"text": "照片里是团团"}],
                    "related_memories": [
                        {"target_type": "paragraph", "target_id": "p1", "content": "团团喜欢睡在窗边。"}
                    ],
                }
            ],
        }

    monkeypatch.setattr(image_injector_module.memory_service, "image_memory", fake_image_memory)

    result = await image_injector_module.image_memory_injector.build_injection_message(
        session_id="chat-real",
        source_messages=[message],
    )

    assert "照片里是团团" in result
    assert "团团喜欢睡在窗边" in result
    assert captured["action"] == "search"
    assert "timeout_ms" not in captured
    assert captured["chat_id"] == "chat-real"
    assert captured["content_hash"] == "sha256-current"
    assert captured["current_external_ref"] == "chat:chat-real:message-1:0"


@pytest.mark.asyncio
async def test_image_memory_injector_does_not_hide_service_timeout(monkeypatch) -> None:
    component = SimpleNamespace(binary_hash="sha256-current")
    message = SimpleNamespace(message_id="message-1", raw_message=SimpleNamespace(components=[component]))
    monkeypatch.setattr(
        image_injector_module,
        "iter_message_image_components",
        lambda components: iter([("0", components[0])]),
    )

    async def failing_image_memory(**kwargs):
        raise TimeoutError("模型请求超时")

    monkeypatch.setattr(image_injector_module.memory_service, "image_memory", failing_image_memory)
    with pytest.raises(TimeoutError, match="模型请求超时"):
        await image_injector_module.image_memory_injector.build_injection_message(
            session_id="chat-real", source_messages=[message],
        )

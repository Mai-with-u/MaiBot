from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.common.data_models.image_data_model import MaiEmoji
from src.emoji_system import emoji_manager as emoji_manager_module


class _PromptTemplate:
    def __init__(self) -> None:
        self.contexts: dict[str, str] = {}

    def add_context(self, name: str, value: str) -> None:
        self.contexts[name] = value


class _PromptManager:
    def __init__(self) -> None:
        self.template = _PromptTemplate()

    def get_prompt(self, prompt_name: str) -> _PromptTemplate:
        assert prompt_name == "emoji_content_analysis"
        return self.template

    async def render_prompt(self, prompt: _PromptTemplate) -> str:
        return f"rendered:{prompt.contexts['image_type']}"


class _VLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str, str, str]] = []

    async def generate_response_for_image(
        self,
        prompt: str,
        image_base64: str,
        image_format: str,
        *,
        session_id: str,
    ) -> SimpleNamespace:
        self.calls.append((prompt, image_base64, image_format, session_id))
        return SimpleNamespace(response=self.response)


def _build_emoji(tmp_path: Path, image_format: str) -> MaiEmoji:
    image_path = tmp_path / f"emoji.{image_format}"
    image_bytes = b"source-image"
    image_path.write_bytes(image_bytes)
    emoji = MaiEmoji(image_path, image_bytes=image_bytes)
    emoji.file_hash = "emoji-hash"
    emoji.image_format = image_format
    return emoji


def _patch_description_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: str,
) -> tuple[_PromptManager, _VLMClient]:
    prompt_manager = _PromptManager()
    vlm_client = _VLMClient(response)
    hook_result = SimpleNamespace(aborted=False, kwargs={})
    runtime_manager = SimpleNamespace(invoke_hook=lambda *args, **kwargs: None)

    async def invoke_hook(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return hook_result

    runtime_manager.invoke_hook = invoke_hook
    monkeypatch.setattr(emoji_manager_module, "_is_vlm_task_configured", lambda: True)
    monkeypatch.setattr(emoji_manager_module, "_get_runtime_manager", lambda: runtime_manager)
    monkeypatch.setattr(emoji_manager_module, "prompt_manager", prompt_manager)
    monkeypatch.setattr(emoji_manager_module, "emoji_manager_vlm", vlm_client)
    return prompt_manager, vlm_client


@pytest.mark.asyncio
async def test_build_emoji_description_uses_static_analysis_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_manager, vlm_client = _patch_description_dependencies(
        monkeypatch,
        response="开心, 开心, 打招呼",
    )
    emoji = _build_emoji(tmp_path, "png")

    success, result = await emoji_manager_module.emoji_manager.build_emoji_description(
        emoji,
        session_id="session-static",
    )

    assert success is True
    assert result is emoji
    assert prompt_manager.template.contexts == {"image_type": "STATIC"}
    assert vlm_client.calls == [
        ("rendered:STATIC", "c291cmNlLWltYWdl", "png", "session-static"),
    ]
    assert emoji.description == "开心,打招呼"
    assert emoji.emotion == ["开心", "打招呼"]


@pytest.mark.asyncio
async def test_build_emoji_description_uses_gif_analysis_prompt_after_frame_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_manager, vlm_client = _patch_description_dependencies(
        monkeypatch,
        response="惊讶,围观",
    )
    emoji = _build_emoji(tmp_path, "gif")
    converted_bytes = b"converted-frames"
    conversion_calls: list[bytes] = []

    def convert_gif(image_bytes: bytes) -> bytes:
        conversion_calls.append(image_bytes)
        return converted_bytes

    monkeypatch.setattr(emoji_manager_module.ImageUtils, "gif_2_static_image", convert_gif)

    success, result = await emoji_manager_module.emoji_manager.build_emoji_description(
        emoji,
        session_id="session-gif",
    )

    assert success is True
    assert result is emoji
    assert conversion_calls == [b"source-image"]
    assert prompt_manager.template.contexts == {"image_type": "GIF"}
    assert vlm_client.calls == [
        ("rendered:GIF", "Y29udmVydGVkLWZyYW1lcw==", "jpg", "session-gif"),
    ]
    assert emoji.description == "惊讶,围观"
    assert emoji.emotion == ["惊讶", "围观"]

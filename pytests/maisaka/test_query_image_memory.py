"""query_image_memory 的工具参数必须对模型可见（上游 issue #2242）。"""

from types import SimpleNamespace
from typing import Any

import pytest

from src.common.data_models.message_component_data_model import ImageComponent, TextComponent
from src.core.tooling import ToolInvocation
from src.maisaka.builtin_tool import query_image_memory as query_image_memory_module
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.builtin_tool.query_image_memory import get_tool_spec, handle_tool, resolve_message_image


def _image(content_hash: str, content: str = "") -> ImageComponent:
    """构造带哈希的图片组件。"""

    return ImageComponent(binary_hash=content_hash, content=content)


def _message(*components: Any, message_id: str = "1924122927") -> Any:
    """构造只带消息编号与组件列表的假消息。"""

    return SimpleNamespace(message_id=message_id, raw_message=SimpleNamespace(components=list(components)))


def _tool_ctx(session_id: str = "chat-1") -> BuiltinToolRuntimeContext:
    """构造工具处理器需要的最小运行上下文。"""

    return BuiltinToolRuntimeContext(SimpleNamespace(), SimpleNamespace(session_id=session_id))


def _invocation(**arguments: Any) -> ToolInvocation:
    """构造 query_image_memory 的工具调用。"""

    return ToolInvocation(tool_name="query_image_memory", arguments=arguments, call_id="call-image")


def test_tool_schema_only_requires_values_the_model_can_see() -> None:
    """component_path 是服务端派生的组件树下标，模型无从获得，不能作为必填参数。"""

    schema = get_tool_spec().parameters_schema

    assert schema["required"] == ["message_id"]
    properties = schema["properties"]
    assert "component_path" not in properties
    assert properties["image_index"]["default"] == 0
    assert properties["image_index"]["minimum"] == 0


def test_resolve_message_image_counts_images_instead_of_all_components() -> None:
    """文本等组件会占用组件树下标，模型看到的图片序号只按图片出现顺序计数。"""

    components = [TextComponent("可惜是烤鸭拌饭"), _image("hash-a"), _image("hash-b")]

    assert resolve_message_image(components, 0)[0] == "1"
    assert resolve_message_image(components, 1)[0] == "2"
    assert resolve_message_image([_image("hash-only")], 0)[0] == "0"


def test_resolve_message_image_rejects_message_without_image() -> None:
    """消息中没有图片时返回可行动的错误。"""

    with pytest.raises(ValueError, match="没有图片"):
        resolve_message_image([TextComponent("没有泡面了")], 0)


def test_resolve_message_image_rejects_out_of_range_index() -> None:
    """序号越界时报错并给出该消息的图片总数。"""

    with pytest.raises(ValueError, match="共有 1 张图片"):
        resolve_message_image([TextComponent("文字"), _image("hash-a")], 1)


@pytest.mark.asyncio
async def test_handle_tool_uses_image_index_to_build_component_path(monkeypatch) -> None:
    """复现 #2242：模型只需提供 msg_id 与图片序号，工具内部换算成持久化 component_path。"""

    captured: dict[str, Any] = {}

    async def fake_image_memory(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "hits": [
                {
                    "match_kind": "exact_hash",
                    "similarity": 1.0,
                    "observations": [{"text": "照片里是泡面"}],
                    "related_memories": [],
                }
            ],
        }

    monkeypatch.setattr(
        query_image_memory_module,
        "find_messages",
        lambda **kwargs: [_message(TextComponent("没有泡面了"), _image("hash-noodle"), message_id="779652950")],
    )
    monkeypatch.setattr(query_image_memory_module.memory_service, "image_memory", fake_image_memory)

    result = await handle_tool(_tool_ctx(), _invocation(message_id="779652950", image_index=0))

    assert result.success
    assert captured["content_hash"] == "hash-noodle"
    assert captured["current_external_ref"] == "chat:chat-1:779652950:1"
    assert "照片里是泡面" in result.content


@pytest.mark.asyncio
async def test_handle_tool_reports_message_without_image(monkeypatch) -> None:
    """模型选错消息时应得到可行动的错误，而不是先报 component_path 缺失。"""

    async def unexpected_image_memory(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("没有图片的消息不应发起图片记忆检索")

    monkeypatch.setattr(
        query_image_memory_module,
        "find_messages",
        lambda **kwargs: [_message(TextComponent("可惜是烤鸭拌饭"))],
    )
    monkeypatch.setattr(query_image_memory_module.memory_service, "image_memory", unexpected_image_memory)

    result = await handle_tool(_tool_ctx(), _invocation(message_id="1924122927"))

    assert not result.success
    assert "没有图片" in result.error_message


@pytest.mark.parametrize("raw_image_index", [1.9, 1.0, True, "1.9", -1, "abc"])
@pytest.mark.asyncio
async def test_handle_tool_rejects_non_integer_image_index(monkeypatch, raw_image_index: Any) -> None:
    """非整数序号必须报错，不能被 int() 截断后静默查询另一张图片。"""

    async def unexpected_image_memory(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("非法的 image_index 不应发起图片记忆检索")

    monkeypatch.setattr(
        query_image_memory_module,
        "find_messages",
        lambda **kwargs: [_message(TextComponent("文字"), _image("hash-first"), _image("hash-second"))],
    )
    monkeypatch.setattr(query_image_memory_module.memory_service, "image_memory", unexpected_image_memory)

    result = await handle_tool(_tool_ctx(), _invocation(message_id="779652950", image_index=raw_image_index))

    assert not result.success
    assert "image_index" in result.error_message


@pytest.mark.asyncio
async def test_handle_tool_accepts_integer_string_image_index(monkeypatch) -> None:
    """模型偶尔把数字写成字符串，整数字符串继续兼容并按序号定位。"""

    captured: dict[str, Any] = {}

    async def fake_image_memory(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "hits": []}

    monkeypatch.setattr(
        query_image_memory_module,
        "find_messages",
        lambda **kwargs: [
            _message(TextComponent("文字"), _image("hash-first"), _image("hash-second"), message_id="779652950")
        ],
    )
    monkeypatch.setattr(query_image_memory_module.memory_service, "image_memory", fake_image_memory)

    result = await handle_tool(_tool_ctx(), _invocation(message_id="779652950", image_index="1"))

    assert result.success
    assert captured["current_external_ref"] == "chat:chat-1:779652950:2"

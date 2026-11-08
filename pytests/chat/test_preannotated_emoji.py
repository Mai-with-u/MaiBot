"""Test the media-processing method without starting the application services."""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import ast
import asyncio
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class Emoji:
    def __init__(self, content, binary_data):
        self.content = content
        self.binary_data = binary_data


def load_size_policy(max_size_mb):
    source = ROOT / "src/emoji_system/emoji_manager.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_get_max_collected_emoji_bytes", "_is_collected_emoji_size_allowed"}
    ]
    constant = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BYTES_PER_MIB" for target in node.targets)
    )
    namespace = {"global_config": SimpleNamespace(emoji=SimpleNamespace(max_emoji_size_mb=max_size_mb))}
    exec(compile(ast.Module(body=[constant, *nodes], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_is_collected_emoji_size_allowed"]


def load_process_methods():
    source = ROOT / "src/chat/message_receive/message.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SessionMessage")
    names = {
        "process",
        "process_single_component",
        "process_emoji_component",
        "_remove_voice_placeholder_text_components",
    }
    methods = [
        node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "EmojiComponent": Emoji,
        "MsgIDMapping": SimpleNamespace,
        "StandardMessageComponents": object,
        "List": list,
        "asyncio": asyncio,
        "logger": SimpleNamespace(error=lambda message: None),
    }
    namespace.update({name: type(name, (), {}) for name in ["TextComponent", "ImageComponent", "VoiceComponent"]})
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
    return {name: namespace[name] for name in names}


class PreannotatedEmojiTests(unittest.IsolatedAsyncioTestCase):
    async def process(self, component, save, *, max_size_mb=1, whole_message=False):
        manager = SimpleNamespace(ensure_emoji_saved=save, get_emoji_description=AsyncMock())
        module = ModuleType("src.emoji_system.emoji_manager")
        module.emoji_manager = manager
        module._is_collected_emoji_size_allowed = load_size_policy(max_size_mb)
        methods = load_process_methods()
        with patch.dict(sys.modules, {module.__name__: module}):
            if whole_message:
                message = type("MessageUnderTest", (), methods)()
                message.session_id = "fixture-session"
                message.raw_message = SimpleNamespace(components=[component])
                await message.process()
                result = message.processed_plain_text
            else:
                result = await methods["process_emoji_component"](
                    SimpleNamespace(session_id="fixture-session"), component
                )
        manager.get_emoji_description.assert_not_awaited()
        return result

    async def test_description_does_not_skip_binary_persistence(self):
        component = SimpleNamespace(content="  [表情包: 开心]  ", binary_data=b"fixture-emoji")
        save = AsyncMock()
        result = await self.process(component, save)
        self.assertEqual(result, "[表情包: 开心]")
        save.assert_awaited_once_with(b"fixture-emoji")
        self.assertEqual(component.binary_data, b"")

    async def test_no_binary_does_not_repeat_persistence(self):
        component = SimpleNamespace(content="[表情包: 开心]", binary_data=b"")
        save = AsyncMock()
        await self.process(component, save)
        save.assert_not_awaited()

    async def test_save_failure_keeps_binary_and_surfaces_error(self):
        for error in [
            OSError("fixture storage failure"),
            ValueError("fixture invalid format"),
            RuntimeError("fixture database failure"),
        ]:
            with self.subTest(error=type(error).__name__):
                component = Emoji(content="[表情包: 开心]", binary_data=b"fixture-emoji")
                save = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    await self.process(component, save)
                self.assertIs(raised.exception, error)
                save.assert_awaited_once_with(b"fixture-emoji")
                self.assertEqual(component.binary_data, b"fixture-emoji")

    async def test_oversized_emoji_keeps_description_and_binary_without_saving(self):
        component = Emoji(content="  [表情包: 开心]  ", binary_data=b"x" * (1024 * 1024 + 1))
        original = component.binary_data
        save = AsyncMock(side_effect=ValueError("fixture collection size exceeded"))
        result = await self.process(component, save)
        self.assertEqual(result, "[表情包: 开心]")
        self.assertEqual(component.content, result)
        self.assertIs(component.binary_data, original)
        save.assert_not_awaited()

    async def test_oversized_description_reaches_processed_plain_text(self):
        component = Emoji(content="  [表情包: 开心]  ", binary_data=b"x" * (1024 * 1024 + 1))
        original = component.binary_data
        save = AsyncMock(side_effect=ValueError("fixture collection size exceeded"))
        result = await self.process(component, save, whole_message=True)
        self.assertEqual(result, "[表情包: 开心]")
        self.assertIs(component.binary_data, original)
        save.assert_not_awaited()

    async def test_exact_size_limit_is_saved(self):
        component = Emoji(content="[表情包: 开心]", binary_data=b"x" * (1024 * 1024))
        save = AsyncMock()
        await self.process(component, save)
        save.assert_awaited_once()
        self.assertEqual(component.binary_data, b"")

    async def test_fractional_size_limit_uses_existing_byte_conversion(self):
        component = Emoji(content="[表情包: 开心]", binary_data=b"x" * 6)
        save = AsyncMock(side_effect=ValueError("fixture collection size exceeded"))
        result = await self.process(component, save, max_size_mb=5.5 / (1024 * 1024))
        self.assertEqual(result, "[表情包: 开心]")
        self.assertEqual(component.binary_data, b"x" * 6)
        save.assert_not_awaited()

    async def test_zero_limit_remains_unlimited(self):
        component = Emoji(content="[表情包: 开心]", binary_data=b"x" * (1024 * 1024 + 1))
        save = AsyncMock()
        await self.process(component, save, max_size_mb=0)
        save.assert_awaited_once()
        self.assertEqual(component.binary_data, b"")


if __name__ == "__main__":
    unittest.main()

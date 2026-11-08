"""Test the media-processing method without starting the application services."""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import ast
import sys
import unittest


def load_process():
    source = Path(__file__).resolve().parents[2] / "src/chat/message_receive/message.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SessionMessage")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_emoji_component")
    namespace = {"EmojiComponent": object}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["process_emoji_component"]


class PreannotatedEmojiTests(unittest.IsolatedAsyncioTestCase):
    async def process(self, component, save):
        manager = SimpleNamespace(ensure_emoji_saved=save, get_emoji_description=AsyncMock())
        module = ModuleType("src.emoji_system.emoji_manager")
        module.emoji_manager = manager
        with patch.dict(sys.modules, {module.__name__: module}):
            result = await load_process()(SimpleNamespace(session_id="fixture-session"), component)
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
        component = SimpleNamespace(content="[表情包: 开心]", binary_data=b"fixture-emoji")
        save = AsyncMock(side_effect=OSError("fixture storage failure"))
        with self.assertRaisesRegex(OSError, "fixture storage failure"):
            await self.process(component, save)
        self.assertEqual(component.binary_data, b"fixture-emoji")


if __name__ == "__main__":
    unittest.main()

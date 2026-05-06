"""业务命名 Hook 集成测试。"""

from types import SimpleNamespace
from typing import Any

import os
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# SDK 包路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "maibot-plugin-sdk"))


class _FakeHookManager:
    """用于业务 Hook 测试的最小运行时管理器。"""

    def __init__(self, responses: dict[str, SimpleNamespace]) -> None:
        """初始化测试管理器。

        Args:
            responses: 按 Hook 名称预设的返回结果映射。
        """

        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_hook(self, hook_name: str, **kwargs: Any) -> SimpleNamespace:
        """模拟调用运行时命名 Hook。

        Args:
            hook_name: 目标 Hook 名称。
            **kwargs: 传入 Hook 的参数。

        Returns:
            SimpleNamespace: 预设的 Hook 返回结果。
        """

        self.calls.append((hook_name, dict(kwargs)))
        return self._responses.get(hook_name, SimpleNamespace(kwargs=dict(kwargs), aborted=False))


def test_builtin_hook_catalog_includes_new_business_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置 Hook 目录应包含三个业务系统新增的 Hook。"""

    monkeypatch.setattr(sys, "exit", lambda code=0: None)
    from src.plugin_runtime.hook_catalog import register_builtin_hook_specs
    from src.plugin_runtime.host.hook_spec_registry import HookSpecRegistry

    registry = HookSpecRegistry()
    hook_names = {spec.name for spec in register_builtin_hook_specs(registry)}

    assert "emoji.maisaka.before_select" in hook_names
    assert "emoji.register.after_build_emotion" in hook_names
    assert "jargon.extract.before_persist" in hook_names
    assert "jargon.query.after_search" in hook_names
    assert "expression.select.before_select" in hook_names
    assert "expression.learn.before_upsert" in hook_names


@pytest.mark.asyncio
async def test_send_emoji_for_maisaka_can_be_aborted_by_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """表情包系统应允许在选择前被 Hook 中止。"""

    from src.emoji_system import maisaka_tool

    fake_manager = _FakeHookManager(
        {
            "emoji.maisaka.before_select": SimpleNamespace(
                kwargs={"abort_message": "插件阻止了表情发送。"},
                aborted=True,
            )
        }
    )
    monkeypatch.setattr(maisaka_tool, "_get_runtime_manager", lambda: fake_manager)

    result = await maisaka_tool.send_emoji_for_maisaka(stream_id="stream-1", requested_emotion="开心", emoji_selector=lambda *a, **kw: (None, ""))

    assert result.success is False
    assert result.message == "插件阻止了表情发送。"
    assert fake_manager.calls[0][0] == "emoji.maisaka.before_select"


@pytest.mark.asyncio
async def test_jargon_extract_can_be_aborted_before_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    """黑话提取结果应允许在写库前被 Hook 中止。"""

    from src.learners.jargon_miner import JargonMiner

    fake_manager = _FakeHookManager(
        {
            "jargon.extract.before_persist": SimpleNamespace(
                kwargs={"entries": []},
                aborted=True,
            )
        }
    )
    monkeypatch.setattr(JargonMiner, "_get_runtime_manager", staticmethod(lambda: fake_manager))

    miner = JargonMiner(session_id="session-1", session_name="测试会话")
    await miner.process_extracted_entries(
        [{"content": "yyds", "raw_content": {"[1] yyds 太强了"}}],
    )

    assert fake_manager.calls[0][0] == "jargon.extract.before_persist"
    assert fake_manager.calls[0][1]["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_expression_selection_can_be_aborted_by_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """表达方式选择流程应允许在开始前被 Hook 中止。"""

    from src.learners.expression_selector import ExpressionSelector

    fake_manager = _FakeHookManager(
        {
            "expression.select.before_select": SimpleNamespace(
                kwargs={},
                aborted=True,
            )
        }
    )
    monkeypatch.setattr(ExpressionSelector, "_get_runtime_manager", staticmethod(lambda: fake_manager))
    monkeypatch.setattr(ExpressionSelector, "can_use_expression_for_chat", lambda self, chat_id: True)

    selector = ExpressionSelector()
    selected_expressions, selected_ids = await selector.select_suitable_expressions(
        chat_id="session-1",
        chat_info="用户刚刚发来一条消息。",
    )

    assert selected_expressions == []
    assert selected_ids == []
    assert fake_manager.calls[0][0] == "expression.select.before_select"

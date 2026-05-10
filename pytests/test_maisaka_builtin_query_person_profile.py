from types import SimpleNamespace
from typing import Any, Dict

import pytest

from src.core.tooling import ToolInvocation
from src.maisaka.builtin_tool import (
    get_all_builtin_tool_specs,
    query_person_profile as query_person_profile_tool,
)
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext


def _build_tool_ctx() -> BuiltinToolRuntimeContext:
    runtime = SimpleNamespace(
        session_id="session-1",
        chat_stream=SimpleNamespace(platform="qq", user_id="alice", group_id=""),
        log_prefix="[session-1]",
    )
    return BuiltinToolRuntimeContext(engine=SimpleNamespace(), runtime=runtime)


def _build_invocation(arguments: Dict[str, Any]) -> ToolInvocation:
    return ToolInvocation(
        tool_name="query_person_profile",
        arguments=dict(arguments),
        call_id="call-query-person-profile",
    )


@pytest.mark.asyncio
async def test_query_person_profile_requires_identifier() -> None:
    result = await query_person_profile_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({}),
    )

    assert result.success is False
    assert "person_id 或 person_name" in result.error_message


@pytest.mark.asyncio
async def test_query_person_profile_supports_person_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    async def fake_profile_admin(*, action: str, **kwargs: Any) -> Dict[str, Any]:
        captured["action"] = action
        captured["kwargs"] = dict(kwargs)
        return {
            "success": True,
            "person_id": "pid-alice",
            "person_name": "Alice",
            "profile_text": "Alice 喜欢咖啡。",
            "profile_source": "auto_snapshot",
            "has_manual_override": False,
            "evidence": [{"content": "不应返回"}],
        }

    monkeypatch.setattr(query_person_profile_tool.memory_service, "profile_admin", fake_profile_admin)

    result = await query_person_profile_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({"person_id": "pid-alice", "limit": 4}),
    )

    assert result.success is True
    assert captured == {
        "action": "query",
        "kwargs": {"person_id": "pid-alice", "limit": 4},
    }
    assert result.content == "Alice 喜欢咖啡。"
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["person_id"] == "pid-alice"
    assert result.structured_content["person_name"] == "Alice"
    assert result.structured_content["summary"] == "Alice 喜欢咖啡。"
    assert "evidence" not in result.structured_content


@pytest.mark.asyncio
async def test_query_person_profile_supports_person_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    async def fake_profile_admin(*, action: str, **kwargs: Any) -> Dict[str, Any]:
        captured["action"] = action
        captured["kwargs"] = dict(kwargs)
        return {
            "success": True,
            "person_id": "pid-bob",
            "person_name": "Bob",
            "summary": "- Bob 常聊游戏\n- Bob 晚上更活跃",
            "profile_source": "manual_override",
            "has_manual_override": True,
        }

    monkeypatch.setattr(query_person_profile_tool.memory_service, "profile_admin", fake_profile_admin)

    result = await query_person_profile_tool.handle_tool(
        _build_tool_ctx(),
        _build_invocation({"person_name": "Bob", "limit": 30}),
    )

    assert result.success is True
    assert captured == {
        "action": "query",
        "kwargs": {"person_keyword": "Bob", "limit": 20},
    }
    assert result.structured_content["person_id"] == "pid-bob"
    assert result.structured_content["profile_source"] == "manual_override"
    assert result.structured_content["has_manual_override"] is True
    assert result.structured_content["traits"] == ["Bob 常聊游戏", "Bob 晚上更活跃"]


def test_builtin_tool_list_contains_query_memory_and_profile_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.maisaka import builtin_tool as builtin_tool_module

    monkeypatch.setattr(
        builtin_tool_module,
        "global_config",
        SimpleNamespace(
            a_memorix=SimpleNamespace(
                integration=SimpleNamespace(
                    enable_memory_query_tool=True,
                    enable_person_profile_query_tool=True,
                )
            )
        ),
    )

    specs = get_all_builtin_tool_specs()
    names = {spec.name for spec in specs if spec.enabled}

    assert "query_memory" in names
    assert "query_person_profile" in names


def test_builtin_tool_list_respects_profile_query_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.maisaka import builtin_tool as builtin_tool_module

    monkeypatch.setattr(
        builtin_tool_module,
        "global_config",
        SimpleNamespace(
            a_memorix=SimpleNamespace(
                integration=SimpleNamespace(
                    enable_memory_query_tool=True,
                    enable_person_profile_query_tool=False,
                )
            )
        ),
    )

    specs = get_all_builtin_tool_specs()
    profile_spec = next(spec for spec in specs if spec.name == "query_person_profile")

    assert profile_spec.enabled is False

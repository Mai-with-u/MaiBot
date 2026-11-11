from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.maisaka import reasoning_engine


@pytest.mark.asyncio
async def test_context_status_tracks_parallel_injections(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = []
    logs = []
    started = asyncio.Event()
    release_profile = asyncio.Event()

    async def memory(**kwargs):
        return "记忆"

    async def profile(**kwargs):
        started.set()
        await release_profile.wait()
        return ["画像"]

    async def image(**kwargs):
        return "图片"

    monkeypatch.setattr(reasoning_engine.heuristic_memory_injector, "build_injection_message", memory)
    monkeypatch.setattr(reasoning_engine.image_memory_injector, "build_injection_message", image)
    monkeypatch.setattr(reasoning_engine, "build_person_profile_injection_messages", profile)
    monkeypatch.setattr(reasoning_engine.global_config.debug, "save_maisaka_reasoning", False)
    monkeypatch.setattr(reasoning_engine, "resolve_enable_visual_planner", lambda: True)
    monkeypatch.setattr(reasoning_engine.logger, "info", logs.append)
    engine = object.__new__(reasoning_engine.MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(
        session_id="session",
        log_prefix="[测试]",
        _chat_history=[],
        _cycle_counter=1,
        _consecutive_wait_count=0,
        _update_stage_status=lambda *args, **kwargs: statuses.append(args),
    )
    engine._build_action_tool_definitions = AsyncMock(return_value=([], ""))
    engine._refresh_jargon_reference_message = lambda: None
    engine._emit_flow_step = AsyncMock()
    engine._run_interruptible_planner = AsyncMock()

    task = asyncio.create_task(
        engine._run_planner_request(
            trigger_message=None,
            source_messages=[],
            round_index=0,
            round_text="第 1 轮",
            state=reasoning_engine.CycleRuntimeState(),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert statuses[-1] == ("组织上下文", "正在等待：人物画像")
    assert not any("上下文准备" in entry for entry in logs)
    release_profile.set()
    await task
    assert engine._run_interruptible_planner.call_args.kwargs["injected_user_messages"] == ["记忆", "画像", "图片"]
    (summary,) = [entry for entry in logs if "上下文准备" in entry]
    assert all(
        f"{name}=" in summary for name in ("总耗时", "可用工具", "黑话参考", "启发式记忆", "人物画像", "图片记忆")
    )

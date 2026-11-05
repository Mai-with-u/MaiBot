from types import SimpleNamespace

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
    monkeypatch.setattr(reasoning_engine.logger, "info", logs.append)
    engine = object.__new__(reasoning_engine.MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(
        session_id="session", log_prefix="[测试]", _update_stage_status=lambda *args, **kwargs: statuses.append(args)
    )

    task = asyncio.create_task(engine._build_planner_injected_user_messages(
        profile_message=None, source_messages=[], deferred_tools_reminder="", round_text="第 1 轮"
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert statuses[-1] == ("组织上下文", "正在等待：人物画像")
    release_profile.set()
    assert await task == ["记忆", "画像", "图片"]
    assert all(any(name in entry for entry in logs) for name in ("启发式记忆", "人物画像", "图片记忆"))

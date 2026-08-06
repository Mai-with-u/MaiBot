from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncio
import json

import pytest

from src.llm_models.payload_content.resp_format import RespFormatType
from src.maisaka.reply_effect import tracker as tracker_module
from src.maisaka.reply_effect.judge import MAX_PROMPT_CHARS, build_judge_prompt, judge_reply_effect
from src.maisaka.reply_effect.models import (
    FollowupMessageSnapshot,
    ReplyEffectRecord,
    ReplyEffectStatus,
    ReplySnapshot,
    SessionSnapshot,
    UserSnapshot,
)
from src.maisaka.reply_effect.tracker import ReplyEffectTracker
from src.maisaka.runtime import MaisakaHeartFlowChatting


class FakeStorage:
    """仅记录状态写入的内存存储，隔离数据库和 JSON 文件。"""

    def __init__(self, unfinished: list[ReplyEffectRecord] | None = None) -> None:
        self.unfinished = list(unfinished or [])
        self.save_count = 0

    def load_unfinished_records(self, session_id: str) -> list[ReplyEffectRecord]:
        assert session_id == "session"
        return list(self.unfinished)

    def load_records_by_ids(self, effect_ids: set[str]) -> list[ReplyEffectRecord]:
        return [record for record in self.unfinished if record.effect_id in effect_ids]

    def load_finalized_records(self, *, exclude_effect_id: str = "") -> list[ReplyEffectRecord]:
        return []

    def save_record(self, record: ReplyEffectRecord) -> None:
        self.save_count += 1


def build_record(effect_id: str = "effect-1", *, followup_count: int = 0) -> ReplyEffectRecord:
    now = datetime.now().astimezone()
    record = ReplyEffectRecord(
        effect_id=effect_id,
        status=ReplyEffectStatus.PENDING,
        created_at=now.isoformat(timespec="seconds"),
        updated_at=now.isoformat(timespec="seconds"),
        session=SessionSnapshot("session", "session", "test", "group", "group", "", "测试群"),
        reply=ReplySnapshot("tool", "target", True, "bot 回复", ["bot 回复"], "reason"),
        target_user=UserSnapshot("target-user", "目标用户", ""),
    )
    for index in range(followup_count):
        record.followup_messages.append(
            FollowupMessageSnapshot(
                message_id=f"message-{index}",
                timestamp=(now + timedelta(seconds=index)).isoformat(timespec="seconds"),
                user_id=f"user-{index}",
                nickname=f"用户{index}",
                cardname="",
                visible_text="后续消息",
                plain_text="后续消息",
                latency_seconds=float(index),
                is_target_user=False,
                candidate_effect_ids=[effect_id],
            )
        )
    return record


def build_judge_payload(record: ReplyEffectRecord) -> str:
    return json.dumps(
        {
            "strategy": {"primary": "answer", "secondary": [], "confidence": 0.8},
            "messages": [
                {"message_id": followup.message_id, "associations": []}
                for followup in record.followup_messages
            ],
        }
    )


def build_tracker(
    *,
    storage: FakeStorage,
    judge_runner: Any,
) -> ReplyEffectTracker:
    chat_stream = SimpleNamespace(
        platform="test",
        group_id="group",
        user_id="",
        is_group_session=True,
    )
    return ReplyEffectTracker(
        session_id="session",
        session_name="测试群",
        chat_stream=chat_stream,
        judge_runner=judge_runner,
        storage=storage,  # type: ignore[arg-type]
    )


def test_judge_prompt_has_total_limit_and_keeps_required_ids() -> None:
    record = build_record(followup_count=10)
    for followup in record.followup_messages:
        followup.visible_text = "很长的后续消息" * 100
        followup.plain_text = followup.visible_text
    record.context_snapshot = [
        {
            "message_id": "target" if index == 0 else f"context-{index}",
            "timestamp": record.created_at,
            "role": "user",
            "source": "chat",
            "text": "目标消息" if index == 0 else "很长的上下文" * 100,
        }
        for index in range(30)
    ]
    candidates = [record]
    for index in range(59):
        candidate = build_record(f"{index:036d}")
        candidate.reply.reply_text = "很长的候选回复" * 100
        candidates.append(candidate)

    prompt = build_judge_prompt(record, candidates)

    assert len(prompt) <= MAX_PROMPT_CHARS
    assert "目标消息" in prompt
    assert record.followup_messages[-1].message_id in prompt
    assert candidates[-1].effect_id in prompt
    assert prompt.endswith("}")


@pytest.mark.asyncio
async def test_transport_timeout_does_not_trigger_json_validation_retry() -> None:
    record = build_record()
    call_count = 0

    async def runner(_prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        raise TimeoutError("network timeout")

    with pytest.raises(TimeoutError, match="network timeout"):
        await judge_reply_effect(record, [record], runner)

    assert call_count == 1


@pytest.mark.asyncio
async def test_invalid_json_still_retries_once() -> None:
    record = build_record()
    responses = iter(["not-json", build_judge_payload(record)])

    async def runner(_prompt: str) -> str:
        return next(responses)

    primary, _, _, _ = await judge_reply_effect(record, [record], runner)

    assert primary == "answer"


@pytest.mark.asyncio
async def test_evaluation_is_scheduled_only_once() -> None:
    record = build_record()
    storage = FakeStorage()
    call_count = 0

    async def runner(_prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        return build_judge_payload(record)

    tracker = build_tracker(storage=storage, judge_runner=runner)
    tracker._started = True
    tracker._pending_records[record.effect_id] = record
    tracker._tracked_records[record.effect_id] = record

    first, second = await asyncio.gather(
        tracker._schedule_evaluation(record.effect_id, "session_followups_limit"),
        tracker._schedule_evaluation(record.effect_id, "session_followups_limit"),
    )
    assert first is not None or second is not None
    await tracker.wait_for_idle()

    assert call_count == 1
    assert record.status == ReplyEffectStatus.FINALIZED


@pytest.mark.asyncio
async def test_evaluation_concurrency_is_limited_to_two() -> None:
    records = [build_record(f"effect-{index}") for index in range(3)]
    storage = FakeStorage()
    active_count = 0
    max_active_count = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def runner(_prompt: str) -> str:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        if active_count == 2:
            two_started.set()
        await release.wait()
        active_count -= 1
        return build_judge_payload(records[0])

    tracker = build_tracker(storage=storage, judge_runner=runner)
    tracker._started = True
    for record in records:
        tracker._pending_records[record.effect_id] = record
        tracker._tracked_records[record.effect_id] = record
        await tracker._schedule_evaluation(record.effect_id, "session_followups_limit")

    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert max_active_count == 2
    release.set()
    await tracker.wait_for_idle()

    assert all(record.status == ReplyEffectStatus.FINALIZED for record in records)


@pytest.mark.asyncio
async def test_evaluation_total_timeout_covers_json_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    record = build_record()
    storage = FakeStorage()

    async def runner(_prompt: str) -> str:
        await asyncio.sleep(1)
        return "not-json"

    monkeypatch.setattr(tracker_module, "EVALUATION_TOTAL_TIMEOUT_SECONDS", 0.01)
    tracker = build_tracker(storage=storage, judge_runner=runner)
    tracker._started = True
    tracker._pending_records[record.effect_id] = record
    tracker._tracked_records[record.effect_id] = record

    await tracker.finalize(record.effect_id, "session_followups_limit")

    assert record.status == ReplyEffectStatus.EVALUATION_FAILED
    assert record.evaluation_error == "回复效果评审超过总时限 0.01 秒"


@pytest.mark.asyncio
async def test_provider_timeout_is_not_reported_as_total_timeout() -> None:
    record = build_record()
    storage = FakeStorage()

    async def runner(_prompt: str) -> str:
        raise TimeoutError("provider timeout")

    tracker = build_tracker(storage=storage, judge_runner=runner)
    tracker._started = True
    tracker._pending_records[record.effect_id] = record
    tracker._tracked_records[record.effect_id] = record

    await tracker.finalize(record.effect_id, "session_followups_limit")

    assert record.status == ReplyEffectStatus.EVALUATION_FAILED
    assert record.evaluation_error == "provider timeout"


@pytest.mark.asyncio
async def test_start_restores_ready_pending_record() -> None:
    record = build_record(followup_count=10)
    storage = FakeStorage([record])

    async def runner(_prompt: str) -> str:
        return build_judge_payload(record)

    tracker = build_tracker(storage=storage, judge_runner=runner)
    await tracker.start()
    await tracker.wait_for_idle()

    assert record.status == ReplyEffectStatus.FINALIZED
    assert record.finalize_reason == "session_followups_limit"


@pytest.mark.asyncio
async def test_reply_effect_judge_uses_isolated_utils_json_request() -> None:
    captured: dict[str, Any] = {}

    async def run_sub_agent(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(content="{}")

    runtime = SimpleNamespace(run_sub_agent=run_sub_agent)
    result = await MaisakaHeartFlowChatting._run_reply_effect_judge(runtime, "judge prompt")  # type: ignore[arg-type]

    assert result == "{}"
    assert captured["model_task_name"] == "utils"
    assert captured["include_parent_context"] is False
    assert captured["response_format"].format_type == RespFormatType.JSON_OBJ

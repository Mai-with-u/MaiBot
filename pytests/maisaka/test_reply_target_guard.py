from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import MessageSequence, ReplyComponent, TextComponent
from src.core.tooling import ToolExecutionResult
from src.maisaka.builtin_tool.reply import (
    _build_reply_failure,
    _build_stale_reply_target_failure,
    _find_latest_user_message,
    _find_recent_reply_to_target,
    _find_stale_reply_target,
)
from src.llm_models.payload_content.context_item import get_item_text
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


_TIMESTAMP = datetime(2026, 11, 9, 18, 27)


def _build_message(
    message_id: str,
    text: str,
    *,
    source_kind: str = "user",
    quote_target_id: str = "",
    timestamp: datetime = _TIMESTAMP,
) -> SessionBackedMessage:
    """构造带可选引用组件的真实 IM 上下文消息。"""

    components = [TextComponent(text)]
    if quote_target_id:
        components.append(ReplyComponent(quote_target_id))
    sequence = MessageSequence(components)
    original = SessionMessage(message_id=message_id, timestamp=timestamp, platform="test")
    return SessionBackedMessage(
        raw_message=sequence,
        visible_text=text,
        timestamp=timestamp,
        message_id=message_id,
        original_message=original,
        source_kind=source_kind,
    )


def _build_history() -> List[LLMContextMessage]:
    """构造“已回复旧消息后又出现新用户消息”的聊天记录。"""

    return [
        _build_message("1001", "第一条用户消息", timestamp=datetime(2026, 11, 9, 18, 27, 8)),
        _build_message(
            "1002",
            "已回复第一条",
            source_kind="guided_reply",
            quote_target_id="1001",
            timestamp=datetime(2026, 11, 9, 18, 27, 18),
        ),
        _build_message("1003", "第二条用户消息", timestamp=datetime(2026, 11, 9, 18, 27, 38)),
    ]


def test_find_latest_user_message_ignores_bot_replies() -> None:
    latest = _find_latest_user_message(_build_history())
    assert latest is not None
    assert latest.message_id == "1003"


def test_find_latest_user_message_skips_notifications() -> None:
    history = _build_history()
    notification = _build_message(
        "1004", "平台通知：戳一戳", timestamp=datetime(2026, 11, 9, 18, 27, 40),
    )
    notification.original_message.is_notify = True
    history.append(notification)
    latest = _find_latest_user_message(history)
    assert latest is not None
    assert latest.message_id == "1003"


def test_find_recent_reply_to_target_requires_guided_reply() -> None:
    history = [
        _build_message("1001", "第一条用户消息"),
        _build_message("1002", "用户引用但非 bot 回复", quote_target_id="1001"),
        _build_message("1003", "已回复第一条", source_kind="guided_reply", quote_target_id="1001"),
    ]
    assert _find_recent_reply_to_target(history, "1001") == "已回复第一条"
    assert _find_recent_reply_to_target(history, "9999") == ""


def test_find_stale_reply_target_returns_newer_user_message() -> None:
    stale_target = _find_stale_reply_target(_build_history(), "1001", "已回复第一条")
    assert stale_target is not None
    assert stale_target.message_id == "1003"


def test_find_stale_reply_target_keeps_supplement_to_latest_message() -> None:
    history = _build_history()
    assert _find_stale_reply_target(history, "1003", "") is None
    assert _find_stale_reply_target(history, "1003", "已回复第三条") is None


def test_find_stale_reply_target_keeps_supplement_after_newer_message() -> None:
    history = [
        _build_message("1001", "第一条用户消息", timestamp=datetime(2026, 11, 9, 18, 27, 8)),
        _build_message("1003", "第二条用户消息", timestamp=datetime(2026, 11, 9, 18, 27, 38)),
        _build_message(
            "1004",
            "补充回复第一条",
            source_kind="guided_reply",
            quote_target_id="1001",
            timestamp=datetime(2026, 11, 9, 18, 27, 50),
        ),
    ]
    assert _find_stale_reply_target(history, "1001", "补充回复第一条") is None


def test_build_stale_reply_target_failure_suggests_latest_message() -> None:
    result = _build_stale_reply_target_failure(
        _StubToolContext(), _build_history(), "1001", "已回复第一条", {},
    )
    assert result is not None
    assert result.structured_content["error_code"] == "stale_reply_target"
    assert result.structured_content["suggested_msg_id"] == "1003"


class _StubRuntime:
    log_prefix = ""


class _StubToolContext:
    runtime = _StubRuntime()

    def build_failure_result(
        self,
        tool_name: str,
        message: str,
        *,
        structured_content: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> ToolExecutionResult:
        """返回与真实工具上下文一致的最小失败结果。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=message,
            structured_content=structured_content,
            metadata=metadata or {},
        )


def test_build_reply_failure_keeps_suggested_target_and_suppress_reason() -> None:
    result = _build_reply_failure(
        _StubToolContext(),
        "1001",
        "stale_reply_target",
        "目标已回复过",
        {},
        extra_content={"suggested_msg_id": "1003", "suppress_reason": "duplicate_turn"},
    )
    assert result.structured_content == {
        "msg_id": "1001",
        "error_code": "stale_reply_target",
        "reason": "目标已回复过",
        "suggested_msg_id": "1003",
        "suppress_reason": "duplicate_turn",
        "message": "目标已回复过",
    }


def test_record_reply_failure_counts_same_target_and_error() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._reply_failure_counts = {}
    result = ToolExecutionResult(
        tool_name="reply",
        success=False,
        error_message="插件抑制",
        structured_content={"msg_id": "1001", "error_code": "plugin_empty_response"},
    )
    assert engine._record_reply_failure(result) == (1, "1001", "plugin_empty_response")
    assert engine._record_reply_failure(result) == (2, "1001", "plugin_empty_response")

    other_target = ToolExecutionResult(
        tool_name="reply",
        success=False,
        error_message="插件抑制",
        structured_content={"msg_id": "1003", "error_code": "plugin_empty_response"},
    )
    assert engine._record_reply_failure(other_target) == (1, "1003", "plugin_empty_response")


def test_record_reply_failure_ignores_parameter_errors() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._reply_failure_counts = {}
    result = ToolExecutionResult(
        tool_name="reply",
        success=False,
        error_message="after_reply 必须为 continue、stop 或 wait。",
    )
    assert engine._record_reply_failure(result) == (0, "", "")
    assert engine._reply_failure_counts == {}


def test_clear_reply_failure_counts_removes_all_errors_for_target() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._reply_failure_counts = {
        ("1001", "plugin_empty_response"): 1,
        ("1001", "stale_reply_target"): 1,
        ("1003", "plugin_empty_response"): 1,
    }
    engine._clear_reply_failure_counts("1001")
    assert engine._reply_failure_counts == {("1003", "plugin_empty_response"): 1}


def test_cycle_end_for_reply_failure_limit() -> None:
    cycle_end = MaisakaReasoningEngine._cycle_end_for_pause_tool("reply_failure_limit")
    assert cycle_end.reason == "reply_failure_limit"
    assert "连续失败" in cycle_end.detail


def test_append_stale_reply_replan_reminder_mentions_suggested_id() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._active_logical_turn_id = "turn-1"
    engine._runtime = SimpleNamespace(_chat_history=[])
    engine._append_stale_reply_replan_reminder("1003")
    assert len(engine._runtime._chat_history) == 1
    assert "1003" in get_item_text(engine._runtime._chat_history[0].output_item)


def test_grant_stale_reply_extra_round_only_once_at_limit() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(_max_internal_rounds=16)
    engine._stale_reply_extra_round_granted = 0

    assert engine._grant_stale_reply_extra_round(14, 1) is False
    assert engine._stale_reply_extra_round_granted == 0
    assert engine._grant_stale_reply_extra_round(15, 1) is True
    assert engine._stale_reply_extra_round_granted == 1
    assert engine._grant_stale_reply_extra_round(16, 1) is False


@pytest.mark.asyncio
async def test_start_cycle_round_logs_effective_total_rounds() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._stale_reply_extra_round_granted = 1
    cycle_detail = SimpleNamespace(cycle_id=7)
    engine._start_cycle = Mock(return_value=cycle_detail)
    engine._emit_flow_step = AsyncMock()
    runtime = SimpleNamespace(
        _max_internal_rounds=16,
        _log_cycle_started=Mock(),
        _update_stage_status=Mock(),
    )
    engine._runtime = runtime

    returned_detail, round_text = await engine._start_cycle_round(16)

    assert returned_detail is cycle_detail
    assert round_text == "第 17/17 轮"
    runtime._log_cycle_started.assert_called_once_with(cycle_detail, 16, 17)


def test_grant_stale_reply_extra_round_respects_failure_limit() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(_max_internal_rounds=16)
    engine._stale_reply_extra_round_granted = 0

    assert engine._grant_stale_reply_extra_round(15, 2) is False
    assert engine._stale_reply_extra_round_granted == 0

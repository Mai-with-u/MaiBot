"""Test for wait timeout backfill without tool_call_id (issue #1926).

When the model only thinks about calling ``wait`` in text but never
issues a structured ``tool_calls`` block, ``_pending_wait_tool_call_id``
can be ``None`` at timeout.  Previously the backfill generated an
orphan ``ToolResultMessage`` with ``tool_call_id="wait_timeout"``,
which caused Gemini (and other strict clients) to crash with
``ValueError`` because no preceding assistant message registered that
ID.  Now the backfill degrades to an ``AssistantMessage`` instead.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.maisaka.context.messages import AssistantMessage, ToolResultMessage


class TestWaitTimeoutBackfill:
    """``_build_wait_completed_message`` must not emit orphan ToolResultMessage."""

    def test_with_tool_call_id_returns_tool_result(self):
        """Normal path: real tool_call_id → ToolResultMessage."""
        from src.maisaka.reasoning_engine import ReasoningEngine

        runtime = MagicMock()
        runtime._consume_pending_wait_state.return_value = (
            "gemini-tool-call-1",
            5.0,
            10.0,
        )
        runtime.log_prefix = "[test]"

        engine = MagicMock(spec=ReasoningEngine)
        engine._runtime = runtime

        # Call the real method on the spec'd mock by binding manually
        msg = ReasoningEngine._build_wait_completed_message(
            engine, has_new_messages=False
        )

        assert isinstance(msg, ToolResultMessage)
        assert msg.tool_call_id == "gemini-tool-call-1"
        assert msg.tool_name == "wait"

    def test_without_tool_call_id_returns_assistant_message(self):
        """No real tool_call_id → AssistantMessage, not orphan ToolResultMessage."""
        from src.maisaka.reasoning_engine import ReasoningEngine

        runtime = MagicMock()
        runtime._consume_pending_wait_state.return_value = (
            None,
            5.0,
            10.0,
        )
        runtime.log_prefix = "[test]"

        engine = MagicMock(spec=ReasoningEngine)
        engine._runtime = runtime

        msg = ReasoningEngine._build_wait_completed_message(
            engine, has_new_messages=False
        )

        assert isinstance(msg, AssistantMessage)
        assert not isinstance(msg, ToolResultMessage)
        assert "等待已超时" in msg.content

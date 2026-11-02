from typing import Any, Dict, List

import pytest

from src.core.tooling import ToolExecutionResult, ToolInvocation
from src.llm_models.payload_content.tool_option import ToolCall
from src.maisaka.monitor.events import _serialize_tool_results
from src.maisaka.reasoning_engine import MaisakaReasoningEngine


@pytest.mark.asyncio
@pytest.mark.parametrize('condensed', ['整理后的游戏详情', None])
async def test_monitor_summary_uses_same_condensed_text_as_planner(condensed: str | None) -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    original = '原始游戏详情与很长的描述'
    result = ToolExecutionResult(tool_name='steam_game_details', success=True, content=original)
    call = ToolCall(call_id='game-1', func_name='steam_game_details')
    invocation = ToolInvocation(tool_name='steam_game_details', arguments={})
    summaries: List[str] = []
    monitor: List[Dict[str, Any]] = []

    await engine._append_tool_display_results(
        tool_result_summaries=summaries,
        tool_monitor_results=monitor,
        tool_call=call,
        invocation=invocation,
        result=result,
        duration_ms=123,
        tool_spec=None,
        condensed_content=condensed,
    )

    expected = original if condensed is None else condensed
    assert expected in summaries[0]
    assert monitor[0]['summary'] == summaries[0]
    assert _serialize_tool_results(monitor)[0]['summary'] == summaries[0]
    if condensed is not None:
        assert original not in monitor[0]['summary']
    assert result.get_history_content() == original


def test_monitor_preserves_error_when_structured_content_masks_it() -> None:
    engine = object.__new__(MaisakaReasoningEngine)
    result = ToolExecutionResult(
        tool_name='query_person_profile',
        success=False,
        error_message='person_id 无效，且未能通过别名解析',
        structured_content={'success': False, 'person_id': ''},
    )
    call = ToolCall(call_id='profile-1', func_name='query_person_profile')
    invocation = ToolInvocation(tool_name='query_person_profile', arguments={'person_name': '某人的妈妈'})

    monitor = engine._build_tool_monitor_result(call, invocation, result, 40)
    serialized = _serialize_tool_results([monitor])[0]

    assert serialized['error_message'] == result.error_message
    assert result.error_message not in serialized['summary']

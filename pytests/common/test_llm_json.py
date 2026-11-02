from pydantic import ValidationError

import json

import pytest

from src.common.llm_json import parse_llm_json, validate_llm_json
from src.maisaka.reply_supplement import ReplySupplementPlan


@pytest.mark.parametrize("response", [
    '{"msg_id": null, "parts": [{"type": "reply"}]}',
    '```json\n{"msg_id": null, "parts": [{"type": "reply"}]}\n```',
    '```\r\n{"msg_id": null, "parts": [{"type": "reply"}]}\r\n```',
])
def test_reply_supplement_json(response: str) -> None:
    plan = validate_llm_json(response, ReplySupplementPlan)
    assert plan.msg_id is None
    assert plan.parts[0].type == "reply"


def test_invalid_or_incomplete_json_remains_an_error() -> None:
    with pytest.raises(ValidationError):
        validate_llm_json('```json\n{"msg_id": null', ReplySupplementPlan)
    with pytest.raises(ValidationError):
        validate_llm_json('```json\n{"msg_id": null, "parts": []}\n```', ReplySupplementPlan)
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json('{"value": 1,}')


def test_repair_is_explicit() -> None:
    assert parse_llm_json('```json\n{"value": 1,}\n```', repair=True) == {"value": 1}
    assert parse_llm_json('```json\n[1, 2]\n```') == [1, 2]


def test_tool_argument_parsing_modes() -> None:
    from src.config.model_configs import ToolArgumentParseMode
    from src.llm_models.exceptions import RespParseException
    from src.llm_models.model_client.openai_client import _parse_tool_arguments

    fenced = '```json\n{"value": 1}\n```'
    assert _parse_tool_arguments(fenced, ToolArgumentParseMode.AUTO, None) == {"value": 1}
    with pytest.raises(RespParseException):
        _parse_tool_arguments(fenced, ToolArgumentParseMode.STRICT, None)
    assert _parse_tool_arguments('{"value": 1}', ToolArgumentParseMode.STRICT, None) == {"value": 1}
    assert _parse_tool_arguments('"{\\"value\\": 1}"', ToolArgumentParseMode.DOUBLE_DECODE, None) == {"value": 1}


def test_nested_tool_arguments_accept_fenced_json() -> None:
    from src.maisaka.builtin_tool.reply import _normalize_reply_arguments
    from src.services.llm_service import _normalize_tool_arguments

    fenced = '```json\n{"value": 1}\n```'
    assert _normalize_tool_arguments(fenced) == {"value": 1}
    assert _normalize_reply_arguments({"arguments": fenced}) == ({"value": 1}, "")


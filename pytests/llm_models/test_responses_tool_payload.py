"""验证 Responses 客户端导出的 function tools 保持非 strict 定义。

schedule 等内置工具的参数 schema 是执行时校验的普通 JSON Schema（含 ``not``、按操作选填等写法），
Responses 在省略 ``strict`` 时会尝试按 strict 模式编译并直接拒绝请求（HTTP 400, Issue #2518），
因此转换结果必须显式携带 ``strict: false``，且 schema 原样透传。
"""

import json

from jsonschema import Draft202012Validator

from src.llm_models.model_client.openai_responses_client import _convert_tool_options
from src.llm_models.payload_content.tool_option import ToolOption
from src.maisaka.builtin_tool.schedule import get_tool_spec

# Responses API function tool 的载荷形状：五个字段，strict 必须显式为 False。
RESPONSES_FUNCTION_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "name", "description", "parameters", "strict"],
    "properties": {
        "type": {"const": "function"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "strict": {"const": False},
        "parameters": {"type": "object"},
    },
}


def _option_from_spec(spec) -> ToolOption:
    return ToolOption.from_definition(
        {
            "name": spec.name,
            "description": spec.description,
            "parameters_schema": spec.parameters_schema,
        }
    )


def test_responses_schedule_tool_payload_is_non_strict() -> None:
    """schedule 工具转换后必须显式 strict:false，schema 原样透传。"""
    spec = get_tool_spec()
    payload = _convert_tool_options([_option_from_spec(spec)])[0]

    Draft202012Validator(RESPONSES_FUNCTION_TOOL_SCHEMA).validate(payload)
    assert payload["name"] == "schedule"
    assert payload["strict"] is False
    # 互斥约束（not）保留给模型参考，运行时校验仍在 schedule 执行入口。
    assert payload["parameters"] == spec.parameters_schema
    assert "not" in payload["parameters"]


def test_responses_all_function_tools_declare_non_strict() -> None:
    """所有 MaiBot function tools 转换后都携带 strict:false，无参数工具同样适用。"""
    plain = ToolOption.from_definition(
        {
            "name": "lookup",
            "description": "查询工具",
            "parameters_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["keyword"],
                "properties": {"keyword": {"type": "string", "minLength": 1}},
            },
        }
    )
    no_args = ToolOption(name="noop", description="无参数工具", params=None, parameters_schema_override=None)

    payload = _convert_tool_options([plain, no_args])

    assert len(payload) == 2
    for tool in payload:
        Draft202012Validator(RESPONSES_FUNCTION_TOOL_SCHEMA).validate(tool)
        assert tool["strict"] is False
    assert payload[1]["parameters"] == {"type": "object", "properties": {}, "required": []}
    json.dumps(payload, ensure_ascii=False, allow_nan=False)

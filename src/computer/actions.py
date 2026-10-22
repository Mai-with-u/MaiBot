"""桌面动作契约，同时用于模型 schema 和执行前整批校验。"""

from typing import Any, Dict, List
import math


def action_schemas() -> List[Dict[str, Any]]:
    number = {"type": "number", "minimum": 0}
    seconds = {"type": "number", "minimum": 0}
    text = {"type": "string"}
    keys = {"type": "array", "items": text, "minItems": 1}
    button = {"type": "string", "enum": ["left", "right", "middle"]}
    definitions = [
        ("observe", {}, []),
        ("release", {}, []),
        ("move", {"x": number, "y": number}, ["x", "y"]),
        ("click", {"x": number, "y": number, "button": button}, ["x", "y"]),
        ("double_click", {"x": number, "y": number, "button": button}, ["x", "y"]),
        ("drag", {"x": number, "y": number, "end_x": number, "end_y": number,
                  "seconds": seconds, "button": button}, ["x", "y", "end_x", "end_y"]),
        ("mouse_down", {"button": button}, []), ("mouse_up", {"button": button}, []),
        ("scroll", {"delta": {"type": "integer"}}, ["delta"]),
        ("keys", {"keys": keys, "seconds": seconds}, ["keys"]),
        ("key_down", {"keys": keys}, ["keys"]), ("key_up", {"keys": keys}, ["keys"]),
        ("type", {"text": text}, ["text"]),
        ("open", {"path": text, "arguments": {"type": "array", "items": text}}, ["path"]),
        ("shell", {"command": text}, ["command"]),
        ("wait", {"seconds": seconds}, ["seconds"]),
        ("wait_window", {"title": text, "seconds": seconds}, ["title", "seconds"]),
        ("wait_process", {"pid": {"type": "integer", "minimum": 1}, "seconds": seconds}, ["pid", "seconds"]),
        ("inspect", {"depth": {"type": "integer", "minimum": 0},
                     "limit": {"type": "integer", "minimum": 0}}, []),
        ("uia", {"runtime_id": text, "operation": {"type": "string", "enum": ["invoke", "focus", "set_value", "select"]},
                 "value": text}, ["runtime_id", "operation"]),
        ("browser", {"method": text, "target": text, "params": {"type": "object"}}, ["method"]),
    ]
    return [{"type": "object", "properties": {"action": {"type": "string", "const": name}, **fields},
             "required": ["action", *required], "additionalProperties": False} for name, fields, required in definitions]


def validate_actions(actions: List[Dict[str, Any]]) -> None:
    """整批校验后才发送，错误批次不会产生部分输入。"""
    schemas = {schema["properties"]["action"]["const"]: schema for schema in action_schemas()}
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("action") not in schemas:
            raise ValueError(f"actions[{index}] 不是已定义的桌面动作")
        schema = schemas[action["action"]]
        for name in schema["required"]:
            if name not in action:
                raise ValueError(f"actions[{index}] 缺少 {name}")
        for name, value in action.items():
            if name not in schema["properties"]:
                raise ValueError(f"actions[{index}] 未知参数 {name}")
            field = schema["properties"][name]
            kind = field["type"]
            valid = {"string": isinstance(value, str), "number": type(value) in (int, float),
                     "integer": type(value) is int, "array": isinstance(value, list), "object": isinstance(value, dict)}[kind]
            if not valid:
                raise ValueError(f"actions[{index}].{name} 必须为 {kind}")
            if kind in {"number", "integer"}:
                if not math.isfinite(value) or ("minimum" in field and value < field["minimum"]):
                    raise ValueError(f"actions[{index}].{name} 数值无效")
            if "enum" in field and value not in field["enum"]:
                raise ValueError(f"actions[{index}].{name} 取值无效")
            if kind == "array" and (any(not isinstance(item, str) for item in value) or len(value) < field.get("minItems", 0)):
                raise ValueError(f"actions[{index}].{name} 必须为有效的字符串数组")
        if action["action"] == "uia" and action["operation"] == "set_value" and "value" not in action:
            raise ValueError(f"actions[{index}] set_value 缺少 value")

"""回复扩展参数声明与调用校验。"""

from copy import deepcopy
from typing import Any, Dict

from jsonschema import Draft202012Validator

import json


def _check_schema_nodes(node: Any) -> None:
    if isinstance(node, dict):
        if "$ref" in node or "$dynamicRef" in node:
            raise ValueError("回复扩展参数不支持 $ref/$dynamicRef，请内联声明，避免调用时访问外部资源")
        for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            for value in node.get(key, {}).values():
                _check_schema_nodes(value)
        for key in (
            "items", "additionalProperties", "contains", "propertyNames", "if", "then", "else", "not",
            "unevaluatedItems", "unevaluatedProperties", "allOf", "anyOf", "oneOf", "prefixItems",
        ):
            if key in node:
                _check_schema_nodes(node[key])
        if "default" in node:
            Draft202012Validator(node).validate(node["default"])
    elif isinstance(node, list):
        for value in node:
            _check_schema_nodes(value)


def validate_parameter_schema(raw_schema: Any) -> Dict[str, Any]:
    """注册时检查 JSON Schema 和默认值；未知顶层参数默认拒绝。"""
    if not isinstance(raw_schema, dict) or raw_schema.get("type") != "object":
        raise ValueError("回复扩展 parameters_schema 必须是 object JSON Schema")
    schema = deepcopy(raw_schema)
    schema.setdefault("additionalProperties", False)
    Draft202012Validator.check_schema(schema)
    _check_schema_nodes(schema)
    json.dumps(schema, allow_nan=False)
    return schema


def _fill_defaults(value: Any, schema: Dict[str, Any]) -> None:
    if isinstance(value, dict):
        for name, child in schema.get("properties", {}).items():
            if not isinstance(child, dict):
                continue
            if name not in value and "default" in child:
                value[name] = deepcopy(child["default"])
            if name in value:
                _fill_defaults(value[name], child)
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for item in value:
            _fill_defaults(item, schema["items"])


def validate_parameters(parameters: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    """每次调用各自复制参数、补默认值并校验，不共享可变状态。"""
    if not isinstance(parameters, dict):
        raise ValueError("回复扩展参数必须是对象")
    result = deepcopy(parameters)
    _fill_defaults(result, schema)
    json.dumps(result, allow_nan=False)
    Draft202012Validator(schema).validate(result)
    return result

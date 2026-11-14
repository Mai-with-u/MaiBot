"""模型文本工具调用兼容层。

parse_text_tool_calls 只接收文本和 JSON Schema，不依赖客户端、配置或工具执行器。
recover_text_tool_calls 负责 Context Item 映射；normalize_text_tool_response 是 LLM 层入口。
支持直接工具标签、tool_call/tool_use JSON、invoke/parameter、function= 及 DSML。
只处理代码引用以外的调用块；未知工具、坏参数和不完整调用不会被静默修复。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import groupby
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Sequence, Tuple
from xml.etree import ElementTree

import json
import re
import uuid

from jsonschema import ValidationError, validate

if TYPE_CHECKING:
    from .model_client.base_client import APIResponse, ResponseRequest
    from .payload_content.context_item import ContextContentPart, ContextItemMeta, ModelOutputItem
    from .payload_content.tool_option import ToolOption


_CONTAINERS = {"tool_call", "tool_use", "function_call", "calls", "function_calls"}
_WRAPPERS = _CONTAINERS | {"invoke", "function"}
_TAG = re.compile(
    r"<(?P<close>/)?(?:[|｜]DSML[|｜]>?\s*)?(?P<name>[A-Za-z_][\w.:-]*)"
    r"(?P<attrs>(?:\s[^<>]*?|=[A-Za-z0-9_.:-]+)?)(?P<empty>/)?>",
)
_CODE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)|`+[^`\n]*`+")


class TextToolCallError(ValueError):
    """文本已包含调用意图，但结构或参数不合法。"""


@dataclass(frozen=True)
class ParsedTextToolCall:
    """尚未绑定上下文的调用；call_id 仅在模型明确给出时存在。"""

    name: str
    arguments: Dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class ParsedTextToolCalls:
    """移除调用块后的正文和按出现顺序排列的调用。"""

    text: str
    calls: Tuple[ParsedTextToolCall, ...]


def _normalize_xml_tag(match: re.Match[str]) -> str:
    """只规范化标签语法，不替换参数正文中的普通文本。"""

    name = match.group("name")
    if name.lower() in _WRAPPERS | {"parameter"}:
        name = name.lower()
    attrs = match.group("attrs")
    if attrs.startswith("=") and name in {"function", "parameter"}:
        attrs = f' name="{attrs[1:]}"'
    return f'<{match.group("close") or ""}{name}{attrs}{match.group("empty") or ""}>'


def _parse_argument(node: ElementTree.Element, schema: Dict[str, Any]) -> Any:
    """按 Schema 解析 XML 参数；对象支持子标签，数组支持 item 子标签。"""

    text = node.text or ""
    if len(node):
        if text.strip() or any((child.tail or "").strip() for child in node):
            raise TextToolCallError(f"参数 {node.tag} 的子标签间不能混入正文")
        if schema.get("type") == "object":
            return _parse_parameters(node, schema, named_parameters=False)
        if schema.get("type") == "array":
            if any(child.tag != "item" or child.attrib for child in node):
                raise TextToolCallError(f"数组参数 {node.tag} 必须使用 item 子标签")
            return [_parse_argument(child, schema.get("items", {})) for child in node]
        raise TextToolCallError(f"参数 {node.tag} 不允许嵌套标签")
    if schema.get("type") == "string" or node.attrib.get("string") == "true":
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 未指定类型或允许字符串的 Schema 可以接收普通文本，最终统一校验。
        return text


def _parse_parameters(
    node: ElementTree.Element, schema: Dict[str, Any], *, named_parameters: bool,
) -> Dict[str, Any]:
    """解析参数标签，拒绝重复参数及 Schema 未定义的参数名。"""

    properties = schema.get("properties", {})
    args: Dict[str, Any] = {}
    for child in node:
        if named_parameters:
            if child.tag != "parameter" or set(child.attrib) - {"name", "string"}:
                raise TextToolCallError("invoke/function 必须使用 parameter name 标签")
            name = child.attrib.get("name", "")
        else:
            if child.attrib:
                raise TextToolCallError(f"参数 {child.tag} 不允许属性")
            name = child.tag
        if not name or name in args:
            raise TextToolCallError(f"参数名缺失或重复: {name}")
        if name not in properties:
            additional = schema.get("additionalProperties", False)
            if not additional:
                raise TextToolCallError(f"参数未定义: {name}")
            parameter_schema = additional if isinstance(additional, dict) else {}
        else:
            parameter_schema = properties[name]
        if (child.tail or "").strip():
            raise TextToolCallError(f"参数 {name} 后存在未归属的正文")
        args[name] = _parse_argument(child, parameter_schema)
    return args


def _checked_call(
    name: Any, arguments: Any, call_id: Any, schemas: Mapping[str, Dict[str, Any]],
) -> ParsedTextToolCall:
    """校验工具可用性、参数类型与显式调用 ID。"""

    if not isinstance(name, str) or name not in schemas:
        raise TextToolCallError(f"本次请求未提供工具: {name}")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise TextToolCallError(f"工具 {name} 的参数必须为对象")
    if call_id is not None and (not isinstance(call_id, str) or not call_id.strip()):
        raise TextToolCallError(f"工具 {name} 的调用 ID 必须为非空字符串")
    validate(arguments, schemas[name])
    return ParsedTextToolCall(name, arguments, call_id)


def _parse_json_call(payload: Any, schemas: Mapping[str, Dict[str, Any]]) -> ParsedTextToolCall:
    """支持 name/input、name/arguments 和 OpenAI function 包装。"""

    if not isinstance(payload, dict):
        raise TextToolCallError("JSON 工具调用必须为对象")
    function = payload.get("function", payload)
    if not isinstance(function, dict):
        raise TextToolCallError("function 必须为对象")
    arguments = function.get("arguments", function.get("input", function.get("parameters", {})))
    return _checked_call(function.get("name"), arguments, payload.get("id", payload.get("call_id")), schemas)


def _parse_xml_call(
    node: ElementTree.Element, schemas: Mapping[str, Dict[str, Any]],
) -> List[ParsedTextToolCall]:
    """将各种 XML 外壳转成同一种调用描述。"""

    text = (node.text or "").strip()
    if node.tag in _CONTAINERS:
        if set(node.attrib) - {"id", "call_id"}:
            raise TextToolCallError(f"{node.tag} 存在未知属性")
        if not len(node):
            payload = json.loads(text)
            payloads = payload if isinstance(payload, list) else [payload]
            if not payloads:
                raise TextToolCallError("工具调用列表不能为空")
            calls = [_parse_json_call(value, schemas) for value in payloads]
        elif text and all(child.tag == "parameter" for child in node):
            # 兼容 <tool_call>工具名<parameter name="参数">值</parameter>。
            if text not in schemas:
                raise TextToolCallError(f"本次请求未提供工具: {text}")
            args = _parse_parameters(node, schemas[text], named_parameters=True)
            calls = [_checked_call(text, args, None, schemas)]
        else:
            if text or any((child.tail or "").strip() for child in node):
                raise TextToolCallError(f"{node.tag} 调用块中存在未归属的正文")
            calls = [call for child in node for call in _parse_xml_call(child, schemas)]
        call_id = node.attrib.get("id", node.attrib.get("call_id"))
        if call_id is not None:
            if len(calls) != 1 or calls[0].call_id not in {None, call_id}:
                raise TextToolCallError("外壳调用 ID 与内部调用不一致")
            calls = [_checked_call(calls[0].name, calls[0].arguments, call_id, schemas)]
        return calls

    named_parameters = node.tag in {"invoke", "function"}
    allowed_attrs = {"name", "id", "call_id"} if named_parameters else {"id", "call_id"}
    if set(node.attrib) - allowed_attrs:
        raise TextToolCallError(f"{node.tag} 存在未知属性")
    name = node.attrib.get("name") if named_parameters else node.tag
    if not isinstance(name, str) or name not in schemas:
        raise TextToolCallError(f"本次请求未提供工具: {name}")
    if len(node):
        if text:
            raise TextToolCallError(f"工具 {name} 参数标签前存在正文")
        args = _parse_parameters(node, schemas[name], named_parameters=named_parameters)
    else:
        args = json.loads(text) if text else {}
    return [_checked_call(name, args, node.attrib.get("id", node.attrib.get("call_id")), schemas)]


def _read_call_block(text: str, opening: re.Match[str]) -> Tuple[ElementTree.Element, int]:
    """读取完整调用块；JSON 内容按 JSON 边界读取，不把字符串里的标签当结束符。"""

    if opening.group("empty"):
        return ElementTree.fromstring(_normalize_xml_tag(opening)), opening.end()
    name = opening.group("name")
    wrapper = name.lower() in _WRAPPERS

    def same_name(tag: re.Match[str]) -> bool:
        return tag.group("name").lower() == name.lower() if wrapper else tag.group("name") == name

    body_start = opening.end()
    while body_start < len(text) and text[body_start].isspace():
        body_start += 1
    if body_start < len(text) and text[body_start] in "{[":
        _, body_end = json.JSONDecoder().raw_decode(text, body_start)
        closing_start = body_end
        while closing_start < len(text) and text[closing_start].isspace():
            closing_start += 1
        closing = _TAG.match(text, closing_start)
        if closing is None or not closing.group("close") or not same_name(closing):
            raise TextToolCallError(f"JSON 调用后缺少闭合标签: {opening.group('name')}")
        node = ElementTree.fromstring(_normalize_xml_tag(opening)[:-1] + "/>")
        node.text = text[body_start:body_end]
        return node, closing.end()
    depth = 1
    for tag in _TAG.finditer(text, opening.end()):
        if same_name(tag):
            if tag.group("close"):
                depth -= 1
            elif not tag.group("empty"):
                depth += 1
        if depth == 0:
            block = text[opening.start():tag.end()]
            if "<!" in block or "<?" in block:
                raise TextToolCallError("工具调用 XML 不允许声明或实体定义")
            return ElementTree.fromstring(_TAG.sub(_normalize_xml_tag, block)), tag.end()
    raise TextToolCallError(f"工具调用标签未闭合: {opening.group('name')}")


def parse_text_tool_calls(
    text: str, schemas: Mapping[str, Dict[str, Any]], *, allow_direct_tags: bool = True,
) -> ParsedTextToolCalls:
    """提取代码引用之外的完整调用块，保留其余正文；不生成或执行调用 ID。

    schemas 的键是本次可用工具名。显式调用外壳中的未知工具会报错，
    普通未知 XML 标签保持原样。所有调用均解析并通过校验后才返回。
    """

    code_ranges = [(match.start(), match.end()) for match in _CODE.finditer(text)]
    calls: List[ParsedTextToolCall] = []
    parts: List[str] = []
    cursor = 0
    allowed_names = _WRAPPERS | schemas.keys() if allow_direct_tags else _WRAPPERS
    search_from = 0
    try:
        while (opening := _TAG.search(text, search_from)) is not None:
            search_from = opening.end()
            name = opening.group("name")
            if (opening.group("close") or (name not in allowed_names and name.lower() not in _WRAPPERS)
                    or any(start <= opening.start() < end for start, end in code_ranges)):
                continue
            node, end = _read_call_block(text, opening)
            calls.extend(_parse_xml_call(node, schemas))
            parts.append(text[cursor:opening.start()])
            cursor = end
            search_from = end
    except (ElementTree.ParseError, ValidationError, ValueError) as error:
        raise TextToolCallError(f"文本工具调用解析失败: {error}") from error
    if not calls:
        return ParsedTextToolCalls(text, ())
    parts.append(text[cursor:])
    return ParsedTextToolCalls("".join(parts).strip(), tuple(calls))


def recover_text_tool_calls(
    items: Tuple[ModelOutputItem, ...], tools: Sequence[ToolOption], logical_turn_id: str,
    *, tool_choice: str | None = None, occupied_call_ids: Sequence[str] = (),
) -> Tuple[ModelOutputItem, ...]:
    """将文本调用绑定为标准 Item；原生调用优先，重复文本仅清理而不执行。

    调用块之外的正文、推理及非文本内容保持原有归属，修改过的 Item 清除 replay。
    不可用工具、禁止调用、非法参数及重复显式 ID 通过 RespParseException 暴露。
    """

    # 上下文适配层在调用时加载项目类型，纯文本解析可独立导入使用。
    from .exceptions import RespParseException
    from .payload_content.context_item import (
        AssistantMessageItem,
        ContextTextPart,
        ContextToolCall,
        FunctionCallItem,
        ReasoningItem,
        ReasoningRepresentation,
    )
    from .payload_content.tool_option import (
        TOOL_CALL_SOURCE_EXTRA_KEY,
        TOOL_CALL_SOURCE_REASONING,
        TOOL_CALL_SOURCE_RESPONSE,
    )

    schemas = {tool.name: tool.parameters_schema or {"type": "object", "properties": {}} for tool in tools}
    native_calls = [item.tool_call for item in items if isinstance(item, FunctionCallItem)]
    used_ids = set(occupied_call_ids) | {call.call_id for call in native_calls}
    output: List[ModelOutputItem] = []
    changed = False

    def convert_text(text: str, meta: ContextItemMeta, source: str) -> Tuple[str, List[FunctionCallItem]]:
        parsed = parse_text_tool_calls(text, schemas, allow_direct_tags=source == TOOL_CALL_SOURCE_RESPONSE)
        converted: List[FunctionCallItem] = []
        for call in parsed.calls:
            if tool_choice == "none":
                raise TextToolCallError("本次请求禁止调用工具，但模型在文本中输出了工具调用")
            if native_calls:
                if not any(
                    call.name == native.func_name and call.arguments == native.materialize_args()
                    and call.call_id in {None, native.call_id} for native in native_calls
                ):
                    raise TextToolCallError("文本工具调用与原生工具调用不一致")
                continue
            call_id = call.call_id or f"call_{uuid.uuid4().hex}"
            if call_id in used_ids:
                raise TextToolCallError(f"工具调用 ID 重复: {call_id}")
            used_ids.add(call_id)
            converted.append(FunctionCallItem(
                meta=replace(meta, item_id=uuid.uuid4().hex, logical_turn_id=logical_turn_id),
                tool_call=ContextToolCall.create(
                    call_id=call_id, func_name=call.name, args=call.arguments,
                    extra_content={TOOL_CALL_SOURCE_EXTRA_KEY: source},
                ),
            ))
        return parsed.text, converted

    try:
        for item in items:
            if isinstance(item, AssistantMessageItem):
                parts: List[ContextContentPart] = []
                calls: List[FunctionCallItem] = []
                item_changed = False
                for is_text, group in groupby(item.parts, key=lambda part: isinstance(part, ContextTextPart)):
                    grouped_parts = list(group)
                    if is_text:
                        text = "".join(part.text for part in grouped_parts if isinstance(part, ContextTextPart))
                        cleaned, recovered = convert_text(text, item.meta, TOOL_CALL_SOURCE_RESPONSE)
                        item_changed |= cleaned != text
                        calls.extend(recovered)
                        if cleaned:
                            parts.append(ContextTextPart(cleaned))
                    else:
                        parts.extend(grouped_parts)
                if parts:
                    output.append(replace(item, parts=tuple(parts), replay=None) if item_changed else item)
                output.extend(calls)
                changed |= item_changed
            elif isinstance(item, ReasoningItem):
                # 保留旧客户端对思维链中显式工具调用的兼容，summary 与原始推理分别处理。
                summary_parts: List[str] = []
                text_parts: List[str] = []
                calls = []
                item_changed = False
                for source_parts, target_parts in ((item.summary_parts, summary_parts), (item.text_parts, text_parts)):
                    for text in source_parts:
                        cleaned, recovered = convert_text(text, item.meta, TOOL_CALL_SOURCE_REASONING)
                        item_changed |= cleaned != text
                        if cleaned:
                            target_parts.append(cleaned)
                        calls.extend(recovered)
                if not item_changed:
                    output.append(item)
                elif summary_parts or text_parts:
                    representation = ReasoningRepresentation.SUMMARY if summary_parts else ReasoningRepresentation.RAW_TEXT
                    output.append(replace(
                        item, summary_parts=tuple(summary_parts), text_parts=tuple(text_parts),
                        representation=representation, replay=None,
                    ))
                output.extend(calls)
                changed |= item_changed
            else:
                output.append(item)
    except TextToolCallError as error:
        raise RespParseException(items, str(error)) from error
    return tuple(output) if changed else items


def normalize_text_tool_response(response: APIResponse, request: ResponseRequest) -> None:
    """统一响应入口；同步解析由调用方放入线程池执行，不改 Provider 原始诊断数据。"""

    from src.common.logger import get_logger

    from .payload_content.context_item import FunctionCallItem, FunctionCallOutputItem

    occupied_ids = [
        item.tool_call.call_id if isinstance(item, FunctionCallItem) else item.call_id
        for item in request.context_items if isinstance(item, (FunctionCallItem, FunctionCallOutputItem))
    ]
    output = recover_text_tool_calls(
        response.output_items, request.tool_options or [], request.logical_turn_id,
        tool_choice=request.tool_choice, occupied_call_ids=occupied_ids,
    )
    if output is response.output_items:
        return
    response.output_items = output
    trace = response.generation_trace
    if trace is not None:
        response.generation_trace = replace(trace, output_item_ids=tuple(item.meta.item_id for item in output))
    get_logger("text_tool_call").warning("已将模型文本中的工具调用转换为标准上下文 Item")

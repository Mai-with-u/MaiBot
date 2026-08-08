"""Context Item 关系协议与裁切闭包。

列表位置是 Item 的唯一顺序。本模块只描述跨 Item 的工具关系，不引入额外的
Response Group、ordinal 或可变时间线对象。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, Sequence, Tuple

from .context_item import (
    ContextItem,
    FunctionCallItem,
    FunctionCallOutputItem,
    ModelOutputItem,
)


class ContextProtocolMode(str, Enum):
    """不同生命周期边界采用的关系约束。"""

    REQUEST_CONTEXT = "request_context"
    MODEL_OUTPUT = "model_output"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class ContextRelationReport:
    """一次纯关系分析的结果。"""

    duplicate_item_ids: Tuple[str, ...] = ()
    duplicate_call_ids: Tuple[str, ...] = ()
    duplicate_output_call_ids: Tuple[str, ...] = ()
    orphan_output_call_ids: Tuple[str, ...] = ()
    unanswered_call_ids: Tuple[str, ...] = ()
    misplaced_output_call_ids: Tuple[str, ...] = ()
    mismatched_turn_call_ids: Tuple[str, ...] = ()
    missing_turn_call_ids: Tuple[str, ...] = ()
    invalid_turn_ids: Tuple[str, ...] = ()


def analyze_context_item_relations(
    items: Sequence[ContextItem],
    *,
    pending_call_ids: Iterable[str] = (),
) -> ContextRelationReport:
    """按列表位置分析 call/output 关系，不修改输入。"""

    pending_calls = {call_id for call_id in pending_call_ids if call_id}
    items_by_id: Dict[str, ContextItem] = {}
    duplicate_item_ids: set[str] = set()
    calls: Dict[str, tuple[int, FunctionCallItem]] = {}
    duplicate_call_ids: set[str] = set()
    outputs: Dict[str, tuple[int, FunctionCallOutputItem]] = {}
    duplicate_output_call_ids: set[str] = set()
    missing_turn_call_ids: set[str] = set()
    invalid_turn_ids: set[str] = set()

    for index, item in enumerate(items):
        item_id = item.meta.item_id
        previous_same_id = items_by_id.get(item_id)
        if previous_same_id is not None:
            duplicate_item_ids.add(item_id)
            if previous_same_id.meta.logical_turn_id:
                invalid_turn_ids.add(previous_same_id.meta.logical_turn_id)
            if item.meta.logical_turn_id:
                invalid_turn_ids.add(item.meta.logical_turn_id)
        else:
            items_by_id[item_id] = item

        if isinstance(item, FunctionCallItem):
            call_id = item.tool_call.call_id
            if call_id in calls:
                duplicate_call_ids.add(call_id)
                previous_item = calls[call_id][1]
                if previous_item.meta.logical_turn_id:
                    invalid_turn_ids.add(previous_item.meta.logical_turn_id)
                if item.meta.logical_turn_id:
                    invalid_turn_ids.add(item.meta.logical_turn_id)
            else:
                calls[call_id] = (index, item)
            if not item.meta.logical_turn_id:
                missing_turn_call_ids.add(call_id)
            continue

        if isinstance(item, FunctionCallOutputItem):
            call_id = item.call_id
            if call_id in outputs:
                duplicate_output_call_ids.add(call_id)
                previous_item = outputs[call_id][1]
                if previous_item.meta.logical_turn_id:
                    invalid_turn_ids.add(previous_item.meta.logical_turn_id)
                if item.meta.logical_turn_id:
                    invalid_turn_ids.add(item.meta.logical_turn_id)
            else:
                outputs[call_id] = (index, item)
            if not item.meta.logical_turn_id:
                missing_turn_call_ids.add(call_id)

    orphan_output_call_ids = set(outputs) - set(calls)
    unanswered_call_ids = set(calls) - set(outputs) - pending_calls
    misplaced_output_call_ids: set[str] = set()
    mismatched_turn_call_ids: set[str] = set()

    for call_id in set(calls) & set(outputs):
        call_index, call_item = calls[call_id]
        output_index, output_item = outputs[call_id]
        call_turn_id = call_item.meta.logical_turn_id
        output_turn_id = output_item.meta.logical_turn_id
        if output_index <= call_index:
            misplaced_output_call_ids.add(call_id)
        if call_turn_id != output_turn_id:
            mismatched_turn_call_ids.add(call_id)
        if output_index <= call_index or call_turn_id != output_turn_id:
            if call_turn_id:
                invalid_turn_ids.add(call_turn_id)
            if output_turn_id:
                invalid_turn_ids.add(output_turn_id)

    for call_id in orphan_output_call_ids:
        turn_id = outputs[call_id][1].meta.logical_turn_id
        if turn_id:
            invalid_turn_ids.add(turn_id)
    for call_id in unanswered_call_ids:
        turn_id = calls[call_id][1].meta.logical_turn_id
        if turn_id:
            invalid_turn_ids.add(turn_id)

    return ContextRelationReport(
        duplicate_item_ids=tuple(sorted(duplicate_item_ids)),
        duplicate_call_ids=tuple(sorted(duplicate_call_ids)),
        duplicate_output_call_ids=tuple(sorted(duplicate_output_call_ids)),
        orphan_output_call_ids=tuple(sorted(orphan_output_call_ids)),
        unanswered_call_ids=tuple(sorted(unanswered_call_ids)),
        misplaced_output_call_ids=tuple(sorted(misplaced_output_call_ids)),
        mismatched_turn_call_ids=tuple(sorted(mismatched_turn_call_ids)),
        missing_turn_call_ids=tuple(sorted(missing_turn_call_ids)),
        invalid_turn_ids=tuple(sorted(invalid_turn_ids)),
    )


def validate_context_items(
    items: Sequence[ContextItem],
    mode: ContextProtocolMode,
    *,
    pending_call_ids: Iterable[str] = (),
) -> None:
    """在明确的生命周期边界校验 Item 类型和工具关系。"""

    if mode == ContextProtocolMode.MODEL_OUTPUT:
        invalid_types = sorted(
            {
                item.__class__.__name__
                for item in items
                if not isinstance(item, ModelOutputItem)
            }
        )
        if invalid_types:
            raise ValueError(f"模型输出包含非模型输出 Item: {invalid_types}")

    report = analyze_context_item_relations(items, pending_call_ids=pending_call_ids)
    if report.duplicate_item_ids:
        raise ValueError(f"Context Items 存在重复 item_id: {list(report.duplicate_item_ids)}")
    if report.duplicate_call_ids:
        raise ValueError(f"Context Items 存在重复 function call: {list(report.duplicate_call_ids)}")
    if report.duplicate_output_call_ids:
        raise ValueError(f"Context Items 存在重复 function output: {list(report.duplicate_output_call_ids)}")
    if report.missing_turn_call_ids:
        raise ValueError(f"工具 Item 缺少 logical_turn_id: {list(report.missing_turn_call_ids)}")
    if report.orphan_output_call_ids:
        raise ValueError(f"Context Items 存在孤儿 function output: {list(report.orphan_output_call_ids)}")
    if report.misplaced_output_call_ids:
        raise ValueError(f"function output 位于调用之前: {list(report.misplaced_output_call_ids)}")
    if report.mismatched_turn_call_ids:
        raise ValueError(
            f"function call/output logical_turn_id 不一致: {list(report.mismatched_turn_call_ids)}"
        )
    if mode != ContextProtocolMode.MODEL_OUTPUT and report.unanswered_call_ids:
        raise ValueError(f"Context Items 存在未回答 function call: {list(report.unanswered_call_ids)}")


def prune_context_items_for_history(
    items: Sequence[ContextItem],
    *,
    pending_call_ids: Iterable[str] = (),
) -> tuple[Tuple[ContextItem, ...], ContextRelationReport]:
    """按 HISTORY 规则删除非法完整 turn，并严格校验保留结果。"""

    pending_calls = tuple(pending_call_ids)
    report = analyze_context_item_relations(items, pending_call_ids=pending_calls)
    invalid_turn_ids = set(report.invalid_turn_ids)
    retained_items = tuple(
        item for item in items if item.meta.logical_turn_id not in invalid_turn_ids
    )
    validate_context_items(
        retained_items,
        ContextProtocolMode.HISTORY,
        pending_call_ids=pending_calls,
    )
    return retained_items, report


def select_context_items_with_protocol_closure(
    items: Sequence[ContextItem],
    selected_item_ids: Iterable[str],
    *,
    pending_call_ids: Iterable[str] = (),
) -> Tuple[ContextItem, ...]:
    """扩展所选工具 turn，并从历史选择结果删除非法 turn。"""

    selected = set(selected_item_ids)
    known_ids = {item.meta.item_id for item in items}
    unknown_ids = selected - known_ids
    if unknown_ids:
        raise KeyError(f"未知 Context Item: {sorted(unknown_ids)}")

    tool_turn_ids = {
        item.meta.logical_turn_id
        for item in items
        if isinstance(item, (FunctionCallItem, FunctionCallOutputItem)) and item.meta.logical_turn_id
    }
    for turn_id in tool_turn_ids:
        turn_item_ids = {item.meta.item_id for item in items if item.meta.logical_turn_id == turn_id}
        if selected & turn_item_ids:
            selected.update(turn_item_ids)

    report = analyze_context_item_relations(items, pending_call_ids=pending_call_ids)
    invalid_turn_ids = set(report.invalid_turn_ids)
    return tuple(
        item
        for item in items
        if item.meta.item_id in selected and item.meta.logical_turn_id not in invalid_turn_ids
    )


__all__ = [
    "ContextProtocolMode",
    "ContextRelationReport",
    "analyze_context_item_relations",
    "prune_context_items_for_history",
    "select_context_items_with_protocol_closure",
    "validate_context_items",
]

"""模型工具参数在写入上下文前按声明过滤。"""

from dataclasses import replace
from typing import Any, Dict, Sequence, Tuple

from src.core.tooling import filter_tool_arguments
from src.llm_models.payload_content.context_item import ContextItem, ContextToolCall, FunctionCallItem


def filter_tool_call_items(
    items: Sequence[ContextItem], schemas: Dict[str, Dict[str, Any] | None],
) -> Tuple[ContextItem, ...]:
    """替换含未声明参数的调用，同时移除携带旧参数的原生回放片段。"""

    result = []
    for item in items:
        if isinstance(item, FunctionCallItem):
            call = item.tool_call
            arguments = call.materialize_args()
            filtered = filter_tool_arguments(arguments, schemas.get(call.func_name))
            if filtered != arguments:
                item = replace(
                    item,
                    tool_call=ContextToolCall.create(
                        call_id=call.call_id, func_name=call.func_name, args=filtered,
                        extra_content=call.materialize_extra_content(),
                    ),
                    replay=None,
                )
        result.append(item)
    return tuple(result)

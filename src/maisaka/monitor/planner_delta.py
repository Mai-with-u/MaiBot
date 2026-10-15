"""Planner 快照的连接级增量编码，不改变可独立恢复的持久化事件。"""

from collections import OrderedDict
from typing import Any, Dict, OrderedDict as OrderedDictType, Tuple


MAX_PLANNER_BASES = 128
PLANNER_EVENTS = {"planner.progress", "planner.finalized"}
IDENTITY_FIELDS = {"session_id", "run_id", "cycle_id"}


def project_planner_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """监控只传展示字段；完整请求和工具详情保留在账本与推理记录中。"""

    projected = dict(payload)
    request = payload.get("request")
    if request is not None:
        projected["request"] = {key: value for key, value in request.items() if key != "messages"}
    projected["tools"] = [
        {key: value for key, value in tool.items() if key != "detail"}
        for tool in payload["tools"]
    ]
    return projected


class PlannerDeltaEncoder:
    """每条连接独立记录已发送的基准；只在串行发送协程中调用。

    输入是已清洗、不会再修改的完整事件。首次发送、缓存淘汰或重连后发送
    完整快照，其余事件只发送变化字段及工具列表发生变化的后缀。
    """

    def __init__(self) -> None:
        self._bases: OrderedDictType[Tuple[str, str, int], Dict[str, Any]] = OrderedDict()

    def encode(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if message.get("domain") != "maisaka_monitor" or message.get("event") not in PLANNER_EVENTS:
            return message
        payload = project_planner_snapshot(message["data"])
        message = {**message, "data": payload}
        if not payload.get("run_id") or type(payload.get("event_id")) is not int or payload["event_id"] <= 0:
            raise ValueError("Planner 快照缺少有效的 run_id/event_id")
        key = (payload["session_id"], payload["run_id"], payload["cycle_id"])
        previous = self._bases.get(key)
        encoded = message
        if previous is not None:
            changes = {
                name: value for name, value in payload.items()
                if name not in IDENTITY_FIELDS and name != "tools"
                and (name not in previous or previous[name] != value)
            }
            # 即使重复回放同一个事件，也明确传递本次快照版本。
            changes["event_id"] = payload["event_id"]
            delta: Dict[str, Any] = {
                "session_id": key[0], "run_id": key[1], "cycle_id": key[2],
                "base_event_id": previous["event_id"],
                "event_type": message["event"],
                "changes": changes,
                "removed_fields": [name for name in previous if name not in payload],
            }
            if payload["tools"] != previous["tools"]:
                tools_from = 0
                for old, new in zip(previous["tools"], payload["tools"], strict=False):
                    if old != new:
                        break
                    tools_from += 1
                delta["tools_from"] = tools_from
                delta["tools"] = payload["tools"][tools_from:]
            encoded = {**message, "event": "planner.delta", "data": delta}

        if message["event"] == "planner.finalized":
            self._bases.pop(key, None)
        else:
            self._bases[key] = payload
            if len(self._bases) > MAX_PLANNER_BASES:
                self._bases.popitem(last=False)
        return encoded

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class NativeToolCallSummary:
    """Provider 原生工具调用的安全可观测摘要。

    该结构只描述本次响应中供应商明确返回的信息，不保存完整工具结果，
    也不参与后续模型上下文回放。
    """

    tool_type: str
    call_id: str = ""
    status: str = ""
    action_type: str = ""
    details: List[str] = field(default_factory=list)
    source_count: int = 0

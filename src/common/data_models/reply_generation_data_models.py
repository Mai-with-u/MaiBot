"""回复生成结果相关数据模型。

该模块用于描述新版回复链中的三个层次：

1. LLM 原始完成结果。
2. 生成过程中的耗时与调试信息。
3. 回复链最终返回给上层的结构化结果。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from . import BaseDataModel
from .llm_service_data_models import PromptMessage

if TYPE_CHECKING:
    from src.common.data_models.message_component_data_model import MessageSequence
    from src.llm_models.payload_content.tool_option import ToolCall


@dataclass
class LLMCompletionResult(BaseDataModel):
    """一次 LLM 调用的原始完成结果。

    该模型只描述模型调用本身的输入与输出，不承载回复切分、
    消息序列拼装或表达方式选择等后处理结果。
    """

    request_prompt: str = field(
        default_factory=str,
        metadata={"description": "实际发送给模型的 Prompt 文本。"},
    )
    response_text: str = field(
        default_factory=str,
        metadata={"description": "模型返回的主文本内容。"},
    )
    reasoning_text: str = field(
        default_factory=str,
        metadata={"description": "模型返回的推理内容。"},
    )
    model_name: str = field(
        default_factory=str,
        metadata={"description": "本次请求实际使用的模型名称。"},
    )
    tool_calls: List["ToolCall"] = field(
        default_factory=list,
        metadata={"description": "模型返回的工具调用列表。"},
    )
    prompt_tokens: int = field(
        default=0,
        metadata={"description": "本次请求的输入 Token 数。"},
    )
    completion_tokens: int = field(
        default=0,
        metadata={"description": "本次请求的输出 Token 数。"},
    )
    total_tokens: int = field(
        default=0,
        metadata={"description": "本次请求的总 Token 数。"},
    )


@dataclass
class GenerationMetrics(BaseDataModel):
    """一次生成流程的耗时与调试指标。"""

    prompt_ms: Optional[float] = field(
        default=None,
        metadata={"description": "Prompt 构建耗时，单位为毫秒。"},
    )
    llm_ms: Optional[float] = field(
        default=None,
        metadata={"description": "LLM 调用耗时，单位为毫秒。"},
    )
    overall_ms: Optional[float] = field(
        default=None,
        metadata={"description": "整个生成流程总耗时，单位为毫秒。"},
    )
    stage_logs: List[str] = field(
        default_factory=list,
        metadata={"description": "各阶段的简短耗时日志列表。"},
    )
    extra: Dict[str, Any] = field(
        default_factory=dict,
        metadata={"description": "额外指标字典，用于承载动态监控信息。"},
    )


@dataclass
class ReplyGenerationResult(BaseDataModel):
    """回复链的最终结构化结果。"""

    success: bool = field(
        default=False,
        metadata={"description": "本次回复生成是否成功。"},
    )
    completion: LLMCompletionResult = field(
        default_factory=LLMCompletionResult,
        metadata={"description": "一次 LLM 调用的原始完成结果。"},
    )
    metrics: GenerationMetrics = field(
        default_factory=GenerationMetrics,
        metadata={"description": "本次生成的耗时与调试指标。"},
    )
    selected_expression_ids: List[int] = field(
        default_factory=list,
        metadata={"description": "本次选中的表达方式 ID 列表。"},
    )
    selected_expression_details: List[Dict[str, Any]] = field(
        default_factory=list,
        metadata={"description": "本次选中的表达方式详情列表。"},
    )
    text_fragments: List[str] = field(
        default_factory=list,
        metadata={"description": "对模型输出进行切分、规范化后的文本片段列表。"},
    )
    message_sequence: Optional["MessageSequence"] = field(
        default=None,
        metadata={"description": "最终可直接发送的消息序列。"},
    )
    error_message: str = field(
        default_factory=str,
        metadata={"description": "失败时的错误描述；成功时通常为空字符串。"},
    )
    monitor_detail: Optional[Dict[str, Any]] = field(
        default=None,
        metadata={"description": "供监控层直接消费的通用 tool 展示详情。"},
    )
    request_message_count: int = field(
        default=0,
        metadata={"description": "本次 replyer 实际发送给模型的消息数量。"},
    )
    request_messages: List[PromptMessage] = field(
        default_factory=list,
        metadata={"description": "本次 replyer 的预览用瘦身请求消息列表。"},
    )

def _format_selected_expression_line(expression: Dict[str, Any], fallback_id: Optional[int] = None) -> str:
    """格式化单条已选表达方式，供终端与监控详情展示。"""

    expression_id = expression.get("id")
    if not isinstance(expression_id, int):
        expression_id = fallback_id
    situation = str(expression.get("situation") or "").strip()
    style = str(expression.get("style") or "").strip()

    prefix = f"{expression_id}：" if expression_id is not None else ""
    if situation and style:
        return f"[{prefix}] {situation} -> {style}"
    if situation:
        return f"{prefix}{situation}"
    if style:
        return f"{prefix}{style}"
    if expression_id is not None:
        return str(expression_id)
    return ""


def _format_selected_expressions(result: ReplyGenerationResult) -> str:
    """将已选表达方式格式化为带 ID 和内容的多行文本。"""

    if result.selected_expression_details:
        detail_map = {
            expression["id"]: expression
            for expression in result.selected_expression_details
            if isinstance(expression.get("id"), int)
        }
        ordered_details: List[Tuple[Dict[str, Any], Optional[int]]] = []
        seen_detail_ids: Set[int] = set()
        for expression_id in result.selected_expression_ids:
            detail = detail_map.get(expression_id)
            if detail is None:
                ordered_details.append(({}, expression_id))
                continue
            ordered_details.append((detail, expression_id))
            seen_detail_ids.add(expression_id)

        for detail in result.selected_expression_details:
            expression_id = detail.get("id")
            if isinstance(expression_id, int) and expression_id in seen_detail_ids:
                continue
            ordered_details.append((detail, expression_id if isinstance(expression_id, int) else None))

        lines = [
            line
            for detail, fallback_id in ordered_details
            if (line := _format_selected_expression_line(detail, fallback_id))
        ]
        if lines:
            return "\n".join(lines)

    if result.selected_expression_ids:
        return ", ".join(str(item) for item in result.selected_expression_ids)
    return ""


def build_reply_monitor_detail(result: ReplyGenerationResult) -> Dict[str, Any]:
    """构建 reply 工具统一监控详情结构。"""

    detail: Dict[str, Any] = {}
    prompt_text = result.completion.request_prompt.strip()
    reasoning_text = result.completion.reasoning_text.strip()
    output_text = result.completion.response_text.strip()

    if result.request_messages:
        detail["request_messages"] = result.request_messages
    elif prompt_text:
        detail["prompt_text"] = prompt_text
    if result.request_message_count > 0:
        detail["request_messages_sanitized"] = bool(result.request_messages)
        detail["request_message_count"] = result.request_message_count
    if reasoning_text:
        detail["reasoning_text"] = reasoning_text
    if output_text:
        detail["output_text"] = output_text

    metrics: Dict[str, Any] = {}
    if result.completion.model_name.strip():
        metrics["model_name"] = result.completion.model_name.strip()
    if result.completion.prompt_tokens > 0:
        metrics["prompt_tokens"] = result.completion.prompt_tokens
    if result.completion.completion_tokens > 0:
        metrics["completion_tokens"] = result.completion.completion_tokens
    if result.completion.total_tokens > 0:
        metrics["total_tokens"] = result.completion.total_tokens
    if result.metrics.prompt_ms is not None:
        metrics["prompt_ms"] = result.metrics.prompt_ms
    if result.metrics.llm_ms is not None:
        metrics["llm_ms"] = result.metrics.llm_ms
    if result.metrics.overall_ms is not None:
        metrics["overall_ms"] = result.metrics.overall_ms
    if metrics:
        detail["metrics"] = metrics

    extra_sections: List[Dict[str, str]] = []
    selected_expression_content = _format_selected_expressions(result)
    if selected_expression_content:
        extra_sections.append({
            "title": "已选表达方式",
            "content": selected_expression_content,
        })
    if result.metrics.stage_logs:
        extra_sections.append({
            "title": "阶段日志",
            "content": "\n".join(result.metrics.stage_logs),
        })
    if result.error_message.strip():
        extra_sections.append({
            "title": "错误信息",
            "content": result.error_message.strip(),
        })
    if extra_sections:
        detail["extra_sections"] = extra_sections

    return detail


__all__ = [
    "GenerationMetrics",
    "LLMCompletionResult",
    "ReplyGenerationResult",
    "build_reply_monitor_detail",
]

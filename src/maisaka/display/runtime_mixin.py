"""Terminal display helpers for the Maisaka runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from src.chat.heart_flow.heartFC_utils import CycleDetail
from src.cli.console import console
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_runtime.hook_payloads import deserialize_prompt_messages

from .display_utils import build_tool_call_summary_lines, format_token_count
from .prompt_cli_renderer import PromptCLIVisualizer

logger = get_logger("maisaka_runtime")


@dataclass(slots=True)
class ToolPromptAccessPanel:
    panel: Panel
    prompt_html_uri: str = ""


class MaisakaRuntimeDisplayMixin:
    """Rich terminal rendering and runtime log helpers."""

    def _render_context_usage_panel(
        self,
        *,
        cycle_id: Optional[int] = None,
        time_records: Optional[dict[str, float]] = None,
        planner_selected_history_count: Optional[int] = None,
        planner_prompt_tokens: Optional[int] = None,
        planner_model_name: Optional[str] = None,
        planner_response: str = "",
        planner_tool_calls: Optional[list[Any]] = None,
        planner_tool_results: Optional[list[str]] = None,
        planner_tool_detail_results: Optional[list[dict[str, Any]]] = None,
        planner_prompt_section: Optional[RenderableType] = None,
        planner_extra_lines: Optional[list[str]] = None,
    ) -> None:
        """在终端展示当前聊天流本轮 cycle 的最终结果。"""
        if not global_config.debug.show_maisaka_thinking:
            return

        body_lines = [
            f"聊天流名称：{getattr(self, 'session_name', self.session_id)}",
            f"聊天流ID：{self.session_id}",
            f"当前回复频率：{self._format_reply_frequency_for_display(self._get_effective_reply_frequency())}",
        ]

        panel_title = "MaiSaka 循环"
        if cycle_id is not None:
            panel_title = f"{panel_title} [{cycle_id}]"
        panel_subtitle = self._build_cycle_time_records_text(time_records or {})
        renderables: list[RenderableType] = [Text("\n".join(body_lines))]
        planner_panel = self._build_cycle_stage_panel(
            title="Planner",
            border_style="green",
            selected_history_count=planner_selected_history_count,
            prompt_tokens=planner_prompt_tokens,
            model_name=planner_model_name,
            response_text=planner_response,
            prompt_section=planner_prompt_section,
            extra_lines=planner_extra_lines,
        )
        if planner_panel is not None:
            renderables.append(planner_panel)

        planner_tool_cards = self._build_tool_activity_cards(
            stage_title="Planner Tool",
            tool_calls=planner_tool_calls,
            tool_results=planner_tool_results,
            tool_detail_results=planner_tool_detail_results,
            planner_style=True,
        )
        if planner_tool_cards:
            renderables.extend(planner_tool_cards)

        console.print(
            Panel(
                Group(*renderables),
                title=panel_title,
                subtitle=panel_subtitle,
                border_style="bright_blue",
                padding=(0, 1),
            )
        )

    def _build_cycle_stage_panel(
        self,
        *,
        title: str,
        border_style: str,
        selected_history_count: Optional[int],
        prompt_tokens: Optional[int],
        model_name: Optional[str] = None,
        response_text: str = "",
        prompt_section: Optional[RenderableType] = None,
        extra_lines: Optional[list[str]] = None,
    ) -> Optional[Panel]:
        """构建单个 cycle 阶段的展示卡片。"""

        has_content = any([
            selected_history_count is not None,
            prompt_tokens is not None,
            bool((model_name or "").strip()),
            bool(response_text.strip()),
            prompt_section is not None,
            bool(extra_lines),
        ])
        if not has_content:
            return None

        body_lines: list[str] = []
        normalized_model_name = (model_name or "").strip()
        if normalized_model_name:
            body_lines.append(f"请求模型：{normalized_model_name}")
        if prompt_tokens is not None:
            body_lines.append(f"本次请求token消耗：{format_token_count(prompt_tokens)}")
        if extra_lines:
            body_lines.extend([line for line in extra_lines if isinstance(line, str) and line.strip()])

        renderables: list[RenderableType] = []
        if body_lines:
            renderables.append(Text("\n".join(body_lines)))
        if prompt_section is not None:
            renderables.append(prompt_section)

        normalized_response = response_text.strip()
        if normalized_response:
            renderables.append(
                Panel(
                    Text(normalized_response),
                    title="Maisaka 返回",
                    border_style=border_style,
                    padding=(0, 1),
                )
            )

        return Panel(
            Group(*renderables),
            title=title,
            border_style=border_style,
            padding=(0, 1),
        )

    def _build_tool_activity_cards(
        self,
        *,
        stage_title: str,
        tool_calls: Optional[list[Any]] = None,
        tool_results: Optional[list[str]] = None,
        tool_detail_results: Optional[list[dict[str, Any]]] = None,
        planner_style: bool = False,
    ) -> list[RenderableType]:
        """构建与阶段同级的工具执行卡片列表。"""

        detail_results = tool_detail_results or []
        cards = self._build_tool_detail_cards(
            detail_results,
            stage_title=stage_title,
            planner_style=planner_style,
        )
        if cards:
            return cards

        # 兼容旧数据结构：若尚无 detail，则降级为简单文本卡片。
        fallback_lines = self._filter_redundant_tool_results(
            tool_results=tool_results or [],
            tool_detail_results=detail_results,
        )
        if not fallback_lines and tool_calls:
            fallback_lines = build_tool_call_summary_lines(tool_calls)
        if not fallback_lines:
            return []

        fallback_border_style = "yellow"
        return [
            Panel(
                Text("\n".join(fallback_lines)),
                title=stage_title,
                border_style=fallback_border_style,
                padding=(0, 1),
            )
        ]

    @staticmethod
    def _build_cycle_time_records_text(time_records: dict[str, float]) -> str:
        """构建循环最外层面板展示的阶段耗时文本。"""

        if not time_records:
            return "流程耗时：无"

        label_map = {
            "planner": "Planner",
            "tool_calls": "工具执行",
        }
        ordered_keys = ["planner", "tool_calls"]

        parts: list[str] = []
        for key in ordered_keys:
            duration = time_records.get(key)
            if isinstance(duration, (int, float)):
                parts.append(f"{label_map.get(key, key)} {float(duration):.2f} s")

        for key, duration in time_records.items():
            if key in ordered_keys or not isinstance(duration, (int, float)):
                continue
            parts.append(f"{label_map.get(key, key)} {float(duration):.2f} s")

        if not parts:
            return "流程耗时：无"
        return "流程耗时：" + " | ".join(parts)

    @staticmethod
    def _filter_redundant_tool_results(
        *,
        tool_results: list[str],
        tool_detail_results: list[dict[str, Any]],
    ) -> list[str]:
        """过滤掉已经在详情卡片中展示过的工具摘要。"""

        detailed_summaries = {
            str(tool_result.get("summary") or "").strip()
            for tool_result in tool_detail_results
            if isinstance(tool_result.get("detail"), dict) and tool_result.get("detail")
        }
        return [
            result.strip()
            for result in tool_results
            if isinstance(result, str)
            and result.strip()
            and result.strip() not in detailed_summaries
        ]

    @staticmethod
    def _build_tool_metrics_text(metrics: dict[str, Any]) -> str:
        """将工具监控 metrics 转换为便于 CLI 阅读的文本。"""

        lines: list[str] = []
        model_name = str(metrics.get("model_name") or "").strip()
        if model_name:
            lines.append(f"模型：{model_name}")

        prompt_tokens = metrics.get("prompt_tokens")
        completion_tokens = metrics.get("completion_tokens")
        total_tokens = metrics.get("total_tokens")
        if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int) or isinstance(total_tokens, int):
            lines.append(
                "Token："
                f"输入 {format_token_count(int(prompt_tokens or 0))} / "
                f"输出 {format_token_count(int(completion_tokens or 0))} / "
                f"总计 {format_token_count(int(total_tokens or 0))}"
            )

        prompt_ms = metrics.get("prompt_ms")
        llm_ms = metrics.get("llm_ms")
        overall_ms = metrics.get("overall_ms")
        timing_parts: list[str] = []
        if isinstance(prompt_ms, (int, float)):
            timing_parts.append(f"prompt {round(float(prompt_ms), 2)} ms")
        if isinstance(llm_ms, (int, float)):
            timing_parts.append(f"llm {round(float(llm_ms), 2)} ms")
        if isinstance(overall_ms, (int, float)):
            timing_parts.append(f"overall {round(float(overall_ms), 2)} ms")
        if timing_parts:
            lines.append("耗时：" + " / ".join(timing_parts))

        return "\n".join(lines)

    @staticmethod
    def _get_tool_detail_labels(tool_name: str) -> dict[str, str]:
        """返回不同工具对应的详情区标题与预览类别。"""

        normalized_tool_name = str(tool_name or "").strip().lower()
        if normalized_tool_name == "reply":
            return {
                "prompt_title": "Reply Prompt",
                "reasoning_title": "Reply 思考",
                "output_title": "Reply 输出",
                "prompt_category": "replyer",
                "request_kind": "replyer",
            }
        if normalized_tool_name == "send_emoji":
            return {
                "prompt_title": "Emotion Prompt",
                "reasoning_title": "Emotion 思考",
                "output_title": "Emotion 输出",
                "prompt_category": "emotion",
                "request_kind": "emotion",
            }
        display_name = normalized_tool_name or "tool"
        return {
            "prompt_title": f"{display_name} Prompt",
            "reasoning_title": f"{display_name} 思考",
            "output_title": f"{display_name} 输出",
            "prompt_category": display_name,
            "request_kind": "sub_agent",
        }

    def _build_tool_prompt_access_panel(
        self,
        *,
        tool_name: str,
        prompt_text: str,
        request_messages: Optional[list[Any]] = None,
        tool_call_id: str,
        border_style: str = "bright_yellow",
        output_content: str = "",
        metadata: Optional[dict[str, Any]] = None,
        prompt_title: str = "",
        prompt_category: str = "",
        request_kind: str = "",
        selection_reason: str = "",
    ) -> ToolPromptAccessPanel:
        """将工具 prompt 渲染为可点击查看的预览入口。"""

        labels = self._get_tool_detail_labels(tool_name)
        active_prompt_title = prompt_title.strip() or labels["prompt_title"]
        active_prompt_category = prompt_category.strip() or labels["prompt_category"]
        active_request_kind = request_kind.strip() or labels["request_kind"]
        subtitle = selection_reason.strip() or f"会话ID: {self.session_id}"
        if tool_call_id:
            subtitle += f"\n调用ID: {tool_call_id}"

        if isinstance(request_messages, list) and request_messages:
            try:
                normalized_messages = deserialize_prompt_messages(request_messages)
            except Exception as exc:
                logger.debug(
                    f"工具 {tool_name} 的 request_messages 无法还原为模型消息，将使用瘦身结构预览: {exc}"
                )
                normalized_messages = request_messages
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                normalized_messages,
                category=active_prompt_category,
                chat_id=self.session_id,
                request_kind=active_request_kind,
                selection_reason=subtitle,
                output_content=output_content,
                metadata=metadata,
            )
            return ToolPromptAccessPanel(
                panel=Panel(
                    preview_access.body,
                    title=active_prompt_title,
                    border_style=border_style,
                    padding=(0, 1),
                ),
                prompt_html_uri=preview_access.preview_web_uri,
            )

        preview_access = PromptCLIVisualizer.build_text_preview_access(
            prompt_text,
            category=active_prompt_category,
            chat_id=self.session_id,
            request_kind=active_request_kind,
            subtitle=subtitle,
            output_content=output_content,
            metadata=metadata,
        )
        return ToolPromptAccessPanel(
            panel=Panel(
                preview_access.body,
                title=active_prompt_title,
                border_style=border_style,
                padding=(0, 1),
            ),
            prompt_html_uri=preview_access.preview_web_uri,
        )

    @staticmethod
    def _build_prompt_preview_metadata_from_tool_metrics(metrics: Any) -> dict[str, Any]:
        """从工具监控 metrics 中提取可写入 Prompt 预览的模型与耗时。"""

        if not isinstance(metrics, dict):
            return {}

        metadata: dict[str, Any] = {}
        model_name = str(metrics.get("model_name") or "").strip()
        if model_name:
            metadata["model_name"] = model_name

        for duration_key in ("llm_ms", "overall_ms"):
            duration_ms = metrics.get(duration_key)
            if isinstance(duration_ms, (int, float)):
                metadata["duration_ms"] = duration_ms
                break

        return metadata

    def _normalize_tool_card_body_lines(self, body: Any) -> list[str]:
        """将工具卡片正文规范化为行列表。"""

        if isinstance(body, str):
            return [line for line in body.splitlines() if line.strip()]
        if isinstance(body, list):
            return [
                str(item).strip()
                for item in body
                if str(item).strip()
            ]
        return []

    def _build_custom_tool_sub_cards(
        self,
        sub_cards: Any,
        *,
        default_border_style: str,
    ) -> list[RenderableType]:
        """构建工具自定义子卡片。"""

        if not isinstance(sub_cards, list):
            return []

        renderables: list[RenderableType] = []
        for sub_card in sub_cards:
            if not isinstance(sub_card, dict):
                continue
            title = str(sub_card.get("title") or "").strip() or "附加信息"
            border_style = str(sub_card.get("border_style") or "").strip() or default_border_style
            body_lines = self._normalize_tool_card_body_lines(
                sub_card.get("body_lines", sub_card.get("content", ""))
            )
            if not body_lines:
                continue
            renderables.append(
                Panel(
                    Text("\n".join(body_lines)),
                    title=title,
                    border_style=border_style,
                    padding=(0, 1),
                )
            )
        return renderables

    def _build_default_tool_detail_parts(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_args: Any,
        summary: str,
        duration_ms: Any,
        detail: dict[str, Any],
        planner_style: bool,
    ) -> list[RenderableType]:
        """构建工具卡片默认内容块。"""

        argument_border_style = "yellow"
        metrics_border_style = "bright_yellow"
        prompt_border_style = "bright_yellow"
        reasoning_border_style = "yellow"
        output_border_style = "bright_yellow"
        extra_info_border_style = "yellow"
        detail_labels = self._get_tool_detail_labels(tool_name)

        parts: list[RenderableType] = []
        header_lines: list[str] = []
        if summary:
            header_lines.append(summary)
        if tool_call_id:
            header_lines.append(f"调用ID：{tool_call_id}")
        if isinstance(duration_ms, (int, float)):
            header_lines.append(f"执行耗时：{round(float(duration_ms), 2)} ms")
        if header_lines:
            parts.append(Text("\n".join(header_lines)))

        if isinstance(tool_args, dict) and tool_args:
            parts.append(
                Panel(
                    Pretty(tool_args, expand_all=True),
                    title="工具参数",
                    border_style=argument_border_style,
                    padding=(0, 1),
                )
            )

        metrics = detail.get("metrics")
        preview_metadata = self._build_prompt_preview_metadata_from_tool_metrics(metrics)
        if isinstance(metrics, dict):
            metrics_text = self._build_tool_metrics_text(metrics)
            if metrics_text:
                parts.append(
                    Panel(
                        Text(metrics_text),
                        title="执行指标",
                        border_style=metrics_border_style,
                        padding=(0, 1),
                    )
                )

        output_text = str(detail.get("output_text") or "").strip()
        prompt_text = str(detail.get("prompt_text") or "").strip()
        request_messages = detail.get("request_messages") if isinstance(detail.get("request_messages"), list) else None
        if prompt_text or request_messages:
            prompt_access_panel = self._build_tool_prompt_access_panel(
                tool_name=tool_name,
                prompt_text=prompt_text,
                request_messages=request_messages,
                tool_call_id=tool_call_id,
                border_style=prompt_border_style,
                output_content=output_text,
                metadata=preview_metadata,
            )
            if prompt_access_panel.prompt_html_uri:
                detail["prompt_html_uri"] = prompt_access_panel.prompt_html_uri
            parts.append(prompt_access_panel.panel)

        additional_prompt_records = detail.get("additional_prompt_records")
        if isinstance(additional_prompt_records, list):
            prompt_record_uris: list[str] = []
            for index, record in enumerate(additional_prompt_records, start=1):
                if not isinstance(record, dict):
                    continue
                record_prompt_text = str(record.get("prompt_text") or "").strip()
                record_request_messages = (
                    record.get("request_messages") if isinstance(record.get("request_messages"), list) else None
                )
                if not record_prompt_text and not record_request_messages:
                    continue
                record_metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
                record_metadata = self._build_prompt_preview_metadata_from_tool_metrics(record_metrics)
                prompt_access_panel = self._build_tool_prompt_access_panel(
                    tool_name=tool_name,
                    prompt_text=record_prompt_text,
                    request_messages=record_request_messages,
                    tool_call_id=tool_call_id,
                    border_style=prompt_border_style,
                    output_content=str(record.get("output_text") or ""),
                    metadata=record_metadata,
                    prompt_title=str(record.get("prompt_title") or f"Prompt #{index}"),
                    prompt_category=str(record.get("prompt_category") or ""),
                    request_kind=str(record.get("request_kind") or ""),
                    selection_reason=str(record.get("selection_reason") or ""),
                )
                if prompt_access_panel.prompt_html_uri:
                    prompt_record_uris.append(prompt_access_panel.prompt_html_uri)
                parts.append(prompt_access_panel.panel)
            if prompt_record_uris:
                detail["additional_prompt_html_uris"] = prompt_record_uris

        reasoning_text = str(detail.get("reasoning_text") or "").strip()
        if reasoning_text:
            parts.append(
                Panel(
                    Text(reasoning_text),
                    title=detail_labels["reasoning_title"],
                    border_style=reasoning_border_style,
                    padding=(0, 1),
                )
            )

        if output_text:
            parts.append(
                Panel(
                    Text(output_text),
                    title=detail_labels["output_title"],
                    border_style=output_border_style,
                    padding=(0, 1),
                )
            )

        extra_sections = detail.get("extra_sections")
        if isinstance(extra_sections, list):
            for section in extra_sections:
                if not isinstance(section, dict):
                    continue
                section_title = str(section.get("title") or "").strip() or "附加信息"
                section_content = str(section.get("content") or "").strip()
                if not section_content:
                    continue
                parts.append(
                    Panel(
                        Text(section_content),
                        title=section_title,
                        border_style=extra_info_border_style,
                        padding=(0, 1),
                    )
                )

        return parts

    def _build_tool_detail_cards(
        self,
        tool_detail_results: list[dict[str, Any]],
        *,
        stage_title: str,
        planner_style: bool = False,
    ) -> list[RenderableType]:
        """将 tool monitor detail 渲染为与 Planner/Timing 平级的工具卡片。"""

        detail_panel_border_style = "yellow"
        sub_card_border_style = "bright_yellow"

        panels: list[RenderableType] = []
        for tool_result in tool_detail_results:
            detail = tool_result.get("detail")
            detail_dict = detail if isinstance(detail, dict) else {}
            tool_name = str(tool_result.get("tool_name") or "unknown").strip() or "unknown"
            tool_title = str(tool_result.get("tool_title") or "").strip() or tool_name
            tool_call_id = str(tool_result.get("tool_call_id") or "").strip()
            tool_args = tool_result.get("tool_args")
            summary = str(tool_result.get("summary") or "").strip()
            duration_ms = tool_result.get("duration_ms")
            custom_card = tool_result.get("card")

            parts: list[RenderableType] = []
            custom_title = ""
            card_border_style = detail_panel_border_style
            replace_default_children = False
            if isinstance(custom_card, dict):
                custom_title = str(custom_card.get("title") or "").strip()
                card_border_style = str(custom_card.get("border_style") or "").strip() or detail_panel_border_style
                replace_default_children = bool(custom_card.get("replace_default_children", False))
                custom_body_lines = self._normalize_tool_card_body_lines(
                    custom_card.get("body_lines", custom_card.get("content", ""))
                )
                if custom_body_lines:
                    parts.append(Text("\n".join(custom_body_lines)))

            if not replace_default_children:
                parts.extend(
                    self._build_default_tool_detail_parts(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_args=tool_args,
                        summary=summary,
                        duration_ms=duration_ms,
                        detail=detail_dict,
                        planner_style=planner_style,
                    )
                )

            if isinstance(custom_card, dict):
                parts.extend(
                    self._build_custom_tool_sub_cards(
                        custom_card.get("sub_cards"),
                        default_border_style=sub_card_border_style,
                    )
                )
            parts.extend(
                self._build_custom_tool_sub_cards(
                    tool_result.get("sub_cards"),
                    default_border_style=sub_card_border_style,
                )
            )

            if parts:
                panels.append(
                    Panel(
                        Group(*parts),
                        title=custom_title or f"{stage_title} · {tool_title}",
                        border_style=card_border_style,
                        padding=(0, 1),
                    )
                )

        return panels

    def _log_cycle_started(self, cycle_detail: CycleDetail, round_index: int) -> None:
        logger.debug(
            f"{self.log_prefix} MaiSaka 轮次开始: 循环编号={cycle_detail.cycle_id} "
            f"回合={round_index + 1}/{self._max_internal_rounds} "
            f"上下文消息数={len(self._chat_history)}"
        )

    def _log_cycle_completed(self, cycle_detail: CycleDetail, timer_strings: list[str]) -> None:
        end_time = cycle_detail.end_time if cycle_detail.end_time is not None else cycle_detail.start_time
        logger.debug(
            f"{self.log_prefix} MaiSaka 轮次结束: 循环编号={cycle_detail.cycle_id} "
            f"总耗时={end_time - cycle_detail.start_time:.2f} 秒; "
            f"阶段耗时={', '.join(timer_strings) if timer_strings else '无'}"
        )

    def _log_history_trimmed(self, removed_count: int, user_message_count: int) -> None:
        logger.debug(
            f"{self.log_prefix} 已裁剪 {removed_count} 条历史消息; "
            # f"剩余计入上下文的消息数={user_message_count}"
        )

    def _log_internal_loop_cancelled(self) -> None:
        logger.info(f"{self.log_prefix} Maisaka 内部循环已取消")

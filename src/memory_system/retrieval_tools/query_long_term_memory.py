"""通过统一长期记忆服务查询信息。"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Iterable, Literal, Tuple

from src.common.logger import get_logger
from src.services.memory_service import MemoryHit, MemorySearchResult, memory_service

from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")

_SUPPORTED_MODES = {"search", "time", "episode", "aggregate"}
_RELATIVE_DAYS_RE = re.compile(r"^最近\s*(\d+)\s*天$")
_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MINUTE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}$")
_TIME_EXPRESSION_HELP = (
    "请改用更具体的时间表达，例如：今天、昨天、前天、本周、上周、本月、上月、最近7天、"
    "2026/03/18、2026/03/18 09:30。"
)


def _format_query_datetime(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M")


def _resolve_time_expression(
    expression: str,
    *,
    now: datetime | None = None,
) -> Tuple[float, float, str, str]:
    clean = str(expression or "").strip()
    if not clean:
        raise ValueError(f"time 模式需要提供 time_expression。{_TIME_EXPRESSION_HELP}")

    current = now or datetime.now()
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)

    if clean == "今天":
        start = day_start
        end = day_start.replace(hour=23, minute=59)
    elif clean == "昨天":
        start = day_start - timedelta(days=1)
        end = start.replace(hour=23, minute=59)
    elif clean == "前天":
        start = day_start - timedelta(days=2)
        end = start.replace(hour=23, minute=59)
    elif clean == "本周":
        start = day_start - timedelta(days=day_start.weekday())
        end = start + timedelta(days=6, hours=23, minutes=59)
    elif clean == "上周":
        this_week_start = day_start - timedelta(days=day_start.weekday())
        start = this_week_start - timedelta(days=7)
        end = start + timedelta(days=6, hours=23, minutes=59)
    elif clean == "本月":
        start = day_start.replace(day=1)
        last_day = monthrange(start.year, start.month)[1]
        end = start.replace(day=last_day, hour=23, minute=59)
    elif clean == "上月":
        year = day_start.year
        month = day_start.month - 1
        if month == 0:
            year -= 1
            month = 12
        start = day_start.replace(year=year, month=month, day=1)
        last_day = monthrange(year, month)[1]
        end = start.replace(day=last_day, hour=23, minute=59)
    else:
        relative_match = _RELATIVE_DAYS_RE.fullmatch(clean)
        if relative_match:
            days = max(1, int(relative_match.group(1)))
            start = day_start - timedelta(days=max(0, days - 1))
            end = day_start.replace(hour=23, minute=59)
        elif _DATE_RE.fullmatch(clean):
            start = datetime.strptime(clean, "%Y/%m/%d")
            end = start.replace(hour=23, minute=59)
        elif _MINUTE_RE.fullmatch(clean):
            start = datetime.strptime(clean, "%Y/%m/%d %H:%M")
            end = start
        else:
            raise ValueError(f"时间表达“{clean}”无法解析。{_TIME_EXPRESSION_HELP}")

    return start.timestamp(), end.timestamp(), _format_query_datetime(start), _format_query_datetime(end)


def _extract_time_label(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    start = metadata.get("event_time_start")
    end = metadata.get("event_time_end")
    event_time = metadata.get("event_time")

    def _fmt(value: object) -> str:
        if value in {None, ""}:
            return ""
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y/%m/%d %H:%M")
        except Exception:
            return str(value)

    start_text = _fmt(start or event_time)
    end_text = _fmt(end)
    if start_text and end_text:
        return f"{start_text} - {end_text}"
    return start_text or end_text


def _truncate(text: str, limit: int = 160) -> str:
    compact = str(text or "").strip().replace("\n", " ")
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _format_search_lines(hits: Iterable[MemoryHit], *, limit: int, include_time: bool = False) -> str:
    lines = []
    for index, item in enumerate(list(hits)[: max(1, int(limit))], start=1):
        time_label = _extract_time_label(item.metadata) if include_time else ""
        prefix = f"[{time_label}] " if time_label else ""
        lines.append(f"{index}. {prefix}{_truncate(item.content)}")
    return "\n".join(lines)


def _format_episode_lines(hits: Iterable[MemoryHit], *, limit: int) -> str:
    lines = []
    for index, item in enumerate(list(hits)[: max(1, int(limit))], start=1):
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        title = str(item.title or "").strip() or "未命名事件"
        summary = _truncate(item.content, limit=180)
        participants = [str(x).strip() for x in (metadata.get("participants") or []) if str(x).strip()]
        keywords = [str(x).strip() for x in (metadata.get("keywords") or []) if str(x).strip()]
        extras = []
        if participants:
            extras.append(f"参与者：{'、'.join(participants[:4])}")
        if keywords:
            extras.append(f"关键词：{'、'.join(keywords[:6])}")
        time_label = _extract_time_label(metadata)
        if time_label:
            extras.append(f"时间：{time_label}")
        suffix = f"（{'；'.join(extras)}）" if extras else ""
        lines.append(f"{index}. 事件《{title}》：{summary}{suffix}")
    return "\n".join(lines)


def _format_aggregate_lines(hits: Iterable[MemoryHit], *, limit: int) -> str:
    lines = []
    for index, item in enumerate(list(hits)[: max(1, int(limit))], start=1):
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        source_branches = [str(x).strip() for x in (metadata.get("source_branches") or []) if str(x).strip()]
        branch_text = f"[{','.join(source_branches)}]" if source_branches else ""
        item_type = str(item.hit_type or "").strip().lower() or "memory"
        if item_type == "episode":
            title = str(item.title or "").strip() or "未命名事件"
            lines.append(f"{index}. {branch_text}[episode] 《{title}》：{_truncate(item.content, 160)}")
        else:
            lines.append(f"{index}. {branch_text}[{item_type}] {_truncate(item.content, 160)}")
    return "\n".join(lines)


def _format_tool_result(
    *,
    result: MemorySearchResult,
    mode: Literal["search", "time", "episode", "aggregate"],
    limit: int,
    query: str,
    time_range_text: str = "",
) -> str:
    if not result.success:
        return f"长期记忆查询失败：{result.error or '未知错误'}"

    if not result.hits:
        if mode == "time":
            return f"在指定时间范围内未找到相关的长期记忆{time_range_text}"
        if mode == "episode":
            return f"未找到与“{query}”相关的事件或情节记忆"
        if mode == "aggregate":
            return f"未找到可用于综合回忆的长期记忆线索{f'（query：{query}）' if query else ''}"
        return f"在长期记忆中未找到与“{query}”相关的信息"

    if mode == "episode":
        text = _format_episode_lines(result.hits, limit=limit)
        return f"你从长期记忆的事件/情节中找到以下信息：\n{text}"

    if mode == "aggregate":
        text = _format_aggregate_lines(result.hits, limit=limit)
        return f"你从长期记忆中综合找到了以下线索：\n{text}"

    if mode == "time":
        text = _format_search_lines(result.hits, limit=limit, include_time=True)
        return f"你从指定时间范围内的长期记忆中找到以下信息{time_range_text}：\n{text}"

    text = _format_search_lines(result.hits, limit=limit)
    return f"你从长期记忆中找到以下信息：\n{text}"


async def query_long_term_memory(
    query: str = "",
    limit: int = 5,
    chat_id: str = "",
    person_id: str = "",
    mode: str = "search",
    time_expression: str = "",
) -> str:
    content = str(query or "").strip()
    safe_limit = max(1, int(limit or 5))
    normalized_mode = str(mode or "search").strip().lower() or "search"
    if normalized_mode not in _SUPPORTED_MODES:
        return f"不支持的长期记忆检索模式：{normalized_mode}。可用模式：search、time、episode、aggregate。"

    if normalized_mode == "search" and not content:
        return "查询关键词为空，请提供你想查找的长期记忆内容。"
    if normalized_mode == "time" and not str(time_expression or "").strip():
        return f"time 模式需要提供 time_expression。{_TIME_EXPRESSION_HELP}"
    if normalized_mode in {"episode", "aggregate"} and not content and not str(time_expression or "").strip():
        return f"{normalized_mode} 模式至少需要提供 query 或 time_expression。"

    time_start = None
    time_end = None
    time_range_text = ""
    if str(time_expression or "").strip():
        try:
            time_start, time_end, time_start_text, time_end_text = _resolve_time_expression(time_expression)
        except ValueError as exc:
            return str(exc)
        time_range_text = f"（时间范围：{time_start_text} 至 {time_end_text}）"

    backend_mode = normalized_mode

    try:
        result = await memory_service.search(
            content,
            limit=safe_limit,
            mode=backend_mode,
            chat_id=str(chat_id or "").strip(),
            person_id=str(person_id or "").strip(),
            time_start=time_start,
            time_end=time_end,
        )
        text = _format_tool_result(
            result=result,
            mode=normalized_mode,  # type: ignore[arg-type]
            limit=safe_limit,
            query=content,
            time_range_text=time_range_text,
        )
        logger.debug(f"长期记忆查询结果({normalized_mode}): {text}")
        return text
    except Exception as exc:
        logger.error(f"长期记忆查询失败: {exc}")
        return f"长期记忆查询失败：{exc}"


def register_tool():
    register_memory_retrieval_tool(
        name="search_long_term_memory",
        description=(
            "从长期记忆中检索信息。支持 search（普通事实检索）、time（按时间范围检索）、"
            "episode（按事件/情节检索）、aggregate（综合检索）四种模式。"
        ),
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "需要查询的问题。search 模式建议用自然语言问句；time/episode/aggregate 模式也可用关键词短语。",
                "required": False,
            },
            {
                "name": "mode",
                "type": "string",
                "description": "检索模式：search（普通长期记忆）、time（按时间窗口）、episode（事件/情节）、aggregate（综合检索）。",
                "required": False,
                "enum": ["search", "time", "episode", "aggregate"],
            },
            {
                "name": "limit",
                "type": "integer",
                "description": "希望返回的相关知识条数，默认为5",
                "required": False,
            },
            {
                "name": "chat_id",
                "type": "string",
                "description": "当前聊天流ID，可选。提供后优先检索当前聊天上下文相关的长期记忆。",
                "required": False,
            },
            {
                "name": "person_id",
                "type": "string",
                "description": "相关人物ID，可选。提供后优先检索该人物相关的长期记忆。",
                "required": False,
            },
            {
                "name": "time_expression",
                "type": "string",
                "description": (
                    "时间表达，可选。time 模式必填；episode/aggregate 模式可选。支持：今天、昨天、前天、本周、上周、本月、上月、"
                    "最近N天，以及 YYYY/MM/DD、YYYY/MM/DD HH:mm。"
                ),
                "required": False,
            },
        ],
        execute_func=query_long_term_memory,
    )

"""LLM prompt cache statistics and local dynamic-diff diagnostics."""

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from math import erf, sqrt
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Tuple

import json
import time
import uuid

from src.common.logger import get_logger

logger = get_logger("llm_cache_stats")

FOCUSED_TASK_NAMES = {"replyer", "planner"}
EXCLUDED_REQUEST_TYPES = {
    "A_Memorix.ChatSummarization",
    "expression.learner",
    "jargon.learner",
    "reply.effect_judge",
}
REPORT_INTERVAL_SECONDS = 300
REPORT_INTERVAL_CALLS = 50
SUMMARY_LIMIT = 5
PROMPT_CACHE_POOL_SIZE = 128
# 统计键包含 session_id，长期运行会随会话数量无界增长；
# 超过上限后按插入顺序 FIFO 淘汰最旧的键（调试统计用途，淘汰不影响正确性）。
MAX_CACHE_STATS_KEYS = 256
CACHE_STATS_DIR = Path("logs") / "llm_cache_stats"
REPORT_FILE_NAME = "report.html"
SESSION_REPORT_FILE_NAME = "sessions.html"
SNIPPET_LIMIT = 160
PROCESS_STARTED_AT = datetime.now().isoformat(timespec="seconds")
RUN_ID = f"{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class LLMCacheStat:
    """Aggregated prompt cache stats for one task/request/model call site."""

    task_name: str
    request_type: str
    model_name: str
    session_id: str = ""
    calls: int = 0
    cache_reported_calls: int = 0
    prompt_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    theoretical_prompt_cache_hit_tokens: int = 0
    theoretical_prompt_cache_miss_tokens: int = 0
    theoretical_compared_calls: int = 0
    theoretical_cache_pool_hits: int = 0
    common_prefix_rate_total: float = 0.0
    suspected_context_sliding_calls: int = 0
    sliding_dropped_messages_total: int = 0
    sliding_aligned_messages_total: int = 0
    dynamic_diff_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def prompt_cache_total_tokens(self) -> int:
        return self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens

    @property
    def prompt_cache_hit_rate(self) -> float:
        total_tokens = self.prompt_cache_total_tokens
        if total_tokens <= 0:
            return 0.0
        return self.prompt_cache_hit_tokens / total_tokens * 100

    @property
    def theoretical_prompt_cache_total_tokens(self) -> int:
        return self.theoretical_prompt_cache_hit_tokens + self.theoretical_prompt_cache_miss_tokens

    @property
    def theoretical_prompt_cache_hit_rate(self) -> float:
        total_tokens = self.theoretical_prompt_cache_total_tokens
        if total_tokens <= 0:
            return 0.0
        return self.theoretical_prompt_cache_hit_tokens / total_tokens * 100

    @property
    def prompt_cache_hit_rate_delta(self) -> float:
        return self.prompt_cache_hit_rate - self.theoretical_prompt_cache_hit_rate

    def _format_top_dynamic_diff_paths(self) -> str:
        if not self.dynamic_diff_counts:
            return ""
        top_items = sorted(
            self.dynamic_diff_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:SUMMARY_LIMIT]
        return "; ".join(f"{path} ({count})" for path, count in top_items)

    def to_dict(self) -> Dict[str, int | str | float]:
        return {
            "task_name": self.task_name,
            "request_type": self.request_type,
            "model_name": self.model_name,
            "session_id": self.session_id,
            "calls": self.calls,
            "cache_reported_calls": self.cache_reported_calls,
            "prompt_tokens": self.prompt_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
            "prompt_cache_hit_rate": round(self.prompt_cache_hit_rate, 2),
            "theoretical_prompt_cache_hit_tokens": self.theoretical_prompt_cache_hit_tokens,
            "theoretical_prompt_cache_miss_tokens": self.theoretical_prompt_cache_miss_tokens,
            "theoretical_compared_calls": self.theoretical_compared_calls,
            "theoretical_cache_pool_hits": self.theoretical_cache_pool_hits,
            "theoretical_prompt_cache_hit_rate": round(self.theoretical_prompt_cache_hit_rate, 2),
            "prompt_cache_hit_rate_delta": round(self.prompt_cache_hit_rate_delta, 2),
            "avg_common_prefix_rate": round(self.common_prefix_rate_total / self.calls, 2) if self.calls else 0.0,
            "suspected_context_sliding_calls": self.suspected_context_sliding_calls,
            "avg_sliding_dropped_messages": (
                round(self.sliding_dropped_messages_total / self.suspected_context_sliding_calls, 2)
                if self.suspected_context_sliding_calls
                else 0.0
            ),
            "avg_sliding_aligned_messages": (
                round(self.sliding_aligned_messages_total / self.suspected_context_sliding_calls, 2)
                if self.suspected_context_sliding_calls
                else 0.0
            ),
            "top_dynamic_diff_paths": self._format_top_dynamic_diff_paths(),
        }


@dataclass(slots=True)
class _TheoreticalCacheMatch:
    hit_tokens: int
    miss_tokens: int
    hit_rate: float
    compared: bool
    pool_size: int
    best_match_rank: int
    best_prompt_text: str | None
    common_prefix_chars: int


@dataclass(slots=True)
class _DynamicDiff:
    path: str
    previous_value: str
    current_value: str


@dataclass(slots=True)
class _PromptCacheDiagnostics:
    current_message_count: int = 0
    best_match_message_count: int = 0
    common_prefix_messages: int = 0
    common_suffix_messages: int = 0
    common_prefix_rate: float = 0.0
    prompt_growth_chars: int = 0
    longest_aligned_message_overlap: int = 0
    aligned_previous_start_index: int = 0
    aligned_current_start_index: int = 0
    suspected_context_sliding: bool = False
    sliding_dropped_head_messages: int = 0
    sliding_aligned_messages: int = 0
    sliding_new_tail_messages: int = 0
    current_first_message_role: str = ""
    best_first_message_role: str = ""
    current_last_message_role: str = ""
    best_last_message_role: str = ""


@dataclass(slots=True)
class _LLMCacheStatsStore:
    stats: Dict[Tuple[str, str, str, str], LLMCacheStat] = field(default_factory=dict)
    prompt_pools: Dict[Tuple[str, str, str, str], List[str]] = field(default_factory=dict)
    total_calls: int = 0
    run_id: str = RUN_ID
    process_started_at: str = PROCESS_STARTED_AT
    calls_in_run: int = 0
    last_report_at: float = 0
    calls_since_report: int = 0
    lock: RLock = field(default_factory=RLock)


_store = _LLMCacheStatsStore()


def _is_llm_cache_stats_enabled() -> bool:
    """读取调试配置，默认关闭 LLM prompt cache 统计。"""

    try:
        from src.config.config import global_config
        return bool(global_config.debug.enable_llm_cache_stats)
    except Exception:
        return False


def _normalize_request_type(request_type: str) -> str:
    normalized = str(request_type or "").strip()
    return normalized or "unknown"


def _normalize_model_name(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    return normalized or "unknown"


def _normalize_session_id(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    return normalized or "unknown"


def _evict_oldest_cache_stats_keys_locked() -> None:
    """限制统计键数量，防止 stats/prompt_pools 随会话数无界增长（需持有 _store.lock）。"""

    overflow = len(_store.stats) - MAX_CACHE_STATS_KEYS
    if overflow <= 0:
        return
    for key in list(_store.stats)[:overflow]:
        _store.stats.pop(key, None)
        _store.prompt_pools.pop(key, None)


def _normalize_cache_tokens(
    *,
    prompt_tokens: int,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
) -> tuple[int, int, bool]:
    hit_tokens = max(int(prompt_cache_hit_tokens or 0), 0)
    miss_tokens = max(int(prompt_cache_miss_tokens or 0), 0)
    has_cache_report = hit_tokens > 0 or miss_tokens > 0

    if miss_tokens == 0 and hit_tokens > 0:
        miss_tokens = max(prompt_tokens - hit_tokens, 0)
    elif hit_tokens == 0 and miss_tokens == 0 and prompt_tokens > 0:
        # Some providers do not return cache details. Treat it as all miss, while keeping reported_calls separate.
        miss_tokens = prompt_tokens

    return hit_tokens, miss_tokens, has_cache_report


def _longest_common_prefix_length(left: str, right: str) -> int:
    max_length = min(len(left), len(right))
    for index in range(max_length):
        if left[index] != right[index]:
            return index
    return max_length


def _calculate_theoretical_cache_match(
    *,
    prompt_tokens: int,
    prompt_text: str | None,
    prompt_pool: List[str],
) -> _TheoreticalCacheMatch:
    """Estimate local theoretical cache hit by matching against the whole prompt pool."""

    if not prompt_text:
        return _TheoreticalCacheMatch(0, 0, 0.0, False, 0, 0, None, 0)
    if not prompt_pool:
        return _TheoreticalCacheMatch(0, prompt_tokens, 0.0, True, 0, 0, None, 0)

    best_prefix_length = 0
    best_match_rank = 0
    best_prompt_text: str | None = None
    # rank=1 means the newest cached prompt in this local pool.
    for rank, cached_prompt_text in enumerate(reversed(prompt_pool), start=1):
        prefix_length = _longest_common_prefix_length(cached_prompt_text, prompt_text)
        if prefix_length > best_prefix_length:
            best_prefix_length = prefix_length
            best_match_rank = rank
            best_prompt_text = cached_prompt_text

    overlap_rate = best_prefix_length / len(prompt_text) if prompt_text else 0.0
    theoretical_hit_tokens = min(prompt_tokens, round(prompt_tokens * overlap_rate))
    theoretical_miss_tokens = max(prompt_tokens - theoretical_hit_tokens, 0)
    return _TheoreticalCacheMatch(
        theoretical_hit_tokens,
        theoretical_miss_tokens,
        overlap_rate * 100,
        True,
        len(prompt_pool),
        best_match_rank,
        best_prompt_text,
        best_prefix_length,
    )


def _summarize_value(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.replace("\n", "\\n")
    else:
        normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(normalized) > SNIPPET_LIMIT:
        return normalized[:SNIPPET_LIMIT] + "..."
    return normalized


def _find_first_structural_diff(previous_value: Any, current_value: Any, path: str = "root") -> _DynamicDiff | None:
    if type(previous_value) is not type(current_value):
        return _DynamicDiff(
            f"{path}.__type__",
            type(previous_value).__name__,
            type(current_value).__name__,
        )

    if isinstance(previous_value, dict):
        previous_keys = set(previous_value)
        current_keys = set(current_value)
        for key in sorted(previous_keys | current_keys):
            key_path = f"{path}.{key}"
            if key not in previous_value:
                return _DynamicDiff(key_path, "<missing>", _summarize_value(current_value[key]))
            if key not in current_value:
                return _DynamicDiff(key_path, _summarize_value(previous_value[key]), "<missing>")
            nested_diff = _find_first_structural_diff(previous_value[key], current_value[key], key_path)
            if nested_diff is not None:
                return nested_diff
        return None

    if isinstance(previous_value, list):
        max_length = max(len(previous_value), len(current_value))
        for index in range(max_length):
            index_path = f"{path}[{index}]"
            if index >= len(previous_value):
                return _DynamicDiff(index_path, "<missing>", _summarize_value(current_value[index]))
            if index >= len(current_value):
                return _DynamicDiff(index_path, _summarize_value(previous_value[index]), "<missing>")
            nested_diff = _find_first_structural_diff(previous_value[index], current_value[index], index_path)
            if nested_diff is not None:
                return nested_diff
        return None

    if previous_value == current_value:
        return None

    if isinstance(previous_value, str) and isinstance(current_value, str):
        diff_index = _longest_common_prefix_length(previous_value, current_value)
        return _DynamicDiff(
            f"{path}@char{diff_index}",
            _summarize_value(previous_value[diff_index:]),
            _summarize_value(current_value[diff_index:]),
        )

    return _DynamicDiff(path, _summarize_value(previous_value), _summarize_value(current_value))


def _diagnose_dynamic_diff(previous_prompt_text: str | None, current_prompt_text: str | None) -> _DynamicDiff:
    if not current_prompt_text:
        return _DynamicDiff("prompt_text.unavailable", "", "")
    if not previous_prompt_text:
        return _DynamicDiff("cache_pool.empty", "", _summarize_value(current_prompt_text))

    try:
        previous_payload = json.loads(previous_prompt_text)
        current_payload = json.loads(current_prompt_text)
    except json.JSONDecodeError:
        diff_index = _longest_common_prefix_length(previous_prompt_text, current_prompt_text)
        return _DynamicDiff(
            f"raw_prompt@char{diff_index}",
            _summarize_value(previous_prompt_text[diff_index:]),
            _summarize_value(current_prompt_text[diff_index:]),
        )

    diff = _find_first_structural_diff(previous_payload, current_payload)
    if diff is None:
        return _DynamicDiff("identical", "", "")
    return diff


def _load_prompt_payload(prompt_text: str | None) -> dict[str, Any] | None:
    if not prompt_text:
        return None
    try:
        payload = json.loads(prompt_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_prompt_messages(prompt_text: str | None) -> list[dict[str, Any]]:
    payload = _load_prompt_payload(prompt_text)
    if payload is None:
        return []
    messages = payload.get("messages")
    return [message for message in messages if isinstance(message, dict)] if isinstance(messages, list) else []


def _message_fingerprints(messages: list[dict[str, Any]]) -> list[str]:
    return [json.dumps(message, ensure_ascii=False, sort_keys=True, default=str) for message in messages]


def _count_common_prefix_items(left_items: list[str], right_items: list[str]) -> int:
    common_count = 0
    for left_item, right_item in zip(left_items, right_items, strict=False):
        if left_item != right_item:
            break
        common_count += 1
    return common_count


def _count_common_suffix_items(left_items: list[str], right_items: list[str]) -> int:
    common_count = 0
    max_count = min(len(left_items), len(right_items))
    while common_count < max_count and left_items[-common_count - 1] == right_items[-common_count - 1]:
        common_count += 1
    return common_count


def _find_longest_message_alignment(previous_items: list[str], current_items: list[str]) -> tuple[int, int, int]:
    best_overlap = 0
    best_previous_start = 0
    best_current_start = 0
    for previous_start in range(len(previous_items)):
        for current_start in range(len(current_items)):
            overlap = 0
            while (
                previous_start + overlap < len(previous_items)
                and current_start + overlap < len(current_items)
                and previous_items[previous_start + overlap] == current_items[current_start + overlap]
            ):
                overlap += 1
            if overlap > best_overlap:
                best_overlap = overlap
                best_previous_start = previous_start
                best_current_start = current_start
    return best_overlap, best_previous_start, best_current_start


def _get_message_role(messages: list[dict[str, Any]], index: int) -> str:
    if not messages:
        return ""
    try:
        value = messages[index].get("role", "")
    except IndexError:
        return ""
    return str(value or "")


def _diagnose_prompt_cache_details(
    *,
    previous_prompt_text: str | None,
    current_prompt_text: str | None,
    common_prefix_chars: int,
) -> _PromptCacheDiagnostics:
    current_messages = _extract_prompt_messages(current_prompt_text)
    previous_messages = _extract_prompt_messages(previous_prompt_text)
    current_items = _message_fingerprints(current_messages)
    previous_items = _message_fingerprints(previous_messages)
    current_prompt_length = len(current_prompt_text or "")
    previous_prompt_length = len(previous_prompt_text or "")
    common_prefix_rate = common_prefix_chars / current_prompt_length * 100 if current_prompt_length > 0 else 0.0

    common_prefix_messages = _count_common_prefix_items(previous_items, current_items)
    common_suffix_messages = _count_common_suffix_items(previous_items, current_items)
    aligned_overlap, aligned_previous_start, aligned_current_start = _find_longest_message_alignment(
        previous_items,
        current_items,
    )
    suspected_context_sliding = (
        aligned_previous_start > aligned_current_start
        and aligned_overlap > common_prefix_messages
    )
    sliding_dropped_head_messages = aligned_previous_start - aligned_current_start if suspected_context_sliding else 0

    return _PromptCacheDiagnostics(
        current_message_count=len(current_messages),
        best_match_message_count=len(previous_messages),
        common_prefix_messages=common_prefix_messages,
        common_suffix_messages=common_suffix_messages,
        common_prefix_rate=common_prefix_rate,
        prompt_growth_chars=current_prompt_length - previous_prompt_length,
        longest_aligned_message_overlap=aligned_overlap,
        aligned_previous_start_index=aligned_previous_start,
        aligned_current_start_index=aligned_current_start,
        suspected_context_sliding=suspected_context_sliding,
        sliding_dropped_head_messages=sliding_dropped_head_messages,
        sliding_aligned_messages=aligned_overlap if suspected_context_sliding else 0,
        sliding_new_tail_messages=(
            max(len(current_messages) - aligned_current_start - aligned_overlap, 0)
            if suspected_context_sliding
            else 0
        ),
        current_first_message_role=_get_message_role(current_messages, 0),
        best_first_message_role=_get_message_role(previous_messages, 0),
        current_last_message_role=_get_message_role(current_messages, -1),
        best_last_message_role=_get_message_role(previous_messages, -1),
    )


def _get_usage_log_path(now: datetime) -> Path:
    return CACHE_STATS_DIR / f"usage_{now:%Y%m%d}.jsonl"


def _get_report_path() -> Path:
    return CACHE_STATS_DIR / REPORT_FILE_NAME


def _get_session_report_path() -> Path:
    return CACHE_STATS_DIR / SESSION_REPORT_FILE_NAME


def _iter_usage_log_paths() -> list[Path]:
    if not CACHE_STATS_DIR.exists():
        return []
    return sorted(CACHE_STATS_DIR.glob("usage_*.jsonl"))


def _read_usage_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for file_path in _iter_usage_log_paths():
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _write_json_line(file_path: Path, payload: Dict[str, int | str | float | bool]) -> None:
    CACHE_STATS_DIR.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _format_int(value: int | str | float) -> str:
    return f"{int(value):,}"


def _format_rate(value: int | str | float) -> str:
    return f"{float(value):.2f}%"


def _calculate_rate(hit_tokens: int, miss_tokens: int) -> float:
    total_tokens = hit_tokens + miss_tokens
    return hit_tokens / total_tokens * 100 if total_tokens > 0 else 0.0


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _confidence_from_z_score(z_score: float) -> float:
    p_value = 2.0 * (1.0 - _normal_cdf(abs(z_score)))
    return max(0.0, min(100.0, (1.0 - p_value) * 100.0))


def _format_significance_label(confidence: float, *, min_confidence: float = 95.0) -> str:
    return "显著" if confidence >= min_confidence else "不显著"


def _calculate_two_proportion_confidence(
    *,
    current_hit: int,
    current_total: int,
    baseline_hit: int,
    baseline_total: int,
) -> float:
    if current_total <= 0 or baseline_total <= 0:
        return 0.0
    current_rate = current_hit / current_total
    baseline_rate = baseline_hit / baseline_total
    pooled_rate = (current_hit + baseline_hit) / (current_total + baseline_total)
    standard_error = sqrt(pooled_rate * (1.0 - pooled_rate) * (1.0 / current_total + 1.0 / baseline_total))
    if standard_error <= 0:
        return 0.0
    return _confidence_from_z_score((current_rate - baseline_rate) / standard_error)


def _calculate_sample_variance(*, value_total: float, square_total: float, count: int) -> float:
    if count <= 1:
        return 0.0
    return max((square_total - (value_total * value_total / count)) / (count - 1), 0.0)


def _calculate_mean_difference_confidence(
    *,
    current_mean: float,
    current_variance: float,
    current_count: int,
    baseline_mean: float,
    baseline_variance: float,
    baseline_count: int,
) -> float:
    if current_count <= 1 or baseline_count <= 1:
        return 0.0
    standard_error = sqrt(current_variance / current_count + baseline_variance / baseline_count)
    if standard_error <= 0:
        return 0.0
    return _confidence_from_z_score((current_mean - baseline_mean) / standard_error)


def _normalize_event_run_id(event: dict[str, Any]) -> str:
    run_id = str(event.get("run_id") or "").strip()
    return run_id or "legacy"


def _aggregate_usage_events_by_run(events: list[dict[str, Any]]) -> list[dict[str, int | str | float]]:
    grouped: dict[str, dict[str, int | str | float]] = {}
    for event in events:
        run_id = _normalize_event_run_id(event)
        item = grouped.setdefault(
            run_id,
            {
                "run_id": run_id,
                "process_started_at": str(event.get("process_started_at") or ""),
                "first_seen_at": str(event.get("created_at") or ""),
                "last_seen_at": str(event.get("created_at") or ""),
                "calls": 0,
                "prompt_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
                "theoretical_prompt_cache_hit_tokens": 0,
                "theoretical_prompt_cache_miss_tokens": 0,
                "common_prefix_rate_total": 0.0,
                "common_prefix_rate_square_total": 0.0,
                "suspected_context_sliding_calls": 0,
            },
        )
        created_at = str(event.get("created_at") or "")
        if created_at:
            if not item["first_seen_at"] or created_at < str(item["first_seen_at"]):
                item["first_seen_at"] = created_at
            if created_at > str(item["last_seen_at"]):
                item["last_seen_at"] = created_at
        item["calls"] = int(item["calls"]) + 1
        item["prompt_tokens"] = int(item["prompt_tokens"]) + int(event.get("prompt_tokens") or 0)
        item["prompt_cache_hit_tokens"] = int(item["prompt_cache_hit_tokens"]) + int(
            event.get("prompt_cache_hit_tokens") or 0
        )
        item["prompt_cache_miss_tokens"] = int(item["prompt_cache_miss_tokens"]) + int(
            event.get("prompt_cache_miss_tokens") or 0
        )
        item["theoretical_prompt_cache_hit_tokens"] = int(item["theoretical_prompt_cache_hit_tokens"]) + int(
            event.get("theoretical_prompt_cache_hit_tokens") or 0
        )
        item["theoretical_prompt_cache_miss_tokens"] = int(item["theoretical_prompt_cache_miss_tokens"]) + int(
            event.get("theoretical_prompt_cache_miss_tokens") or 0
        )
        item["common_prefix_rate_total"] = float(item["common_prefix_rate_total"]) + float(
            event.get("theoretical_common_prefix_rate") or 0.0
        )
        if bool(event.get("suspected_context_sliding", False)):
            item["suspected_context_sliding_calls"] = int(item["suspected_context_sliding_calls"]) + 1

    result: list[dict[str, int | str | float]] = []
    for item in grouped.values():
        calls = int(item["calls"])
        hit_tokens = int(item["prompt_cache_hit_tokens"])
        miss_tokens = int(item["prompt_cache_miss_tokens"])
        theoretical_hit_tokens = int(item["theoretical_prompt_cache_hit_tokens"])
        theoretical_miss_tokens = int(item["theoretical_prompt_cache_miss_tokens"])
        item["prompt_cache_hit_rate"] = round(_calculate_rate(hit_tokens, miss_tokens), 2)
        item["theoretical_prompt_cache_hit_rate"] = round(
            _calculate_rate(theoretical_hit_tokens, theoretical_miss_tokens),
            2,
        )
        item["avg_common_prefix_rate"] = round(float(item["common_prefix_rate_total"]) / calls, 2) if calls else 0.0
        result.append(item)

    return sorted(result, key=lambda item: str(item["first_seen_at"]))


def _get_previous_run_id(run_stats: list[dict[str, int | str | float]], current_run_id: str) -> str:
    run_ids = [str(item["run_id"]) for item in run_stats]
    if current_run_id not in run_ids:
        return ""
    current_index = run_ids.index(current_run_id)
    if current_index <= 0:
        return ""
    return run_ids[current_index - 1]


def _aggregate_usage_events_by_call_site(
    events: list[dict[str, Any]],
    *,
    run_id: str,
    include_session: bool = True,
) -> dict[tuple[str, ...], dict[str, int | str | float]]:
    grouped: dict[tuple[str, ...], dict[str, int | str | float]] = {}
    for event in events:
        if _normalize_event_run_id(event) != run_id:
            continue
        base_key = (
            str(event.get("task_name") or ""),
            str(event.get("request_type") or ""),
            str(event.get("model_name") or ""),
        )
        key = (
            *base_key,
            _normalize_session_id(str(event.get("session_id") or "")),
        ) if include_session else base_key
        item = grouped.setdefault(
            key,
            {
                "task_name": key[0],
                "request_type": key[1],
                "model_name": key[2],
                "session_id": key[3] if include_session else "",
                "calls": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
                "theoretical_prompt_cache_hit_tokens": 0,
                "theoretical_prompt_cache_miss_tokens": 0,
                "common_prefix_rate_total": 0.0,
                "common_prefix_rate_square_total": 0.0,
                "suspected_context_sliding_calls": 0,
            },
        )
        item["calls"] = int(item["calls"]) + 1
        item["prompt_cache_hit_tokens"] = int(item["prompt_cache_hit_tokens"]) + int(
            event.get("prompt_cache_hit_tokens") or 0
        )
        item["prompt_cache_miss_tokens"] = int(item["prompt_cache_miss_tokens"]) + int(
            event.get("prompt_cache_miss_tokens") or 0
        )
        item["theoretical_prompt_cache_hit_tokens"] = int(item["theoretical_prompt_cache_hit_tokens"]) + int(
            event.get("theoretical_prompt_cache_hit_tokens") or 0
        )
        item["theoretical_prompt_cache_miss_tokens"] = int(item["theoretical_prompt_cache_miss_tokens"]) + int(
            event.get("theoretical_prompt_cache_miss_tokens") or 0
        )
        prefix_rate = float(event.get("theoretical_common_prefix_rate") or 0.0)
        item["common_prefix_rate_total"] = float(item["common_prefix_rate_total"]) + prefix_rate
        item["common_prefix_rate_square_total"] = float(item["common_prefix_rate_square_total"]) + prefix_rate * prefix_rate
        if bool(event.get("suspected_context_sliding", False)):
            item["suspected_context_sliding_calls"] = int(item["suspected_context_sliding_calls"]) + 1

    for item in grouped.values():
        calls = int(item["calls"])
        prefix_total = float(item["common_prefix_rate_total"])
        prefix_square_total = float(item["common_prefix_rate_square_total"])
        item["prompt_cache_hit_rate"] = round(
            _calculate_rate(int(item["prompt_cache_hit_tokens"]), int(item["prompt_cache_miss_tokens"])),
            2,
        )
        item["theoretical_prompt_cache_hit_rate"] = round(
            _calculate_rate(
                int(item["theoretical_prompt_cache_hit_tokens"]),
                int(item["theoretical_prompt_cache_miss_tokens"]),
            ),
            2,
        )
        item["avg_common_prefix_rate"] = round(prefix_total / calls, 2) if calls else 0.0
        item["common_prefix_rate_variance"] = round(
            _calculate_sample_variance(
                value_total=prefix_total,
                square_total=prefix_square_total,
                count=calls,
            ),
            4,
        )
    return grouped


def _render_run_rows(run_stats: list[dict[str, int | str | float]], current_run_id: str) -> str:
    rows: list[str] = []
    for item in reversed(run_stats[-12:]):
        current_marker = "当前" if str(item["run_id"]) == current_run_id else ""
        rows.append(
            "<tr>"
            f"<td>{escape(current_marker)}</td>"
            f"<td>{escape(str(item['run_id']))}</td>"
            f"<td>{escape(str(item['process_started_at']))}</td>"
            f"<td>{escape(str(item['first_seen_at']))}</td>"
            f"<td>{escape(str(item['last_seen_at']))}</td>"
            f"<td>{_format_int(item['calls'])}</td>"
            f"<td>{_format_int(item['prompt_tokens'])}</td>"
            f"<td>{_format_rate(item['prompt_cache_hit_rate'])}</td>"
            f"<td>{_format_rate(item['theoretical_prompt_cache_hit_rate'])}</td>"
            f"<td>{_format_rate(item['avg_common_prefix_rate'])}</td>"
            f"<td>{_format_int(item['suspected_context_sliding_calls'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _render_run_comparison_rows(
    *,
    current_by_call_site: dict[tuple[str, ...], dict[str, int | str | float]],
    previous_by_call_site: dict[tuple[str, ...], dict[str, int | str | float]],
    include_session: bool,
) -> str:
    rows: list[str] = []
    keys = sorted(set(current_by_call_site) | set(previous_by_call_site))
    for key in keys:
        current_item = current_by_call_site.get(key, {})
        previous_item = previous_by_call_site.get(key, {})
        current_api = float(current_item.get("prompt_cache_hit_rate") or 0.0)
        previous_api = float(previous_item.get("prompt_cache_hit_rate") or 0.0)
        current_theory = float(current_item.get("theoretical_prompt_cache_hit_rate") or 0.0)
        previous_theory = float(previous_item.get("theoretical_prompt_cache_hit_rate") or 0.0)
        current_prefix = float(current_item.get("avg_common_prefix_rate") or 0.0)
        previous_prefix = float(previous_item.get("avg_common_prefix_rate") or 0.0)
        rows.append(
            "<tr>"
            f"<td>{escape(key[0])}</td>"
            f"<td>{escape(key[1])}</td>"
            f"<td>{escape(key[2])}</td>"
            + (f"<td>{escape(key[3])}</td>" if include_session and len(key) > 3 else "")
            +
            f"<td>{_format_int(current_item.get('calls', 0))}</td>"
            f"<td>{_format_int(previous_item.get('calls', 0))}</td>"
            f"<td>{_format_rate(current_api)}</td>"
            f"<td>{_format_rate(previous_api)}</td>"
            f"<td>{_format_rate(current_api - previous_api)}</td>"
            f"<td>{_format_rate(current_theory)}</td>"
            f"<td>{_format_rate(previous_theory)}</td>"
            f"<td>{_format_rate(current_theory - previous_theory)}</td>"
            f"<td>{_format_rate(current_prefix)}</td>"
            f"<td>{_format_rate(previous_prefix)}</td>"
            f"<td>{_format_rate(current_prefix - previous_prefix)}</td>"
            f"<td>{_format_int(current_item.get('suspected_context_sliding_calls', 0))}</td>"
            f"<td>{_format_int(previous_item.get('suspected_context_sliding_calls', 0))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _format_run_time_label(run_stat: dict[str, int | str | float] | None) -> str:
    if not run_stat:
        return ""
    first_seen_at = str(run_stat.get("first_seen_at") or "").strip()
    last_seen_at = str(run_stat.get("last_seen_at") or "").strip()
    process_started_at = str(run_stat.get("process_started_at") or "").strip()
    if first_seen_at and last_seen_at and first_seen_at != last_seen_at:
        return f"{first_seen_at} -> {last_seen_at}"
    if first_seen_at:
        return first_seen_at
    return process_started_at


def _get_previous_run_stats(
    run_stats: list[dict[str, int | str | float]],
    current_run_id: str,
) -> list[dict[str, int | str | float]]:
    return [
        item
        for item in run_stats
        if str(item["run_id"]) != current_run_id
    ]


def _render_run_significance_controls(
    run_stats: list[dict[str, int | str | float]],
    current_run_id: str,
) -> str:
    previous_run_stats = _get_previous_run_stats(run_stats, current_run_id)
    if not previous_run_stats:
        return (
            "<div class=\"run-switcher\" id=\"run-significance-switcher\" data-run-count=\"0\">"
            "<span class=\"muted\">No previous runs to compare.</span>"
            "</div>"
        )

    option_payload = [
        {
            "run_id": str(item["run_id"]),
            "time_label": _format_run_time_label(item),
            "calls": int(item.get("calls") or 0),
        }
        for item in previous_run_stats
    ]
    option_json = escape(json.dumps(option_payload, ensure_ascii=False), quote=True)
    max_index = len(previous_run_stats) - 1
    return (
        "<div class=\"run-switcher\" id=\"run-significance-switcher\" "
        f"data-run-options=\"{option_json}\" data-run-count=\"{len(previous_run_stats)}\">"
        "<button type=\"button\" id=\"run-significance-prev\" aria-label=\"Previous baseline run\">&lt;</button>"
        "<input id=\"run-significance-slider\" type=\"range\" min=\"0\" "
        f"max=\"{max_index}\" value=\"{max_index}\" step=\"1\">"
        "<button type=\"button\" id=\"run-significance-next\" aria-label=\"Next baseline run\">&gt;</button>"
        "<div class=\"run-switcher-meta\">"
        "<div class=\"label\">Baseline run</div>"
        "<div class=\"value\" id=\"run-significance-label\"></div>"
        "<div class=\"muted\" id=\"run-significance-time\"></div>"
        "</div>"
        "</div>"
    )


def _render_run_significance_script() -> str:
    return """
  <script>
    (() => {
      const switcher = document.getElementById("run-significance-switcher");
      const tableBody = document.getElementById("run-significance-body");
      if (!switcher || !tableBody) {
        return;
      }
      let options = [];
      try {
        options = JSON.parse(switcher.dataset.runOptions || "[]");
      } catch {
        options = [];
      }
      if (!options.length) {
        return;
      }

      const slider = document.getElementById("run-significance-slider");
      const prevButton = document.getElementById("run-significance-prev");
      const nextButton = document.getElementById("run-significance-next");
      const label = document.getElementById("run-significance-label");
      const time = document.getElementById("run-significance-time");

      const clampIndex = (value) => Math.max(0, Math.min(options.length - 1, value));
      const showRun = (index) => {
        const activeIndex = clampIndex(Number.parseInt(String(index), 10) || 0);
        const active = options[activeIndex];
        slider.value = String(activeIndex);
        label.textContent = `${activeIndex + 1}/${options.length} - ${active.run_id}`;
        time.textContent = `${active.time_label || "No timestamp"} - ${active.calls} calls`;
        tableBody.querySelectorAll("tr[data-baseline-run-id]").forEach((row) => {
          row.hidden = row.dataset.baselineRunId !== active.run_id;
        });
      };

      slider.addEventListener("input", () => showRun(slider.value));
      prevButton.addEventListener("click", () => showRun(Number(slider.value) - 1));
      nextButton.addEventListener("click", () => showRun(Number(slider.value) + 1));
      showRun(options.length - 1);
    })();
  </script>
"""


def _build_run_significance_rows(
    *,
    usage_events: list[dict[str, Any]],
    run_stats: list[dict[str, int | str | float]],
    current_run_id: str,
    include_session: bool,
) -> str:
    current_by_call_site = _aggregate_usage_events_by_call_site(
        usage_events,
        run_id=current_run_id,
        include_session=include_session,
    )
    rows: list[str] = []
    previous_run_stats = _get_previous_run_stats(run_stats, current_run_id)
    for previous_run_stat in previous_run_stats:
        previous_run_id = str(previous_run_stat["run_id"])
        baseline_time = _format_run_time_label(previous_run_stat)
        previous_by_call_site = _aggregate_usage_events_by_call_site(
            usage_events,
            run_id=previous_run_id,
            include_session=include_session,
        )
        keys = sorted(set(current_by_call_site) & set(previous_by_call_site))
        for key in keys:
            current_item = current_by_call_site[key]
            previous_item = previous_by_call_site[key]
            current_hit = int(current_item.get("prompt_cache_hit_tokens") or 0)
            current_miss = int(current_item.get("prompt_cache_miss_tokens") or 0)
            previous_hit = int(previous_item.get("prompt_cache_hit_tokens") or 0)
            previous_miss = int(previous_item.get("prompt_cache_miss_tokens") or 0)
            current_total = current_hit + current_miss
            previous_total = previous_hit + previous_miss
            current_api = _calculate_rate(current_hit, current_miss)
            previous_api = _calculate_rate(previous_hit, previous_miss)
            api_confidence = _calculate_two_proportion_confidence(
                current_hit=current_hit,
                current_total=current_total,
                baseline_hit=previous_hit,
                baseline_total=previous_total,
            )
            current_calls = int(current_item.get("calls") or 0)
            previous_calls = int(previous_item.get("calls") or 0)
            current_prefix = float(current_item.get("avg_common_prefix_rate") or 0.0)
            previous_prefix = float(previous_item.get("avg_common_prefix_rate") or 0.0)
            prefix_confidence = _calculate_mean_difference_confidence(
                current_mean=current_prefix,
                current_variance=float(current_item.get("common_prefix_rate_variance") or 0.0),
                current_count=current_calls,
                baseline_mean=previous_prefix,
                baseline_variance=float(previous_item.get("common_prefix_rate_variance") or 0.0),
                baseline_count=previous_calls,
            )
            rows.append(
                f"<tr data-baseline-run-id=\"{escape(previous_run_id)}\">"
                f"<td>{escape(previous_run_id)}</td>"
                f"<td>{escape(baseline_time)}</td>"
                f"<td>{escape(key[0])}</td>"
                f"<td>{escape(key[1])}</td>"
                f"<td>{escape(key[2])}</td>"
                + (f"<td>{escape(key[3])}</td>" if include_session and len(key) > 3 else "")
                +
                f"<td>{_format_int(current_calls)}</td>"
                f"<td>{_format_int(previous_calls)}</td>"
                f"<td>{_format_rate(current_api - previous_api)}</td>"
                f"<td>{_format_rate(api_confidence)}</td>"
                f"<td>{escape(_format_significance_label(api_confidence))}</td>"
                f"<td>{_format_rate(current_prefix - previous_prefix)}</td>"
                f"<td>{_format_rate(prefix_confidence)}</td>"
                f"<td>{escape(_format_significance_label(prefix_confidence))}</td>"
                f"<td>{_format_int(current_item.get('suspected_context_sliding_calls', 0))}</td>"
                f"<td>{_format_int(previous_item.get('suspected_context_sliding_calls', 0))}</td>"
                "</tr>"
            )

    if not rows:
        return (
            "<tr><td colspan=\"14\">当前 run 还没有可与历史 run 比较的同类调用点，"
            "或历史数据缺少 run_id。</td></tr>"
        )
    return "\n".join(rows)


def _render_stat_rows(stats: List[Dict[str, int | str | float]], *, include_session: bool) -> str:
    rows: list[str] = []
    for item in stats:
        rows.append(
            "<tr>"
            f"<td>{escape(str(item['task_name']))}</td>"
            f"<td>{escape(str(item['request_type']))}</td>"
            f"<td>{escape(str(item['model_name']))}</td>"
            + (f"<td>{escape(str(item.get('session_id', '')))}</td>" if include_session else "")
            +
            f"<td>{_format_rate(item['prompt_cache_hit_rate'])}</td>"
            f"<td>{_format_rate(item['theoretical_prompt_cache_hit_rate'])}</td>"
            f"<td>{_format_rate(item['prompt_cache_hit_rate_delta'])}</td>"
            f"<td>{_format_int(item['prompt_cache_hit_tokens'])}</td>"
            f"<td>{_format_int(item['prompt_cache_miss_tokens'])}</td>"
            f"<td>{_format_int(item['theoretical_prompt_cache_hit_tokens'])}</td>"
            f"<td>{_format_int(item['theoretical_prompt_cache_miss_tokens'])}</td>"
            f"<td>{_format_int(item['prompt_tokens'])}</td>"
            f"<td>{_format_int(item['calls'])}</td>"
            f"<td>{_format_int(item['cache_reported_calls'])}</td>"
            f"<td>{_format_int(item['theoretical_compared_calls'])}</td>"
            f"<td>{_format_int(item['theoretical_cache_pool_hits'])}</td>"
            f"<td>{_format_rate(item['avg_common_prefix_rate'])}</td>"
            f"<td>{_format_int(item['suspected_context_sliding_calls'])}</td>"
            f"<td>{item['avg_sliding_dropped_messages']}</td>"
            f"<td>{item['avg_sliding_aligned_messages']}</td>"
            f"<td>{escape(str(item.get('top_dynamic_diff_paths', '')))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _aggregate_stats_snapshot(
    stats_snapshot: List[Dict[str, int | str | float]],
    *,
    include_session: bool,
) -> List[Dict[str, int | str | float]]:
    grouped: dict[tuple[str, ...], dict[str, int | str | float]] = {}
    for item in stats_snapshot:
        base_key = (
            str(item.get("task_name") or ""),
            str(item.get("request_type") or ""),
            str(item.get("model_name") or ""),
        )
        key = (*base_key, str(item.get("session_id") or "")) if include_session else base_key
        target = grouped.setdefault(
            key,
            {
                "task_name": base_key[0],
                "request_type": base_key[1],
                "model_name": base_key[2],
                "session_id": str(item.get("session_id") or "") if include_session else "",
                "calls": 0,
                "cache_reported_calls": 0,
                "prompt_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
                "theoretical_prompt_cache_hit_tokens": 0,
                "theoretical_prompt_cache_miss_tokens": 0,
                "theoretical_compared_calls": 0,
                "theoretical_cache_pool_hits": 0,
                "common_prefix_rate_weighted_total": 0.0,
                "suspected_context_sliding_calls": 0,
                "sliding_dropped_weighted_total": 0.0,
                "sliding_aligned_weighted_total": 0.0,
                "top_dynamic_diff_paths": "",
            },
        )
        calls = int(item.get("calls") or 0)
        sliding_calls = int(item.get("suspected_context_sliding_calls") or 0)
        target["calls"] = int(target["calls"]) + calls
        target["cache_reported_calls"] = int(target["cache_reported_calls"]) + int(item.get("cache_reported_calls") or 0)
        target["prompt_tokens"] = int(target["prompt_tokens"]) + int(item.get("prompt_tokens") or 0)
        target["prompt_cache_hit_tokens"] = int(target["prompt_cache_hit_tokens"]) + int(item.get("prompt_cache_hit_tokens") or 0)
        target["prompt_cache_miss_tokens"] = int(target["prompt_cache_miss_tokens"]) + int(item.get("prompt_cache_miss_tokens") or 0)
        target["theoretical_prompt_cache_hit_tokens"] = int(target["theoretical_prompt_cache_hit_tokens"]) + int(
            item.get("theoretical_prompt_cache_hit_tokens") or 0
        )
        target["theoretical_prompt_cache_miss_tokens"] = int(target["theoretical_prompt_cache_miss_tokens"]) + int(
            item.get("theoretical_prompt_cache_miss_tokens") or 0
        )
        target["theoretical_compared_calls"] = int(target["theoretical_compared_calls"]) + int(
            item.get("theoretical_compared_calls") or 0
        )
        target["theoretical_cache_pool_hits"] = int(target["theoretical_cache_pool_hits"]) + int(
            item.get("theoretical_cache_pool_hits") or 0
        )
        target["common_prefix_rate_weighted_total"] = float(target["common_prefix_rate_weighted_total"]) + (
            float(item.get("avg_common_prefix_rate") or 0.0) * calls
        )
        target["suspected_context_sliding_calls"] = int(target["suspected_context_sliding_calls"]) + sliding_calls
        target["sliding_dropped_weighted_total"] = float(target["sliding_dropped_weighted_total"]) + (
            float(item.get("avg_sliding_dropped_messages") or 0.0) * sliding_calls
        )
        target["sliding_aligned_weighted_total"] = float(target["sliding_aligned_weighted_total"]) + (
            float(item.get("avg_sliding_aligned_messages") or 0.0) * sliding_calls
        )
        if include_session:
            target["top_dynamic_diff_paths"] = item.get("top_dynamic_diff_paths", "")

    result: list[dict[str, int | str | float]] = []
    for item in grouped.values():
        calls = int(item["calls"])
        sliding_calls = int(item["suspected_context_sliding_calls"])
        hit_tokens = int(item["prompt_cache_hit_tokens"])
        miss_tokens = int(item["prompt_cache_miss_tokens"])
        theoretical_hit_tokens = int(item["theoretical_prompt_cache_hit_tokens"])
        theoretical_miss_tokens = int(item["theoretical_prompt_cache_miss_tokens"])
        item["prompt_cache_hit_rate"] = round(_calculate_rate(hit_tokens, miss_tokens), 2)
        item["theoretical_prompt_cache_hit_rate"] = round(
            _calculate_rate(theoretical_hit_tokens, theoretical_miss_tokens),
            2,
        )
        item["prompt_cache_hit_rate_delta"] = round(
            float(item["prompt_cache_hit_rate"]) - float(item["theoretical_prompt_cache_hit_rate"]),
            2,
        )
        item["avg_common_prefix_rate"] = (
            round(float(item["common_prefix_rate_weighted_total"]) / calls, 2) if calls else 0.0
        )
        item["avg_sliding_dropped_messages"] = (
            round(float(item["sliding_dropped_weighted_total"]) / sliding_calls, 2) if sliding_calls else 0.0
        )
        item["avg_sliding_aligned_messages"] = (
            round(float(item["sliding_aligned_weighted_total"]) / sliding_calls, 2) if sliding_calls else 0.0
        )
        result.append(item)
    return result


def _render_html_report(stats_snapshot: List[Dict[str, int | str | float]], *, include_session: bool = False) -> str:
    updated_at = datetime.now().isoformat(timespec="seconds")
    visible_stats_snapshot = _aggregate_stats_snapshot(stats_snapshot, include_session=include_session)
    usage_events = _read_usage_events()
    run_stats = _aggregate_usage_events_by_run(usage_events)
    current_run_id = _store.run_id
    previous_run_id = _get_previous_run_id(run_stats, current_run_id)
    current_by_call_site = _aggregate_usage_events_by_call_site(
        usage_events,
        run_id=current_run_id,
        include_session=include_session,
    )
    previous_by_call_site = (
        _aggregate_usage_events_by_call_site(
            usage_events,
            run_id=previous_run_id,
            include_session=include_session,
        ) if previous_run_id else {}
    )
    sorted_by_rate = sorted(
        visible_stats_snapshot,
        key=lambda item: (
            float(item["prompt_cache_hit_rate"]),
            -int(item["prompt_cache_miss_tokens"]),
        ),
    )
    low_stats = sorted_by_rate[:SUMMARY_LIMIT]
    high_stats = list(reversed(sorted_by_rate[-SUMMARY_LIMIT:]))
    all_stats = sorted(
        visible_stats_snapshot,
        key=lambda item: (
            str(item["task_name"]),
            str(item["request_type"]),
            str(item["model_name"]),
        ),
    )
    total_calls = sum(int(item["calls"]) for item in visible_stats_snapshot)
    total_prompt_tokens = sum(int(item["prompt_tokens"]) for item in visible_stats_snapshot)
    total_hit_tokens = sum(int(item["prompt_cache_hit_tokens"]) for item in visible_stats_snapshot)
    total_theoretical_hit_tokens = sum(int(item["theoretical_prompt_cache_hit_tokens"]) for item in visible_stats_snapshot)
    total_miss_tokens = sum(int(item["prompt_cache_miss_tokens"]) for item in visible_stats_snapshot)
    total_theoretical_miss_tokens = sum(int(item["theoretical_prompt_cache_miss_tokens"]) for item in visible_stats_snapshot)
    total_cache_tokens = total_hit_tokens + total_miss_tokens
    total_theoretical_cache_tokens = total_theoretical_hit_tokens + total_theoretical_miss_tokens
    overall_hit_rate = total_hit_tokens / total_cache_tokens * 100 if total_cache_tokens > 0 else 0.0
    overall_theoretical_hit_rate = (
        total_theoretical_hit_tokens / total_theoretical_cache_tokens * 100
        if total_theoretical_cache_tokens > 0
        else 0.0
    )
    session_head = "<th>Session</th>" if include_session else ""
    report_title = "LLM Prompt Cache Stats By Session" if include_session else "LLM Prompt Cache Stats"
    peer_report_link = (
        f"<a href=\"{REPORT_FILE_NAME}\">Overview report</a>"
        if include_session
        else f"<a href=\"{SESSION_REPORT_FILE_NAME}\">Session detail report</a>"
    )
    table_head = (
        f"<thead><tr><th>Task</th><th>Request</th><th>Model</th>{session_head}<th>API hit</th><th>Theory hit</th>"
        "<th>Delta</th><th>API hit tok</th><th>API miss tok</th><th>Theory hit tok</th><th>Theory miss tok</th>"
        "<th>Prompt tok</th><th>Calls</th><th>Reported</th><th>Compared</th><th>Pool hits</th>"
        "<th>Avg prefix</th><th>Sliding calls</th><th>Avg dropped msg</th><th>Avg aligned msg</th>"
        "<th>Top dynamic diff paths</th></tr></thead>"
    )
    run_table_head = (
        "<thead><tr><th></th><th>Run ID</th><th>Process started</th><th>First event</th><th>Last event</th>"
        "<th>Calls</th><th>Prompt tok</th><th>API hit</th><th>Theory hit</th><th>Avg prefix</th>"
        "<th>Sliding calls</th></tr></thead>"
    )
    run_compare_head = (
        f"<thead><tr><th>Task</th><th>Request</th><th>Model</th>{session_head}<th>Current calls</th><th>Previous calls</th>"
        "<th>Current API</th><th>Previous API</th><th>API delta</th>"
        "<th>Current Theory</th><th>Previous Theory</th><th>Theory delta</th>"
        "<th>Current Prefix</th><th>Previous Prefix</th><th>Prefix delta</th>"
        "<th>Current Sliding</th><th>Previous Sliding</th></tr></thead>"
    )
    run_significance_head = (
        f"<thead><tr><th>Baseline run</th><th>Baseline time</th><th>Task</th><th>Request</th><th>Model</th>{session_head}"
        "<th>Current calls</th><th>Baseline calls</th>"
        "<th>API delta</th><th>API confidence</th><th>API significant</th>"
        "<th>Prefix delta</th><th>Prefix confidence</th><th>Prefix significant</th>"
        "<th>Current sliding</th><th>Baseline sliding</th></tr></thead>"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(report_title)}</title>
  <style>
    body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px; color: #202124; background: #f7f8fa; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 28px 0 12px; }}
    .meta {{ color: #5f6368; margin-bottom: 20px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0 24px; }}
    .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #5f6368; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 650; margin-top: 6px; }}
    .muted {{ color: #5f6368; font-size: 13px; }}
    .run-switcher {{ display: flex; align-items: center; gap: 10px; margin: 0 0 12px; padding: 12px; background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; }}
    .run-switcher button {{ width: 32px; height: 32px; border: 1px solid #cbd3dc; background: #f8fafc; border-radius: 6px; cursor: pointer; }}
    .run-switcher input[type="range"] {{ min-width: 180px; flex: 1; }}
    .run-switcher-meta {{ min-width: 320px; }}
    .run-switcher-meta .value {{ font-size: 15px; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e3e6ea; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #edf0f2; text-align: left; font-size: 13px; vertical-align: top; }}
    th {{ background: #f0f3f6; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
  </style>
</head>
<body>
  <h1>{escape(report_title)}</h1>
  <div class="meta">Updated at: {escape(updated_at)}. Current run: {escape(current_run_id)}. Process started at: {escape(_store.process_started_at)}. Grouped by task_name / request_type / model_name{escape(' / session_id' if include_session else '')}. Local prompt pool size: {PROMPT_CACHE_POOL_SIZE}. {peer_report_link}</div>
  <div class="cards">
    <div class="card"><div class="label">Calls</div><div class="value">{_format_int(total_calls)}</div></div>
    <div class="card"><div class="label">Prompt tokens</div><div class="value">{_format_int(total_prompt_tokens)}</div></div>
    <div class="card"><div class="label">API hit tokens</div><div class="value">{_format_int(total_hit_tokens)}</div></div>
    <div class="card"><div class="label">API hit rate</div><div class="value">{_format_rate(overall_hit_rate)}</div></div>
    <div class="card"><div class="label">Theory hit tokens</div><div class="value">{_format_int(total_theoretical_hit_tokens)}</div></div>
    <div class="card"><div class="label">Theory hit rate</div><div class="value">{_format_rate(overall_theoretical_hit_rate)}</div></div>
  </div>
  <h2>Run Comparison</h2>
  <table>
    {run_table_head}
    <tbody>{_render_run_rows(run_stats, current_run_id)}</tbody>
  </table>
  <h2>Current vs Previous Run By Call Site</h2>
  <table>
    {run_compare_head}
    <tbody>{_render_run_comparison_rows(current_by_call_site=current_by_call_site, previous_by_call_site=previous_by_call_site, include_session=include_session)}</tbody>
  </table>
  <h2>Current vs Every Previous Run Significance</h2>
  {_render_run_significance_controls(run_stats, current_run_id)}
  <table>
    {run_significance_head}
    <tbody id="run-significance-body">{_build_run_significance_rows(usage_events=usage_events, run_stats=run_stats, current_run_id=current_run_id, include_session=include_session)}</tbody>
  </table>
  <h2>Low API Hit Rate</h2>
  <table>
    {table_head}
    <tbody>{_render_stat_rows(low_stats, include_session=include_session)}</tbody>
  </table>
  <h2>High API Hit Rate</h2>
  <table>
    {table_head}
    <tbody>{_render_stat_rows(high_stats, include_session=include_session)}</tbody>
  </table>
  <h2>All Call Sites</h2>
  <table>
    {table_head}
    <tbody>{_render_stat_rows(all_stats, include_session=include_session)}</tbody>
  </table>
  {_render_run_significance_script()}
</body>
</html>
"""


def _write_html_report(stats_snapshot: List[Dict[str, int | str | float]]) -> None:
    CACHE_STATS_DIR.mkdir(parents=True, exist_ok=True)
    _get_report_path().write_text(_render_html_report(stats_snapshot, include_session=False), encoding="utf-8")
    _get_session_report_path().write_text(_render_html_report(stats_snapshot, include_session=True), encoding="utf-8")


def _write_usage_event(event: Dict[str, int | str | float | bool]) -> None:
    try:
        _write_json_line(_get_usage_log_path(datetime.now()), event)
    except Exception as exc:
        logger.warning(f"写入 LLM prompt cache 明细失败: {exc}")


def _write_report(stats_snapshot: List[Dict[str, int | str | float]]) -> None:
    try:
        _write_html_report(stats_snapshot)
    except Exception as exc:
        logger.warning(f"写入 LLM prompt cache HTML 报告失败: {exc}")


def record_llm_cache_usage(
    *,
    task_name: str,
    request_type: str,
    model_name: str,
    session_id: str = "",
    prompt_tokens: int,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
    prompt_text: str | None = None,
) -> None:
    """Record one LLM prompt cache usage event."""

    if not _is_llm_cache_stats_enabled():
        return

    normalized_task_name = str(task_name or "").strip()
    if normalized_task_name not in FOCUSED_TASK_NAMES:
        return

    normalized_request_type = _normalize_request_type(request_type)
    if normalized_request_type in EXCLUDED_REQUEST_TYPES:
        return

    normalized_model_name = _normalize_model_name(model_name)
    normalized_session_id = _normalize_session_id(session_id)
    normalized_prompt_tokens = max(int(prompt_tokens or 0), 0)
    hit_tokens, miss_tokens, has_cache_report = _normalize_cache_tokens(
        prompt_tokens=normalized_prompt_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )

    with _store.lock:
        key = (normalized_task_name, normalized_request_type, normalized_model_name, normalized_session_id)
        prompt_pool = _store.prompt_pools.get(key, [])
        cache_match = _calculate_theoretical_cache_match(
            prompt_tokens=normalized_prompt_tokens,
            prompt_text=prompt_text,
            prompt_pool=prompt_pool,
        )
        dynamic_diff = _diagnose_dynamic_diff(cache_match.best_prompt_text, prompt_text)
        prompt_diagnostics = _diagnose_prompt_cache_details(
            previous_prompt_text=cache_match.best_prompt_text,
            current_prompt_text=prompt_text,
            common_prefix_chars=cache_match.common_prefix_chars,
        )
        if prompt_text:
            next_prompt_pool = [*prompt_pool, prompt_text]
            if len(next_prompt_pool) > PROMPT_CACHE_POOL_SIZE:
                next_prompt_pool = next_prompt_pool[-PROMPT_CACHE_POOL_SIZE:]
            _store.prompt_pools[key] = next_prompt_pool

        stat = _store.stats.get(key)
        if stat is None:
            stat = LLMCacheStat(
                task_name=normalized_task_name,
                request_type=normalized_request_type,
                model_name=normalized_model_name,
                session_id=normalized_session_id,
            )
            _store.stats[key] = stat
            _evict_oldest_cache_stats_keys_locked()

        stat.calls += 1
        stat.prompt_tokens += normalized_prompt_tokens
        stat.prompt_cache_hit_tokens += hit_tokens
        stat.prompt_cache_miss_tokens += miss_tokens
        stat.theoretical_prompt_cache_hit_tokens += cache_match.hit_tokens
        stat.theoretical_prompt_cache_miss_tokens += cache_match.miss_tokens
        stat.common_prefix_rate_total += prompt_diagnostics.common_prefix_rate
        if prompt_diagnostics.suspected_context_sliding:
            stat.suspected_context_sliding_calls += 1
            stat.sliding_dropped_messages_total += prompt_diagnostics.sliding_dropped_head_messages
            stat.sliding_aligned_messages_total += prompt_diagnostics.sliding_aligned_messages
        stat.dynamic_diff_counts[dynamic_diff.path] = stat.dynamic_diff_counts.get(dynamic_diff.path, 0) + 1
        if has_cache_report:
            stat.cache_reported_calls += 1
        if cache_match.compared:
            stat.theoretical_compared_calls += 1
        if cache_match.hit_tokens > 0:
            stat.theoretical_cache_pool_hits += 1
        _store.total_calls += 1
        _store.calls_since_report += 1
        _store.calls_in_run += 1

        api_hit_rate = hit_tokens / (hit_tokens + miss_tokens) * 100 if hit_tokens + miss_tokens > 0 else 0.0
        event = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "run_id": _store.run_id,
            "process_started_at": _store.process_started_at,
            "call_index_in_run": _store.calls_in_run,
            "task_name": normalized_task_name,
            "request_type": normalized_request_type,
            "model_name": normalized_model_name,
            "session_id": normalized_session_id,
            "prompt_tokens": normalized_prompt_tokens,
            "prompt_chars": len(prompt_text or ""),
            "prompt_cache_hit_tokens": hit_tokens,
            "prompt_cache_miss_tokens": miss_tokens,
            "prompt_cache_hit_rate": round(api_hit_rate, 2),
            "theoretical_prompt_cache_hit_tokens": cache_match.hit_tokens,
            "theoretical_prompt_cache_miss_tokens": cache_match.miss_tokens,
            "theoretical_prompt_cache_hit_rate": round(cache_match.hit_rate, 2),
            "theoretical_cache_pool_size": cache_match.pool_size,
            "theoretical_best_match_rank": cache_match.best_match_rank,
            "theoretical_common_prefix_chars": cache_match.common_prefix_chars,
            "theoretical_common_prefix_rate": round(prompt_diagnostics.common_prefix_rate, 2),
            "current_message_count": prompt_diagnostics.current_message_count,
            "best_match_message_count": prompt_diagnostics.best_match_message_count,
            "common_prefix_messages": prompt_diagnostics.common_prefix_messages,
            "common_suffix_messages": prompt_diagnostics.common_suffix_messages,
            "prompt_growth_chars": prompt_diagnostics.prompt_growth_chars,
            "longest_aligned_message_overlap": prompt_diagnostics.longest_aligned_message_overlap,
            "aligned_previous_start_index": prompt_diagnostics.aligned_previous_start_index,
            "aligned_current_start_index": prompt_diagnostics.aligned_current_start_index,
            "suspected_context_sliding": prompt_diagnostics.suspected_context_sliding,
            "sliding_dropped_head_messages": prompt_diagnostics.sliding_dropped_head_messages,
            "sliding_aligned_messages": prompt_diagnostics.sliding_aligned_messages,
            "sliding_new_tail_messages": prompt_diagnostics.sliding_new_tail_messages,
            "current_first_message_role": prompt_diagnostics.current_first_message_role,
            "best_first_message_role": prompt_diagnostics.best_first_message_role,
            "current_last_message_role": prompt_diagnostics.current_last_message_role,
            "best_last_message_role": prompt_diagnostics.best_last_message_role,
            "prompt_cache_hit_rate_delta": round(api_hit_rate - cache_match.hit_rate, 2),
            "dynamic_diff_path": dynamic_diff.path,
            "dynamic_diff_previous": dynamic_diff.previous_value,
            "dynamic_diff_current": dynamic_diff.current_value,
            "cache_reported": has_cache_report,
            "theoretical_compared": cache_match.compared,
        }
        stats_snapshot = [stat.to_dict() for stat in _store.stats.values()]

        now = time.time()
        should_update_report = (
            _store.last_report_at <= 0
            or _store.calls_since_report >= REPORT_INTERVAL_CALLS
            or now - _store.last_report_at >= REPORT_INTERVAL_SECONDS
        )
        if should_update_report:
            _store.last_report_at = now
            _store.calls_since_report = 0
            stats_snapshot_to_report = stats_snapshot
        else:
            stats_snapshot_to_report = []

    _write_usage_event(event)
    if stats_snapshot_to_report:
        _write_report(stats_snapshot_to_report)
        log_llm_cache_stats_summary(stats_snapshot_to_report)


def get_llm_cache_stats_snapshot() -> List[Dict[str, int | str | float]]:
    """Return current in-process LLM prompt cache stats."""

    with _store.lock:
        return [stat.to_dict() for stat in _store.stats.values()]


def reset_llm_cache_stats() -> None:
    """Reset in-process stats. Intended for tests and local debugging."""

    with _store.lock:
        _store.stats.clear()
        _store.prompt_pools.clear()
        _store.total_calls = 0
        _store.calls_in_run = 0
        _store.last_report_at = 0
        _store.calls_since_report = 0


def log_llm_cache_stats_summary(stats_snapshot: List[Dict[str, int | str | float]] | None = None) -> None:
    """Log current highest and lowest prompt cache hit-rate call sites."""

    snapshot = stats_snapshot or get_llm_cache_stats_snapshot()
    if not snapshot:
        return

    sorted_stats = sorted(
        snapshot,
        key=lambda item: (
            float(item["prompt_cache_hit_rate"]),
            -int(item["prompt_cache_miss_tokens"]),
        ),
    )
    low_stats = sorted_stats[:SUMMARY_LIMIT]
    high_stats = list(reversed(sorted_stats[-SUMMARY_LIMIT:]))

    def _format_stat(item: Dict[str, int | str | float]) -> str:
        return (
            f"{item['task_name']}/{item['request_type']}/{item['model_name']}: "
            f"api_hit_rate={float(item['prompt_cache_hit_rate']):.2f}%, "
            f"theory_hit_rate={float(item['theoretical_prompt_cache_hit_rate']):.2f}%, "
            f"delta={float(item['prompt_cache_hit_rate_delta']):.2f}%, "
            f"avg_prefix={float(item['avg_common_prefix_rate']):.2f}%, "
            f"sliding_calls={item['suspected_context_sliding_calls']}, "
            f"top_dynamic={item.get('top_dynamic_diff_paths', '')}, "
            f"hit={item['prompt_cache_hit_tokens']}, "
            f"miss={item['prompt_cache_miss_tokens']}, "
            f"prompt={item['prompt_tokens']}, "
            f"calls={item['calls']}, "
            f"reported={item['cache_reported_calls']}"
        )

    logger.info(
        "LLM prompt cache 统计摘要\n"
        "低命中调用点:\n- " + "\n- ".join(_format_stat(item) for item in low_stats) + "\n"
        "高命中调用点:\n- " + "\n- ".join(_format_stat(item) for item in high_stats)
    )

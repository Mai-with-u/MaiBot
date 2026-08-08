"""三种表达选择器离线运行器。

该脚本用于把同一批 live-log 样本重放成三种完整选择流程：
1. legacy 候选池 + 精细选择器
2. vector 不带 intent 候选池 + 精细选择器
3. live-log 中真实 vector_intent 精细选择结果
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from random import Random
from sys import path as sys_path
from typing import Any, List, Sequence

import argparse
import asyncio
import json
import sys

from json_repair import repair_json

ROOT_PATH = Path(__file__).resolve().parents[2]
if str(ROOT_PATH) not in sys_path:
    sys_path.insert(0, str(ROOT_PATH))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.chat.replyer.maisaka_expression_selector import maisaka_expression_selector  # noqa: E402
from src.chat.replyer.expression_vector_index import expression_vector_index  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.services.llm_service import LLMServiceClient, _build_message_from_dict  # noqa: E402

DEFAULT_INPUT_JSON = "data/analysis/expression_selection_batch_compare_live_intent_20260622_164359.json"
DEFAULT_INDEX_JSON = "data/expression_selection/expression_vector_index.json"
METHOD_KEYS = ["legacy_precise", "vector_no_intent_precise", "vector_intent_online"]


@dataclass(frozen=True)
class SelectorReplayResult:
    """单次精细选择重放结果。"""

    candidate_pool_size: int
    candidate_pool: List[dict[str, Any]]
    selected_ids: List[int]
    selected_expressions: List[dict[str, Any]]
    raw_response: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int


def build_argument_parser() -> ArgumentParser:
    """构建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="构建表达选择完整链路评测批次。")
    parser.add_argument("--input-json", default=DEFAULT_INPUT_JSON, help="live-log batch JSON。")
    parser.add_argument("--index-json", default=DEFAULT_INDEX_JSON, help="表达向量索引 JSON。")
    parser.add_argument("--output-dir", default="data/analysis", help="输出目录。")
    parser.add_argument("--limit", type=int, default=30, help="最多处理多少条样本；0 表示全部。")
    parser.add_argument("--selector-task-name", default="expression_use", help="精细选择器使用的任务名。")
    parser.add_argument("--selector-max-tokens", type=int, default=4096, help="精细选择器最大输出 token。")
    parser.add_argument("--vector-pool-size", type=int, default=50, help="向量召回交给精细选择器的候选数。")
    parser.add_argument("--cluster-pool-size", type=int, default=16, help="向量召回扫描的近邻簇数量。")
    return parser


def parse_args() -> Namespace:
    """解析命令行参数。"""

    return build_argument_parser().parse_args()


def resolve_path(raw_path: str) -> Path:
    """解析相对项目根目录的路径。"""

    path = Path(str(raw_path or "").strip()).expanduser()
    return path if path.is_absolute() else ROOT_PATH / path


def stable_hash(value: str) -> int:
    """生成稳定 hash。"""

    return int.from_bytes(sha256(value.encode("utf-8")).digest()[:8], "big")


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """统一表达候选格式。"""

    return {
        "id": item.get("id"),
        "situation": str(item.get("situation") or "").strip(),
        "style": str(item.get("style") or "").strip(),
        "count": item.get("count", 1) or 1,
    }


def load_global_candidates(index_path: Path) -> List[dict[str, Any]]:
    """从当前向量索引读取全局表达候选池。"""

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    candidates = [
        normalize_item(item)
        for item in payload.get("expressions") or []
        if item.get("id") is not None and item.get("situation") and item.get("style")
    ]
    if len(candidates) < 10:
        raise ValueError(f"全局表达候选不足: {len(candidates)}")
    return candidates


def compute_weights(population: Sequence[dict[str, Any]]) -> List[float]:
    """按线上 legacy 抽样逻辑计算 count 权重。"""

    counts: List[float] = []
    for item in population:
        try:
            count_value = float(item.get("count", 1) or 1)
        except (TypeError, ValueError):
            count_value = 1.0
        counts.append(max(count_value, 0.0))

    if not counts:
        return []
    min_count = min(counts)
    max_count = max(counts)
    if max_count == min_count:
        return [1.0 for _ in counts]
    return [1.0 + ((count_value - min_count) / (max_count - min_count)) * 4.0 for count_value in counts]


def stable_weighted_sample(population: Sequence[dict[str, Any]], k: int, seed: str) -> List[dict[str, Any]]:
    """稳定复现 legacy weighted_sample 的无放回抽样。"""

    if not population or k <= 0:
        return []
    if len(population) <= k:
        return [dict(item) for item in population]

    rng = Random(stable_hash(seed))
    selected: List[dict[str, Any]] = []
    population_copy = [dict(item) for item in population]
    for _ in range(min(k, len(population_copy))):
        weights = compute_weights(population_copy)
        total_weight = sum(weights)
        if total_weight <= 0:
            index = rng.randint(0, len(population_copy) - 1)
            selected.append(population_copy.pop(index))
            continue

        threshold = rng.uniform(0, total_weight)
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if threshold <= cumulative:
                selected.append(population_copy.pop(index))
                break
    return selected


def build_legacy_candidate_pool(global_candidates: Sequence[dict[str, Any]], sample_id: str) -> List[dict[str, Any]]:
    """按 legacy 候选池逻辑构建稳定随机候选。"""

    high_count_candidates = [item for item in global_candidates if (item.get("count", 1) or 1) > 1]
    selected_high = (
        stable_weighted_sample(
            high_count_candidates,
            min(len(high_count_candidates), 5),
            f"{sample_id}:legacy_high",
        )
        if len(high_count_candidates) >= 10
        else []
    )
    selected_random = stable_weighted_sample(
        global_candidates,
        min(len(global_candidates), 5),
        f"{sample_id}:legacy_random",
    )

    candidate_pool: List[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for candidate in [*selected_high, *selected_random]:
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, int) or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        candidate_pool.append(normalize_item(candidate))
    return candidate_pool


def build_reply_tool_args(sample: dict[str, Any]) -> dict[str, Any]:
    """从样本中恢复 reply 工具参数。"""

    reply_tool_args: dict[str, Any] = {}
    reply_guide = str(sample.get("reply_guide") or "").strip()
    if reply_guide:
        reply_tool_args["reply_guide"] = reply_guide
    expression_intent = sample.get("expression_intent")
    if isinstance(expression_intent, dict) and expression_intent:
        reply_tool_args["expression_intent"] = expression_intent
    return reply_tool_args


def load_selector_context_messages(selector_log_path: str, system_prompt: str) -> List[dict[str, Any]]:
    """复用真实 expression_selector 日志中的聊天上下文，只替换 system 候选列表。"""

    path = resolve_path(selector_log_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_messages = list(payload.get("messages") or [])
    if not raw_messages:
        raise ValueError(f"selector 日志缺少 messages: {path}")

    messages = [dict(message) for message in raw_messages]
    messages[0] = {
        "role": "system",
        "content": system_prompt,
    }
    normalized_messages: List[dict[str, Any]] = []
    for message in messages:
        normalized_messages.append(
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "tool_calls": message.get("tool_calls"),
                "tool_call_id": message.get("tool_call_id"),
            }
        )
    return normalized_messages


def parse_selector_response(raw_response: str, candidates: List[dict[str, Any]]) -> List[int]:
    """解析精细选择器输出。"""

    if not raw_response.strip():
        return []
    try:
        parsed_result = json.loads(repair_json(raw_response))
    except Exception:
        return []
    raw_selected_ids = parsed_result.get("selected_ids", []) if isinstance(parsed_result, dict) else []
    return maisaka_expression_selector._parse_selected_ids(
        json.dumps({"selected_ids": raw_selected_ids}, ensure_ascii=False),
        candidates,
    )


async def replay_precise_selector(
    *,
    sample: dict[str, Any],
    candidates: List[dict[str, Any]],
    llm_client: LLMServiceClient,
    max_tokens: int,
) -> SelectorReplayResult:
    """用真实 selector 上下文重放精细选择器。"""

    system_prompt = maisaka_expression_selector._build_selector_prompt(candidates=candidates)
    raw_messages = load_selector_context_messages(str(sample.get("selector_log") or ""), system_prompt)

    def message_factory(_client: object) -> list[Any]:
        del _client
        return [_build_message_from_dict(message) for message in raw_messages]

    response = await llm_client.generate_response_with_messages(
        message_factory,
        LLMGenerationOptions(temperature=0, max_tokens=max(1, int(max_tokens))),
        session_id=str(sample.get("target_session_id") or ""),
    )
    selected_ids = parse_selector_response(response.response.strip(), candidates)
    candidate_map = {
        candidate["id"]: candidate
        for candidate in candidates
        if isinstance(candidate.get("id"), int)
    }
    selected_expressions = [
        candidate_map[expression_id]
        for expression_id in selected_ids
        if expression_id in candidate_map
    ]
    return SelectorReplayResult(
        candidate_pool_size=len(candidates),
        candidate_pool=list(candidates),
        selected_ids=selected_ids,
        selected_expressions=selected_expressions,
        raw_response=response.response.strip(),
        model_name=response.model_name,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
    )


def build_online_vector_intent_result(sample: dict[str, Any]) -> dict[str, Any]:
    """读取 live-log 中真实 vector_intent 精细选择结果。"""

    vector_recall = sample.get("vector_recall") or {}
    return {
        "method": "online_log_vector_intent_precise",
        "candidate_pool_size": len(vector_recall.get("candidate_pool") or []),
        "candidate_pool": [
            normalize_item(item)
            for item in vector_recall.get("candidate_pool") or []
            if isinstance(item, dict)
        ],
        "selected_ids": list(vector_recall.get("selected_ids") or []),
        "selected_expressions": [
            normalize_item(item)
            for item in vector_recall.get("matches") or []
            if isinstance(item, dict)
        ],
        "raw_response": str(vector_recall.get("raw_selector_response") or ""),
        "model_name": str(vector_recall.get("selector_model_name") or ""),
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


async def build_vector_no_intent_candidates(
    *,
    sample: dict[str, Any],
    global_candidates: List[dict[str, Any]],
    candidate_pool_size: int,
    cluster_pool_size: int,
) -> List[dict[str, Any]]:
    """构建不带 intent 的向量候选池。"""

    reply_reason = str(sample.get("content") or "").strip()
    reply_tool_args = build_reply_tool_args(sample)
    query_text = maisaka_expression_selector._build_expression_query_text(
        reply_reason,
        reply_tool_args,
        use_expression_intent=False,
    )
    return await expression_vector_index.select_candidates(
        index_path=global_config.expression.expression_vector_index_path,
        session_id=str(sample.get("target_session_id") or ""),
        query_text=query_text,
        scoped_candidates=global_candidates,
        candidate_pool_size=candidate_pool_size,
        cluster_pool_size=cluster_pool_size,
    )


async def async_main() -> None:
    """脚本入口。"""

    args = parse_args()
    input_path = resolve_path(args.input_json)
    index_path = resolve_path(args.index_json)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_payload = json.loads(input_path.read_text(encoding="utf-8"))
    source_samples = list(source_payload.get("samples") or [])
    if int(args.limit) > 0:
        source_samples = source_samples[: int(args.limit)]
    if not source_samples:
        raise ValueError(f"输入文件没有 samples: {input_path}")

    global_candidates = load_global_candidates(index_path)
    llm_client = LLMServiceClient(
        task_name=str(args.selector_task_name or "planner"),
        request_type="expression.full_pipeline_selector_replay",
    )
    samples: List[dict[str, Any]] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for index, raw_sample in enumerate(source_samples, 1):
        sample = deepcopy(raw_sample)
        sample_id = str(sample.get("sample_id") or f"sample_{index}")

        legacy_candidates = build_legacy_candidate_pool(global_candidates, sample_id)
        vector_no_intent_candidates = await build_vector_no_intent_candidates(
            sample=sample,
            global_candidates=global_candidates,
            candidate_pool_size=max(1, int(args.vector_pool_size)),
            cluster_pool_size=max(1, int(args.cluster_pool_size)),
        )

        legacy_result = await replay_precise_selector(
            sample=sample,
            candidates=legacy_candidates,
            llm_client=llm_client,
            max_tokens=int(args.selector_max_tokens),
        )
        vector_no_intent_result = await replay_precise_selector(
            sample=sample,
            candidates=vector_no_intent_candidates,
            llm_client=llm_client,
            max_tokens=int(args.selector_max_tokens),
        )
        usage["prompt_tokens"] += legacy_result.prompt_tokens + vector_no_intent_result.prompt_tokens
        usage["completion_tokens"] += legacy_result.completion_tokens + vector_no_intent_result.completion_tokens
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        sample["legacy_precise"] = {
            "method": "stable_legacy_candidate_pool_then_precise_selector",
            **asdict(legacy_result),
        }
        sample["vector_no_intent_precise"] = {
            "method": "vector_no_intent_top50_then_precise_selector",
            **asdict(vector_no_intent_result),
        }
        sample["vector_intent_online"] = build_online_vector_intent_result(sample)
        sample.pop("old_direct", None)
        sample.pop("precise_selection", None)
        sample.pop("vector_recall", None)

        samples.append(sample)
        print(
            f"已重放 {index}/{len(source_samples)} "
            f"legacy={len(legacy_result.selected_ids)} "
            f"vector_no_intent={len(vector_no_intent_result.selected_ids)} "
            f"vector_intent_online={len(sample['vector_intent_online']['selected_ids'])}",
            flush=True,
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_source": source_payload.get("sample_source"),
        "input_json": str(input_path),
        "method_keys": METHOD_KEYS,
        "args": {
            "limit": len(samples),
            "selector_task_name": str(args.selector_task_name),
            "selector_max_tokens": int(args.selector_max_tokens),
            "vector_pool_size": int(args.vector_pool_size),
            "cluster_pool_size": int(args.cluster_pool_size),
            "global_candidate_count": len(global_candidates),
            "vector_intent_source": "live_online_expression_selector_log",
        },
        "method_notes": {
            "legacy_precise": "传统 legacy 候选池稳定重放后，使用真实 expression_selector 日志上下文跑精细选择器。",
            "vector_no_intent_precise": "向量召回不拼 expression_intent，取 top50 后，使用真实 expression_selector 日志上下文跑精细选择器。",
            "vector_intent_online": "直接使用 live-log 中线上 vector_intent 候选池和精细选择器最终 selected_ids。",
        },
        "selector_replay_usage": usage,
        "samples": samples,
    }
    output_path = output_dir / f"expression_selection_batch_compare_full_pipeline_{timestamp}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON 输出: {output_path.resolve()}", flush=True)


if __name__ == "__main__":
    asyncio.run(async_main())

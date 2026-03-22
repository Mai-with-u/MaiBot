"""
聚合查询服务：
- 并发执行 search/time/episode 分支
- 统一分支结果结构
- 可选混合排序（Weighted RRF）
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.common.logger import get_logger

logger = get_logger("A_Memorix.AggregateQueryService")

BranchRunner = Callable[[], Awaitable[Dict[str, Any]]]


class AggregateQueryService:
    """聚合查询执行服务（search/time/episode）。"""

    def __init__(self, plugin_config: Optional[Any] = None):
        self.plugin_config = plugin_config or {}

    def _cfg(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.plugin_config, "get_config", None)
        if callable(getter):
            return getter(key, default)

        current: Any = self.plugin_config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def _rrf_k(self) -> float:
        raw = self._cfg("retrieval.aggregate.rrf_k", 60.0)
        value = self._as_float(raw, 60.0)
        return max(1.0, value)

    def _weights(self) -> Dict[str, float]:
        defaults = {"search": 1.0, "time": 1.0, "episode": 1.0}
        raw = self._cfg("retrieval.aggregate.weights", {})
        if not isinstance(raw, dict):
            return defaults

        out = dict(defaults)
        for key in ("search", "time", "episode"):
            if key in raw:
                out[key] = max(0.0, self._as_float(raw.get(key), defaults[key]))
        return out

    @staticmethod
    def _normalize_branch_payload(
        name: str,
        payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        results_raw = data.get("results", [])
        results = results_raw if isinstance(results_raw, list) else []
        count = data.get("count")
        if count is None:
            count = len(results)
        return {
            "name": name,
            "success": bool(data.get("success", False)),
            "skipped": bool(data.get("skipped", False)),
            "skip_reason": str(data.get("skip_reason", "") or "").strip(),
            "error": str(data.get("error", "") or "").strip(),
            "results": results,
            "count": max(0, int(count)),
            "elapsed_ms": max(0.0, float(data.get("elapsed_ms", 0.0) or 0.0)),
            "content": str(data.get("content", "") or ""),
            "query_type": str(data.get("query_type", "") or name),
        }

    @staticmethod
    def _mix_key(item: Dict[str, Any], branch: str, rank: int) -> str:
        item_type = str(item.get("type", "") or "").strip().lower()
        if item_type == "episode":
            episode_id = str(item.get("episode_id", "") or "").strip()
            if episode_id:
                return f"episode:{episode_id}"

        item_hash = str(item.get("hash", "") or "").strip()
        if item_hash:
            return f"{item_type}:{item_hash}"

        return f"{branch}:{item_type}:{rank}:{str(item.get('content', '') or '')[:80]}"

    def _build_mixed_results(
        self,
        *,
        branches: Dict[str, Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        rrf_k = self._rrf_k()
        weights = self._weights()
        bucket: Dict[str, Dict[str, Any]] = {}

        for branch_name, branch in branches.items():
            if not branch.get("success", False):
                continue
            results = branch.get("results", [])
            if not isinstance(results, list):
                continue

            weight = max(0.0, float(weights.get(branch_name, 1.0)))
            for idx, item in enumerate(results, start=1):
                if not isinstance(item, dict):
                    continue
                key = self._mix_key(item, branch_name, idx)
                score = weight / (rrf_k + float(idx))
                if key not in bucket:
                    merged = dict(item)
                    merged["fusion_score"] = 0.0
                    merged["_source_branches"] = set()
                    bucket[key] = merged

                target = bucket[key]
                target["fusion_score"] = float(target.get("fusion_score", 0.0)) + score
                target["_source_branches"].add(branch_name)

        mixed = list(bucket.values())
        mixed.sort(
            key=lambda x: (
                -float(x.get("fusion_score", 0.0)),
                str(x.get("type", "") or ""),
                str(x.get("hash", "") or x.get("episode_id", "") or ""),
            )
        )

        out: List[Dict[str, Any]] = []
        for rank, item in enumerate(mixed[: max(1, int(top_k))], start=1):
            merged = dict(item)
            branches_set = merged.pop("_source_branches", set())
            merged["source_branches"] = sorted(list(branches_set))
            merged["rank"] = rank
            out.append(merged)
        return out

    @staticmethod
    def _status(branch: Dict[str, Any]) -> str:
        if branch.get("skipped", False):
            return "skipped"
        if branch.get("success", False):
            return "success"
        return "failed"

    def _build_summary(self, branches: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        summary: Dict[str, Dict[str, Any]] = {}
        for name, branch in branches.items():
            status = self._status(branch)
            summary[name] = {
                "status": status,
                "count": int(branch.get("count", 0) or 0),
            }
            if status == "skipped":
                summary[name]["reason"] = str(branch.get("skip_reason", "") or "")
            if status == "failed":
                summary[name]["error"] = str(branch.get("error", "") or "")
        return summary

    def _build_content(
        self,
        *,
        query: str,
        branches: Dict[str, Dict[str, Any]],
        errors: List[Dict[str, str]],
        mixed_results: Optional[List[Dict[str, Any]]],
    ) -> str:
        lines: List[str] = [
            f"🔀 聚合查询结果（query='{query or 'N/A'}'）",
            "",
            "分支状态：",
        ]
        for name in ("search", "time", "episode"):
            branch = branches.get(name, {})
            status = self._status(branch)
            count = int(branch.get("count", 0) or 0)
            line = f"- {name}: {status}, count={count}"
            reason = str(branch.get("skip_reason", "") or "").strip()
            err = str(branch.get("error", "") or "").strip()
            if status == "skipped" and reason:
                line += f" ({reason})"
            if status == "failed" and err:
                line += f" ({err})"
            lines.append(line)

        if errors:
            lines.append("")
            lines.append("错误：")
            for item in errors[:6]:
                lines.append(f"- {item.get('branch', 'unknown')}: {item.get('error', 'unknown error')}")

        if mixed_results is not None:
            lines.append("")
            lines.append(f"🧩 混合结果（{len(mixed_results)} 条）：")
            for idx, item in enumerate(mixed_results[:5], start=1):
                src = ",".join(item.get("source_branches", []) or [])
                if str(item.get("type", "") or "") == "episode":
                    title = str(item.get("title", "") or "Untitled")
                    lines.append(f"{idx}. 🧠 {title} [{src}]")
                else:
                    text = str(item.get("content", "") or "")
                    if len(text) > 80:
                        text = text[:80] + "..."
                    lines.append(f"{idx}. {text} [{src}]")

        return "\n".join(lines)

    async def execute(
        self,
        *,
        query: str,
        top_k: int,
        mix: bool,
        mix_top_k: Optional[int],
        time_from: Optional[str],
        time_to: Optional[str],
        search_runner: Optional[BranchRunner],
        time_runner: Optional[BranchRunner],
        episode_runner: Optional[BranchRunner],
    ) -> Dict[str, Any]:
        clean_query = str(query or "").strip()
        safe_top_k = max(1, int(top_k))
        safe_mix_top_k = max(1, int(mix_top_k if mix_top_k is not None else safe_top_k))

        branches: Dict[str, Dict[str, Any]] = {}
        errors: List[Dict[str, str]] = []
        scheduled: List[Tuple[str, asyncio.Task]] = []

        if clean_query:
            if search_runner is not None:
                scheduled.append(("search", asyncio.create_task(search_runner())))
            else:
                branches["search"] = self._normalize_branch_payload(
                    "search",
                    {"success": False, "error": "search runner unavailable", "results": []},
                )
        else:
            branches["search"] = self._normalize_branch_payload(
                "search",
                {
                    "success": False,
                    "skipped": True,
                    "skip_reason": "missing_query",
                    "results": [],
                    "count": 0,
                },
            )

        if time_from or time_to:
            if time_runner is not None:
                scheduled.append(("time", asyncio.create_task(time_runner())))
            else:
                branches["time"] = self._normalize_branch_payload(
                    "time",
                    {"success": False, "error": "time runner unavailable", "results": []},
                )
        else:
            branches["time"] = self._normalize_branch_payload(
                "time",
                {
                    "success": False,
                    "skipped": True,
                    "skip_reason": "missing_time_window",
                    "results": [],
                    "count": 0,
                },
            )

        if episode_runner is not None:
            scheduled.append(("episode", asyncio.create_task(episode_runner())))
        else:
            branches["episode"] = self._normalize_branch_payload(
                "episode",
                {"success": False, "error": "episode runner unavailable", "results": []},
            )

        if scheduled:
            done = await asyncio.gather(
                *[task for _, task in scheduled],
                return_exceptions=True,
            )
            for (branch_name, _), payload in zip(scheduled, done):
                if isinstance(payload, Exception):
                    logger.error(f"aggregate branch failed: branch={branch_name} error={payload}")
                    normalized = self._normalize_branch_payload(
                        branch_name,
                        {
                            "success": False,
                            "error": str(payload),
                            "results": [],
                        },
                    )
                else:
                    normalized = self._normalize_branch_payload(branch_name, payload)
                branches[branch_name] = normalized

        for name in ("search", "time", "episode"):
            branch = branches.get(name)
            if not branch:
                continue
            if branch.get("skipped", False):
                continue
            if not branch.get("success", False):
                errors.append(
                    {
                        "branch": name,
                        "error": str(branch.get("error", "") or "unknown error"),
                    }
                )

        success = any(
            bool(branches.get(name, {}).get("success", False))
            for name in ("search", "time", "episode")
        )
        mixed_results: Optional[List[Dict[str, Any]]] = None
        if mix:
            mixed_results = self._build_mixed_results(branches=branches, top_k=safe_mix_top_k)

        payload: Dict[str, Any] = {
            "success": success,
            "query_type": "aggregate",
            "query": clean_query,
            "top_k": safe_top_k,
            "mix": bool(mix),
            "mix_top_k": safe_mix_top_k,
            "branches": branches,
            "errors": errors,
            "summary": self._build_summary(branches),
        }
        if mixed_results is not None:
            payload["mixed_results"] = mixed_results

        payload["content"] = self._build_content(
            query=clean_query,
            branches=branches,
            errors=errors,
            mixed_results=mixed_results,
        )
        return payload

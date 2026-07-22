"""
Episode 聚合与落库服务。

流程：
1. 读取 source 的完整活跃段落快照
2. 按真实时间区间连通分量分组
3. 复用输入指纹未变的旧 Episode，其余调用 LLM 切分
4. 返回完整物化计划，由 source revision CAS 原子发布
5. LLM 失败时使用确定性 fallback
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.config.config import global_config

from .episode_segmentation_service import EpisodeSegmentationService
from .hash import compute_hash
from .runtime_payloads import argument_tokens

logger = get_logger("A_Memorix.EpisodeService")


class EpisodeService:
    """Episode MVP 后台处理服务。"""

    MATERIALIZATION_VERSION = "episode_source_revision_v1"

    def __init__(
        self,
        *,
        metadata_store: Any,
        plugin_config: Optional[Any] = None,
        segmentation_service: Optional[EpisodeSegmentationService] = None,
    ):
        self.metadata_store = metadata_store
        self.plugin_config = plugin_config or {}
        self.segmentation_service = segmentation_service or EpisodeSegmentationService(
            plugin_config=self._config_dict(),
        )

    def _config_dict(self) -> Dict[str, Any]:
        if isinstance(self.plugin_config, dict):
            return self.plugin_config
        return {}

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
    def _to_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _clamp_score(value: Any, default: float = 1.0) -> float:
        try:
            num = float(value)
        except Exception:
            num = default
        if num < 0.0:
            return 0.0
        if num > 1.0:
            return 1.0
        return num

    @staticmethod
    def _paragraph_interval(paragraph: Dict[str, Any]) -> Tuple[float, float]:
        created_at = EpisodeService._to_optional_float(paragraph.get("created_at"))
        event_time = EpisodeService._to_optional_float(paragraph.get("event_time"))
        event_start = EpisodeService._to_optional_float(paragraph.get("event_time_start"))
        event_end = EpisodeService._to_optional_float(paragraph.get("event_time_end"))
        start = event_start
        if start is None:
            start = event_time if event_time is not None else event_end
        if start is None:
            start = created_at if created_at is not None else 0.0
        end = event_end
        if end is None:
            end = event_time if event_time is not None else event_start
        if end is None:
            end = created_at if created_at is not None else start
        if end < start:
            start, end = end, start
        return float(start), float(end)

    @staticmethod
    def _paragraph_anchor(paragraph: Dict[str, Any]) -> float:
        return EpisodeService._paragraph_interval(paragraph)[0]

    @staticmethod
    def _paragraph_sort_key(paragraph: Dict[str, Any]) -> Tuple[float, float, str]:
        start, end = EpisodeService._paragraph_interval(paragraph)
        return (start, end, str(paragraph.get("hash", "") or ""))

    def generation_signature(self) -> Dict[str, Any]:
        memory_cfg = global_config.a_memorix.integration
        disabled_source_types = sorted(
            {
                str(item or "").strip().lower()
                for item in argument_tokens(self._cfg("episode.disabled_source_types", ["person_fact"]))
                if str(item or "").strip()
            }
        )
        return {
            "materialization_version": self.MATERIALIZATION_VERSION,
            "segmentation": dict(self.segmentation_service.generation_signature()),
            "grouping": {
                "max_paragraphs_per_call": max(1, int(self._cfg("episode.max_paragraphs_per_call", 20))),
                "max_chars_per_call": max(200, int(self._cfg("episode.max_chars_per_call", 6000))),
                "source_time_window_seconds": max(
                    60.0,
                    float(self._cfg("episode.source_time_window_hours", 24)) * 3600.0,
                ),
            },
            "source_policy": {
                "enabled": bool(self._cfg("episode.enabled", True)),
                "generation_enabled": bool(self._cfg("episode.generation_enabled", True)),
                "disabled_source_types": disabled_source_types,
                "feedback_hard_filter_enabled": bool(
                    getattr(memory_cfg, "feedback_correction_paragraph_hard_filter_enabled", True)
                ),
            },
        }

    def generation_hash(self, signature: Optional[Dict[str, Any]] = None) -> str:
        payload = signature if isinstance(signature, dict) else self.generation_signature()
        return compute_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _enrich_paragraph_participants(self, paragraphs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        hashes = [
            str(paragraph.get("hash", "") or "").strip()
            for paragraph in paragraphs
            if str(paragraph.get("hash", "") or "").strip()
        ]
        entity_map = self.metadata_store.get_paragraph_entities_by_hashes(hashes)
        enriched: List[Dict[str, Any]] = []
        for paragraph in paragraphs:
            item = dict(paragraph)
            paragraph_hash = str(item.get("hash", "") or "").strip()
            item["_participant_names"] = sorted(
                {
                    str(entity.get("name", "") or "").strip()
                    for entity in entity_map.get(paragraph_hash, [])
                    if str(entity.get("name", "") or "").strip()
                }
            )
            enriched.append(item)
        return enriched

    def _group_input_fingerprint(self, group: Dict[str, Any]) -> str:
        """计算分组输入版本，只有会影响分段或派生字段的内容进入指纹。"""
        paragraphs = sorted(list(group.get("paragraphs") or []), key=self._paragraph_sort_key)
        paragraph_payloads: List[Dict[str, Any]] = []
        for paragraph in paragraphs:
            paragraph_hash = str(paragraph.get("hash", "") or "").strip()
            participant_names = list(paragraph.get("_participant_names") or [])
            paragraph_payloads.append(
                {
                    "hash": paragraph_hash,
                    "content": str(paragraph.get("content", "") or ""),
                    "created_at": self._to_optional_float(paragraph.get("created_at")),
                    "event_time": self._to_optional_float(paragraph.get("event_time")),
                    "event_time_start": self._to_optional_float(paragraph.get("event_time_start")),
                    "event_time_end": self._to_optional_float(paragraph.get("event_time_end")),
                    "time_granularity": str(paragraph.get("time_granularity", "") or "").strip(),
                    "time_confidence": self._clamp_score(paragraph.get("time_confidence"), default=1.0),
                    "participants": participant_names,
                }
            )
        segmentation_generation = group.get("_segmentation_generation")
        if not isinstance(segmentation_generation, dict):
            segmentation_generation = self.segmentation_service.generation_signature()
        fingerprint_payload = {
            "source": str(group.get("source", "") or "").strip(),
            "segmentation_generation": segmentation_generation,
            "grouping": {
                "max_paragraphs_per_call": max(1, int(self._cfg("episode.max_paragraphs_per_call", 20))),
                "max_chars_per_call": max(200, int(self._cfg("episode.max_chars_per_call", 6000))),
                "source_time_window_hours": max(
                    60.0,
                    float(self._cfg("episode.source_time_window_hours", 24)) * 3600.0,
                ),
            },
            "paragraphs": paragraph_payloads,
        }
        return compute_hash(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _reusable_group_payloads(
        group: Dict[str, Any],
        input_fingerprint: str,
        cached_by_fingerprint: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        cached = cached_by_fingerprint.get(input_fingerprint, [])
        if not cached:
            return []
        group_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in (group.get("paragraphs") or [])
            if str(item.get("hash", "") or "").strip()
        ]
        cached_hashes = [
            str(paragraph_hash).strip()
            for episode in cached
            for paragraph_hash in (episode.get("evidence_ids") or [])
            if str(paragraph_hash).strip()
        ]
        if len(cached_hashes) != len(set(cached_hashes)):
            return []
        if set(cached_hashes) != set(group_hashes):
            return []
        if any(
            not str(episode.get("title", "") or "").strip()
            or not str(episode.get("summary", "") or "").strip()
            or str(episode.get("segmentation_model", "") or "").strip() == "fallback_rule"
            for episode in cached
        ):
            return []
        return [dict(episode) for episode in cached]

    def group_paragraphs(self, paragraphs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        按 source + 时间邻近窗口组批，并受段落数/字符数上限约束。
        """
        if not paragraphs:
            return []

        max_paragraphs = max(1, int(self._cfg("episode.max_paragraphs_per_call", 20)))
        max_chars = max(200, int(self._cfg("episode.max_chars_per_call", 6000)))
        window_seconds = max(
            60.0,
            float(self._cfg("episode.source_time_window_hours", 24)) * 3600.0,
        )

        by_source: Dict[str, List[Dict[str, Any]]] = {}
        for paragraph in paragraphs:
            source = str(paragraph.get("source", "") or "").strip()
            by_source.setdefault(source, []).append(paragraph)

        groups: List[Dict[str, Any]] = []
        for source, items in by_source.items():
            ordered = sorted(items, key=self._paragraph_sort_key)
            temporal_components: List[List[Dict[str, Any]]] = []
            component: List[Dict[str, Any]] = []
            frontier_end: Optional[float] = None

            for paragraph in ordered:
                interval_start, interval_end = self._paragraph_interval(paragraph)
                if component and frontier_end is not None and interval_start > frontier_end + window_seconds:
                    temporal_components.append(component)
                    component = []
                    frontier_end = None
                component.append(paragraph)
                frontier_end = interval_end if frontier_end is None else max(frontier_end, interval_end)
            if component:
                temporal_components.append(component)

            # 时间连通性不受模型调用上限影响；上限只在已确定的连通分量内切块。
            for temporal_component in temporal_components:
                current: List[Dict[str, Any]] = []
                current_chars = 0
                for paragraph in temporal_component:
                    content_len = len(str(paragraph.get("content", "") or ""))
                    if current and (
                        len(current) >= max_paragraphs or current_chars + content_len > max_chars
                    ):
                        groups.append({"source": source, "paragraphs": current})
                        current = []
                        current_chars = 0
                    current.append(paragraph)
                    current_chars += content_len
                if current:
                    groups.append({"source": source, "paragraphs": current})

        groups.sort(
            key=lambda group: (
                self._paragraph_anchor(group["paragraphs"][0]) if group.get("paragraphs") else 0.0,
                str(group.get("source", "") or ""),
                str(group["paragraphs"][0].get("hash", "") or "") if group.get("paragraphs") else "",
            )
        )
        return groups

    def _compute_time_meta(
        self, paragraphs: List[Dict[str, Any]]
    ) -> Tuple[Optional[float], Optional[float], Optional[str], float]:
        starts: List[float] = []
        ends: List[float] = []
        granularity_priority = {
            "minute": 4,
            "hour": 3,
            "day": 2,
            "month": 1,
            "year": 0,
        }
        granularity = None
        granularity_rank = -1
        conf_values: List[float] = []

        for p in paragraphs:
            s = self._to_optional_float(p.get("event_time_start"))
            e = self._to_optional_float(p.get("event_time_end"))
            t = self._to_optional_float(p.get("event_time"))
            c = self._to_optional_float(p.get("created_at"))

            start_candidate = s if s is not None else (t if t is not None else (e if e is not None else c))
            end_candidate = e if e is not None else (t if t is not None else (s if s is not None else c))

            if start_candidate is not None and end_candidate is not None and end_candidate < start_candidate:
                start_candidate, end_candidate = end_candidate, start_candidate

            if start_candidate is not None:
                starts.append(start_candidate)
            if end_candidate is not None:
                ends.append(end_candidate)

            g = str(p.get("time_granularity", "") or "").strip().lower()
            if g in granularity_priority and granularity_priority[g] > granularity_rank:
                granularity_rank = granularity_priority[g]
                granularity = g

            conf_values.append(self._clamp_score(p.get("time_confidence"), default=1.0))

        time_start = min(starts) if starts else None
        time_end = max(ends) if ends else None
        time_conf = sum(conf_values) / len(conf_values) if conf_values else 1.0
        return time_start, time_end, granularity, self._clamp_score(time_conf, default=1.0)

    @staticmethod
    def _collect_participants(paragraphs: List[Dict[str, Any]], limit: int = 16) -> List[str]:
        seen = set()
        participants: List[str] = []
        for paragraph in paragraphs:
            for raw_name in paragraph.get("_participant_names") or []:
                name = str(raw_name or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                participants.append(name)
                if len(participants) >= limit:
                    return participants
        return participants

    @staticmethod
    def _derive_keywords(paragraphs: List[Dict[str, Any]], limit: int = 12) -> List[str]:
        token_counter: Counter[str] = Counter()
        token_pattern = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}")
        stop_words = {
            "the",
            "and",
            "that",
            "this",
            "with",
            "from",
            "for",
            "have",
            "will",
            "your",
            "you",
            "我们",
            "你们",
            "他们",
            "以及",
            "一个",
            "这个",
            "那个",
            "然后",
            "因为",
            "所以",
        }
        for p in paragraphs:
            text = str(p.get("content", "") or "").lower()
            for token in token_pattern.findall(text):
                if token in stop_words:
                    continue
                token_counter[token] += 1

        return [token for token, _ in token_counter.most_common(limit)]

    def _build_fallback_episode(self, group: Dict[str, Any]) -> Dict[str, Any]:
        paragraphs = group.get("paragraphs", []) or []
        source = str(group.get("source", "") or "").strip()
        hashes = [str(p.get("hash", "") or "").strip() for p in paragraphs if str(p.get("hash", "") or "").strip()]
        snippets = []
        for p in paragraphs[:3]:
            text = str(p.get("content", "") or "").strip().replace("\n", " ")
            if text:
                snippets.append(text[:140])
        summary = "；".join(snippets)[:500] if snippets else "自动回退生成的情景记忆。"

        time_start, time_end, granularity, time_conf = self._compute_time_meta(paragraphs)
        participants = self._collect_participants(paragraphs, limit=12)
        keywords = self._derive_keywords(paragraphs, limit=10)

        if time_start is not None:
            day_text = datetime.fromtimestamp(time_start).strftime("%Y-%m-%d")
            title = f"{source or 'unknown'} {day_text} 情景片段"
        else:
            title = f"{source or 'unknown'} 情景片段"

        return {
            "title": title[:80],
            "summary": summary,
            "paragraph_hashes": hashes,
            "participants": participants,
            "keywords": keywords,
            "time_confidence": time_conf,
            "llm_confidence": 0.0,
            "event_time_start": time_start,
            "event_time_end": time_end,
            "time_granularity": granularity,
            "segmentation_model": "fallback_rule",
            "segmentation_version": EpisodeSegmentationService.SEGMENTATION_VERSION,
        }

    @staticmethod
    def _normalize_episode_hashes(episode_hashes: List[str], group_hashes_ordered: List[str]) -> List[str]:
        in_group = set(group_hashes_ordered)
        dedup: List[str] = []
        seen = set()
        for h in episode_hashes or []:
            token = str(h or "").strip()
            if not token or token not in in_group or token in seen:
                continue
            seen.add(token)
            dedup.append(token)
        return dedup

    async def _build_episode_payloads_for_group(self, group: Dict[str, Any]) -> Dict[str, Any]:
        paragraphs = group.get("paragraphs", []) or []
        if not paragraphs:
            return {
                "payloads": [],
                "done_hashes": [],
                "episode_count": 0,
                "fallback_count": 0,
            }

        source = str(group.get("source", "") or "").strip()
        input_fingerprint = str(group.get("_input_fingerprint", "") or "").strip()
        if not input_fingerprint:
            input_fingerprint = self._group_input_fingerprint(group)
        group_hashes = [
            str(p.get("hash", "") or "").strip() for p in paragraphs if str(p.get("hash", "") or "").strip()
        ]
        group_start, group_end, _, _ = self._compute_time_meta(paragraphs)

        fallback_used = False
        segmentation_model = "fallback_rule"
        segmentation_version = EpisodeSegmentationService.SEGMENTATION_VERSION

        try:
            llm_result = await self.segmentation_service.segment(
                source=source,
                window_start=group_start,
                window_end=group_end,
                paragraphs=paragraphs,
            )
            episodes = list(llm_result.get("episodes") or [])
            segmentation_model = str(llm_result.get("segmentation_model", "") or "").strip() or "auto"
            segmentation_version = (
                str(llm_result.get("segmentation_version", "") or "").strip()
                or EpisodeSegmentationService.SEGMENTATION_VERSION
            )
            if not episodes:
                raise ValueError("llm_empty_episodes")
            EpisodeSegmentationService.validate_episode_coverage(episodes, group_hashes)
        except Exception as e:
            logger.warning(f"Episode segmentation fallback: source={source} size={len(group_hashes)} err={e}")
            episodes = [self._build_fallback_episode(group)]
            fallback_used = True

        stored_payloads: List[Dict[str, Any]] = []
        for episode in episodes:
            ordered_hashes = self._normalize_episode_hashes(
                episode_hashes=episode.get("paragraph_hashes", []),
                group_hashes_ordered=group_hashes,
            )
            if not ordered_hashes:
                continue

            sub_paragraphs = [p for p in paragraphs if str(p.get("hash", "") or "") in set(ordered_hashes)]
            event_start, event_end, granularity, time_conf_default = self._compute_time_meta(sub_paragraphs)

            participants = [str(x).strip() for x in (episode.get("participants", []) or []) if str(x).strip()]
            keywords = [str(x).strip() for x in (episode.get("keywords", []) or []) if str(x).strip()]
            if not participants:
                participants = self._collect_participants(sub_paragraphs, limit=16)
            if not keywords:
                keywords = self._derive_keywords(sub_paragraphs, limit=12)

            title = str(episode.get("title", "") or "").strip()[:120]
            summary = str(episode.get("summary", "") or "").strip()[:2000]
            if not title or not summary:
                continue

            seed = json.dumps(
                {
                    "source": source,
                    "hashes": ordered_hashes,
                    "version": segmentation_version,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            episode_id = compute_hash(seed)

            payload = {
                "episode_id": episode_id,
                "source": source or None,
                "title": title,
                "summary": summary,
                "event_time_start": episode.get("event_time_start", event_start),
                "event_time_end": episode.get("event_time_end", event_end),
                "time_granularity": episode.get("time_granularity", granularity),
                "time_confidence": self._clamp_score(
                    episode.get("time_confidence"),
                    default=time_conf_default,
                ),
                "participants": participants[:16],
                "keywords": keywords[:20],
                "evidence_ids": ordered_hashes,
                "paragraph_count": len(ordered_hashes),
                "llm_confidence": self._clamp_score(
                    episode.get("llm_confidence"),
                    default=0.0 if fallback_used else 0.6,
                ),
                "segmentation_model": (
                    str(episode.get("segmentation_model", "") or "").strip()
                    or ("fallback_rule" if fallback_used else segmentation_model)
                ),
                "segmentation_version": (
                    str(episode.get("segmentation_version", "") or "").strip() or segmentation_version
                ),
                "input_fingerprint": input_fingerprint,
            }
            stored_payloads.append(payload)

        stored_hashes = {
            str(paragraph_hash or "").strip()
            for payload in stored_payloads
            for paragraph_hash in (payload.get("evidence_ids") or [])
            if str(paragraph_hash or "").strip()
        }
        return {
            "payloads": stored_payloads,
            "done_hashes": [hash_value for hash_value in group_hashes if hash_value in stored_hashes],
            "episode_count": len(stored_payloads),
            "fallback_count": 1 if fallback_used else 0,
        }

    @staticmethod
    def _validate_source_payload_coverage(
        paragraphs: List[Dict[str, Any]],
        payloads: List[Dict[str, Any]],
    ) -> None:
        expected = [
            str(paragraph.get("hash", "") or "").strip()
            for paragraph in paragraphs
            if str(paragraph.get("hash", "") or "").strip()
        ]
        assigned = [
            str(paragraph_hash or "").strip()
            for payload in payloads
            for paragraph_hash in (payload.get("evidence_ids") or [])
            if str(paragraph_hash or "").strip()
        ]
        if Counter(expected) != Counter(assigned):
            raise ValueError("episode_source_coverage_invalid")

    async def plan_source_rebuild(
        self,
        source: str,
        *,
        segmentation_generation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """读取完整来源并生成可供CAS发布的确定性物化计划。"""
        token = str(source or "").strip()
        if not token:
            return {
                "source": "",
                "payloads": [],
                "episode_count": 0,
                "fallback_count": 0,
                "group_count": 0,
                "paragraph_count": 0,
            }

        memory_cfg = global_config.a_memorix.integration
        paragraphs = self.metadata_store.get_live_paragraphs_by_source(
            token,
            exclude_stale=bool(getattr(memory_cfg, "feedback_correction_paragraph_hard_filter_enabled", True)),
        )
        generation = (
            dict(segmentation_generation)
            if isinstance(segmentation_generation, dict)
            else self.generation_signature()
        )
        generation_hash = self.generation_hash(generation)
        if not paragraphs:
            return {
                "source": token,
                "payloads": [],
                "episode_count": 0,
                "fallback_count": 0,
                "group_count": 0,
                "paragraph_count": 0,
                "generation_hash": generation_hash,
                "reused_group_count": 0,
                "reused_episode_count": 0,
                "recomputed_group_count": 0,
            }

        paragraphs = self._enrich_paragraph_participants(paragraphs)
        groups = self.group_paragraphs(paragraphs)
        existing_episodes = self.metadata_store.get_episodes_by_source(token)
        cached_by_fingerprint: Dict[str, List[Dict[str, Any]]] = {}
        for episode in existing_episodes:
            input_fingerprint = str(episode.get("input_fingerprint", "") or "").strip()
            if input_fingerprint:
                cached_by_fingerprint.setdefault(input_fingerprint, []).append(episode)
        payloads: List[Dict[str, Any]] = []
        fallback_count = 0
        reused_group_count = 0
        reused_episode_count = 0
        recomputed_group_count = 0

        for group in groups:
            group["_segmentation_generation"] = generation
            input_fingerprint = self._group_input_fingerprint(group)
            group["_input_fingerprint"] = input_fingerprint
            cached_payloads = self._reusable_group_payloads(
                group,
                input_fingerprint,
                cached_by_fingerprint,
            )
            if cached_payloads:
                payloads.extend(cached_payloads)
                reused_group_count += 1
                reused_episode_count += len(cached_payloads)
                continue
            result = await self._build_episode_payloads_for_group(group)
            payloads.extend(list(result.get("payloads") or []))
            fallback_count += int(result.get("fallback_count") or 0)
            recomputed_group_count += 1

        self._validate_source_payload_coverage(paragraphs, payloads)
        return {
            "source": token,
            "payloads": payloads,
            "episode_count": len(payloads),
            "fallback_count": fallback_count,
            "group_count": len(groups),
            "paragraph_count": len(paragraphs),
            "reused_group_count": reused_group_count,
            "reused_episode_count": reused_episode_count,
            "recomputed_group_count": recomputed_group_count,
            "generation_hash": generation_hash,
        }

    async def rebuild_source(self, source: str) -> Dict[str, Any]:
        """离线管理入口；在线后台必须使用带租约的来源发布流程。"""
        plan = await self.plan_source_rebuild(source)
        token = str(plan.get("source", "") or "").strip()
        if not token:
            return {key: value for key, value in plan.items() if key != "payloads"}
        replace_result = self.metadata_store.replace_episodes_for_source(
            token,
            list(plan.get("payloads") or []),
        )
        result = {key: value for key, value in plan.items() if key != "payloads"}
        result["episode_count"] = int(replace_result.get("episode_count") or 0)
        return result

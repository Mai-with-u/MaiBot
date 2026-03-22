"""
Episode 聚合与落库服务。

流程：
1. 从 pending 队列读取段落并组批
2. 按 source + 时间窗口切组
3. 调用 LLM 语义切分
4. 写入 episodes + episode_paragraphs
5. LLM 失败时使用确定性 fallback
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger

from .episode_segmentation_service import EpisodeSegmentationService
from .hash import compute_hash

logger = get_logger("A_Memorix.EpisodeService")


class EpisodeService:
    """Episode MVP 后台处理服务。"""

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
    def _paragraph_anchor(paragraph: Dict[str, Any]) -> float:
        for key in ("event_time_end", "event_time_start", "event_time", "created_at"):
            value = paragraph.get(key)
            try:
                if value is not None:
                    return float(value)
            except Exception:
                continue
        return 0.0

    @staticmethod
    def _paragraph_sort_key(paragraph: Dict[str, Any]) -> Tuple[float, str]:
        return (
            EpisodeService._paragraph_anchor(paragraph),
            str(paragraph.get("hash", "") or ""),
        )

    def load_pending_paragraphs(
        self,
        pending_rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        将 pending 行展开为段落上下文。

        Returns:
            (loaded_paragraphs, missing_hashes)
        """
        loaded: List[Dict[str, Any]] = []
        missing: List[str] = []
        for row in pending_rows or []:
            p_hash = str(row.get("paragraph_hash", "") or "").strip()
            if not p_hash:
                continue

            paragraph = self.metadata_store.get_paragraph(p_hash)
            if not paragraph:
                missing.append(p_hash)
                continue

            loaded.append(
                {
                    "hash": p_hash,
                    "source": str(row.get("source") or paragraph.get("source") or "").strip(),
                    "content": str(paragraph.get("content", "") or ""),
                    "created_at": self._to_optional_float(paragraph.get("created_at"))
                    or self._to_optional_float(row.get("created_at"))
                    or 0.0,
                    "event_time": self._to_optional_float(paragraph.get("event_time")),
                    "event_time_start": self._to_optional_float(paragraph.get("event_time_start")),
                    "event_time_end": self._to_optional_float(paragraph.get("event_time_end")),
                    "time_granularity": str(paragraph.get("time_granularity", "") or "").strip() or None,
                    "time_confidence": self._clamp_score(paragraph.get("time_confidence"), default=1.0),
                }
            )
        return loaded, missing

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

            current: List[Dict[str, Any]] = []
            current_chars = 0
            last_anchor: Optional[float] = None

            def flush() -> None:
                nonlocal current, current_chars, last_anchor
                if not current:
                    return
                sorted_current = sorted(current, key=self._paragraph_sort_key)
                groups.append(
                    {
                        "source": source,
                        "paragraphs": sorted_current,
                    }
                )
                current = []
                current_chars = 0
                last_anchor = None

            for paragraph in ordered:
                anchor = self._paragraph_anchor(paragraph)
                content_len = len(str(paragraph.get("content", "") or ""))

                need_flush = False
                if current:
                    if len(current) >= max_paragraphs:
                        need_flush = True
                    elif current_chars + content_len > max_chars:
                        need_flush = True
                    elif last_anchor is not None and abs(anchor - last_anchor) > window_seconds:
                        need_flush = True

                if need_flush:
                    flush()

                current.append(paragraph)
                current_chars += content_len
                last_anchor = anchor

            flush()

        groups.sort(
            key=lambda g: self._paragraph_anchor(g["paragraphs"][0]) if g.get("paragraphs") else 0.0
        )
        return groups

    def _compute_time_meta(self, paragraphs: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], Optional[str], float]:
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

    def _collect_participants(self, paragraph_hashes: List[str], limit: int = 16) -> List[str]:
        seen = set()
        participants: List[str] = []
        for p_hash in paragraph_hashes:
            try:
                entities = self.metadata_store.get_paragraph_entities(p_hash)
            except Exception:
                entities = []
            for item in entities:
                name = str(item.get("name", "") or "").strip()
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
        participants = self._collect_participants(hashes, limit=12)
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
        group_hashes = [str(p.get("hash", "") or "").strip() for p in paragraphs if str(p.get("hash", "") or "").strip()]
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
            segmentation_version = str(llm_result.get("segmentation_version", "") or "").strip() or EpisodeSegmentationService.SEGMENTATION_VERSION
            if not episodes:
                raise ValueError("llm_empty_episodes")
        except Exception as e:
            logger.warning(
                "Episode segmentation fallback: "
                f"source={source} "
                f"size={len(group_hashes)} "
                f"err={e}"
            )
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
                participants = self._collect_participants(ordered_hashes, limit=16)
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
                    str(episode.get("segmentation_version", "") or "").strip()
                    or segmentation_version
                ),
            }
            stored_payloads.append(payload)

        return {
            "payloads": stored_payloads,
            "done_hashes": group_hashes,
            "episode_count": len(stored_payloads),
            "fallback_count": 1 if fallback_used else 0,
        }

    async def process_group(self, group: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._build_episode_payloads_for_group(group)
        stored_count = 0
        for payload in result.get("payloads") or []:
            stored = self.metadata_store.upsert_episode(payload)
            final_id = str(stored.get("episode_id") or payload.get("episode_id") or "")
            if final_id:
                self.metadata_store.bind_episode_paragraphs(
                    final_id,
                    list(payload.get("evidence_ids") or []),
                )
                stored_count += 1

        result["episode_count"] = stored_count
        return {
            "done_hashes": list(result.get("done_hashes") or []),
            "episode_count": stored_count,
            "fallback_count": int(result.get("fallback_count") or 0),
        }

    async def process_pending_rows(self, pending_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        loaded, missing_hashes = self.load_pending_paragraphs(pending_rows)
        groups = self.group_paragraphs(loaded)

        done_hashes: List[str] = list(missing_hashes)
        failed_hashes: Dict[str, str] = {}
        episode_count = 0
        fallback_count = 0

        for group in groups:
            group_hashes = [str(p.get("hash", "") or "").strip() for p in (group.get("paragraphs") or [])]
            try:
                result = await self.process_group(group)
                done_hashes.extend(result.get("done_hashes") or [])
                episode_count += int(result.get("episode_count") or 0)
                fallback_count += int(result.get("fallback_count") or 0)
            except Exception as e:
                err = str(e)[:500]
                for h in group_hashes:
                    if h:
                        failed_hashes[h] = err

        dedup_done = list(dict.fromkeys([h for h in done_hashes if h]))
        return {
            "done_hashes": dedup_done,
            "failed_hashes": failed_hashes,
            "episode_count": episode_count,
            "fallback_count": fallback_count,
            "missing_count": len(missing_hashes),
            "group_count": len(groups),
        }

    async def rebuild_source(self, source: str) -> Dict[str, Any]:
        token = str(source or "").strip()
        if not token:
            return {
                "source": "",
                "episode_count": 0,
                "fallback_count": 0,
                "group_count": 0,
                "paragraph_count": 0,
            }

        paragraphs = self.metadata_store.get_live_paragraphs_by_source(token)
        if not paragraphs:
            replace_result = self.metadata_store.replace_episodes_for_source(token, [])
            return {
                "source": token,
                "episode_count": int(replace_result.get("episode_count") or 0),
                "fallback_count": 0,
                "group_count": 0,
                "paragraph_count": 0,
            }

        groups = self.group_paragraphs(paragraphs)
        payloads: List[Dict[str, Any]] = []
        fallback_count = 0

        for group in groups:
            result = await self._build_episode_payloads_for_group(group)
            payloads.extend(list(result.get("payloads") or []))
            fallback_count += int(result.get("fallback_count") or 0)

        replace_result = self.metadata_store.replace_episodes_for_source(token, payloads)
        return {
            "source": token,
            "episode_count": int(replace_result.get("episode_count") or 0),
            "fallback_count": fallback_count,
            "group_count": len(groups),
            "paragraph_count": len(paragraphs),
        }

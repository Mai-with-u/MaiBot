from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncio

from src.common.logger import get_logger
from src.common.utils.utils_config import BehaviorConfigUtils

from .behavior_pattern_maintenance import behavior_pattern_maintenance
from .behavior_pattern_store import (
    LEARNING_OBSERVED,
    LEARNING_SELF_REFLECTION,
    behavior_pattern_to_dict,
    list_behavior_patterns_for_sessions,
    mark_behavior_pattern_selected,
)
from .behavior_scene_cluster_store import build_profile_tag_mapping, retrieve_behavior_scores_from_scene_clusters
from .behavior_scenario import BehaviorScenarioProfile, behavior_scenario_analyzer

logger = get_logger("behavior_selector")

ScenarioAgentRunner = Callable[[str], Awaitable[str]]
MAX_SELECTOR_CANDIDATES = 12
COLD_SINGLETON_BEHAVIOR_FACTOR = 0.45
PROFILE_TAG_MATCH_BONUS_CAP = 0.25
PROFILE_TAG_MATCH_BONUS_FACTOR = 0.35
PROFILE_TAG_MATCH_KINDS = {"attitude", "need"}


@dataclass
class BehaviorPatternRetrievalResult:
    """planner 侧行为表现召回结果。"""

    reference_text: str = ""
    behaviors: list[dict[str, Any]] = field(default_factory=list)
    scenario_profile: BehaviorScenarioProfile = field(default_factory=BehaviorScenarioProfile)


class BehaviorPatternSelector:
    """根据当前 planner 上下文挑选可选行为表现参考。"""

    @staticmethod
    def _build_compact_scenario_text(scenario_profile: BehaviorScenarioProfile) -> str:
        if not scenario_profile.has_signal:
            return "无可用场景画像。"

        lines = []
        if scenario_profile.summary:
            lines.append(f"场景摘要：{scenario_profile.summary}")
        if scenario_profile.tag_clusters:
            lines.append(f"场景标签：{scenario_profile.tag_cluster_text()}")
        return "\n".join(lines) if lines else "无可用场景画像。"

    def _can_use_behaviors(self, session_id: str) -> bool:
        try:
            use_behavior, _ = BehaviorConfigUtils.get_behavior_config_for_chat(session_id)
            return use_behavior
        except Exception as exc:
            logger.error(f"检查行为表现使用开关失败: {exc}")
            return False

    def _resolve_behavior_group_scope(self, session_id: str) -> tuple[set[str], bool]:
        return BehaviorConfigUtils.resolve_behavior_group_scope(session_id)

    @staticmethod
    def _candidate_weight(candidate: dict[str, Any]) -> float:
        count = max(float(candidate.get("count") or 0.0), 0.0)
        score = float(candidate.get("score") or 0.0)
        success_count = max(float(candidate.get("success_count") or 0.0), 0.0)
        failure_count = max(float(candidate.get("failure_count") or 0.0), 0.0)
        activation_count = max(float(candidate.get("activation_count") or 0.0), 0.0)
        learning_type = str(candidate.get("learning_type") or "").strip()
        self_feedback_bonus = 0.15 if learning_type == "self_reflection" else 0.0
        weight = max(
            0.2,
            1.0
            + count * 0.15
            + score * 0.7
            + success_count * 0.4
            - failure_count * 0.6
            - activation_count * 0.03
            + self_feedback_bonus,
        )
        is_cold_singleton = (
            count <= 1
            and activation_count <= 0
            and success_count <= 0
            and failure_count <= 0
            and abs(score) <= 0.0001
        )
        if is_cold_singleton:
            weight *= COLD_SINGLETON_BEHAVIOR_FACTOR
        return max(0.2, weight)

    @staticmethod
    def _profile_tag_mapping_from_distribution(distribution: Any) -> dict[str, float]:
        if not isinstance(distribution, list):
            return {}
        tag_probs: dict[str, float] = {}
        for item in distribution:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip()
            if ":" not in tag:
                continue
            tag_kind = tag.split(":", 1)[0]
            if tag_kind not in PROFILE_TAG_MATCH_KINDS:
                continue
            try:
                probability = float(item.get("probability") or 0.0)
            except (TypeError, ValueError):
                continue
            if probability > 0:
                tag_probs[tag] = tag_probs.get(tag, 0.0) + probability
        total_probability = sum(tag_probs.values())
        if total_probability <= 0:
            return {}
        return {tag: probability / total_probability for tag, probability in tag_probs.items()}

    @classmethod
    def _profile_tag_match_score(
        cls,
        candidate: dict[str, Any],
        *,
        query_profile_tags: dict[str, float],
    ) -> float:
        if not query_profile_tags:
            return 0.0
        candidate_profile_tags = cls._profile_tag_mapping_from_distribution(candidate.get("profile_tag_distribution"))
        if not candidate_profile_tags:
            return 0.0
        shared_tags = set(query_profile_tags) & set(candidate_profile_tags)
        if not shared_tags:
            return 0.0
        return sum(min(query_profile_tags[tag], candidate_profile_tags[tag]) for tag in shared_tags)

    def _rank_candidates_by_scene_cluster(
        self,
        candidates: list[dict[str, Any]],
        *,
        scene_cluster_scores: dict[int, float],
        scenario_profile: BehaviorScenarioProfile | None = None,
        max_count: int,
    ) -> list[dict[str, Any]]:
        if not scene_cluster_scores:
            return []

        matched_candidates: list[dict[str, Any]] = []
        query_profile_tags = (
            self._profile_tag_mapping_from_distribution(
                [
                    {"tag": tag, "probability": probability}
                    for tag, probability in build_profile_tag_mapping(scenario_profile).items()
                ]
            )
            if scenario_profile is not None and scenario_profile.has_signal
            else {}
        )
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, int):
                continue
            cluster_score = scene_cluster_scores.get(candidate_id)
            if cluster_score is None:
                continue
            candidate = dict(candidate)
            candidate["scene_cluster_score"] = round(cluster_score, 4)
            candidate_weight = self._candidate_weight(candidate)
            profile_tag_match_score = self._profile_tag_match_score(
                candidate,
                query_profile_tags=query_profile_tags,
            )
            profile_tag_bonus = 1.0 + min(
                PROFILE_TAG_MATCH_BONUS_CAP,
                profile_tag_match_score * PROFILE_TAG_MATCH_BONUS_FACTOR,
            )
            candidate["profile_tag_match_score"] = round(profile_tag_match_score, 4)
            candidate["behavior_retrieval_score"] = round(float(cluster_score) * candidate_weight * profile_tag_bonus, 4)
            matched_candidates.append(candidate)

        if not matched_candidates:
            return []

        matched_candidates.sort(
            key=lambda candidate: (
                float(candidate.get("behavior_retrieval_score") or 0.0),
                float(candidate.get("scene_cluster_score") or 0.0),
                float(candidate.get("profile_tag_match_score") or 0.0),
                int(candidate.get("success_count") or 0),
                int(candidate.get("id") or 0),
            ),
            reverse=True,
        )
        return matched_candidates[:max_count]

    def _load_behavior_candidates(
        self,
        session_id: str,
        *,
        scenario_profile: BehaviorScenarioProfile | None = None,
        max_count: int = MAX_SELECTOR_CANDIDATES,
    ) -> list[dict[str, Any]]:
        related_session_ids, has_global_share = self._resolve_behavior_group_scope(session_id)
        behavior_pattern_maintenance.maybe_maintain_session(
            session_id=session_id,
            related_session_ids=related_session_ids,
        )
        patterns = list_behavior_patterns_for_sessions(
            session_ids=related_session_ids,
            include_global=has_global_share,
        )
        candidates: list[dict[str, Any]] = []
        for pattern in patterns:
            if pattern.id is None:
                continue
            candidate = behavior_pattern_to_dict(pattern)
            if not candidate:
                continue
            if not candidate.get("action") or not candidate.get("outcome"):
                continue
            candidates.append(candidate)
        if scenario_profile is not None and scenario_profile.has_signal:
            scene_cluster_scores = retrieve_behavior_scores_from_scene_clusters(
                session_ids=related_session_ids,
                include_global=has_global_share,
                profile=scenario_profile,
            )
            scene_cluster_ranked_candidates = self._rank_candidates_by_scene_cluster(
                candidates,
                scene_cluster_scores=scene_cluster_scores,
                scenario_profile=scenario_profile,
                max_count=max_count,
            )
            if scene_cluster_ranked_candidates:
                return scene_cluster_ranked_candidates

        return []

    @staticmethod
    def _build_group_reference_text(
        *,
        behaviors: list[dict[str, Any]],
        scenario_profile: BehaviorScenarioProfile,
    ) -> str:
        self_reflection_items: list[str] = []
        observed_behavior_items: list[str] = []
        for behavior in behaviors:
            behavior_id = behavior.get("id")
            action = str(behavior.get("action") or "").strip()
            outcome = str(behavior.get("outcome") or "").strip()
            learning_type = str(behavior.get("learning_type") or "").strip()
            if learning_type == LEARNING_SELF_REFLECTION:
                self_reflection_items.append(
                    f"{len(self_reflection_items) + 1}.\n"
                    f"behavior_id：{behavior_id}\n"
                    f"麦麦过去采用的做法：{action}\n"
                    f"当时观察到的结果：{outcome}"
                )
                continue
            if learning_type == LEARNING_OBSERVED:
                observed_behavior_items.append(
                    f"{len(observed_behavior_items) + 1}.\n"
                    f"behavior_id：{behavior_id}\n"
                    f"观察到的互动方式：{action}\n"
                    f"观察到的后续变化：{outcome}"
                )
                continue
            logger.warning(
                "跳过学习类型未知的行为表现参考: "
                f"behavior_id={behavior_id} learning_type={learning_type or '[空]'}"
            )

        scenario_text = BehaviorPatternSelector._build_compact_scenario_text(scenario_profile)
        reference_sections = [
            "以下是根据当前场景召回的过往互动经验。请结合当前上下文、人物关系和可用工具，"
            "自然地吸收其中合适的部分，无需复现原做法或预设相同结果。",
            f"当前场景画像：\n{scenario_text}",
        ]
        if self_reflection_items:
            reference_sections.append(
                "麦麦的过往经验：\n"
                "这些做法由麦麦在相似场景中采用过，可结合当前情况调整或组合。\n"
                f"{chr(10).join(self_reflection_items)}"
            )
        if observed_behavior_items:
            reference_sections.append(
                "其他人的互动经验：\n"
                "这些做法来自他人或群体，可以在适合麦麦当前身份、关系和情境时灵活借鉴。\n"
                f"{chr(10).join(observed_behavior_items)}"
            )
        return "\n\n".join(reference_sections)

    async def retrieve_for_planner(
        self,
        *,
        session_id: str,
        scenario_agent_runner: ScenarioAgentRunner | None = None,
        context_text: str = "",
        include_context_in_prompt: bool = True,
        max_count: int = 3,
    ) -> BehaviorPatternRetrievalResult:
        """基于裁切后的 planner 上下文召回行为表现，不再使用 LLM 做最终选择。"""

        if not session_id:
            return BehaviorPatternRetrievalResult()
        if not self._can_use_behaviors(session_id):
            logger.debug(f"行为表现召回已跳过：当前会话未启用行为使用，session_id={session_id}")
            return BehaviorPatternRetrievalResult()

        scenario_profile = await behavior_scenario_analyzer.analyze(
            context_text=context_text,
            sub_agent_runner=scenario_agent_runner,
            include_context_in_prompt=include_context_in_prompt,
        )
        candidates = await asyncio.to_thread(
            self._load_behavior_candidates,
            session_id,
            scenario_profile=scenario_profile,
            max_count=max(1, min(3, int(max_count))),
        )
        if not candidates:
            logger.debug(f"行为表现召回未命中候选：session_id={session_id}")
            return BehaviorPatternRetrievalResult(scenario_profile=scenario_profile)

        selected_behaviors: list[dict[str, Any]] = []
        for candidate in candidates[: max(1, min(3, int(max_count)))]:
            candidate_id = int(candidate.get("id") or 0)
            marked_pattern = mark_behavior_pattern_selected(candidate_id)
            selected_behavior = (
                behavior_pattern_to_dict(marked_pattern)
                if marked_pattern is not None
                else candidate
            )
            if selected_behavior:
                for score_key in ("scene_cluster_score", "profile_tag_match_score", "behavior_retrieval_score"):
                    if score_key in candidate:
                        selected_behavior[score_key] = candidate[score_key]
                selected_behaviors.append(selected_behavior)

        if not selected_behaviors:
            return BehaviorPatternRetrievalResult(scenario_profile=scenario_profile)

        reference_text = self._build_group_reference_text(
            behaviors=selected_behaviors,
            scenario_profile=scenario_profile,
        )
        logger.debug(
            f"行为表现参考已召回：session_id={session_id} "
            f"ids={[behavior.get('id') for behavior in selected_behaviors]}"
        )
        return BehaviorPatternRetrievalResult(
            reference_text=reference_text,
            behaviors=selected_behaviors,
            scenario_profile=scenario_profile,
        )

behavior_pattern_selector = BehaviorPatternSelector()

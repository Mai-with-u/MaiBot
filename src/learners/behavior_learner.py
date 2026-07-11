from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Sequence

from json_repair import repair_json

import asyncio
import json
import re

from src.chat.utils.utils import is_bot_self
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.services.llm_service import LLMServiceClient

from .behavior_pattern_maintenance import behavior_pattern_maintenance
from .behavior_pattern_store import (
    ACTOR_GROUP_COLLECTIVE,
    ACTOR_MAIBOT_SELF,
    ACTOR_OTHER_USER,
    BehaviorExperienceUpsertItem,
    LEARNING_OBSERVED,
    LEARNING_SELF_REFLECTION,
    apply_behavior_feedback,
    behavior_pattern_to_dict,
    get_behavior_pattern,
    upsert_behavior_patterns_with_results,
)
from .behavior_scenario import BehaviorScenarioProfile, BehaviorScenarioSegment, behavior_scenario_analyzer

if TYPE_CHECKING:
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.context.messages import LLMContextMessage


logger = get_logger("behavior_learner")

behavior_learn_model = LLMServiceClient(task_name="learner", request_type="behavior.learner")
behavior_scene_model = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
behavior_feedback_model = LLMServiceClient(task_name="learner", request_type="behavior.feedback")

BEHAVIOR_REFERENCE_ID_PATTERN = re.compile(r"\bbehavior_id\s*[：:]\s*(\d+)\b", re.IGNORECASE)
FEEDBACK_STATUS_SUCCESS = "success"
FEEDBACK_STATUS_PARTIAL_SUCCESS = "partial_success"
FEEDBACK_STATUS_FAILED = "failed"
FEEDBACK_STATUS_NEUTRAL = "neutral"
ALLOWED_FEEDBACK_STATUSES = {
    FEEDBACK_STATUS_SUCCESS,
    FEEDBACK_STATUS_PARTIAL_SUCCESS,
    FEEDBACK_STATUS_FAILED,
    FEEDBACK_STATUS_NEUTRAL,
}

@dataclass(frozen=True)
class BehaviorCandidate:
    """从聊天历史中抽取出的场景-行为-结果候选。"""

    action: str
    outcome: str
    source_ids: list[str]
    segment_id: str = ""
    actor_type: str = ACTOR_OTHER_USER
    learning_type: str = LEARNING_OBSERVED


@dataclass(frozen=True)
class BehaviorParseDiagnostics:
    """行为学习输出解析诊断信息。"""

    normalized_response: str
    parsed_item_count: int = 0
    accepted_item_count: int = 0
    invalid_item_count: int = 0
    empty_output: bool = False
    missing_scene_start: bool = False
    parse_error: str = ""
    non_list_output: bool = False


@dataclass(frozen=True)
class BehaviorParseResult:
    """行为学习输出解析结果。"""

    candidates: list[BehaviorCandidate]
    diagnostics: BehaviorParseDiagnostics


@dataclass(frozen=True)
class BehaviorFilterResult:
    """行为学习候选过滤结果。"""

    candidates: list[BehaviorCandidate]
    skipped_reasons: dict[str, int]


@dataclass(frozen=True)
class BehaviorReferenceCandidate:
    """裁切上下文中出现过的行为参考路径。"""

    behavior_id: int
    action: str
    outcome: str
    actor_type: str
    learning_type: str
    session_id: str = ""


@dataclass(frozen=True)
class BehaviorFeedbackContextItem:
    """反馈评估时间线中的一项。"""

    item_id: str
    item_type: str
    text: str
    speaker: str = ""
    source: str = ""


@dataclass(frozen=True)
class BehaviorFeedbackContext:
    """行为路径反馈评估所需的裁切上下文。"""

    references: list[BehaviorReferenceCandidate]
    timeline_items: list[BehaviorFeedbackContextItem]


@dataclass(frozen=True)
class BehaviorFeedbackCandidate:
    """模型判断出的行为路径反馈。"""

    behavior_id: int
    adopted: bool
    status: str
    score_delta: float
    reason: str
    outcome: str
    source_ids: list[str]


@dataclass(frozen=True)
class BehaviorLearningAcquireResult:
    """行为学习批次并发闸门的申请结果。"""

    acquired: bool
    reason: str = ""
    active_count: int = 0
    max_count: int = 0


class BehaviorLearningBatchGate:
    """控制行为学习批次的聊天流互斥与全局并发上限。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_session_ids: set[str] = set()

    async def acquire(self, session_id: str) -> BehaviorLearningAcquireResult:
        max_count = int(global_config.expression.max_expression_learner)
        if max_count <= 0:
            return BehaviorLearningAcquireResult(False, "max_expression_learner <= 0", 0, max_count)

        async with self._lock:
            active_count = len(self._active_session_ids)
            if session_id in self._active_session_ids:
                return BehaviorLearningAcquireResult(False, "session_busy", active_count, max_count)
            if active_count >= max_count:
                return BehaviorLearningAcquireResult(False, "global_limit", active_count, max_count)

            self._active_session_ids.add(session_id)
            return BehaviorLearningAcquireResult(True, active_count=active_count + 1, max_count=max_count)

    async def release(self, session_id: str) -> None:
        async with self._lock:
            self._active_session_ids.discard(session_id)


behavior_learning_batch_gate = BehaviorLearningBatchGate()


def _strip_json_code_fence(raw_response: str) -> str:
    normalized_response = raw_response.strip()
    if not normalized_response.startswith("```"):
        return normalized_response

    lines = normalized_response.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return normalized_response


def _coerce_source_ids(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        raw_items = raw_value
    elif raw_value is None:
        raw_items = []
    else:
        raw_items = [raw_value]

    source_ids: list[str] = []
    for raw_item in raw_items:
        if isinstance(raw_item, str) and "," in raw_item:
            split_items = raw_item.split(",")
        else:
            split_items = [raw_item]
        for split_item in split_items:
            source_id = str(split_item or "").strip()
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
    return source_ids


def _coerce_actor_type(raw_value: Any) -> str:
    normalized_value = str(raw_value or "").strip().lower()
    if normalized_value in {ACTOR_OTHER_USER, ACTOR_GROUP_COLLECTIVE, ACTOR_MAIBOT_SELF, "unknown"}:
        return normalized_value
    return ""


def _coerce_learning_type(raw_value: Any) -> str:
    normalized_value = str(raw_value or "").strip().lower()
    if normalized_value in {LEARNING_OBSERVED, LEARNING_SELF_REFLECTION}:
        return normalized_value
    return ""


def _coerce_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value != 0
    normalized_value = str(raw_value or "").strip().lower()
    return normalized_value in {"1", "true", "yes", "y", "adopted", "used"}


def _coerce_feedback_status(raw_value: Any) -> str:
    normalized_value = str(raw_value or "").strip().lower()
    if normalized_value in ALLOWED_FEEDBACK_STATUSES:
        return normalized_value
    if normalized_value in {"succeeded", "completed", "positive"}:
        return FEEDBACK_STATUS_SUCCESS
    if normalized_value in {"partial", "partially_successful", "weak_success"}:
        return FEEDBACK_STATUS_PARTIAL_SUCCESS
    if normalized_value in {"blocked", "abandoned", "negative", "failure"}:
        return FEEDBACK_STATUS_FAILED
    return FEEDBACK_STATUS_NEUTRAL


def _coerce_score_delta(raw_value: Any, *, status: str) -> float:
    try:
        score_delta = float(raw_value)
    except (TypeError, ValueError):
        if status == FEEDBACK_STATUS_SUCCESS:
            score_delta = 0.6
        elif status == FEEDBACK_STATUS_PARTIAL_SUCCESS:
            score_delta = 0.25
        elif status == FEEDBACK_STATUS_FAILED:
            score_delta = -0.6
        else:
            score_delta = 0.0
    if status == FEEDBACK_STATUS_SUCCESS:
        return max(0.1, min(1.0, abs(score_delta)))
    if status == FEEDBACK_STATUS_PARTIAL_SUCCESS:
        return max(0.05, min(0.35, abs(score_delta)))
    if status == FEEDBACK_STATUS_FAILED:
        return -max(0.1, min(1.0, abs(score_delta)))
    return 0.0


def _compact_log_text(text: str, *, max_length: int = 1200) -> str:
    """压缩长文本到适合日志展示的长度。"""

    compacted_text = " ".join((text or "").split()).strip()
    if len(compacted_text) <= max_length:
        return compacted_text
    return compacted_text[:max_length].rstrip() + "..."


def _parse_behavior_item(
    raw_item: Any,
) -> Optional[BehaviorCandidate]:
    if not isinstance(raw_item, dict):
        return None

    action = str(raw_item.get("action") or "").strip()
    outcome = str(raw_item.get("outcome") or "").strip()
    source_ids = _coerce_source_ids(raw_item.get("source_ids"))
    segment_id = str(raw_item.get("segment_id") or raw_item.get("scene_id") or "").strip()
    actor_type = _coerce_actor_type(raw_item.get("actor_type"))
    learning_type = _coerce_learning_type(raw_item.get("learning_type"))
    if not action or not outcome or not actor_type or not learning_type:
        return None
    if actor_type == ACTOR_MAIBOT_SELF and learning_type != LEARNING_SELF_REFLECTION:
        return None
    if actor_type != ACTOR_MAIBOT_SELF and learning_type != LEARNING_OBSERVED:
        return None
    return BehaviorCandidate(
        action=action,
        outcome=outcome,
        source_ids=source_ids,
        segment_id=segment_id,
        actor_type=actor_type,
        learning_type=learning_type,
    )


def parse_behavior_response_with_diagnostics(
    response: str,
    *,
    scene_start: str,
) -> BehaviorParseResult:
    """解析行为学习模型返回的 JSON，并保留诊断信息供日志输出。"""

    normalized_response = _strip_json_code_fence(response or "")
    normalized_scene_start = scene_start.strip()
    if not normalized_response:
        return BehaviorParseResult(
            candidates=[],
            diagnostics=BehaviorParseDiagnostics(normalized_response="", empty_output=True),
        )
    if not normalized_scene_start:
        return BehaviorParseResult(
            candidates=[],
            diagnostics=BehaviorParseDiagnostics(
                normalized_response=normalized_response,
                missing_scene_start=True,
            ),
        )

    try:
        parsed_response = json.loads(repair_json(normalized_response))
    except Exception as exc:
        return BehaviorParseResult(
            candidates=[],
            diagnostics=BehaviorParseDiagnostics(
                normalized_response=normalized_response,
                parse_error=str(exc),
            ),
        )

    if not isinstance(parsed_response, list):
        return BehaviorParseResult(
            candidates=[],
            diagnostics=BehaviorParseDiagnostics(
                normalized_response=normalized_response,
                non_list_output=True,
            ),
        )

    candidates: list[BehaviorCandidate] = []
    invalid_item_count = 0
    for raw_item in parsed_response:
        candidate = _parse_behavior_item(raw_item)
        if candidate is not None:
            candidates.append(candidate)
        else:
            invalid_item_count += 1

    return BehaviorParseResult(
        candidates=candidates,
        diagnostics=BehaviorParseDiagnostics(
            normalized_response=normalized_response,
            parsed_item_count=len(parsed_response),
            accepted_item_count=len(candidates),
            invalid_item_count=invalid_item_count,
        ),
    )


def parse_behavior_response(response: str, *, scene_start: str) -> list[BehaviorCandidate]:
    """解析行为学习模型返回的 JSON。"""

    parse_result = parse_behavior_response_with_diagnostics(response, scene_start=scene_start)
    if parse_result.diagnostics.parse_error:
        logger.warning(f"行为学习结果解析失败: {parse_result.diagnostics.normalized_response!r}")
    return parse_result.candidates


def parse_behavior_feedback_response(response: str) -> list[BehaviorFeedbackCandidate]:
    """解析行为路径反馈模型返回的 JSON。"""

    normalized_response = _strip_json_code_fence(response or "")
    if not normalized_response:
        return []

    try:
        parsed_response = json.loads(repair_json(normalized_response))
    except Exception:
        logger.warning(f"行为路径反馈结果解析失败: {normalized_response!r}")
        return []

    if isinstance(parsed_response, dict):
        raw_items = parsed_response.get("feedback") or parsed_response.get("items") or []
    else:
        raw_items = parsed_response

    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []

    feedback_items: list[BehaviorFeedbackCandidate] = []
    used_behavior_ids: set[int] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        try:
            behavior_id = int(raw_item.get("behavior_id") or raw_item.get("id") or 0)
        except (TypeError, ValueError):
            behavior_id = 0
        if behavior_id <= 0 or behavior_id in used_behavior_ids:
            continue

        adopted = _coerce_bool(raw_item.get("adopted"))
        status = _coerce_feedback_status(raw_item.get("status"))
        score_delta = _coerce_score_delta(raw_item.get("score_delta"), status=status)
        reason = str(raw_item.get("reason") or "").strip()
        outcome = str(raw_item.get("outcome") or "").strip()
        source_ids = _coerce_source_ids(raw_item.get("source_ids"))
        if (
            not adopted
            or status == FEEDBACK_STATUS_NEUTRAL
            or abs(score_delta) <= 0.0001
            or not reason
            or not source_ids
        ):
            continue

        used_behavior_ids.add(behavior_id)
        feedback_items.append(
            BehaviorFeedbackCandidate(
                behavior_id=behavior_id,
                adopted=adopted,
                status=status,
                score_delta=score_delta,
                reason=reason,
                outcome=outcome,
                source_ids=source_ids,
            )
        )
    return feedback_items


def _validate_behavior_feedback_evidence(
    feedback_item: BehaviorFeedbackCandidate,
    feedback_context: BehaviorFeedbackContext,
) -> tuple[bool, str, list[str]]:
    item_by_id = {
        item.item_id: item
        for item in feedback_context.timeline_items
        if item.item_type == "chat_message" and item.item_id
    }
    valid_source_ids: list[str] = []
    cited_items: list[BehaviorFeedbackContextItem] = []
    for source_id in feedback_item.source_ids:
        item = item_by_id.get(source_id)
        if item is None:
            continue
        valid_source_ids.append(source_id)
        cited_items.append(item)
    if not valid_source_ids:
        return False, "invalid_source_ids", []

    has_self_adoption_evidence = any(item.speaker == "SELF" for item in cited_items)
    if not has_self_adoption_evidence:
        return False, "missing_self_adoption_evidence", valid_source_ids

    return True, "", valid_source_ids


class BehaviorLearner:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.min_messages_for_extraction = 10

    async def learn_from_context_messages(
        self,
        context_messages: Sequence["LLMContextMessage"],
    ) -> bool:
        """从 Maisaka 被裁切的上下文消息中学习行为表现模式。"""

        source_messages = self._extract_session_messages_from_context(context_messages)
        feedback_context = self._extract_behavior_feedback_context(context_messages)
        learner_tasks: list[asyncio.Task[bool]] = []

        if source_messages and len(source_messages) >= self.min_messages_for_extraction:
            learner_tasks.append(asyncio.create_task(self._learn_from_session_messages(source_messages)))
        elif not source_messages:
            logger.debug("裁切历史中没有可用于行为学习的真实聊天消息")
        else:
            logger.debug(
                f"裁切历史可学习行为消息不足: 可学习={len(source_messages)} 阈值={self.min_messages_for_extraction}"
            )

        if feedback_context is not None:
            learner_tasks.append(asyncio.create_task(self._evaluate_behavior_feedback(feedback_context)))
        else:
            logger.debug("裁切历史中没有可用于行为反馈评估的行为路径参考，跳过反馈阶段")

        if not learner_tasks:
            return False

        results = await asyncio.gather(*learner_tasks)
        return any(results)

    @staticmethod
    def _extract_session_messages_from_context(
        context_messages: Sequence["LLMContextMessage"],
    ) -> list["SessionMessage"]:
        """从上下文消息中过滤出真实聊天消息。"""

        from src.maisaka.context.messages import SessionBackedMessage

        source_messages: list["SessionMessage"] = []
        seen_message_ids: set[str] = set()
        seen_object_ids: set[int] = set()

        for context_message in context_messages:
            if not isinstance(context_message, SessionBackedMessage):
                continue
            if context_message.source_kind not in {"user", "guided_reply", "outbound_send"}:
                continue

            original_message = context_message.original_message
            if original_message is None:
                continue

            message_id = str(original_message.message_id or "").strip()
            if message_id:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
            else:
                object_id = id(original_message)
                if object_id in seen_object_ids:
                    continue
                seen_object_ids.add(object_id)

            source_messages.append(original_message)

        return source_messages

    def _extract_behavior_feedback_context(
        self,
        context_messages: Sequence["LLMContextMessage"],
    ) -> Optional[BehaviorFeedbackContext]:
        """从裁切上下文中提取行为路径参考与后续真实聊天，用于反馈评估。"""

        from src.maisaka.context.messages import ReferenceMessage, SessionBackedMessage

        reference_ids: list[int] = []
        timeline_items: list[BehaviorFeedbackContextItem] = []
        chat_item_index = 0
        reference_item_index = 0

        for context_message in context_messages:
            if isinstance(context_message, ReferenceMessage) and context_message.source == "behavior_pattern":
                reference_item_index += 1
                reference_text = str(context_message.content or context_message.processed_plain_text or "").strip()
                matched_ids = [int(match.group(1)) for match in BEHAVIOR_REFERENCE_ID_PATTERN.finditer(reference_text)]
                for behavior_id in matched_ids:
                    if behavior_id not in reference_ids:
                        reference_ids.append(behavior_id)
                timeline_items.append(
                    BehaviorFeedbackContextItem(
                        item_id=f"ref{reference_item_index}",
                        item_type="behavior_reference",
                        text=reference_text,
                        source=context_message.source,
                    )
                )
                continue

            if not isinstance(context_message, SessionBackedMessage):
                continue
            if context_message.source_kind not in {"user", "guided_reply", "outbound_send"}:
                continue
            original_message = context_message.original_message
            if original_message is None:
                continue

            chat_item_index += 1
            user_info = original_message.message_info.user_info
            speaker_kind = "SELF" if is_bot_self(original_message.platform, user_info.user_id) else "USER"
            timeline_items.append(
                BehaviorFeedbackContextItem(
                    item_id=f"m{chat_item_index}",
                    item_type="chat_message",
                    text=str(context_message.processed_plain_text or "").strip() or "[空消息]",
                    speaker=speaker_kind,
                    source=context_message.source_kind,
                )
            )

        if not reference_ids:
            return None

        references: list[BehaviorReferenceCandidate] = []
        for behavior_id in reference_ids:
            path = get_behavior_pattern(behavior_id)
            if path is None:
                logger.info(f"行为反馈评估跳过不存在的行为路径: behavior_id={behavior_id}")
                continue
            payload = behavior_pattern_to_dict(path)
            if not payload:
                continue
            references.append(
                BehaviorReferenceCandidate(
                    behavior_id=behavior_id,
                    action=str(payload.get("action") or "").strip(),
                    outcome=str(payload.get("outcome") or "").strip(),
                    actor_type=str(payload.get("actor_type") or "").strip(),
                    learning_type=str(payload.get("learning_type") or "").strip(),
                    session_id=str(payload.get("session_id") or "").strip(),
                )
            )

        if not references:
            return None
        chat_items = [item for item in timeline_items if item.item_type == "chat_message"]
        if not chat_items:
            logger.debug("行为反馈评估已跳过：裁切上下文有行为参考但没有后续真实聊天")
            return None
        return BehaviorFeedbackContext(
            references=references,
            timeline_items=timeline_items,
        )

    @staticmethod
    def _format_feedback_references_for_system_prompt(references: Sequence[BehaviorReferenceCandidate]) -> str:
        """把选择的行为参考路径格式化成 system prompt 中的可读文本。"""

        formatted_references: list[str] = []
        for index, reference in enumerate(references, start=1):
            formatted_references.append(
                "\n".join(
                    [
                        f"路径 {index}",
                        f"- behavior_id: {reference.behavior_id}",
                        f"- actor_type: {reference.actor_type or 'unknown'}",
                        f"- learning_type: {reference.learning_type or 'unknown'}",
                        f"- session_id: {reference.session_id or 'unknown'}",
                        f"- 采用行为: {reference.action or '[空]'}",
                        f"- 预期结果: {reference.outcome or '[空]'}",
                    ]
                )
            )
        return "\n\n".join(formatted_references)

    @staticmethod
    def _format_feedback_timeline_message(item: BehaviorFeedbackContextItem) -> str:
        """把裁切上下文时间线中的单项格式化成独立消息。"""

        return "\n".join(
            [
                "[timeline_item]",
                f"[item_id:{item.item_id}]",
                f"[type:{item.item_type}]",
                f"[speaker:{item.speaker or 'unknown'}]",
                f"[source:{item.source or 'unknown'}]",
                "[content]",
                _compact_log_text(item.text, max_length=900) or "[空]",
            ]
        )

    def _build_behavior_feedback_messages(self, feedback_context: BehaviorFeedbackContext) -> list[Message]:
        """构造行为路径反馈使用的多 message 请求。"""

        prompt = load_prompt(
            "evaluate_behavior_feedback",
            bot_name=global_config.bot.nickname,
            behavior_references=self._format_feedback_references_for_system_prompt(feedback_context.references),
        )
        feedback_messages = [
            MessageBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{prompt}\n\n"
                "注意：候选行为路径已经在本 system prompt 中列出。"
                "后续聊天时间线会在后续多条 user message 中给出；每条时间线消息包含 item_id，"
                "source_ids 必须引用时间线消息中的 item_id。"
            )
            .build(),
            MessageBuilder().set_role(RoleType.User).add_text_content("以下是后续聊天时间线。").build()
        ]
        for item in feedback_context.timeline_items:
            feedback_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(self._format_feedback_timeline_message(item))
                .build()
            )

        feedback_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content("请根据以上行为参考和后续聊天时间线输出反馈 JSON。")
            .build()
        )
        return feedback_messages

    async def _evaluate_behavior_feedback(self, feedback_context: BehaviorFeedbackContext) -> bool:
        """评估裁切上下文中历史行为路径参考的采用与结果，并写回反馈。"""

        reference_by_id = {reference.behavior_id: reference for reference in feedback_context.references}
        feedback_messages = self._build_behavior_feedback_messages(feedback_context)

        try:
            generation_result = await behavior_feedback_model.generate_response_with_messages(
                lambda _client: feedback_messages,
                options=LLMGenerationOptions(temperature=0.15),
                session_id=self.session_id,
            )
            response = generation_result.response or ""
            self._log_behavior_feedback_preview(
                feedback_messages,
                reference_count=len(feedback_context.references),
                timeline_count=len(feedback_context.timeline_items),
                output_content=response,
            )
        except Exception as exc:
            logger.error(f"行为路径反馈评估失败: {exc}")
            return False

        feedback_items = parse_behavior_feedback_response(response)
        if not feedback_items:
            logger.debug("行为路径反馈评估未产生可写入反馈")
            return False

        wrote_count = 0
        skipped_reasons: Counter[str] = Counter()
        for feedback_item in feedback_items:
            reference = reference_by_id.get(feedback_item.behavior_id)
            if reference is None:
                skipped_reasons["unknown_behavior_id"] += 1
                continue
            evidence_valid, invalid_reason, valid_source_ids = _validate_behavior_feedback_evidence(
                feedback_item,
                feedback_context,
            )
            if not evidence_valid:
                skipped_reasons[invalid_reason] += 1
                continue

            feedback_path = apply_behavior_feedback(
                pattern_id=feedback_item.behavior_id,
                score_delta=feedback_item.score_delta,
                status=feedback_item.status,
                reason=feedback_item.reason,
                outcome=feedback_item.outcome,
                session_id=reference.session_id or self.session_id,
                source_ids=valid_source_ids,
            )
            if feedback_path is None:
                skipped_reasons["write_failed"] += 1
                continue
            wrote_count += 1
            logger.info(
                f"行为路径反馈已写入: behavior_id={feedback_item.behavior_id} "
                f"status={feedback_item.status} score_delta={feedback_item.score_delta} "
                f"source_ids={valid_source_ids}"
            )

        logger.info(
            f"行为路径反馈写入概览: 候选={len(feedback_items)} 成功={wrote_count} 跳过原因={dict(skipped_reasons)}"
        )
        return wrote_count > 0

    async def _learn_from_session_messages(self, pending_messages: list["SessionMessage"]) -> bool:
        learning_session_id = self._resolve_learning_session_id(pending_messages)
        if learning_session_id is None:
            logger.warning("行为学习已跳过：无法解析到有效聊天流")
            return False
        session_display_name = self._get_session_display_name(learning_session_id)
        log_prefix = f"[{session_display_name}]"
        if learning_session_id != self.session_id:
            logger.info(
                f"{log_prefix} 行为学习会话已按真实消息修正到当前聊天流"
            )

        acquire_result = await behavior_learning_batch_gate.acquire(learning_session_id)
        if not acquire_result.acquired:
            if acquire_result.reason == "session_busy":
                logger.info(f"{log_prefix} 已有行为学习批次正在运行，放弃新的批次")
            elif acquire_result.reason == "global_limit":
                logger.info(
                    f"{log_prefix} 行为学习全局并发已满，放弃新的批次: "
                    f"active={acquire_result.active_count}, max={acquire_result.max_count}, "
                    f"chat={session_display_name}"
                )
            else:
                logger.warning(
                    f"{log_prefix} 行为学习并发配置无效，放弃新的批次: "
                    f"max_expression_learner={acquire_result.max_count}, chat={session_display_name}"
                )
            return False

        try:
            return await self._run_learning_batch(
                pending_messages,
                learning_session_id=learning_session_id,
                session_display_name=session_display_name,
            )
        finally:
            await behavior_learning_batch_gate.release(learning_session_id)

    @staticmethod
    def _get_session_display_name(session_id: str) -> str:
        """获取聊天流展示名称，无法解析时回退到 session_id。"""

        from src.chat.message_receive.chat_manager import chat_manager

        session_name = chat_manager.get_session_name(session_id)
        if session_name:
            return session_name

        chat_manager.get_existing_session_by_session_id(session_id)
        return chat_manager.get_session_name(session_id) or session_id

    def _resolve_learning_session_id(self, messages: list["SessionMessage"]) -> Optional[str]:
        """根据真实消息解析本轮行为学习应该归属的会话 ID。"""

        from src.chat.message_receive.chat_manager import chat_manager

        candidates = [
            str(getattr(message, "session_id", "") or "").strip()
            for message in messages
            if str(getattr(message, "session_id", "") or "").strip()
        ]

        def session_exists(session_id: str) -> bool:
            if not session_id:
                return False
            return chat_manager.get_existing_session_by_session_id(session_id) is not None

        for session_id, _ in Counter(candidates).most_common():
            if session_exists(session_id):
                return session_id

        if session_exists(self.session_id):
            return self.session_id

        logger.warning(
            "行为学习无法从真实消息中找到已注册聊天流，也无法确认学习归属: "
            f"候选聊天流数量={len(set(candidates))}"
        )
        return None

    async def _run_learning_batch(
        self,
        pending_messages: list["SessionMessage"],
        *,
        learning_session_id: str,
        session_display_name: str,
    ) -> bool:
        """执行已经获得并发闸门的行为学习批次。"""

        log_prefix = f"[{session_display_name}]"
        scene_segments = await self._analyze_learning_scene_segments(
            pending_messages,
            learning_session_id=learning_session_id,
            session_display_name=session_display_name,
        )
        if not scene_segments:
            logger.debug(f"{log_prefix} 行为学习未形成可用场景片段，跳过本批次")
            return False

        scene_start_by_segment_id = {
            segment.segment_id: segment.profile.tag_cluster_text()
            for segment in scene_segments
            if segment.profile.tag_cluster_text()
        }
        primary_segment = scene_segments[0]
        scene_start = scene_start_by_segment_id.get(primary_segment.segment_id, "")
        if not scene_start:
            logger.debug(f"{log_prefix} 行为学习未形成可用 tag 场景，跳过本批次")
            return False

        prompt = load_prompt(
            "learn_behavior",
            bot_name=global_config.bot.nickname,
            chat_str="聊天记录将在后续多条 user message 中给出；请以每条消息中的 source_id 作为来源行编号。",
            scene_profile=self._format_scene_segments_for_prompt(scene_segments),
        )

        try:
            learning_messages = await self._build_multi_learning_messages(pending_messages, prompt)
            generation_result = await behavior_learn_model.generate_response_with_messages(
                lambda _client: learning_messages,
                options=LLMGenerationOptions(temperature=0.25),
                session_id=learning_session_id,
            )
            response = generation_result.response or ""
            self._log_learning_context_preview(
                learning_messages,
                session_id=learning_session_id,
                session_display_name=session_display_name,
                source_message_count=len(pending_messages),
                output_content=response,
            )
        except Exception as exc:
            logger.error(f"{log_prefix} 学习行为表现失败: {exc}")
            return False

        parse_result = parse_behavior_response_with_diagnostics(
            response,
            scene_start=scene_start,
        )
        self._log_parse_diagnostics(
            session_display_name=session_display_name,
            parse_result=parse_result,
        )

        filter_result = self._filter_behavior_candidates(parse_result.candidates, pending_messages)
        behavior_candidates = filter_result.candidates
        logger.info(
            f"{log_prefix} 行为学习过滤概览: "
            f"解析候选={len(parse_result.candidates)} "
            f"有效候选={len(behavior_candidates)} "
            f"跳过原因={filter_result.skipped_reasons}"
        )
        if not behavior_candidates:
            logger.info(
                f"{log_prefix} 行为学习未抽取到有效候选: "
                f"模型输出预览={_compact_log_text(parse_result.diagnostics.normalized_response, max_length=1600)!r}"
            )
            return False

        wrote_pattern = False
        write_success_count = 0
        write_skipped_count = 0
        write_failed_count = 0
        write_plans: list[tuple[BehaviorCandidate, BehaviorScenarioSegment, str]] = []
        for candidate in behavior_candidates[:12]:
            matched_segment = self._select_segment_for_candidate(candidate, scene_segments)
            candidate_scene_start = scene_start_by_segment_id.get(matched_segment.segment_id, scene_start)
            logger.info(
                f"{log_prefix} 准备写入行为经验路径: "
                f"segment_id={matched_segment.segment_id} action={candidate.action} "
                f"outcome={candidate.outcome} actor_type={candidate.actor_type} "
                f"learning_type={candidate.learning_type} source_ids={candidate.source_ids}"
            )
            write_plans.append((candidate, matched_segment, candidate_scene_start))

        write_results = upsert_behavior_patterns_with_results(
            session_id=learning_session_id,
            items=[
                BehaviorExperienceUpsertItem(
                    action=candidate.action,
                    outcome=candidate.outcome,
                    source_ids=candidate.source_ids,
                    scenario_profile=matched_segment.profile,
                    scene_start=candidate_scene_start,
                    actor_type=candidate.actor_type,
                    learning_type=candidate.learning_type,
                )
                for candidate, matched_segment, candidate_scene_start in write_plans
            ],
        )
        for (candidate, matched_segment, candidate_scene_start), write_result in zip(
            write_plans,
            write_results,
            strict=False,
        ):
            if write_result.path is None:
                skipped_reason = write_result.skipped_reason or "未知原因"
                if write_result.skipped_reason:
                    write_skipped_count += 1
                    logger.info(
                        f"{log_prefix} 行为经验路径跳过写入: "
                        f"原因={skipped_reason} "
                        f"segment_id={matched_segment.segment_id} action={candidate.action} "
                        f"outcome={candidate.outcome} actor_type={candidate.actor_type} "
                        f"learning_type={candidate.learning_type} source_ids={candidate.source_ids}"
                    )
                else:
                    write_failed_count += 1
                    logger.warning(
                        f"{log_prefix} 行为经验路径写入未成功: "
                        f"原因={skipped_reason} "
                        f"segment_id={matched_segment.segment_id} action={candidate.action} "
                        f"outcome={candidate.outcome} actor_type={candidate.actor_type} "
                        f"learning_type={candidate.learning_type} source_ids={candidate.source_ids}"
                    )
                continue
            wrote_pattern = True
            write_success_count += 1
            path = write_result.path
            logger.info(
                f"{log_prefix} 学习到行为经验路径 [ID: {path.id}]: "
                f"场景片段={matched_segment.segment_id} 场景={candidate_scene_start} "
                f"主体={candidate.actor_type} 类型={candidate.learning_type} "
                f"行为={candidate.action} 结果={candidate.outcome}"
            )

        if len(write_results) < len(write_plans):
            for candidate, matched_segment, _candidate_scene_start in write_plans[len(write_results) :]:
                write_failed_count += 1
                logger.warning(
                    f"{log_prefix} 行为经验路径写入未返回结果: "
                    f"segment_id={matched_segment.segment_id} action={candidate.action} "
                    f"outcome={candidate.outcome} actor_type={candidate.actor_type} "
                    f"learning_type={candidate.learning_type} source_ids={candidate.source_ids}"
                )

        logger.info(
            f"{log_prefix} 行为学习写入概览: "
            f"有效候选={len(behavior_candidates)} "
            f"尝试写入={min(len(behavior_candidates), 12)} "
            f"成功={write_success_count} "
            f"跳过={write_skipped_count} "
            f"失败={write_failed_count}"
        )

        if wrote_pattern:
            maintenance_result = behavior_pattern_maintenance.maybe_maintain_session(
                session_id=learning_session_id,
            )
            if maintenance_result.changed:
                logger.info(
                    f"{log_prefix} 行为表现已完成学习后维护: "
                    f"衰减={maintenance_result.decayed_count} "
                    f"禁用={maintenance_result.disabled_count} "
                    f"合并={maintenance_result.merged_count}"
                )

        return wrote_pattern

    def _log_parse_diagnostics(
        self,
        *,
        session_display_name: str,
        parse_result: BehaviorParseResult,
    ) -> None:
        """输出行为学习结果解析阶段的详细诊断日志。"""

        diagnostics = parse_result.diagnostics
        log_prefix = f"[{session_display_name}]"
        response_preview = _compact_log_text(diagnostics.normalized_response, max_length=1600)
        logger.info(
            f"{log_prefix} 行为学习解析概览: "
            f"输出长度={len(diagnostics.normalized_response)} "
            f"数组项={diagnostics.parsed_item_count} "
            f"解析候选={diagnostics.accepted_item_count} "
            f"无效项={diagnostics.invalid_item_count} "
            f"空输出={diagnostics.empty_output} "
            f"缺少场景={diagnostics.missing_scene_start} "
            f"非数组={diagnostics.non_list_output} "
            f"解析错误={diagnostics.parse_error or '无'} "
            f"输出预览={response_preview!r}"
        )
        for index, candidate in enumerate(parse_result.candidates[:12], start=1):
            learning_label = self._format_learning_type_label(candidate.learning_type)
            logger.info(
                f"{log_prefix} 行为[{index}] | 场景: {candidate.segment_id or 'auto'} "
                f"| source_ids={candidate.source_ids} | {learning_label}\n"
                f"动作: {candidate.action}\n"
                f"结果: {candidate.outcome}"
            )

    @staticmethod
    def _format_learning_type_label(learning_type: str) -> str:
        """把行为学习类型转换成适合日志阅读的中文标签。"""

        if learning_type == LEARNING_OBSERVED:
            return "观察学习"
        if learning_type == LEARNING_SELF_REFLECTION:
            return "自我复盘"
        return learning_type or "未知类型"

    @staticmethod
    def _format_scene_segments_for_prompt(segments: Sequence[BehaviorScenarioSegment]) -> str:
        """把场景片段压成学习 prompt 中稳定可引用的 JSON。"""

        return json.dumps(
            {"segments": [segment.to_prompt_payload() for segment in segments]},
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _select_segment_for_candidate(
        candidate: BehaviorCandidate,
        segments: Sequence[BehaviorScenarioSegment],
    ) -> BehaviorScenarioSegment:
        """根据候选的 segment_id 或 source_ids 选择最贴近的场景片段。"""

        if not segments:
            return BehaviorScenarioSegment(segment_id="s1", title="主场景", profile=BehaviorScenarioProfile())

        if candidate.segment_id:
            for segment in segments:
                if segment.segment_id == candidate.segment_id:
                    return segment

        candidate_source_ids = set(candidate.source_ids)
        best_segment = segments[0]
        best_overlap = -1
        for segment in segments:
            segment_source_ids = set(segment.source_ids)
            overlap = len(candidate_source_ids & segment_source_ids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_segment = segment
        return best_segment

    async def _analyze_learning_scene(
        self,
        messages: list["SessionMessage"],
        *,
        learning_session_id: str,
        session_display_name: str,
    ) -> BehaviorScenarioProfile:
        """在行为学习前，用同一套场景画像语言确定本批次的 start。"""

        context_text = await self._build_learning_context_text(messages)
        if not context_text:
            return BehaviorScenarioProfile()

        async def run_scene_prompt(prompt: str) -> str:
            scene_messages = await self._build_scene_analysis_messages(messages, prompt)
            generation_result = await behavior_scene_model.generate_response_with_messages(
                lambda _client: scene_messages,
                options=LLMGenerationOptions(temperature=0.2),
                session_id=learning_session_id,
            )
            response = generation_result.response or ""
            self._log_learning_scene_preview(
                prompt,
                session_id=learning_session_id,
                session_display_name=session_display_name,
                source_message_count=len(messages),
                request_messages=scene_messages,
                output_content=response,
            )
            return response

        return await behavior_scenario_analyzer.analyze(
            context_text=context_text,
            sub_agent_runner=run_scene_prompt,
        )

    async def _analyze_learning_scene_segments(
        self,
        messages: list["SessionMessage"],
        *,
        learning_session_id: str,
        session_display_name: str,
    ) -> list[BehaviorScenarioSegment]:
        """在行为学习前，将同一学习窗口拆成 1~3 个可独立学习的场景片段。"""

        context_text = await self._build_learning_context_text(messages)
        if not context_text:
            return []

        async def run_scene_prompt(prompt: str) -> str:
            scene_messages = await self._build_scene_analysis_messages(messages, prompt)
            generation_result = await behavior_scene_model.generate_response_with_messages(
                lambda _client: scene_messages,
                options=LLMGenerationOptions(temperature=0.2),
                session_id=learning_session_id,
            )
            response = generation_result.response or ""
            self._log_learning_scene_preview(
                prompt,
                session_id=learning_session_id,
                session_display_name=session_display_name,
                source_message_count=len(messages),
                request_messages=scene_messages,
                output_content=response,
            )
            return response

        segments = await behavior_scenario_analyzer.analyze_segments(
            context_text=context_text,
            sub_agent_runner=run_scene_prompt,
        )
        if segments:
            log_prefix = f"[{session_display_name}]"
            logger.info(
                f"{log_prefix} 行为学习场景片段分析完成: "
                f"片段数={len(segments)} "
                f"片段={[{'id': segment.segment_id, 'sources': segment.source_ids, 'title': segment.title} for segment in segments]}"
            )
            return segments

        profile = await behavior_scenario_analyzer.analyze(
            context_text=context_text,
            sub_agent_runner=run_scene_prompt,
        )
        if not profile.has_signal:
            return []
        return [
            BehaviorScenarioSegment(
                segment_id="s1",
                title=profile.summary or "主场景",
                source_ids=[str(index) for index in range(1, len(messages) + 1)],
                profile=profile,
            )
        ]

    async def _build_learning_context_text(self, messages: list["SessionMessage"]) -> str:
        """构建场景分析用的紧凑学习窗口文本。"""

        context_lines: list[str] = []
        for index, message in enumerate(messages, start=1):
            await message.process()
            user_info = message.message_info.user_info
            speaker_kind = "SELF" if is_bot_self(message.platform, user_info.user_id) else "USER"
            content = " ".join((message.processed_plain_text or "").split()).strip()
            if not content:
                content = "[空消息]"
            if len(content) > 300:
                content = content[:300].rstrip() + "..."
            context_lines.append(
                "\n".join(
                    [
                        f"[source_id:{index}]",
                        f"[speaker:{speaker_kind}]",
                        f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                        "[content]",
                        content,
                    ]
                )
            )
        return "\n\n".join(context_lines).strip()

    async def _build_scene_analysis_messages(
        self,
        messages: list["SessionMessage"],
        system_prompt: str,
    ) -> list[Message]:
        """构造场景概括请求：规则在 system，真实聊天作为后续 user 消息。"""

        scene_messages = [
            MessageBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天记录会在后续多条 user message 中给出。每条消息内的 source_id "
                "是本轮场景概括的来源编号；speaker=SELF 表示这条真实聊天消息由麦麦发出。"
            )
            .build()
        ]

        for index, message in enumerate(messages, start=1):
            await message.process()
            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"
            speaker_kind = "SELF" if is_bot_self(message.platform, user_info.user_id) else "USER"
            content = (message.processed_plain_text or "").strip()
            if not content:
                content = "[空消息]"
            scene_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(
                    "\n".join(
                        [
                            f"[source_id:{index}]",
                            f"[speaker:{speaker_kind}]",
                            f"[name:{speaker_name}]",
                            f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                            "[content]",
                            content,
                        ]
                    )
                )
                .build()
            )

        scene_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content("请根据以上真实聊天消息输出场景片段 JSON。")
            .build()
        )
        return scene_messages

    async def _build_multi_learning_messages(
        self,
        messages: list["SessionMessage"],
        system_prompt: str,
    ) -> list[Message]:
        """构造行为学习使用的多 message 请求。"""

        learning_messages = [
            MessageBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天记录会在后续多条 user message 中给出。每条消息内的 source_id "
                "是本轮学习的来源编号；speaker=SELF 的消息可以作为行为链的一部分，"
                "如果行为主体是 speaker=SELF，请用 actor_type=maibot_self 与 "
                "learning_type=self_reflection 表示；但 action/outcome 不要直接写 SELF 或具体昵称。"
            )
            .build()
        ]

        for index, message in enumerate(messages, start=1):
            await message.process()
            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"
            speaker_kind = "SELF" if is_bot_self(message.platform, user_info.user_id) else "USER"
            content = (message.processed_plain_text or "").strip()
            if not content:
                content = "[空消息]"
            learning_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(
                    "\n".join(
                        [
                            f"[source_id:{index}]",
                            f"[speaker:{speaker_kind}]",
                            f"[name:{speaker_name}]",
                            f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                            "[content]",
                            content,
                        ]
                    )
                )
                .build()
            )

        learning_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content("请根据以上聊天消息输出 JSON。")
            .build()
        )
        return learning_messages

    def _log_learning_scene_preview(
        self,
        prompt: str,
        *,
        session_id: str,
        session_display_name: str,
        source_message_count: int,
        request_messages: Optional[list[Message]] = None,
        output_content: str,
    ) -> None:
        """保存行为学习前的场景画像请求预览。"""

        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                request_messages
                or [
                    MessageBuilder()
                    .set_role(RoleType.User)
                    .add_text_content(prompt)
                    .build()
                ],
                category="behavior_scenario_analyzer",
                chat_id=session_id,
                request_kind="behavior_scenario_analyzer",
                selection_reason=(
                    f"会话ID: {session_id}\n"
                    f"Learner会话ID: {self.session_id}\n"
                    f"来源: behavior_learning_scene\n"
                    f"真实聊天消息数: {source_message_count}\n"
                    f"构建消息数: {len(request_messages or [])}"
                ),
                output_content=output_content,
            )
        except Exception as exc:
            logger.warning(f"[{session_display_name}] 行为学习场景画像预览保存失败: {exc}")
            return

        logger.info(
            f"[{session_display_name}] 行为学习场景画像预览已生成: "
            f"WebUI={preview_access.preview_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    def _log_learning_context_preview(
        self,
        messages: list[Message],
        *,
        session_id: str,
        session_display_name: str,
        source_message_count: int,
        output_content: str,
    ) -> None:
        """保存行为学习上下文预览，并在日志中输出查看入口。"""

        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                messages,
                category="behavior_learner",
                chat_id=session_id,
                request_kind="behavior_learner",
                selection_reason=(
                    f"会话ID: {session_id}\n"
                    f"Learner会话ID: {self.session_id}\n"
                    f"来源: trimmed_history\n"
                    f"真实聊天消息数: {source_message_count}\n"
                    f"构建消息数: {len(messages)}"
                ),
                output_content=output_content,
            )
        except Exception as exc:
            logger.warning(f"[{session_display_name}] 行为学习上下文预览保存失败: {exc}")
            return

        logger.info(
            f"[{session_display_name}] 行为学习上下文预览已生成: "
            f"WebUI={preview_access.preview_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    def _log_behavior_feedback_preview(
        self,
        messages: list[Message],
        *,
        reference_count: int,
        timeline_count: int,
        output_content: str,
    ) -> None:
        """保存行为路径反馈评估上下文预览。"""

        session_display_name = self._get_session_display_name(self.session_id)
        log_prefix = f"[{session_display_name}]"
        try:
            preview_access = PromptCLIVisualizer.build_prompt_preview_access(
                messages,
                category="behavior_feedback",
                chat_id=self.session_id,
                request_kind="behavior_feedback",
                selection_reason=(
                    f"会话ID: {self.session_id}\n"
                    f"来源: trimmed_history_behavior_reference\n"
                    f"行为参考数: {reference_count}\n"
                    f"时间线项数: {timeline_count}\n"
                    f"构建消息数: {len(messages)}"
                ),
                output_content=output_content,
            )
        except Exception as exc:
            logger.warning(f"{log_prefix} 行为路径反馈预览保存失败: {exc}")
            return

        logger.info(
            f"{log_prefix} 行为路径反馈预览已生成: "
            f"WebUI={preview_access.preview_web_uri} "
            f"JSON={preview_access.record_path}"
        )

    def _filter_behavior_candidates(
        self,
        candidates: list[BehaviorCandidate],
        messages: list["SessionMessage"],
    ) -> BehaviorFilterResult:
        """过滤行为表现候选，确保来源行有效且内容可复用。"""

        filtered_candidates: list[BehaviorCandidate] = []
        skipped_reasons: Counter[str] = Counter()
        for candidate in candidates:
            if "SELF" in candidate.action or "SELF" in candidate.outcome:
                skipped_reasons["contains_self_literal"] += 1
                logger.info(
                    f"跳过包含 SELF 字面量的行为表现："
                    f"action={candidate.action}, outcome={candidate.outcome}"
                )
                continue

            valid_source_ids: list[str] = []
            for source_id in candidate.source_ids:
                source_id_str = source_id.strip()
                if not source_id_str.isdigit():
                    continue
                line_index = int(source_id_str) - 1
                if line_index < 0 or line_index >= len(messages):
                    continue
                if source_id_str not in valid_source_ids:
                    valid_source_ids.append(source_id_str)
            if not valid_source_ids:
                skipped_reasons["invalid_source_ids"] += 1
                logger.info(
                    f"跳过来源无效的行为表现: "
                    f"action={candidate.action} outcome={candidate.outcome} source_ids={candidate.source_ids}"
                )
                continue

            has_source_text = any(
                (messages[int(source_id) - 1].processed_plain_text or "").strip()
                for source_id in valid_source_ids
            )
            if not has_source_text:
                skipped_reasons["empty_source_text"] += 1
                logger.info(
                    f"跳过来源为空的行为表现: "
                    f"action={candidate.action} outcome={candidate.outcome} source_ids={valid_source_ids}"
                )
                continue

            filtered_candidates.append(
                BehaviorCandidate(
                    action=candidate.action.strip(),
                    outcome=candidate.outcome.strip(),
                    source_ids=valid_source_ids,
                    segment_id=candidate.segment_id.strip(),
                    actor_type=candidate.actor_type,
                    learning_type=candidate.learning_type,
                )
            )

        return BehaviorFilterResult(
            candidates=filtered_candidates,
            skipped_reasons=dict(skipped_reasons),
        )

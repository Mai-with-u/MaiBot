"""Jev 回复决策逻辑。

把待处理消息与近期聊天记录交给 Jev 模型，由模型直接给出结构化的
「回复 / 不回复」判断，用于替代本地规则决定是否进入 Planner。
"""

from dataclasses import dataclass
from typing import Any, Sequence

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import is_bot_self
from src.common.i18n import t
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.context.messages import LLMContextMessage

from .client import JevAnswer, JevClient, JevEvaluationResult

logger = get_logger("maisaka_jev_decision")

REPLY_QUESTION_ID = "reply_action"
REPLY_ACTION = "reply"
IGNORE_ACTION = "ignore"


@dataclass(frozen=True, slots=True)
class JevReplyDecision:
    """Jev 对待处理消息的回复决策结果。"""

    should_reply: bool
    """是否应该进入 Planner 进行回复。"""

    action: str
    """Jev 选中的动作名，取值为 ``reply`` 或 ``ignore``。"""

    probability: float
    """该动作被选中的概率。"""

    confidence: float
    """Jev 对本次决策的置信度。"""

    detail: str
    """用于日志与监控的决策说明。"""


def _resolve_sender_name(message: SessionMessage) -> str:
    """解析待处理消息的发送者展示名。"""

    user_info = message.message_info.user_info
    return user_info.user_cardname or user_info.user_nickname or user_info.user_id


def _resolve_history_sender(text: str, fallback_name: str) -> str:
    """从带说话人前缀的历史文本中解析发送者。"""

    speaker_name, _ = parse_speaker_content(text)
    return speaker_name or fallback_name


def _resolve_history_content(text: str) -> str:
    """去掉历史文本中的说话人前缀，只保留可见内容。"""

    _, visible_text = parse_speaker_content(text)
    return visible_text.strip()


def build_reply_state(
    *,
    is_group_chat: bool,
    pending_messages: Sequence[SessionMessage],
    chat_history: Sequence[LLMContextMessage],
    context_message_count: int,
) -> dict[str, Any]:
    """构造交给 Jev 评估的结构化状态。

    Args:
        is_group_chat: 当前会话是否为群聊。
        pending_messages: 尚未进入 Planner 的待处理消息。
        chat_history: Maisaka 近期聊天记录。
        context_message_count: 附带多少条最近记录作为上下文。

    Returns:
        dict[str, Any]: Jev 可评估的状态对象。
    """

    bot_name = global_config.bot.nickname.strip()
    recent_messages: list[dict[str, Any]] = []
    history_window = list(chat_history)[-context_message_count:] if context_message_count > 0 else []
    for message in history_window:
        raw_text = str(message.processed_plain_text or "").strip()
        if not raw_text:
            continue
        recent_messages.append(
            {
                "sender": _resolve_history_sender(raw_text, bot_name),
                "text": _resolve_history_content(raw_text),
            }
        )

    pending_payload: list[dict[str, Any]] = []
    for message in pending_messages:
        if is_bot_self(message.platform, message.message_info.user_info.user_id):
            continue
        pending_payload.append(
            {
                "sender": _resolve_sender_name(message),
                "text": str(message.processed_plain_text or "").strip(),
                "at_bot": bool(message.is_at),
            }
        )

    return {
        "chat_type": t("jev.chat_type.group") if is_group_chat else t("jev.chat_type.private"),
        "bot_name": bot_name,
        "recent_messages": recent_messages,
        "pending_messages": pending_payload,
    }


def build_reply_questions(*, is_group_chat: bool) -> dict[str, dict[str, Any]]:
    """构造 Jev 回复决策问题表。

    Args:
        is_group_chat: 当前会话是否为群聊，用于渲染对应场景的判断提示词。

    Returns:
        dict[str, dict[str, Any]]: Jev 问题表。
    """

    bot_name = global_config.bot.nickname.strip()
    instructions = load_prompt(
        "jev_reply_decision",
        bot_name=bot_name,
        chat_type=t("jev.chat_type.group") if is_group_chat else t("jev.chat_type.private"),
    )
    return {
        REPLY_QUESTION_ID: {
            "type": "choice",
            "instructions": instructions,
            "criteria": {
                REPLY_ACTION: t("jev.criteria.reply"),
                IGNORE_ACTION: t("jev.criteria.ignore"),
            },
        }
    }


def parse_reply_answer(
    result: JevEvaluationResult,
    *,
    pending_count: int,
) -> JevReplyDecision:
    """把 Jev 评估结果解析为回复决策。

    Args:
        result: Jev 评估结果。
        pending_count: 本次参与判断的待处理消息数量，用于日志。

    Returns:
        JevReplyDecision: 解析后的回复决策。

    Raises:
        ValueError: Jev 未返回预期的回复决策答案，或选中的动作不在约定范围内。
    """

    answer: JevAnswer | None = result.answers.get(REPLY_QUESTION_ID)
    if answer is None:
        raise ValueError("Jev 未返回回复决策答案，请检查 Jev 配置与提示词")

    if answer.answer_type != "choice":
        raise ValueError(f"Jev 回复决策答案类型应为 choice，实际为 {answer.answer_type!r}")

    action = answer.choice
    if action not in (REPLY_ACTION, IGNORE_ACTION):
        raise ValueError(f"Jev 选中的动作 {action!r} 不在约定的 {REPLY_ACTION}/{IGNORE_ACTION} 范围内")

    probability = answer.probabilities.get(action, 0.0)
    detail = (
        f"Jev决策: 待处理={pending_count} 动作={action} "
        f"概率={probability:.3f} 置信度={answer.confidence:.3f} "
        f"tokens={result.input_tokens}/{result.output_tokens}"
    )
    return JevReplyDecision(
        should_reply=action == REPLY_ACTION,
        action=action,
        probability=probability,
        confidence=answer.confidence,
        detail=detail,
    )


async def decide_reply_with_jev(
    *,
    client: JevClient,
    is_group_chat: bool,
    pending_messages: Sequence[SessionMessage],
    chat_history: Sequence[LLMContextMessage],
    context_message_count: int,
) -> JevReplyDecision:
    """调用 Jev 判断当前待处理消息是否应该进入 Planner。

    Args:
        client: 已初始化的 Jev 客户端。
        is_group_chat: 当前会话是否为群聊。
        pending_messages: 尚未进入 Planner 的待处理消息。
        chat_history: Maisaka 近期聊天记录。
        context_message_count: 附带多少条最近记录作为上下文。

    Returns:
        JevReplyDecision: Jev 给出的回复决策。
    """

    state = build_reply_state(
        is_group_chat=is_group_chat,
        pending_messages=pending_messages,
        chat_history=chat_history,
        context_message_count=context_message_count,
    )
    questions = build_reply_questions(is_group_chat=is_group_chat)
    result = await client.evaluate(state=state, questions=questions)
    return parse_reply_answer(result, pending_count=len(pending_messages))

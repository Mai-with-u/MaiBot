"""Jev 决策模型 HTTP 客户端。

Jev 是 TypeSafe 的 System One 结构化决策模型：调用方传入待评估的 ``state``
与一组带类型的 ``questions``，模型直接返回可被代码消费的类型化答案。
文档：https://docs.typesafe.ai/api
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("maisaka_jev_client")

_EVALUATE_PATH = "systemone"


class JevConfigError(ValueError):
    """Jev 配置缺失或不合法时抛出。"""


@dataclass(frozen=True, slots=True)
class JevAnswer:
    """单个 Jev 问题的类型化答案。"""

    answer_type: str
    """答案类型，取值为 ``choice``/``score``/``noul``。"""

    choice: str = ""
    """Choice 类型选中的选项名。"""

    probabilities: Mapping[str, float] = field(default_factory=dict)
    """Choice 类型各选项的概率分布。"""

    noul: float = 0.0
    """Noul 类型「是」的概率，取值 0 到 1。"""

    confidence: float = 0.0
    """模型对本次答案的置信度。"""


@dataclass(frozen=True, slots=True)
class JevEvaluationResult:
    """一次 Jev 评估的完整结果。"""

    model: str
    """实际处理请求的模型名。"""

    answers: Mapping[str, JevAnswer]
    """按问题 ID 索引的答案表。"""

    input_tokens: int = 0
    """本次请求消耗的输入 Token 数。"""

    output_tokens: int = 0
    """本次请求消耗的输出 Token 数。"""


class JevClient:
    """调用 TypeSafe Jev 结构化决策模型的轻量异步客户端。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: float) -> None:
        """初始化 Jev 客户端。

        Args:
            api_key: TypeSafe API Key。
            base_url: Jev API 基础地址，不带末尾斜杠。
            model: Jev 模型名称，例如 ``jev-latest``。
            timeout_seconds: 单次请求超时时间（秒）。
        """

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def evaluate(
        self,
        *,
        state: str | Mapping[str, Any] | list[Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> JevEvaluationResult:
        """把 ``state`` 与 ``questions`` 交给 Jev 评估，并返回结构化结果。

        Args:
            state: 待评估内容，可以是文本、对象或数组。
            questions: 问题表，键为自定义问题 ID，值为带类型的问题定义。

        Returns:
            JevEvaluationResult: 解析后的评估结果。

        Raises:
            httpx.HTTPStatusError: Jev 返回非 2xx 状态码。
            ValueError: 响应体结构不符合 Jev 协议。
        """

        request_payload: dict[str, Any] = {
            "state": state,
            "model": self._model,
            "questions": dict(questions),
        }
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/{_EVALUATE_PATH}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            response.raise_for_status()
            response_payload = response.json()

        result = _parse_evaluation_payload(response_payload)
        logger.debug(
            f"Jev 评估完成: model={result.model} "
            f"问题数={len(result.answers)} "
            f"tokens={result.input_tokens}/{result.output_tokens}"
        )
        return result


def build_jev_client_from_config() -> JevClient:
    """按当前全局配置构建 Jev 客户端。

    Returns:
        JevClient: 可直接使用的 Jev 客户端。

    Raises:
        JevConfigError: 未配置 API Key 或基础地址。
    """

    jev_config = global_config.chat.jev
    api_key = jev_config.api_key.strip()
    if not api_key:
        raise JevConfigError("尚未配置 Jev API Key，请在「聊天 - Jev 决策」中填写后再启用 Jev 决策回复触发模式")

    base_url = jev_config.base_url.strip()
    if not base_url:
        raise JevConfigError("尚未配置 Jev API 地址，请在「聊天 - Jev 决策」中填写后再启用 Jev 决策回复触发模式")

    return JevClient(
        api_key=api_key,
        base_url=base_url,
        model=jev_config.model.strip(),
        timeout_seconds=jev_config.timeout_seconds,
    )


def _parse_evaluation_payload(response_payload: Any) -> JevEvaluationResult:
    """解析 Jev 响应体为结构化结果。"""

    if not isinstance(response_payload, dict):
        raise ValueError(f"Jev 响应体不是 JSON 对象: {type(response_payload).__name__}")

    raw_answers = response_payload.get("answers")
    if not isinstance(raw_answers, dict) or not raw_answers:
        raise ValueError("Jev 响应体缺少 answers 字段")

    usage = response_payload.get("usage")
    usage_dict = usage if isinstance(usage, dict) else {}
    return JevEvaluationResult(
        model=str(response_payload.get("model") or ""),
        answers={
            str(question_id): _parse_answer(question_id, raw_answer) for question_id, raw_answer in raw_answers.items()
        },
        input_tokens=_read_int_token(usage_dict.get("input_tokens")),
        output_tokens=_read_int_token(usage_dict.get("output_tokens")),
    )


def _parse_answer(question_id: str, raw_answer: Any) -> JevAnswer:
    """解析单个问题的答案。"""

    if not isinstance(raw_answer, dict):
        raise ValueError(f"Jev 问题 {question_id} 的答案不是 JSON 对象")

    answer_type = str(raw_answer.get("type") or "").strip()
    if answer_type == "choice":
        return JevAnswer(
            answer_type=answer_type,
            choice=str(raw_answer.get("choice") or ""),
            probabilities=_parse_probabilities(raw_answer.get("probabilities")),
            confidence=_read_float(raw_answer.get("confidence")),
        )
    if answer_type == "noul":
        return JevAnswer(
            answer_type=answer_type,
            noul=_read_float(raw_answer.get("noul")),
            confidence=_read_float(raw_answer.get("confidence")),
        )
    if answer_type == "score":
        return JevAnswer(
            answer_type=answer_type,
            confidence=_read_float(raw_answer.get("confidence")),
        )
    raise ValueError(f"Jev 问题 {question_id} 返回了未知答案类型: {answer_type!r}")


def _parse_probabilities(raw_probabilities: Any) -> dict[str, float]:
    """解析选项概率分布。"""

    if not isinstance(raw_probabilities, dict):
        return {}
    return {str(option_name): _read_float(probability) for option_name, probability in raw_probabilities.items()}


def _read_float(raw_value: Any) -> float:
    """把响应中的数值字段转换为浮点数。"""

    if raw_value is None:
        return 0.0
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"Jev 响应中的数值字段类型非法: {raw_value!r}")
    return float(raw_value)


def _read_int_token(raw_value: Any) -> int:
    """把响应中的 Token 数字段转换为整数。"""

    if raw_value is None:
        return 0
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"Jev 响应中的 Token 字段类型非法: {raw_value!r}")
    return int(raw_value)

"""长文学习的分段、候选校验与模型调用；候选在用户确认前不写数据库。"""

from typing import Dict, List, Literal, Optional

import asyncio
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.common.llm_json import validate_llm_json
from src.common.logger import get_logger

logger = get_logger("webui.text_learning")

MAX_SOURCE_LENGTH = 200_000
CHUNK_LENGTH = 4_500
LearningKind = Literal["expression", "jargon"]


class LearnedCandidate(BaseModel):
    """两类学习共用的候选：标题对应情境/黑话，描述对应风格/含义。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(default="", max_length=500)

    @field_validator("title", "description")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("候选字段不能为空")
        return value


class ExtractedCandidate(BaseModel):
    """模型可选原文编号，原文内容由程序提取，避免生成式复制改写引文。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    source_id: Optional[int] = Field(default=None, ge=1, strict=True)


class CandidateExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: List[ExtractedCandidate]


def build_learning_sources(text: str) -> Dict[int, str]:
    """为原文建立稳定编号；长段按句末或空白切到 400 字以内，依据直接取自原文。"""
    sources: Dict[int, str] = {}
    for line in text.splitlines():
        remaining = line.strip()
        while remaining:
            cut = min(400, len(remaining))
            if len(remaining) > 400:
                boundaries = [
                    match.end() for match in re.finditer(r"[。！？；;.!?]\s*|\s+", remaining[:400])
                    if match.end() >= 200
                ]
                if boundaries:
                    cut = boundaries[-1]
            excerpt = remaining[:cut].strip()
            if excerpt:
                sources[len(sources) + 1] = excerpt
            remaining = remaining[cut:].lstrip()
    return sources


def split_learning_text(text: str) -> List[str]:
    """优先在段落、换行和句末切分，超长单句硬切；不丢失源文字符。"""
    if not text.strip():
        raise ValueError("请先输入要学习的文段")
    if len(text) > MAX_SOURCE_LENGTH:
        raise ValueError(f"文段不能超过 {MAX_SOURCE_LENGTH:,} 字")
    chunks: List[str] = []
    while len(text) > CHUNK_LENGTH:
        window = text[:CHUNK_LENGTH]
        cut = CHUNK_LENGTH
        for boundary in (r"\n\s*\n", r"\n", r"[。！？]|[.!?](?:[\s”’\"']|$)"):
            matches = [match.end() for match in re.finditer(boundary, window) if match.end() >= CHUNK_LENGTH // 2]
            if matches:
                cut = matches[-1]
                break
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def parse_learning_candidates(response: str, source: str, kind: LearningKind) -> List[LearnedCandidate]:
    """校验模型 JSON，原文依据可选；缺少有效引用的候选仍供用户确认。"""
    payload = response.strip()
    if not payload:
        raise ValueError("学习模型未返回正文，请检查学习模型的输出 token 上限后重试当前段")
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("学习模型返回了不完整的 JSON 代码块")
    try:
        candidates = validate_llm_json(payload, CandidateExtraction).candidates
    except ValidationError as exc:
        if any(error["type"] == "json_invalid" for error in exc.errors()):
            raise ValueError("学习模型返回的 JSON 格式错误或未完整输出，请检查输出 token 上限后重试当前段") from exc
        raise ValueError("学习模型返回的候选字段不符合要求，请重试当前段；详细原因见后台日志") from exc
    if len(candidates) > 30:
        raise ValueError("学习模型单段返回超过 30 条候选，请重试该段")
    unique: List[LearnedCandidate] = []
    seen = set()
    sources = build_learning_sources(source)
    for candidate in candidates:
        evidence = ""
        if candidate.source_id is not None:
            if candidate.source_id in sources:
                evidence = sources[candidate.source_id]
            else:
                logger.warning(f"学习候选引用不存在的原文编号，保留为无依据候选: kind={kind}, source_id={candidate.source_id}")
        key = candidate.title.casefold() if kind == "jargon" else (candidate.title, candidate.description)
        if key not in seen:
            seen.add(key)
            unique.append(LearnedCandidate(
                title=candidate.title,
                description=candidate.description,
                evidence=evidence,
            ))
    return unique


async def extract_text_candidates(text: str, kind: LearningKind) -> List[LearnedCandidate]:
    """在 bot 主循环使用 learner 任务；WebUI 只等待异步结果。"""
    from src.common.data_models.llm_service_data_models import LLMGenerationOptions
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
    from src.llm_models.payload_content.resp_format import RespFormat, RespFormatType
    from src.prompt.prompt_manager import prompt_manager
    from src.services.llm_service import LLMServiceClient

    template = await asyncio.to_thread(prompt_manager.get_prompt, f"text_learning_{kind}")
    instructions = await prompt_manager.render_prompt(template)
    numbered_sources = json.dumps(
        [{"id": source_id, "text": excerpt} for source_id, excerpt in build_learning_sources(text).items()],
        ensure_ascii=False,
    )
    messages = [
        ContextItemBuilder().set_role(RoleType.System).add_text_content(instructions).build(),
        ContextItemBuilder().set_role(RoleType.User).add_text_content(numbered_sources).build(),
    ]
    client = LLMServiceClient(task_name="learner", request_type=f"webui.text_learning.{kind}")
    # 输出预算交由模型/任务配置决定；推理模型的思考也占用该预算，不能在此写死截断上限。
    result = await client.generate_response_with_context(
        lambda _client: messages,
        options=LLMGenerationOptions(response_format=RespFormat(format_type=RespFormatType.JSON_OBJ)),
    )
    if not result.response.strip():
        logger.warning(
            f"长文学习返回空正文: kind={kind}, model={result.model_name}, "
            f"completion_tokens={result.completion_tokens}, reasoning_chars={len(result.reasoning)}"
        )
        if result.reasoning.strip():
            raise ValueError("学习模型只返回了思考内容，没有生成候选结果，请检查输出 token 上限后重试当前段")
    return parse_learning_candidates(result.response, text, kind)

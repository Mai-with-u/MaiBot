"""A_Memorix 内部模型任务选择工具。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from src.common.logger import get_logger
from src.common.data_models.llm_service_data_models import LLMServiceResult
from src.services import llm_service as llm_api

logger = get_logger("A_Memorix.ModelRouting")

NON_TEXT_GENERATION_TASK_NAMES = {"embedding", "voice", "vlm"}
A_MEMORIX_TEXT_TASK_PRIORITY = (
    "memory",
    "utils",
    "lpmm_entity_extract",
    "lpmm_rdf_build",
    "planner",
    "replyer",
    "learner",
    "emoji",
    "tool_use",
)


@dataclass(frozen=True)
class ResolvedLLMModel:
    """A_Memorix 内部使用的 LLM 选择结果。"""

    task_name: str
    task_config: Any
    selected_model_name: str = ""

    @property
    def is_single_model(self) -> bool:
        return bool(self.selected_model_name)


def task_has_model_list(task_config: Any) -> bool:
    """判断任务配置是否有可用模型候选。"""

    model_list = getattr(task_config, "model_list", [])
    return any(str(model_name).strip() for model_name in (model_list or []))


def is_text_generation_task_name(task_name: str) -> bool:
    """判断任务名是否适合 A_Memorix 的普通文本生成调用。"""

    return str(task_name or "").strip().lower() not in NON_TEXT_GENERATION_TASK_NAMES


def get_text_generation_model_tasks(llm_api: Any, *, include_empty: bool = False) -> Dict[str, Any]:
    """从宿主 LLM API 中读取 A_Memorix 可用的文本生成任务配置。"""

    models = llm_api.get_available_models() or {}
    return {
        task_name: task_config
        for task_name, task_config in models.items()
        if is_text_generation_task_name(task_name) and (include_empty or task_has_model_list(task_config))
    }


def _iter_preferred_task_names(available_tasks: Dict[str, Any], preferred: Iterable[str]) -> Iterable[str]:
    yielded: set[str] = set()
    for task_name in preferred:
        if task_name in available_tasks:
            yielded.add(task_name)
            yield task_name
    for task_name in available_tasks:
        if task_name not in yielded:
            yield task_name


def pick_text_generation_task(
    available_tasks: Dict[str, Any],
    preferred: Iterable[str] = A_MEMORIX_TEXT_TASK_PRIORITY,
) -> Tuple[Optional[str], Optional[Any]]:
    """按 A_Memorix 优先级选择文本生成任务。"""

    for task_name in _iter_preferred_task_names(available_tasks, preferred):
        task_config = available_tasks.get(task_name)
        if task_has_model_list(task_config):
            return task_name, task_config
    return None, None


def find_text_generation_task_for_model(
    available_tasks: Dict[str, Any],
    model_name: str,
) -> Tuple[Optional[str], Optional[Any]]:
    """按模型名查找其所属的文本生成任务。"""

    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        return None, None
    for task_name, task_config in available_tasks.items():
        model_list = getattr(task_config, "model_list", []) or []
        task_models = [str(item).strip() for item in model_list if str(item).strip()]
        if normalized_model_name in task_models:
            return task_name, task_config
    return None, None


def build_single_model_task(model_name: str, template: Any) -> Any:
    """基于现有任务模板构造只包含单个文本生成模型的任务配置。"""

    return type(template)(
        model_list=[model_name],
        max_tokens=template.max_tokens,
        temperature=template.temperature,
        slow_threshold=template.slow_threshold,
        selection_strategy=template.selection_strategy,
    )


def resolve_text_generation_model_selector(
    available_tasks: Dict[str, Any],
    selector: str,
) -> Tuple[Optional[str], Optional[Any], str]:
    """解析任务名或具体模型名选择器。"""

    normalized_selector = str(selector or "").strip()
    if not normalized_selector or normalized_selector.lower() == "auto":
        return None, None, ""

    task_config = available_tasks.get(normalized_selector)
    if task_has_model_list(task_config):
        return normalized_selector, task_config, ""

    task_name, task_config = find_text_generation_task_for_model(available_tasks, normalized_selector)
    if task_name and task_config:
        return task_name, build_single_model_task(normalized_selector, task_config), normalized_selector
    return None, None, ""


async def generate_with_resolved_model(
    model: ResolvedLLMModel,
    request_type: str,
    prompt: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMServiceResult:
    """按 A_Memorix 解析出的模型执行文本生成。"""

    if not model.is_single_model:
        return await llm_api.generate(
            llm_api.LLMServiceRequest(
                task_name=model.task_name,
                request_type=request_type,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

    client = llm_api.LLMServiceClient(task_name=model.task_name, request_type=request_type)
    client._orchestrator.model_for_task = model.task_config
    client._orchestrator.model_usage = {model.selected_model_name: (0, 0, 0)}

    def _refresh_single_model_task() -> Any:
        client._orchestrator.model_for_task = model.task_config
        client._orchestrator.model_usage = {
            model.selected_model_name: client._orchestrator.model_usage.get(
                model.selected_model_name,
                (0, 0, 0),
            )
        }
        return model.task_config

    client._orchestrator._refresh_task_config = _refresh_single_model_task
    try:
        completion = await client.generate_response(
            prompt=prompt,
            options=llm_api.LLMGenerationOptions(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        return llm_api.LLMServiceResult.from_response_result(completion)
    except Exception as exc:
        error_message = f"生成内容时出错: {exc}"
        logger.error(f"[A_Memorix.ModelRouting] {error_message}")
        return llm_api.LLMServiceResult.from_error(error_message, str(exc))


def resolve_default_text_generation_task(llm_api: Any) -> str:
    """解析默认文本生成任务，避免宿主默认值落到 embedding。"""

    available_tasks = get_text_generation_model_tasks(llm_api)
    task_name, _ = pick_text_generation_task(available_tasks)
    if not task_name:
        raise RuntimeError("没有可用的文本生成模型配置")
    return task_name


def resolve_text_generation_task_name_from_model_config(
    llm_api: Any,
    model_config: Any,
    *,
    preferred_task_name: str = "",
) -> str:
    """根据旧版 TaskConfig 对象解析文本生成任务名。"""

    available_tasks = get_text_generation_model_tasks(llm_api)
    if not available_tasks:
        raise RuntimeError("没有可用的文本生成模型配置")

    normalized_preferred = str(preferred_task_name or "").strip()
    if normalized_preferred and normalized_preferred in available_tasks:
        return normalized_preferred

    for task_name, task_config in available_tasks.items():
        if task_config is model_config:
            return task_name

    requested_model_list = [
        str(item).strip()
        for item in (getattr(model_config, "model_list", []) or [])
        if str(item).strip()
    ]
    if requested_model_list:
        for task_name, task_config in available_tasks.items():
            candidate_model_list = [
                str(item).strip()
                for item in (getattr(task_config, "model_list", []) or [])
                if str(item).strip()
            ]
            if requested_model_list == candidate_model_list:
                return task_name

        for requested_model in requested_model_list:
            task_name, _ = find_text_generation_task_for_model(available_tasks, requested_model)
            if task_name:
                logger.info(
                    f"旧版文本生成 model_config 按模型 `{requested_model}` 近似映射到任务 `{task_name}`"
                )
                return task_name

    fallback_task_name, _ = pick_text_generation_task(available_tasks)
    if fallback_task_name:
        if normalized_preferred:
            logger.warning(f"无法映射文本生成 model_config，回退默认任务: preferred={normalized_preferred}")
        return fallback_task_name
    raise RuntimeError("没有可用的文本生成模型配置")

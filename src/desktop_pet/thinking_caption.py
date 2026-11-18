"""可丢弃的桌宠阶段摘要；不参与主推理链路。"""

from typing import Any, Awaitable, Callable, Dict, Optional
import asyncio
import json

from src.common.logger import get_logger

logger = get_logger("desktop_pet")
SUMMARY_TIMEOUT = 2.0
THINKING_PLACEHOLDER = "麦麦思考中......"
INACTIVE_STAGES = {"空闲", "等待", "错误", "失败", "停止", "Focus 共享", ""}


def fast_model_configured() -> bool:
    from src.config.config import model_config

    names = model_config.model_task_config.fast_model.model_list
    available = {model.name for model in model_config.models}
    return bool(names) and all(name in available for name in names)


def caption_enabled() -> bool:
    from src.config.config import global_config

    return global_config.desktop_pet.show_current_task and fast_model_configured()


async def summarize_stage(data: Dict[str, Any]) -> str:
    from src.common.data_models.llm_service_data_models import LLMGenerationOptions
    from src.prompt.prompt_manager import prompt_manager
    from src.services.llm_service import LLMServiceClient

    if not caption_enabled():
        return ""
    template = await asyncio.to_thread(prompt_manager.get_prompt, "desktop_pet_thinking_caption")
    prompt = await prompt_manager.render_prompt(template)
    # 仅使用阶段元数据，不读取 Planner 正文或模型内部推理。
    context = {key: str(data.get(key, ""))[:240] for key in ("stage", "detail", "round_text")}
    client = LLMServiceClient(task_name="fast_model", request_type="desktop_pet.thinking_caption")
    result = await client.generate_response(
        prompt + "\n" + json.dumps(context, ensure_ascii=False),
        options=LLMGenerationOptions(max_tokens=64, temperature=0.2),
        session_id=data["session_id"],
    )
    if not caption_enabled():
        return ""
    return " ".join(result.response.split()).strip('"“”')[:18]


class ThinkingCaption:
    def __init__(
        self, send: Callable[[Dict[str, Any]], Awaitable[Any]],
        summarize: Callable[[Dict[str, Any]], Awaitable[str]] = summarize_stage,
    ) -> None:
        self._send = send
        self._summarize = summarize
        self._task: Optional[asyncio.Task[None]] = None
        self._key = None
        self._revision = 0

    def cancel(self) -> None:
        self._revision += 1
        self._key = None
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def update(self, data: Dict[str, Any]) -> None:
        key = tuple(data.get(k, "") for k in ("session_id", "stage", "detail", "round_text", "stage_started_at"))
        if key == self._key:
            return
        self.cancel()
        self._key = key
        self._task = asyncio.create_task(self._run(dict(data), self._revision), name="pet-thinking-caption")

    async def _publish(self, text: str, revision: int) -> None:
        if revision != self._revision:
            return
        # 可选状态也不能长时间占用窗口的写锁，影响真正的聊天消息。
        async with asyncio.timeout(0.25):
            await self._send({"type": "thinking_caption", "text": text, "revision": revision})

    async def _run(self, data: Dict[str, Any], revision: int) -> None:
        try:
            if data.get("stage", "") in INACTIVE_STAGES:
                await self._publish("", revision)
                return
            # 防抖结束后的「消息整理」即刻显示占位；新阶段不能沿用旧动作摘要。
            await self._publish(THINKING_PLACEHOLDER, revision)
            deadline = asyncio.get_running_loop().time() + SUMMARY_TIMEOUT
            async with asyncio.timeout_at(deadline):
                text = await self._summarize(data)
            if text.strip() and asyncio.get_running_loop().time() < deadline:
                await self._publish(text, revision)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 摘要缺失不影响主程序，不重试、不替代真实阶段。
            logger.debug("桌宠阶段摘要已丢弃: %s", exc)

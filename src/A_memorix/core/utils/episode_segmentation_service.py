"""
Episode 语义切分服务（LLM 主路径）。

职责：
1. 组装语义切分提示词
2. 调用 LLM 生成结构化 episode JSON
3. 将模型返回的段落序号映射为内部 hash，返回标准化结果
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import json

from src.common.logger import get_logger
from src.config.model_configs import TaskConfig
from src.services import llm_service as llm_api

from .model_routing import (
    ResolvedLLMModel,
    generate_with_resolved_model,
    get_text_generation_model_tasks,
    pick_text_generation_task,
    resolve_text_generation_model_selector,
)

logger = get_logger("A_Memorix.EpisodeSegmentationService")


class EpisodeSegmentationService:
    """基于 LLM 的 episode 语义切分服务。"""

    SEGMENTATION_VERSION = "episode_indices_v2"

    def __init__(self, plugin_config: Optional[dict] = None):
        self.plugin_config = plugin_config or {}

    def _cfg(self, key: str, default: Any = None) -> Any:
        current: Any = self.plugin_config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @staticmethod
    def _is_task_config(obj: Any) -> bool:
        return hasattr(obj, "model_list") and bool(getattr(obj, "model_list", []))

    def _pick_template_task(self, available_tasks: Dict[str, Any]) -> Optional[TaskConfig]:
        _, task_config = pick_text_generation_task(
            available_tasks,
            preferred=("memory", "utils", "replyer", "planner", "tool_use"),
        )
        return task_config

    def _resolve_model_config(self) -> Tuple[Optional[ResolvedLLMModel], str]:
        available_tasks = get_text_generation_model_tasks(llm_api) or {}
        if not available_tasks:
            return None, "unavailable"

        selector = str(self._cfg("episode.segmentation_model", "auto") or "auto").strip()

        if selector and selector.lower() != "auto":
            task_name, task_config, selected_model_name = resolve_text_generation_model_selector(
                available_tasks,
                selector,
            )
            if task_name and task_config:
                return (
                    ResolvedLLMModel(
                        task_name=task_name,
                        task_config=task_config,
                        selected_model_name=selected_model_name,
                    ),
                    selector,
                )

            logger.warning(f"episode.segmentation_model='{selector}' 不可用，回退 auto")

        task_name, task_config = pick_text_generation_task(
            available_tasks,
            preferred=("memory", "utils", "replyer", "planner", "tool_use"),
        )
        if task_name and task_config:
            return ResolvedLLMModel(task_name=task_name, task_config=task_config), task_name

        fallback = self._pick_template_task(available_tasks)
        if fallback is not None:
            task_name, task_config = pick_text_generation_task(available_tasks)
            if task_name and task_config:
                return ResolvedLLMModel(task_name=task_name, task_config=task_config), "auto"
        return None, "unavailable"

    def generation_signature(self) -> Dict[str, Any]:
        """返回会影响分段输出的实现与模型配置签名。"""
        model, selector = self._resolve_model_config()
        if model is None:
            return {
                "segmentation_version": self.SEGMENTATION_VERSION,
                "selector": selector,
                "mode": "unavailable",
            }
        return {
            "segmentation_version": self.SEGMENTATION_VERSION,
            "selector": selector,
            "mode": "llm",
            "task_name": model.task_name,
            "selected_model_name": model.selected_model_name,
            "model_list": [
                str(item).strip()
                for item in getattr(model.task_config, "model_list", [])
                if str(item).strip()
            ],
            "temperature": getattr(model.task_config, "temperature", None),
            "max_tokens": getattr(model.task_config, "max_tokens", None),
        }

    @staticmethod
    def _clamp_score(value: Any, default: float = 0.0) -> float:
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
    def _safe_json_loads(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("empty_response")

        if "```" in raw:
            raw = raw.replace("```json", "```").replace("```JSON", "```")
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("{") and part.endswith("}"):
                    raw = part
                    break

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            logger.debug("Episode 分段响应不是完整 JSON，继续尝试提取对象片段")

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            candidate = raw[start : end + 1]
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data

        raise ValueError("invalid_json_response")

    def _build_prompt(
        self,
        *,
        source: str,
        window_start: Optional[float],
        window_end: Optional[float],
        paragraphs: List[Dict[str, Any]],
    ) -> str:
        rows: List[str] = []
        for idx, item in enumerate(paragraphs, 1):
            content = str(item.get("content", "") or "").strip().replace("\r\n", "\n")
            content = content[:800]
            event_start = item.get("event_time_start")
            event_end = item.get("event_time_end")
            event_time = item.get("event_time")
            rows.append(
                (
                    f"[{idx}]\n"
                    f"event_time={event_time}\n"
                    f"event_time_start={event_start}\n"
                    f"event_time_end={event_end}\n"
                    f"content={content}"
                )
            )

        source_text = str(source or "").strip() or "unknown"
        return (
            "You are an episode segmentation engine.\n"
            "Group meaningful events into coherent episodes. Skip paragraphs that do not describe an event.\n"
            "Return JSON ONLY. No markdown, no explanation.\n"
            "\n"
            "JSON schema:\n"
            "{\n"
            '  "episodes": [\n'
            "    {\n"
            '      "title": "string",\n'
            '      "summary": "string",\n'
            '      "paragraph_indices": [1, 2],\n'
            '      "participants": ["person1", "person2"],\n'
            '      "keywords": ["kw1", "kw2"],\n'
            '      "time_confidence": 0.0,\n'
            '      "llm_confidence": 0.0\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "\n"
            "Rules:\n"
            "1) paragraph_indices are the 1-based numbers shown in the input.\n"
            "   You may omit irrelevant paragraphs and reuse a paragraph across related episodes.\n"
            '   Return {"episodes": []} if there are no meaningful events.\n'
            "2) title and summary must be non-empty.\n"
            "3) keep participants/keywords concise and deduplicated.\n"
            "4) if uncertain, still provide best effort confidence values.\n"
            "\n"
            f"source={source_text}\n"
            f"window_start={window_start}\n"
            f"window_end={window_end}\n"
            "paragraphs:\n" + "\n\n".join(rows)
        )

    def _normalize_episodes(
        self,
        *,
        payload: Dict[str, Any],
        input_hashes: List[str],
    ) -> List[Dict[str, Any]]:
        raw_episodes = payload.get("episodes")
        if not isinstance(raw_episodes, list):
            raise ValueError("episodes_missing_or_not_list")

        normalized: List[Dict[str, Any]] = []
        for item in raw_episodes:
            if not isinstance(item, dict):
                raise ValueError("episode_not_object")

            title = str(item.get("title", "") or "").strip()
            summary = str(item.get("summary", "") or "").strip()
            if not title or not summary:
                raise ValueError("episode_title_or_summary_missing")

            indices = item.get("paragraph_indices")
            if not isinstance(indices, list) or not indices:
                raise ValueError("episode_paragraph_indices_missing")
            dedup_hashes: List[str] = []
            for index in indices:
                if type(index) is not int or not 1 <= index <= len(input_hashes):
                    raise ValueError(f"episode_paragraph_index_invalid: {index}")
                paragraph_hash = input_hashes[index - 1]
                if paragraph_hash not in dedup_hashes:
                    dedup_hashes.append(paragraph_hash)

            participants = []
            for p in item.get("participants", []) or []:
                token = str(p or "").strip()
                if token:
                    participants.append(token)

            keywords = []
            for kw in item.get("keywords", []) or []:
                token = str(kw or "").strip()
                if token:
                    keywords.append(token)

            normalized.append(
                {
                    "title": title,
                    "summary": summary,
                    "paragraph_hashes": dedup_hashes,
                    "participants": participants[:16],
                    "keywords": keywords[:20],
                    "time_confidence": self._clamp_score(item.get("time_confidence"), default=1.0),
                    "llm_confidence": self._clamp_score(item.get("llm_confidence"), default=0.5),
                }
            )

        return normalized

    async def segment(
        self,
        *,
        source: str,
        window_start: Optional[float],
        window_end: Optional[float],
        paragraphs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not paragraphs:
            raise ValueError("paragraphs_empty")

        resolved_model, model_label = self._resolve_model_config()
        if resolved_model is None:
            raise RuntimeError("episode segmentation model unavailable")

        prompt = self._build_prompt(
            source=source,
            window_start=window_start,
            window_end=window_end,
            paragraphs=paragraphs,
        )
        result = await generate_with_resolved_model(
            resolved_model,
            request_type="A_Memorix.EpisodeSegmentation",
            prompt=prompt,
            temperature=getattr(resolved_model.task_config, "temperature", None),
            max_tokens=getattr(resolved_model.task_config, "max_tokens", None),
        )
        success = bool(result.success)
        response = str(result.completion.response or "")
        if not success or not response:
            raise RuntimeError("llm_generate_failed")

        payload = self._safe_json_loads(str(response))
        input_hashes = [str(p.get("hash", "") or "").strip() for p in paragraphs]
        episodes = self._normalize_episodes(payload=payload, input_hashes=input_hashes)

        return {
            "episodes": episodes,
            "segmentation_model": model_label,
            "segmentation_version": self.SEGMENTATION_VERSION,
        }

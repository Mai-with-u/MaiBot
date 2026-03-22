"""
Episode 语义切分服务（LLM 主路径）。

职责：
1. 组装语义切分提示词
2. 调用 LLM 生成结构化 episode JSON
3. 严格校验输出结构，返回标准化结果
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.config.model_configs import TaskConfig
from src.config.config import model_config as host_model_config
from src.services import llm_service as llm_api

logger = get_logger("A_Memorix.EpisodeSegmentationService")


class EpisodeSegmentationService:
    """基于 LLM 的 episode 语义切分服务。"""

    SEGMENTATION_VERSION = "episode_mvp_v1"

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

    def _build_single_model_task(self, model_name: str, template: TaskConfig) -> TaskConfig:
        return TaskConfig(
            model_list=[model_name],
            max_tokens=template.max_tokens,
            temperature=template.temperature,
            slow_threshold=template.slow_threshold,
            selection_strategy=template.selection_strategy,
        )

    def _pick_template_task(self, available_tasks: Dict[str, Any]) -> Optional[TaskConfig]:
        preferred = ("utils", "replyer", "planner", "tool_use")
        for task_name in preferred:
            cfg = available_tasks.get(task_name)
            if self._is_task_config(cfg):
                return cfg
        for task_name, cfg in available_tasks.items():
            if task_name != "embedding" and self._is_task_config(cfg):
                return cfg
        for cfg in available_tasks.values():
            if self._is_task_config(cfg):
                return cfg
        return None

    def _resolve_model_config(self) -> Tuple[Optional[Any], str]:
        available_tasks = llm_api.get_available_models() or {}
        if not available_tasks:
            return None, "unavailable"

        selector = str(self._cfg("episode.segmentation_model", "auto") or "auto").strip()
        model_dict = getattr(host_model_config, "models_dict", {}) or {}

        if selector and selector.lower() != "auto":
            direct_task = available_tasks.get(selector)
            if self._is_task_config(direct_task):
                return direct_task, selector

            if selector in model_dict:
                template = self._pick_template_task(available_tasks)
                if template is not None:
                    return self._build_single_model_task(selector, template), selector

            logger.warning(f"episode.segmentation_model='{selector}' 不可用，回退 auto")

        for task_name in ("utils", "replyer", "planner", "tool_use"):
            cfg = available_tasks.get(task_name)
            if self._is_task_config(cfg):
                return cfg, task_name

        fallback = self._pick_template_task(available_tasks)
        if fallback is not None:
            return fallback, "auto"
        return None, "unavailable"

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
        except Exception:
            pass

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
            p_hash = str(item.get("hash", "") or "").strip()
            content = str(item.get("content", "") or "").strip().replace("\r\n", "\n")
            content = content[:800]
            event_start = item.get("event_time_start")
            event_end = item.get("event_time_end")
            event_time = item.get("event_time")
            rows.append(
                (
                    f"[{idx}] hash={p_hash}\n"
                    f"event_time={event_time}\n"
                    f"event_time_start={event_start}\n"
                    f"event_time_end={event_end}\n"
                    f"content={content}"
                )
            )

        source_text = str(source or "").strip() or "unknown"
        return (
            "You are an episode segmentation engine.\n"
            "Group the given paragraphs into one or more coherent episodes.\n"
            "Return JSON ONLY. No markdown, no explanation.\n"
            "\n"
            "Hard JSON schema:\n"
            "{\n"
            '  "episodes": [\n'
            "    {\n"
            '      "title": "string",\n'
            '      "summary": "string",\n'
            '      "paragraph_hashes": ["hash1", "hash2"],\n'
            '      "participants": ["person1", "person2"],\n'
            '      "keywords": ["kw1", "kw2"],\n'
            '      "time_confidence": 0.0,\n'
            '      "llm_confidence": 0.0\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "\n"
            "Rules:\n"
            "1) paragraph_hashes must come from input only.\n"
            "2) title and summary must be non-empty.\n"
            "3) keep participants/keywords concise and deduplicated.\n"
            "4) if uncertain, still provide best effort confidence values.\n"
            "\n"
            f"source={source_text}\n"
            f"window_start={window_start}\n"
            f"window_end={window_end}\n"
            "paragraphs:\n"
            + "\n\n".join(rows)
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

        valid_hashes = set(input_hashes)
        normalized: List[Dict[str, Any]] = []
        for item in raw_episodes:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "") or "").strip()
            summary = str(item.get("summary", "") or "").strip()
            if not title or not summary:
                continue

            raw_hashes = item.get("paragraph_hashes")
            if not isinstance(raw_hashes, list):
                continue

            dedup_hashes: List[str] = []
            seen_hashes = set()
            for h in raw_hashes:
                token = str(h or "").strip()
                if not token or token in seen_hashes or token not in valid_hashes:
                    continue
                seen_hashes.add(token)
                dedup_hashes.append(token)

            if not dedup_hashes:
                continue

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

        if not normalized:
            raise ValueError("episodes_all_invalid")
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

        model_config, model_label = self._resolve_model_config()
        if model_config is None:
            raise RuntimeError("episode segmentation model unavailable")

        prompt = self._build_prompt(
            source=source,
            window_start=window_start,
            window_end=window_end,
            paragraphs=paragraphs,
        )
        success, response, _, _ = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="A_Memorix.EpisodeSegmentation",
        )
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


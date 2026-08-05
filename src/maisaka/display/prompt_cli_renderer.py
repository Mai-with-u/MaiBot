"""CLI 下的 Prompt 可视化渲染模块。"""

from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, TypeAlias
from urllib.parse import quote

import hashlib
import json

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from src.llm_models.model_client.base_client import GenerationAttempt
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItem,
    ContextItemMeta,
    ContextRefusalPart,
    ContextTextPart,
    FunctionCallItem,
    FunctionCallOutputItem,
    ProviderActivityItem,
    ProviderOpaqueItem,
    ReasoningItem,
    SystemMessageItem,
    UserMessageItem,
    get_item_text,
)

from .display_utils import (
    format_token_count,
    format_tool_call_for_display as normalize_tool_call_for_display,
    get_request_panel_style as get_shared_request_panel_style,
)
from .preview_path_utils import build_display_path, build_file_uri, REPO_ROOT
from .prompt_preview_logger import PromptPreviewLogger

DATA_IMAGE_DIR = REPO_ROOT / "data" / "images"
DATA_EMOJI_DIR = REPO_ROOT / "data" / "emoji"
DATA_PROMPT_IMAGE_DIR = REPO_ROOT / "data" / "prompt_imgs"
SUPPORTED_STRUCTURED_IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "gif"}
PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES = 64 * 1024
GenerationAttemptInput: TypeAlias = GenerationAttempt | Mapping[str, Any]


@dataclass(frozen=True)
class PromptPreviewRouteTarget:
    """Prompt 预览记录对应的 WebUI 路由目标。"""

    relative_path: Path
    stage: str
    session: str
    stem: str


def _build_webui_local_base_url() -> str:
    """构建终端可直接打开的本机 WebUI 地址。"""

    try:
        from src.config.config import global_config

        host = _select_webui_local_host(global_config.webui.host)
        port = int(global_config.webui.port or 8001)
    except Exception:
        host = "127.0.0.1"
        port = 8001

    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _select_webui_local_host(hosts: Any) -> str:
    """从 WebUI 监听地址中选择适合终端打开的本机地址。"""

    if isinstance(hosts, str):
        return hosts.strip() or "127.0.0.1"
    if isinstance(hosts, list):
        normalized_hosts = [host.strip() for host in hosts if isinstance(host, str) and host.strip()]
        if "127.0.0.1" in normalized_hosts:
            return "127.0.0.1"
        if "::1" in normalized_hosts:
            return "::1"
        if normalized_hosts:
            return normalized_hosts[0]
    return "127.0.0.1"


def _resolve_prompt_preview_route_target(file_path: Path) -> PromptPreviewRouteTarget | None:
    try:
        relative_path = file_path.resolve().relative_to(PromptPreviewLogger._BASE_DIR.resolve())
    except ValueError:
        return None

    parts = relative_path.parts
    if len(parts) < 3:
        return None

    stage, session, filename = parts[0], parts[1], parts[-1]
    stem = Path(filename).stem
    if not stage or not session or not stem:
        return None

    return PromptPreviewRouteTarget(relative_path=relative_path, stage=stage, session=session, stem=stem)


def _build_prompt_preview_web_uri(file_path: Path) -> str:
    """构建 WebUI 可访问的 Prompt 预览地址。"""

    route_target = _resolve_prompt_preview_route_target(file_path)
    if route_target is None:
        return build_file_uri(file_path)
    return f"/api/webui/config/maisaka-prompt-preview?path={quote(route_target.relative_path.as_posix(), safe='')}"


def _build_prompt_reasoning_web_uri(file_path: Path) -> str | None:
    """构建 WebUI 推理过程页面地址。"""

    route_target = _resolve_prompt_preview_route_target(file_path)
    if route_target is None:
        return None

    return (
        f"{_build_webui_local_base_url()}/reasoning-process"
        f"?stage={quote(route_target.stage, safe='')}"
        f"&session={quote(route_target.session, safe='')}"
        f"&stem={quote(route_target.stem, safe='')}"
    )


@dataclass(frozen=True)
class PromptPreviewAccess:
    """Prompt 预览文件的展示入口和可直接打开的路径。"""

    body: RenderableType
    record_path: Path
    record_uri: str
    preview_web_uri: str
    reasoning_web_uri: str | None


@dataclass(frozen=True)
class PromptSectionResult:
    """Prompt 面板及其结构化预览入口。"""

    panel: Panel
    preview_access: PromptPreviewAccess


class PromptCLIVisualizer:
    """负责构建 CLI 下 prompt 展示所需的所有可视化组件。"""

    @staticmethod
    def _normalize_preview_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        """规范化 Prompt 预览元数据，只保留 WebUI 需要稳定展示的字段。"""

        if not metadata:
            return {}

        normalized: dict[str, Any] = {}
        model_name = str(metadata.get("model_name") or metadata.get("model") or "").strip()
        if model_name:
            normalized["model_name"] = model_name

        raw_duration_ms = metadata.get("duration_ms")
        if raw_duration_ms is not None:
            try:
                normalized["duration_ms"] = round(float(raw_duration_ms), 2)
            except (TypeError, ValueError):
                pass

        return normalized

    @staticmethod
    def get_request_panel_style(request_kind: str) -> tuple[str, str]:
        """返回不同请求类型对应的标题与边框颜色。"""

        return get_shared_request_panel_style(request_kind)

    @staticmethod
    def _format_token_count(token_count: int) -> str:
        return format_token_count(token_count)

    @classmethod
    def build_prompt_stats_text(
        cls,
        *,
        selected_history_count: int,
        built_message_count: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> str:
        """构造 prompt 统计文本。"""
        return (
            f"上下文消息数量={selected_history_count} "
            f"已构建消息数={built_message_count} "
            f"实际输入Token={cls._format_token_count(prompt_tokens)} "
            f"输出Token={cls._format_token_count(completion_tokens)} "
            f"总Token={cls._format_token_count(total_tokens)}"
        )

    @staticmethod
    def _normalize_image_format(image_format: str) -> str:
        """归一化图片扩展名。"""
        normalized = image_format.strip().lower()
        if normalized == "jpg":
            return "jpeg"
        return normalized

    @staticmethod
    def _build_image_cache_path(image_format: str, image_bytes: bytes) -> Path:
        image_format = PromptCLIVisualizer._normalize_image_format(image_format) or "bin"
        DATA_PROMPT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(image_bytes).hexdigest()
        return DATA_PROMPT_IMAGE_DIR / f"{digest}.{image_format}"

    @staticmethod
    def _build_official_image_path(image_format: str, image_bytes: bytes) -> Path | None:
        normalized_format = PromptCLIVisualizer._normalize_image_format(image_format) or "bin"
        digest = hashlib.sha256(image_bytes).hexdigest()
        for image_dir in (DATA_IMAGE_DIR, DATA_EMOJI_DIR):
            official_path = image_dir / f"{digest}.{normalized_format}"
            if official_path.exists():
                return official_path
        return None

    @staticmethod
    def _build_image_file_link(image_format: str, image_base64: str) -> tuple[str, Path] | None:
        """优先返回已有 data 图片路径；不存在时落盘到 prompt 图片缓存。"""
        normalized_format = PromptCLIVisualizer._normalize_image_format(image_format) or "bin"
        try:
            image_bytes = b64decode(image_base64)
        except Exception:
            return None

        official_path = PromptCLIVisualizer._build_official_image_path(normalized_format, image_bytes)
        if official_path is not None:
            return build_file_uri(official_path), official_path

        path = PromptCLIVisualizer._build_image_cache_path(normalized_format, image_bytes)
        if not path.exists():
            try:
                path.write_bytes(image_bytes)
            except Exception:
                return None
        return build_file_uri(path), path

    @staticmethod
    def _extract_image_pair(item: Any) -> tuple[str, str] | None:
        """兼容图片片段被序列化为 tuple 或 list 的两种形式。"""

        if isinstance(item, (tuple, list)) and len(item) == 2:
            image_format, image_base64 = item
            if not isinstance(image_format, str) or not isinstance(image_base64, str):
                return None
            normalized_format = PromptCLIVisualizer._normalize_image_format(image_format)
            if normalized_format not in SUPPORTED_STRUCTURED_IMAGE_FORMATS:
                return None
            try:
                if not b64decode(image_base64, validate=True):
                    return None
            except Exception:
                return None
            return normalized_format, image_base64
        return None

    @staticmethod
    def _extract_data_url_image(image_url: str) -> tuple[str, str] | None:
        """从 data URL 中提取图片格式和 Base64 内容。"""

        normalized_url = image_url.strip()
        if not normalized_url.startswith("data:image/") or ";base64," not in normalized_url:
            return None
        prefix, image_base64 = normalized_url.split(";base64,", maxsplit=1)
        image_format = prefix.removeprefix("data:image/").strip().lower()
        if not image_format or not image_base64:
            return None
        return image_format, image_base64

    @classmethod
    def _extract_image_dict_pair(cls, item: Any) -> tuple[str, str] | None:
        """兼容 OpenAI/Responses 风格的图片 content part。"""

        if not isinstance(item, dict):
            return None

        part_type = str(item.get("type") or "").strip()
        if part_type not in {"image", "image_url", "input_image"}:
            return None

        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        if isinstance(image_url, str):
            image_pair = cls._extract_data_url_image(image_url)
            if image_pair is not None:
                return image_pair

        image_base64 = item.get("image_base64") or item.get("base64")
        image_format = item.get("image_format") or item.get("format")
        if isinstance(image_format, str) and isinstance(image_base64, str):
            return image_format, image_base64
        return None

    @classmethod
    def format_tool_call_for_display(cls, tool_call: Any) -> Dict[str, Any]:
        return normalize_tool_call_for_display(tool_call)

    @classmethod
    def _project_context_item_for_display(cls, item: ContextItem) -> dict[str, Any]:
        """把 Context Item 投影为日志/WebUI 结构，不暴露 replay payload。"""

        payload: dict[str, Any] = {
            "item_id": item.meta.item_id,
            "item_type": item.__class__.__name__,
            "logical_turn_id": item.meta.logical_turn_id,
        }
        if isinstance(item, SystemMessageItem):
            payload["role"] = "system"
        elif isinstance(item, UserMessageItem):
            payload["role"] = "user"
        elif isinstance(item, AssistantMessageItem):
            payload["role"] = "assistant"
        elif isinstance(item, ReasoningItem):
            payload.update(
                {
                    "content": get_item_text(item),
                    "reasoning_representation": item.representation.value,
                    "role": "reasoning",
                }
            )
            return payload
        elif isinstance(item, FunctionCallItem):
            payload.update(
                {
                    "content": "",
                    "role": "function_call",
                    "tool_calls": [
                        {
                            "id": item.tool_call.call_id,
                            "function": {
                                "name": item.tool_call.func_name,
                                "arguments": item.tool_call.materialize_args(),
                            },
                        }
                    ],
                }
            )
            return payload
        elif isinstance(item, FunctionCallOutputItem):
            payload.update(
                {
                    "content": item.output,
                    "role": "tool",
                    "tool_call_id": item.call_id,
                    "tool_name": item.tool_name,
                }
            )
            return payload
        elif isinstance(item, ProviderActivityItem):
            payload.update(
                {
                    "content": item.display_summary,
                    "provider_type": item.provider_type,
                    "role": "provider_activity",
                    "status": item.status,
                }
            )
            return payload
        elif isinstance(item, ProviderOpaqueItem):
            payload.update(
                {
                    "content": item.display_summary,
                    "provider_type": item.provider_type,
                    "role": "provider_opaque",
                }
            )
            return payload

        content: list[Any] = []
        for part in item.parts:
            if isinstance(part, ContextTextPart):
                content.append(part.text)
            elif isinstance(part, ContextImagePart):
                content.append((part.image_format, part.image_base64))
            elif isinstance(part, ContextRefusalPart):
                content.append(part.refusal)
        payload["content"] = content
        return payload

    @classmethod
    def _serialize_message_content_for_dump(cls, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                image_pair = cls._extract_image_pair(item)
                if image_pair is not None:
                    image_format, image_base64 = image_pair
                    approx_size = max(0, len(str(image_base64)) * 3 // 4)
                    parts.append(f"[图片 image/{image_format} {approx_size} B]")
                    continue
                image_dict_pair = cls._extract_image_dict_pair(item)
                if image_dict_pair is not None:
                    image_format, image_base64 = image_dict_pair
                    approx_size = max(0, len(str(image_base64)) * 3 // 4)
                    parts.append(f"[图片 image/{image_format} {approx_size} B]")
                    continue
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                try:
                    parts.append(json.dumps(item, ensure_ascii=False, indent=2, default=str))
                except Exception:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part).strip()
        if content is None:
            return ""
        try:
            return json.dumps(content, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(content)

    @classmethod
    def build_prompt_dump_text(cls, messages: Sequence[Any]) -> str:
        """构建用于结果摘要与调试展示的纯文本 Prompt。"""

        sections: list[str] = []
        for index, item in enumerate(cls._normalize_context_items(messages), start=1):
            message = cls._project_context_item_for_display(item)
            raw_role = message.get("role", "unknown")
            content = message.get("content")
            reasoning_content = message.get("reasoning_content")
            tool_call_id = message.get("tool_call_id")
            tool_name = message.get("tool_name")
            tool_calls = message.get("tool_calls") or []

            role = raw_role.value if hasattr(raw_role, "value") else str(raw_role)
            block_lines = [f"[{index}] role={role}"]
            if tool_call_id:
                block_lines.append(f"tool_call_id={tool_call_id}")
            if tool_name:
                block_lines.append(f"tool_name={tool_name}")

            normalized_content = cls._serialize_message_content_for_dump(content)
            if normalized_content:
                block_lines.append("")
                block_lines.append(normalized_content)
            if reasoning_content:
                block_lines.append("")
                block_lines.append("reasoning_content:")
                block_lines.append(str(reasoning_content))

            if tool_calls:
                block_lines.append("")
                block_lines.append("tool_calls:")
                for tool_call in tool_calls:
                    normalized_tool_call = cls.format_tool_call_for_display(tool_call)
                    block_lines.append(json.dumps(normalized_tool_call, ensure_ascii=False, indent=2, default=str))

            sections.append("\n".join(block_lines).strip())

        return "\n\n" + ("\n\n" + ("=" * 80) + "\n\n").join(sections) if sections else "[空 Prompt]"

    @staticmethod
    def _should_keep_prompt_preview_json_base64() -> bool:
        try:
            from src.config.config import global_config

            return bool(global_config.debug.keep_prompt_preview_json_base64)
        except Exception:
            return False

    @classmethod
    def _build_structured_image_reference(cls, image_format: str, image_base64: str) -> dict[str, Any]:
        """构建结构化 JSON 中的图片引用，避免默认写入大块 base64。"""

        normalized_format = cls._normalize_image_format(image_format) or "bin"
        approx_size = max(0, len(image_base64) * 3 // 4)
        payload: dict[str, Any] = {
            "type": "image",
            "image_format": normalized_format,
            "size_bytes": approx_size,
            "base64_omitted": True,
        }

        path_result = cls._build_image_file_link(normalized_format, image_base64)
        if path_result is None:
            payload["image_available"] = False
            return payload

        file_uri, file_path = path_result
        payload.update(
            {
                "image_available": True,
                "image_path": build_display_path(file_path),
                "image_uri": file_uri,
            }
        )
        return payload

    @classmethod
    def _build_structured_image_content_part(
        cls,
        item: dict[str, Any],
        image_format: str,
        image_base64: str,
    ) -> dict[str, Any]:
        sanitized_item = {
            key: cls._sanitize_structured_value(value, keep_base64=False)
            for key, value in item.items()
            if key not in {"base64", "image_base64", "image_url"}
        }
        image_reference = cls._build_structured_image_reference(image_format, image_base64)
        embedded_image_reference = {
            key: value for key, value in image_reference.items() if key not in {"type", "image_format"}
        }

        sanitized_item.update(
            {
                "image_format": image_reference["image_format"],
                "image_reference": embedded_image_reference,
            }
        )
        return sanitized_item

    @classmethod
    def _sanitize_structured_value(cls, value: Any, *, keep_base64: bool) -> Any:
        if keep_base64:
            return value

        if isinstance(value, str):
            image_pair = cls._extract_data_url_image(value)
            if image_pair is None:
                return value
            image_format, image_base64 = image_pair
            return cls._build_structured_image_reference(image_format, image_base64)

        image_pair = cls._extract_image_pair(value)
        if image_pair is not None:
            image_format, image_base64 = image_pair
            return cls._build_structured_image_reference(image_format, image_base64)

        if isinstance(value, dict):
            image_dict_pair = cls._extract_image_dict_pair(value)
            if image_dict_pair is not None:
                image_format, image_base64 = image_dict_pair
                return cls._build_structured_image_content_part(value, image_format, image_base64)
            return {key: cls._sanitize_structured_value(item, keep_base64=False) for key, item in value.items()}

        if isinstance(value, list):
            return [cls._sanitize_structured_value(item, keep_base64=False) for item in value]

        return value

    @staticmethod
    def _build_omitted_binary_reference(
        value: str | bytes,
        *,
        size_bytes: int,
        media_type: str = "",
    ) -> dict[str, Any]:
        """为未写入 Prompt JSON 的大块 Base64 或二进制数据构建可核验占位。"""

        raw_bytes = value.encode("utf-8") if isinstance(value, str) else value
        reference: dict[str, Any] = {
            "type": "omitted_binary",
            "size_bytes": size_bytes,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }
        if isinstance(value, str):
            reference["base64_omitted"] = True
            reference["encoded_chars"] = len(value)
        else:
            reference["binary_omitted"] = True
        if media_type:
            reference["media_type"] = media_type
        return reference

    @staticmethod
    def _looks_like_large_base64(value: str) -> bool:
        """识别超过阈值的普通或 URL-safe Base64 字符串。"""

        normalized = value.strip()
        if len(normalized) * 3 // 4 <= PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES:
            return False
        allowed_characters = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")
        return bool(normalized) and all(character in allowed_characters for character in normalized)

    @classmethod
    def _sanitize_provider_response_value(
        cls,
        value: Any,
        *,
        keep_base64: bool,
        key: str = "",
    ) -> Any:
        """保留可观测字段，并始终省略只服务于原生回放的 opaque 数据。"""

        normalized_key = key.strip().lower().replace("-", "_")
        if normalized_key in {"encrypted_content", "thought_signature"}:
            return "[仅在内存 replay fragment 中保留]"
        if isinstance(value, bytes):
            if keep_base64:
                return value
            return cls._build_omitted_binary_reference(value, size_bytes=len(value))
        if isinstance(value, str):
            if keep_base64:
                return value
            normalized_value = value.strip()
            if normalized_value.startswith("data:") and ";base64," in normalized_value:
                header, base64_value = normalized_value.split(",", maxsplit=1)
                size_bytes = max(0, len(base64_value) * 3 // 4)
                if size_bytes > PROVIDER_RESPONSE_BASE64_OMIT_THRESHOLD_BYTES:
                    media_type = header.removeprefix("data:").split(";", maxsplit=1)[0]
                    return cls._build_omitted_binary_reference(
                        base64_value,
                        size_bytes=size_bytes,
                        media_type=media_type,
                    )
                return value
            if cls._looks_like_large_base64(normalized_value):
                return cls._build_omitted_binary_reference(
                    normalized_value,
                    size_bytes=max(0, len(normalized_value) * 3 // 4),
                )
            return value
        if isinstance(value, dict):
            return {
                str(item_key): cls._sanitize_provider_response_value(
                    item,
                    keep_base64=keep_base64,
                    key=str(item_key),
                )
                for item_key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_provider_response_value(item, keep_base64=keep_base64) for item in value]
        return value

    @classmethod
    def _normalize_context_items(cls, items: Sequence[Any]) -> list[ContextItem]:
        """只接受运行时 Context Items 或已序列化 Item 快照。"""

        # 延迟导入以避开 request_snapshot -> model_client -> display 的初始化环。
        from src.llm_models.request_snapshot import deserialize_context_item_snapshot

        normalized_items: list[ContextItem] = []
        for item in items:
            if isinstance(
                item,
                (
                    SystemMessageItem,
                    UserMessageItem,
                    AssistantMessageItem,
                    ReasoningItem,
                    FunctionCallItem,
                    FunctionCallOutputItem,
                    ProviderActivityItem,
                    ProviderOpaqueItem,
                ),
            ):
                normalized_items.append(item)
                continue

            if isinstance(item, Mapping):
                normalized_items.append(deserialize_context_item_snapshot(dict(item)))
                continue
            raise TypeError(
                "Prompt 渲染器仅接受 ContextItem 或 Item 快照，"
                f"实际收到 {item.__class__.__name__}"
            )
        return normalized_items

    @classmethod
    def build_structured_context_item_payload(
        cls,
        items: Sequence[Any],
        *,
        keep_base64: bool,
    ) -> list[dict[str, Any]]:
        """构建 Item-first 结构化记录；图片只在持久化边界替换为安全引用。"""

        from src.llm_models.request_snapshot import serialize_context_item_snapshot

        structured_items: list[dict[str, Any]] = []
        for item in items:
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("item_type"), str)
                and isinstance(item.get("meta"), Mapping)
            ):
                structured_items.append(
                    cls.sanitize_structured_context_item_snapshot(item, keep_base64=keep_base64)
                )
                continue

            structured_items.extend(
                cls.sanitize_structured_context_item_snapshot(
                    serialize_context_item_snapshot(normalized_item),
                    keep_base64=keep_base64,
                )
                for normalized_item in cls._normalize_context_items([item])
            )
        return structured_items

    @classmethod
    def sanitize_structured_context_item_snapshot(
        cls,
        item: Mapping[str, Any],
        *,
        keep_base64: bool,
    ) -> dict[str, Any]:
        """在持久化边界安全处理一个已经序列化的 Context Item。"""

        sanitized_item = cls._sanitize_structured_value(dict(item), keep_base64=keep_base64)
        if not isinstance(sanitized_item, dict):
            raise TypeError("Context Item 快照清理后必须保持字典结构")
        return sanitized_item

    @classmethod
    def _build_structured_preview_payload(
        cls,
        request_items: list[Any],
        *,
        request_kind: str,
        selection_reason: str,
        tool_definitions: list[dict[str, Any]] | None,
        output_title: str,
        output_items: Sequence[Any],
        metadata: Mapping[str, Any] | None,
        generation_attempts: Sequence[GenerationAttemptInput],
        keep_base64: bool,
    ) -> dict[str, Any]:
        """构建 Prompt 预览 JSON，供 WebUI 稳定解析展示。"""

        payload = {
            "schema_version": 6,
            "request": {
                "kind": request_kind,
                "selection_reason": selection_reason,
            },
            "metadata": cls._normalize_preview_metadata(metadata),
            "presentation": {"output_title": output_title},
            "request_items": cls.build_structured_context_item_payload(
                request_items,
                keep_base64=keep_base64,
            ),
            "output_items": cls.build_structured_context_item_payload(
                output_items,
                keep_base64=keep_base64,
            ),
            "tool_definitions": tool_definitions or [],
            "generation_attempts": [
                cls._build_generation_attempt_payload(attempt)
                for attempt in generation_attempts
            ],
        }
        return payload

    @classmethod
    def _build_generation_attempt_payload(cls, attempt: GenerationAttemptInput) -> dict[str, Any]:
        """把 Attempt DTO 或已有 v6 快照规范化为 JSON-safe 结构。"""

        if isinstance(attempt, Mapping):
            sanitized_attempt = cls._sanitize_provider_response_value(dict(attempt), keep_base64=False)
            if not isinstance(sanitized_attempt, dict):
                raise TypeError("Generation Attempt 快照必须是字典")
            return sanitized_attempt

        from src.llm_models.request_snapshot import serialize_generation_attempt

        return serialize_generation_attempt(attempt)

    @classmethod
    def _build_preview_access_body(
        cls,
        *,
        record_path: Path,
        record_link_text: str,
    ) -> RenderableType:
        record_uri = build_file_uri(record_path)
        record_display_path = build_display_path(record_path)
        reasoning_web_uri = _build_prompt_reasoning_web_uri(record_path)
        lines: list[RenderableType] = [
            cls._build_preview_link_line(
                label=f"结构化记录：{record_display_path}",
                label_style="bold green",
                link_uri=record_uri,
                link_text=record_link_text,
            )
        ]
        reasoning_line = (
            cls._build_preview_link_line(
                label=f"推理详情浏览：{reasoning_web_uri}",
                label_style="bold cyan",
                link_uri=reasoning_web_uri,
                link_text="点击跳转到推理页面",
            )
            if reasoning_web_uri
            else None
        )
        if reasoning_line is not None:
            lines.append(reasoning_line)

        return Group(*lines)

    @staticmethod
    def _build_preview_link_line(
        *,
        label: str,
        label_style: str,
        link_uri: str,
        link_text: str,
    ) -> Text:
        line = Text()
        line.append(label, style=label_style)
        line.append(" ")
        line.append(link_text, style=f"link {link_uri}")
        return line

    @classmethod
    def _save_structured_preview_access(
        cls,
        *,
        chat_id: str,
        category: str,
        payload: dict[str, Any],
    ) -> PromptPreviewAccess:
        structured_preview_text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        record_path = PromptPreviewLogger.save_preview_file(
            chat_id,
            category,
            structured_preview_text,
        )
        body = cls._build_preview_access_body(
            record_path=record_path,
            record_link_text="点击打开 JSON 记录",
        )
        return PromptPreviewAccess(
            body=body,
            record_path=record_path,
            record_uri=build_file_uri(record_path),
            preview_web_uri=_build_prompt_preview_web_uri(record_path),
            reasoning_web_uri=_build_prompt_reasoning_web_uri(record_path),
        )

    @classmethod
    def build_prompt_preview_access(
        cls,
        request_items: list[Any],
        *,
        category: str,
        chat_id: str,
        request_kind: str,
        selection_reason: str,
        tool_definitions: list[dict[str, Any]] | None = None,
        output_title: str = "输出结果",
        output_items: Sequence[Any] = (),
        metadata: Mapping[str, Any] | None = None,
        generation_attempts: Sequence[GenerationAttemptInput] = (),
    ) -> PromptPreviewAccess:
        """保存 Prompt 预览文件，并返回 CLI 展示入口与浏览器可打开的 URI。"""

        keep_json_base64 = cls._should_keep_prompt_preview_json_base64()
        return cls._save_structured_preview_access(
            chat_id=chat_id,
            category=category,
            payload=cls._build_structured_preview_payload(
                request_items,
                request_kind=request_kind,
                selection_reason=selection_reason,
                tool_definitions=tool_definitions,
                output_title=output_title,
                output_items=output_items,
                metadata=metadata,
                generation_attempts=generation_attempts,
                keep_base64=keep_json_base64,
            ),
        )

    @classmethod
    def build_prompt_access_panel(
        cls,
        request_items: list[Any],
        *,
        category: str,
        chat_id: str,
        request_kind: str,
        selection_reason: str,
        tool_definitions: list[dict[str, Any]] | None = None,
        output_title: str = "输出结果",
        output_items: Sequence[Any] = (),
        metadata: Mapping[str, Any] | None = None,
        generation_attempts: Sequence[GenerationAttemptInput] = (),
    ) -> RenderableType:
        """构建用于查看完整 prompt 的折叠入口内容。"""

        return cls.build_prompt_preview_access(
            request_items,
            category=category,
            chat_id=chat_id,
            request_kind=request_kind,
            selection_reason=selection_reason,
            tool_definitions=tool_definitions,
            output_title=output_title,
            output_items=output_items,
            metadata=metadata,
            generation_attempts=generation_attempts,
        ).body

    @classmethod
    def build_prompt_section_result(
        cls,
        request_items: list[Any],
        *,
        category: str,
        chat_id: str,
        request_kind: str,
        selection_reason: str,
        tool_definitions: list[dict[str, Any]] | None = None,
        output_title: str = "输出结果",
        output_items: Sequence[Any] = (),
        metadata: Mapping[str, Any] | None = None,
        generation_attempts: Sequence[GenerationAttemptInput] = (),
    ) -> PromptSectionResult:
        """构建默认折叠的 Prompt 面板，并返回对应的结构化预览入口。"""

        panel_title, panel_border_style = cls.get_request_panel_style(request_kind)
        preview_access = cls.build_prompt_preview_access(
            request_items,
            category=category,
            chat_id=chat_id,
            request_kind=request_kind,
            selection_reason=selection_reason,
            tool_definitions=tool_definitions,
            output_title=output_title,
            output_items=output_items,
            metadata=metadata,
            generation_attempts=generation_attempts,
        )

        return PromptSectionResult(
            panel=Panel(
                preview_access.body,
                title=panel_title,
                subtitle=selection_reason,
                border_style=panel_border_style,
                padding=(0, 1),
            ),
            preview_access=preview_access,
        )

    @classmethod
    def build_text_access_panel(
        cls,
        content: str,
        *,
        category: str,
        chat_id: str,
        request_kind: str,
        subtitle: str,
        output_title: str = "输出结果",
        output_items: Sequence[Any] = (),
        metadata: Mapping[str, Any] | None = None,
        generation_attempts: Sequence[GenerationAttemptInput] = (),
    ) -> RenderableType:
        """构建文本型 Prompt 的折叠入口内容。"""

        return cls.build_text_preview_access(
            content,
            category=category,
            chat_id=chat_id,
            request_kind=request_kind,
            subtitle=subtitle,
            output_title=output_title,
            output_items=output_items,
            metadata=metadata,
            generation_attempts=generation_attempts,
        ).body

    @classmethod
    def build_text_preview_access(
        cls,
        content: str,
        *,
        category: str,
        chat_id: str,
        request_kind: str,
        subtitle: str,
        output_title: str = "输出结果",
        output_items: Sequence[Any] = (),
        metadata: Mapping[str, Any] | None = None,
        generation_attempts: Sequence[GenerationAttemptInput] = (),
    ) -> PromptPreviewAccess:
        """保存文本型 Prompt 预览文件，并返回对应访问入口。"""

        keep_json_base64 = cls._should_keep_prompt_preview_json_base64()
        return cls._save_structured_preview_access(
            chat_id=chat_id,
            category=category,
            payload=cls._build_structured_preview_payload(
                [
                    UserMessageItem(
                        meta=ContextItemMeta.create(),
                        parts=(ContextTextPart(content),),
                    )
                ],
                request_kind=request_kind,
                selection_reason=subtitle,
                tool_definitions=None,
                output_title=output_title,
                output_items=output_items,
                metadata=metadata,
                generation_attempts=generation_attempts,
                keep_base64=keep_json_base64,
            ),
        )

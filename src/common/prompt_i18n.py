from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from tomlkit import parse as parse_toml

import json
import logging
import os
import re

from .i18n import get_locale, t
from .i18n.loaders import extract_placeholders, normalize_locale

logger = logging.getLogger("maibot.prompt_i18n")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_ROOT = (PROJECT_ROOT / "prompts").resolve()
CUSTOM_PROMPTS_ROOT = (PROJECT_ROOT / "data" / "custom_prompts").resolve()
PROMPT_EXTENSIONS = (".prompt",)
# Prompt 模板找不到时固定回退到 zh-CN，不跟随 UI 的 DEFAULT_LOCALE 探测结果。
PROMPT_FALLBACK_LOCALE = "zh-CN"
SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
STRICT_ENV_KEYS = ("MAIBOT_PROMPT_I18N_STRICT", "MAIBOT_I18N_STRICT")
STRICT_ENV_VALUES = {"1", "true", "yes", "on"}
_PROMPT_CACHE_REVISION = 0

extract_prompt_placeholders = extract_placeholders


@dataclass(frozen=True)
class PromptMetadata:
    display_name: str = ""
    advanced: bool = False
    description: str = ""


@dataclass(frozen=True)
class PromptTemplateInfo:
    path: Path
    metadata: PromptMetadata


@dataclass(frozen=True)
class ResolvedPromptTemplate:
    path: Path
    template: str
    customized: bool
    locale: str | None


def get_prompts_root(prompts_root: Path | None = None) -> Path:
    return (prompts_root or PROMPTS_ROOT).resolve()


def get_custom_prompts_root(
    custom_prompts_root: Path | None = None,
    prompts_root: Path | None = None,
) -> Path:
    if custom_prompts_root is not None:
        return custom_prompts_root.resolve()
    if prompts_root is not None:
        return (prompts_root.resolve().parent / "data" / "custom_prompts").resolve()
    return CUSTOM_PROMPTS_ROOT


def normalize_prompt_name(name: str) -> str:
    candidate_name = name.strip()
    for suffix in PROMPT_EXTENSIONS:
        if candidate_name.endswith(suffix):
            candidate_name = candidate_name[: -len(suffix)]
            break

    if candidate_name in {".", ".."} or not candidate_name or not SAFE_SEGMENT_PATTERN.fullmatch(candidate_name):
        raise ValueError(t("prompt.invalid_name", name=name))
    return candidate_name


def normalize_prompt_category(category: str | None) -> str | None:
    if category is None:
        return None

    category_parts = [part for part in category.strip().split("/") if part]
    if not category_parts:
        raise ValueError(t("prompt.invalid_category", category=category))

    for part in category_parts:
        if part in {".", ".."} or not SAFE_SEGMENT_PATTERN.fullmatch(part):
            raise ValueError(t("prompt.invalid_category", category=category))
    return "/".join(category_parts)


def is_strict_prompt_i18n_mode() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True

    return any(os.getenv(env_key, "").strip().lower() in STRICT_ENV_VALUES for env_key in STRICT_ENV_KEYS)


def discover_prompt_locales(prompts_root: Path | None = None) -> list[str]:
    resolved_prompts_root = get_prompts_root(prompts_root)
    if not resolved_prompts_root.exists():
        return []

    locale_names = [path.name for path in resolved_prompts_root.iterdir() if path.is_dir()]
    return sorted(locale_names)


def iter_prompt_files(directory: Path, recursive: bool = True) -> list[Path]:
    if not directory.exists():
        return []

    search = directory.rglob if recursive else directory.glob
    prompt_files: list[Path] = []
    for suffix in PROMPT_EXTENSIONS:
        prompt_files.extend(path for path in search(f"*{suffix}") if path.is_file())
    return sorted(set(prompt_files))


def _raise_duplicate_prompt_name(name: str, first_path: Path, second_path: Path, prompts_root: Path) -> None:
    path_a = first_path.relative_to(prompts_root).as_posix()
    path_b = second_path.relative_to(prompts_root).as_posix()
    raise ValueError(
        t(
            "prompt.duplicate_template_name",
            name=name,
            path_a=path_a,
            path_b=path_b,
        )
    )


def _coerce_metadata(raw_metadata: Any) -> PromptMetadata:
    if not isinstance(raw_metadata, dict):
        return PromptMetadata()

    display_name = raw_metadata.get("display_name", "")
    advanced = raw_metadata.get("advanced", False)
    description = raw_metadata.get("description", "")

    return PromptMetadata(
        display_name=display_name if isinstance(display_name, str) else "",
        advanced=advanced if isinstance(advanced, bool) else False,
        description=description if isinstance(description, str) else "",
    )


def _read_metadata_file(metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.is_file():
        return {}

    try:
        if metadata_path.suffix == ".json":
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        else:
            metadata = parse_toml(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"读取 Prompt 元信息文件 {metadata_path} 失败：{exc}")
        return {}

    return dict(metadata) if isinstance(metadata, dict) else {}


def _extract_template_metadata(metadata: dict[str, Any], prompt_name: str) -> dict[str, Any]:
    templates = metadata.get("templates")
    if isinstance(templates, dict) and isinstance(templates.get(prompt_name), dict):
        return dict(templates[prompt_name])

    prompt_metadata = metadata.get(prompt_name)
    if isinstance(prompt_metadata, dict):
        return dict(prompt_metadata)

    return metadata if any(key in metadata for key in ("display_name", "advanced", "description")) else {}


def _load_prompt_metadata(prompt_path: Path) -> PromptMetadata:
    prompt_name = prompt_path.stem
    metadata_sources = (
        prompt_path.with_name(f"{prompt_name}.meta.toml"),
        prompt_path.with_name(f"{prompt_name}.meta.json"),
        prompt_path.parent / ".meta.toml",
        prompt_path.parent / ".meta.json",
    )

    merged_metadata: dict[str, Any] = {}
    for metadata_path in reversed(metadata_sources):
        raw_metadata = _read_metadata_file(metadata_path)
        merged_metadata.update(_extract_template_metadata(raw_metadata, prompt_name))

    return _coerce_metadata(merged_metadata)


def _scan_prompt_directory(directory: Path, prompts_root: Path) -> dict[str, PromptTemplateInfo]:
    prompt_paths: dict[str, PromptTemplateInfo] = {}
    for prompt_path in iter_prompt_files(directory):
        prompt_name = prompt_path.stem
        existing_info = prompt_paths.get(prompt_name)
        if existing_info is not None:
            _raise_duplicate_prompt_name(prompt_name, existing_info.path, prompt_path, prompts_root)
        prompt_paths[prompt_name] = PromptTemplateInfo(path=prompt_path, metadata=_load_prompt_metadata(prompt_path))
    return prompt_paths


def _iter_prompt_template_layers(prompts_root: Path, requested_locale: str) -> list[Path]:
    prompt_layers: list[Path] = [prompts_root / PROMPT_FALLBACK_LOCALE]
    if requested_locale != PROMPT_FALLBACK_LOCALE:
        prompt_layers.append(prompts_root / requested_locale)
    return prompt_layers


def _iter_locale_candidates(requested_locale: str) -> list[str]:
    locale_candidates: list[str] = [requested_locale]
    if requested_locale != PROMPT_FALLBACK_LOCALE:
        locale_candidates.append(PROMPT_FALLBACK_LOCALE)
    return locale_candidates


def _iter_prompt_path_candidates(base_dir: Path, name: str, category: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    for suffix in PROMPT_EXTENSIONS:
        if category is not None:
            candidates.append((base_dir / category / f"{name}{suffix}").resolve())
        candidates.append((base_dir / f"{name}{suffix}").resolve())
    return candidates


def _resolve_custom_prompt_path(
    name: str,
    locale: str,
    category: str | None,
    custom_prompts_root: Path,
) -> Path | None:
    custom_locale_dir = custom_prompts_root / locale
    for candidate_path in _iter_prompt_path_candidates(custom_locale_dir, name, category):
        if candidate_path.is_file():
            return candidate_path
    return None


def _resolve_legacy_custom_prompt_path(name: str, custom_prompts_root: Path) -> Path | None:
    for suffix in PROMPT_EXTENSIONS:
        candidate_path = (custom_prompts_root / f"{name}{suffix}").resolve()
        if candidate_path.is_file():
            return candidate_path
    return None


def list_prompt_templates(locale: str | None = None, prompts_root: Path | None = None) -> dict[str, PromptTemplateInfo]:
    resolved_prompts_root = get_prompts_root(prompts_root)
    requested_locale = normalize_locale(locale or get_locale())

    prompt_paths: dict[str, PromptTemplateInfo] = {}
    for directory in _iter_prompt_template_layers(resolved_prompts_root, requested_locale):
        prompt_paths.update(_scan_prompt_directory(directory, resolved_prompts_root))

    return prompt_paths


def resolve_prompt_path(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    include_legacy_custom: bool = False,
) -> Path:
    resolved_prompts_root = get_prompts_root(prompts_root)
    resolved_custom_prompts_root = get_custom_prompts_root(custom_prompts_root, prompts_root)
    normalized_name = normalize_prompt_name(name)
    normalized_category = normalize_prompt_category(category)
    requested_locale = normalize_locale(locale or get_locale())

    if normalized_category is not None:
        for locale_candidate in _iter_locale_candidates(requested_locale):
            custom_path = _resolve_custom_prompt_path(
                normalized_name,
                locale_candidate,
                normalized_category,
                resolved_custom_prompts_root,
            )
            if custom_path is not None:
                return custom_path

            if include_legacy_custom:
                legacy_custom_path = _resolve_legacy_custom_prompt_path(normalized_name, resolved_custom_prompts_root)
                if legacy_custom_path is not None:
                    return legacy_custom_path

            base_dir = resolved_prompts_root / locale_candidate
            for suffix in PROMPT_EXTENSIONS:
                candidate_path = (base_dir / normalized_category / f"{normalized_name}{suffix}").resolve()
                if candidate_path.is_file():
                    return candidate_path

                # 允许带 category 的调用继续复用 locale 根目录下的平铺模板。
                fallback_path = (base_dir / f"{normalized_name}{suffix}").resolve()
                if fallback_path.is_file():
                    return fallback_path
    else:
        for locale_candidate in _iter_locale_candidates(requested_locale):
            custom_path = _resolve_custom_prompt_path(
                normalized_name,
                locale_candidate,
                None,
                resolved_custom_prompts_root,
            )
            if custom_path is not None:
                return custom_path

            if include_legacy_custom:
                legacy_custom_path = _resolve_legacy_custom_prompt_path(normalized_name, resolved_custom_prompts_root)
                if legacy_custom_path is not None:
                    return legacy_custom_path

            base_dir = resolved_prompts_root / locale_candidate
            for candidate_path in _iter_prompt_path_candidates(base_dir, normalized_name):
                if candidate_path.is_file():
                    return candidate_path

    raise FileNotFoundError(t("prompt.template_not_found", locale=requested_locale, name=normalized_name))


@lru_cache(maxsize=None)
def _read_prompt_template(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8")


def _get_locale_from_prompt_path(prompt_path: Path, root: Path) -> str | None:
    try:
        relative_path = prompt_path.resolve().relative_to(root.resolve())
    except ValueError:
        return None

    return relative_path.parts[0] if len(relative_path.parts) > 1 else None


def load_prompt_template(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    include_legacy_custom: bool = False,
) -> ResolvedPromptTemplate:
    resolved_prompts_root = get_prompts_root(prompts_root)
    resolved_custom_prompts_root = get_custom_prompts_root(custom_prompts_root, prompts_root)
    normalized_name = normalize_prompt_name(name)
    prompt_path = resolve_prompt_path(
        name=normalized_name,
        locale=locale,
        category=category,
        prompts_root=resolved_prompts_root,
        custom_prompts_root=resolved_custom_prompts_root,
        include_legacy_custom=include_legacy_custom,
    )
    prompt_locale = _get_locale_from_prompt_path(prompt_path, resolved_prompts_root)
    customized = False
    if prompt_locale is None:
        prompt_locale = _get_locale_from_prompt_path(prompt_path, resolved_custom_prompts_root)
        customized = prompt_path.is_relative_to(resolved_custom_prompts_root)
    return ResolvedPromptTemplate(
        path=prompt_path,
        template=_read_prompt_template(prompt_path),
        customized=customized,
        locale=prompt_locale,
    )


def _format_prompt_template(name: str, template: str, **kwargs: object) -> str:
    if not kwargs:
        return template

    try:
        return template.format(**kwargs)
    except KeyError as exc:
        missing_placeholder = exc.args[0]
        error = KeyError(t("prompt.missing_placeholder", name=name, placeholder=missing_placeholder))
        if is_strict_prompt_i18n_mode():
            raise error from exc
        logger.error(f"{error}")
        return template
    except Exception as exc:
        logger.error(t("prompt.format_failed", name=name, error=exc))
        if is_strict_prompt_i18n_mode():
            raise
        return template


def load_prompt(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    **kwargs: object,
) -> str:
    normalized_name = normalize_prompt_name(name)
    resolved_template = load_prompt_template(
        name=normalized_name,
        locale=locale,
        category=category,
        prompts_root=prompts_root,
        custom_prompts_root=custom_prompts_root,
    )
    return _format_prompt_template(normalized_name, resolved_template.template, **kwargs)


def clear_prompt_cache() -> None:
    global _PROMPT_CACHE_REVISION
    _PROMPT_CACHE_REVISION += 1
    _read_prompt_template.cache_clear()


def get_prompt_cache_revision() -> int:
    return _PROMPT_CACHE_REVISION

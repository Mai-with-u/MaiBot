from __future__ import annotations

from pathlib import Path
from string import Formatter

import json
import locale

from .exceptions import (
    DuplicateTranslationKeyError,
    InvalidLocaleError,
    InvalidTranslationFileError,
    LocaleNotFoundError,
)

_FORMATTER = Formatter()
_FALLBACK_DEFAULT_LOCALE = "zh-CN"
PLURAL_CATEGORIES = {"zero", "one", "two", "few", "many", "other"}
TranslationValue = str | dict[str, str]


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def get_locales_root(locales_root: Path | None = None) -> Path:
    if locales_root is not None:
        return locales_root.resolve()
    return (get_project_root() / "locales").resolve()


def normalize_locale(locale: str) -> str:
    cleaned_locale = locale.strip().replace("_", "-")
    if not cleaned_locale:
        raise InvalidLocaleError("Locale 不能为空")

    parts = [part for part in cleaned_locale.split("-") if part]
    if not parts:
        raise InvalidLocaleError(f"Locale 非法: {locale}")

    normalized_parts: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            normalized_parts.append(part.lower())
        elif len(part) == 2:
            normalized_parts.append(part.upper())
        elif len(part) == 4:
            normalized_parts.append(part.title())
        else:
            normalized_parts.append(part)
    return "-".join(normalized_parts)


def _detect_default_locale() -> str:
    try:
        system_locale, _encoding = locale.getlocale()
    except (TypeError, ValueError, locale.Error):
        system_locale = None

    if system_locale:
        try:
            normalized_locale = normalize_locale(system_locale)
        except InvalidLocaleError:
            normalized_locale = ""

        if normalized_locale and (get_locales_root() / normalized_locale).is_dir():
            return normalized_locale

    return _FALLBACK_DEFAULT_LOCALE


DEFAULT_LOCALE = _detect_default_locale()


def to_babel_locale(locale: str) -> str:
    return normalize_locale(locale).replace("-", "_")


def discover_locales(locales_root: Path | None = None) -> list[str]:
    root = get_locales_root(locales_root)
    if not root.exists():
        return []

    locale_names = [path.name for path in root.iterdir() if path.is_dir()]
    return sorted(locale_names)


def iter_locale_files(locale_dir: Path) -> list[Path]:
    return sorted(path for path in locale_dir.glob("*.json") if path.is_file())


def validate_translation_value(key: str, value: object, file_path: Path) -> TranslationValue:
    if isinstance(value, str):
        return value

    if not isinstance(value, dict):
        raise InvalidTranslationFileError(f"{file_path} 中的 key '{key}' 必须是字符串或 plural 对象")

    if not value:
        raise InvalidTranslationFileError(f"{file_path} 中的 key '{key}' 不能为空对象")

    validated_value: dict[str, str] = {}
    for category, category_value in value.items():
        if category not in PLURAL_CATEGORIES:
            raise InvalidTranslationFileError(f"{file_path} 中的 key '{key}' 使用了非法 plural category: '{category}'")
        if not isinstance(category_value, str):
            raise InvalidTranslationFileError(
                f"{file_path} 中的 key '{key}' 的 plural category '{category}' 必须是字符串"
            )
        validated_value[category] = category_value
    return validated_value


def load_translation_file(file_path: Path) -> dict[str, TranslationValue]:
    try:
        raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidTranslationFileError(f"{file_path} 不是合法 JSON: {exc}") from exc

    if not isinstance(raw_payload, dict):
        raise InvalidTranslationFileError(f"{file_path} 顶层必须是 JSON object")

    translations: dict[str, TranslationValue] = {}
    for raw_key, raw_value in raw_payload.items():
        if not isinstance(raw_key, str):
            raise InvalidTranslationFileError(f"{file_path} 中存在非字符串 key")
        if not raw_key.strip():
            raise InvalidTranslationFileError(f"{file_path} 中存在空字符串 key")
        translations[raw_key] = validate_translation_value(raw_key, raw_value, file_path)
    return translations


def load_locale_catalog(locale: str, locales_root: Path | None = None) -> dict[str, TranslationValue]:
    normalized_locale = normalize_locale(locale)
    locale_dir = get_locales_root(locales_root) / normalized_locale
    if not locale_dir.exists():
        raise LocaleNotFoundError(f"未找到 locale 目录: {locale_dir}")

    merged_translations: dict[str, TranslationValue] = {}
    for file_path in iter_locale_files(locale_dir):
        file_translations = load_translation_file(file_path)
        for key, value in file_translations.items():
            if key in merged_translations:
                raise DuplicateTranslationKeyError(
                    f"locale '{normalized_locale}' 中存在重复 key: '{key}'，冲突文件包含 {file_path.name}"
                )
            merged_translations[key] = value
    return merged_translations


def extract_placeholders(template: str) -> set[str]:
    placeholders: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(template):
        if not field_name:
            continue
        placeholders.add(field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0])
    return placeholders


def format_template(template: str, **kwargs: object) -> str:
    return template.format(**kwargs)

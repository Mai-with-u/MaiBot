"""
配置管理API路由
"""

from pathlib import Path
from typing import Annotated, Any, Dict, List, Tuple, Union, get_args, get_origin
import asyncio
import copy
import json
import os
import re
import shutil
import time
import types

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
import tomlkit

from src.common.logger import get_logger
from src.common.prompt_i18n import clear_prompt_cache, extract_prompt_placeholders, list_prompt_templates
from src.common.paths import resource_path
from src.config.config import CONFIG_DIR, MODEL_CONFIG_VERSION, Config, ModelConfig, PROJECT_ROOT, config_manager
from src.config.config_base import AttributeData, ConfigBase
from src.config.config_upgrade_hooks import apply_config_upgrade_hooks
from src.config.model_configs import (
    APIProvider,
    ModelInfo,
    ModelTaskConfig,
)
from src.config.official_configs import (
    AMemorixConfig,
    BotConfig,
    ChatConfig,
    ChineseTypoConfig,
    DatabaseConfig,
    DebugConfig,
    EmojiConfig,
    ExpressionConfig,
    JargonConfig,
    KeywordReactionConfig,
    LogConfig,
    MaimMessageConfig,
    MCPConfig,
    MessageReceiveConfig,
    PersonalityConfig,
    PluginConfig,
    PluginRuntimeConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    VisualConfig,
    VoiceConfig,
    WebUIConfig,
)
from src.webui.config_schema import ConfigSchemaGenerator
from src.webui.dependencies import require_auth
from src.webui.utils.toml_utils import _update_toml_doc, save_toml_with_format

logger = get_logger("webui")

# 模块级别的类型别名（解决 B008 ruff 错误）
ConfigBody = Annotated[Dict[str, Any], Body()]
SectionBody = Annotated[Any, Body()]
RawContentBody = Annotated[str, Body(embed=True)]
PathBody = Annotated[Dict[str, str], Body()]

router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_auth)])
compat_router = APIRouter(prefix="/api/config", tags=["config-compat"], dependencies=[Depends(require_auth)])

PROMPTS_DIR = resource_path("prompts")
CUSTOM_PROMPTS_DIR = PROJECT_ROOT / "data" / "custom_prompts"
MAISAKA_PROMPT_PREVIEW_DIR = (PROJECT_ROOT / "logs" / "maisaka_prompt").resolve()
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_VERSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEGACY_CUSTOM_PROMPT_VERSION_ID = "legacy-current"
_MODEL_CONFIG_VERSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


async def _reload_model_config_after_save() -> None:
    """将刚保存的模型配置同步到运行时。"""

    if await config_manager.reload_config(changed_scopes=["model"]):
        return
    raise HTTPException(status_code=500, detail="模型配置已保存，但运行时热重载失败，请检查日志")


class PromptFileInfo(BaseModel):
    """Prompt 文件信息。"""

    name: str = Field(..., description="Prompt 文件名")
    size: int = Field(..., description="文件大小")
    modified_at: float = Field(..., description="最后修改时间戳")
    display_name: str = Field(default="", description="Prompt 展示名称")
    advanced: bool = Field(default=False, description="是否为高级 Prompt")
    description: str = Field(default="", description="Prompt 描述")
    customized: bool = Field(default=False, description="是否存在用户自定义覆盖")
    custom_version_count: int = Field(default=0, description="用户自定义版本数量")


class PromptValidationResult(BaseModel):
    """Prompt 参数校验结果。"""

    valid: bool = True
    missing_placeholders: List[str] = Field(default_factory=list)
    extra_placeholders: List[str] = Field(default_factory=list)
    message: str = ""


class PromptVersionInfo(BaseModel):
    """Prompt 自定义版本信息。"""

    id: str
    label: str
    created_at: float
    modified_at: float
    size: int
    active: bool = False


class PromptCatalogResponse(BaseModel):
    """Prompt 目录响应。"""

    success: bool = True
    languages: List[str]
    files: Dict[str, List[PromptFileInfo]]


class PromptFileResponse(BaseModel):
    """Prompt 文件内容响应。"""

    success: bool = True
    language: str
    filename: str
    content: str
    customized: bool = False
    active_version_id: str | None = None
    versions: List[PromptVersionInfo] = Field(default_factory=list)
    validation: PromptValidationResult = Field(default_factory=PromptValidationResult)


class PromptVersionFileResponse(PromptFileResponse):
    """Prompt 自定义版本内容响应。"""

    version_id: str


class PromptVersionListResponse(BaseModel):
    """Prompt 自定义版本列表响应。"""

    success: bool = True
    language: str
    filename: str
    active_version_id: str | None = None
    versions: List[PromptVersionInfo] = Field(default_factory=list)


class PromptUpdateRequest(BaseModel):
    """Prompt 保存请求。"""

    content: str
    version_id: str | None = None
    label: str = ""
    create_version: bool = False


class ModelConfigVersionInfo(BaseModel):
    """模型配置文件副本信息。"""

    id: str
    label: str
    created_at: float
    modified_at: float
    size: int
    active: bool = False
    inner_config_version: str | None = None
    valid: bool = True
    error: str | None = None


class ModelConfigVersionListResponse(BaseModel):
    """模型配置文件副本列表响应。"""

    success: bool = True
    active_version: ModelConfigVersionInfo
    versions: List[ModelConfigVersionInfo] = Field(default_factory=list)


class ModelConfigVersionCreateRequest(BaseModel):
    """模型配置文件副本创建请求。"""

    label: str = Field(default="", max_length=80, description="副本展示名称")


class ModelConfigVersionUpdateRequest(BaseModel):
    """模型配置文件副本更新请求。"""

    label: str = Field(..., min_length=1, max_length=80, description="副本展示名称")


class ModelConfigVersionSwitchRequest(BaseModel):
    """模型配置文件副本切换请求。"""

    archive_current: bool = Field(default=True, description="切换前是否归档当前启用配置")
    archive_label: str = Field(default="", max_length=80, description="当前配置归档副本名")


class ModelConfigVersionResponse(BaseModel):
    """单个模型配置文件副本操作响应。"""

    success: bool = True
    version: ModelConfigVersionInfo
    message: str = ""


def _get_cached_schema(cache_key: str, config_class: type[ConfigBase], include_nested: bool = True) -> Dict[str, Any]:
    schema = _SCHEMA_CACHE.get(cache_key)
    if schema is None:
        schema = ConfigSchemaGenerator.generate_config_schema(config_class, include_nested=include_nested)
        _SCHEMA_CACHE[cache_key] = schema
    return copy.deepcopy(schema)


def _safe_prompt_path(language: str, filename: str) -> Path:
    """校验并解析 prompts 下的文件路径。"""

    normalized_language = language.strip()
    normalized_filename = filename.strip()

    if not normalized_language or any(part in normalized_language for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="无效的 Prompt 语言目录")
    if not normalized_filename.endswith(".prompt") or any(part in normalized_filename for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="无效的 Prompt 文件名")

    prompt_path = (PROMPTS_DIR / normalized_language / normalized_filename).resolve()
    prompts_root = PROMPTS_DIR.resolve()
    try:
        prompt_path.relative_to(prompts_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Prompt 路径越界") from exc
    return prompt_path


def _safe_custom_prompt_path(language: str, filename: str) -> Path:
    """校验并解析 data/custom_prompts 下的用户覆盖文件路径。"""

    normalized_language = language.strip()
    normalized_filename = filename.strip()

    if not normalized_language or any(part in normalized_language for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="无效的 Prompt 语言目录")
    if not normalized_filename.endswith(".prompt") or any(part in normalized_filename for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="无效的 Prompt 文件名")

    prompt_path = (CUSTOM_PROMPTS_DIR / normalized_language / normalized_filename).resolve()
    custom_prompts_root = CUSTOM_PROMPTS_DIR.resolve()
    try:
        prompt_path.relative_to(custom_prompts_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Prompt 路径越界") from exc
    return prompt_path


def _safe_prompt_version_id(version_id: str) -> str:
    """校验 Prompt 自定义版本 ID。"""

    normalized_version_id = version_id.strip()
    if (
        not normalized_version_id
        or normalized_version_id in {".", ".."}
        or not _PROMPT_VERSION_ID_PATTERN.fullmatch(normalized_version_id)
    ):
        raise HTTPException(status_code=400, detail="无效的 Prompt 版本 ID")
    return normalized_version_id


def _safe_custom_prompt_versions_dir(language: str, filename: str) -> Path:
    """解析指定 Prompt 的自定义版本目录。"""

    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    versions_dir = custom_prompt_path.parent / ".versions" / custom_prompt_path.stem
    custom_prompts_root = CUSTOM_PROMPTS_DIR.resolve()
    resolved_versions_dir = versions_dir.resolve()
    try:
        resolved_versions_dir.relative_to(custom_prompts_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Prompt 版本路径越界") from exc
    return resolved_versions_dir


def _prompt_version_manifest_path(language: str, filename: str) -> Path:
    return _safe_custom_prompt_versions_dir(language, filename) / "manifest.json"


def _prompt_version_file_path(language: str, filename: str, version_id: str) -> Path:
    normalized_version_id = _safe_prompt_version_id(version_id)
    version_path = _safe_custom_prompt_versions_dir(language, filename) / f"{normalized_version_id}.prompt"
    versions_dir = _safe_custom_prompt_versions_dir(language, filename).resolve()
    resolved_version_path = version_path.resolve()
    try:
        resolved_version_path.relative_to(versions_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Prompt 版本路径越界") from exc
    return resolved_version_path


def _read_prompt_version_manifest(language: str, filename: str) -> Dict[str, Any]:
    manifest_path = _prompt_version_manifest_path(language, filename)
    if not manifest_path.exists():
        return {"active_version_id": None, "versions": []}

    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Prompt 版本清单损坏: {manifest_path}") from exc

    if not isinstance(raw_manifest, dict):
        raise HTTPException(status_code=500, detail=f"Prompt 版本清单格式错误: {manifest_path}")

    versions = raw_manifest.get("versions", [])
    if not isinstance(versions, list):
        versions = []

    return {
        "active_version_id": raw_manifest.get("active_version_id")
        if isinstance(raw_manifest.get("active_version_id"), str)
        else None,
        "versions": [version for version in versions if isinstance(version, dict)],
    }


def _write_prompt_version_manifest(language: str, filename: str, manifest: Dict[str, Any]) -> None:
    manifest_path = _prompt_version_manifest_path(language, filename)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _active_model_config_path() -> Path:
    """返回当前启用的模型配置文件路径。"""

    return (Path(CONFIG_DIR) / "model_config.toml").resolve()


def _model_config_versions_dir() -> Path:
    """返回模型配置文件副本目录。"""

    return (Path(CONFIG_DIR) / "versions" / "model").resolve()


def _model_config_version_manifest_path() -> Path:
    return _model_config_versions_dir() / "manifest.json"


def _safe_model_config_version_id(version_id: str) -> str:
    """校验模型配置文件副本 ID，避免目录穿越。"""

    normalized_version_id = version_id.strip()
    if (
        not normalized_version_id
        or normalized_version_id in {".", "..", "active"}
        or not _MODEL_CONFIG_VERSION_ID_PATTERN.fullmatch(normalized_version_id)
    ):
        raise HTTPException(status_code=400, detail="无效的模型配置副本 ID")
    return normalized_version_id


def _model_config_version_path(version_id: str) -> Path:
    normalized_version_id = _safe_model_config_version_id(version_id)
    versions_dir = _model_config_versions_dir()
    version_path = (versions_dir / f"{normalized_version_id}.toml").resolve()
    try:
        version_path.relative_to(versions_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="模型配置副本路径越界") from exc
    return version_path


def _read_model_version_manifest() -> Dict[str, Any]:
    manifest_path = _model_config_version_manifest_path()
    if not manifest_path.exists():
        return {"active_label": "默认配置", "versions": []}

    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"模型配置副本清单损坏: {manifest_path}") from exc

    if not isinstance(raw_manifest, dict):
        raise HTTPException(status_code=500, detail=f"模型配置副本清单格式错误: {manifest_path}")

    versions = raw_manifest.get("versions", [])
    if not isinstance(versions, list):
        versions = []

    active_label = raw_manifest.get("active_label")
    return {
        "active_label": active_label.strip() if isinstance(active_label, str) and active_label.strip() else "默认配置",
        "versions": [version for version in versions if isinstance(version, dict)],
    }


def _write_model_version_manifest(manifest: Dict[str, Any]) -> None:
    manifest_path = _model_config_version_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(manifest.get("active_label"), str) or not manifest["active_label"].strip():
        manifest["active_label"] = "默认配置"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _get_active_model_config_label() -> str:
    manifest = _read_model_version_manifest()
    active_label = manifest.get("active_label")
    return active_label.strip() if isinstance(active_label, str) and active_label.strip() else "默认配置"


def _set_active_model_config_label(label: str) -> None:
    manifest = _read_model_version_manifest()
    manifest["active_label"] = label.strip() or "默认配置"
    _write_model_version_manifest(manifest)


def _model_version_metadata_by_id() -> Dict[str, Dict[str, Any]]:
    return {
        str(version.get("id")): version
        for version in _read_model_version_manifest().get("versions", [])
        if isinstance(version.get("id"), str)
    }


def _upsert_model_version_metadata(version_id: str, label: str, created_at: float | None = None) -> None:
    manifest = _read_model_version_manifest()
    versions = manifest.get("versions", [])
    now = time.time()
    normalized_label = label.strip() or _default_model_config_version_label()
    found = False

    for version in versions:
        if version.get("id") != version_id:
            continue
        version["label"] = normalized_label
        version["created_at"] = float(version.get("created_at") or created_at or now)
        found = True
        break

    if not found:
        versions.append(
            {
                "id": version_id,
                "label": normalized_label,
                "created_at": float(created_at or now),
            }
        )

    manifest["versions"] = versions
    _write_model_version_manifest(manifest)


def _remove_model_version_metadata(version_id: str) -> None:
    manifest = _read_model_version_manifest()
    manifest["versions"] = [
        version for version in manifest.get("versions", []) if version.get("id") != version_id
    ]
    _write_model_version_manifest(manifest)


def _create_model_config_version_id() -> str:
    base_version_id = time.strftime("v%Y%m%d%H%M%S")
    version_id = base_version_id
    suffix = 2
    while _model_config_version_path(version_id).exists():
        version_id = f"{base_version_id}-{suffix}"
        suffix += 1
    return version_id


def _default_model_config_version_label() -> str:
    return f"模型配置副本 {time.strftime('%Y-%m-%d %H:%M:%S')}"


def _default_model_config_archive_label() -> str:
    return _get_active_model_config_label()


def _read_model_config_version_inner_version(config_path: Path) -> str | None:
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config_data = tomlkit.load(handle)
    except Exception:
        return None

    inner_table = config_data.get("inner")
    if not isinstance(inner_table, dict):
        return None
    inner_version = inner_table.get("version")
    return inner_version if isinstance(inner_version, str) else None


def _validate_model_config_file(config_path: Path) -> None:
    """校验指定 TOML 文件可作为模型配置加载。"""

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config_data = tomlkit.load(handle)
        plain_config_data = _coerce_config_numeric_values(_toml_to_plain_dict(config_data), ModelConfig)
        # 旧副本先按同一升级钩子校验；切换后的实际写回由配置加载链路完成。
        plain_config_data = apply_config_upgrade_hooks(
            plain_config_data, "model_config.toml", plain_config_data["inner"]["version"], MODEL_CONFIG_VERSION
        ).data
        ModelConfig.from_dict(AttributeData(), copy.deepcopy(plain_config_data))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"模型配置副本无效: {str(exc)}") from exc


def _build_model_config_version_info(
    *,
    version_id: str,
    label: str,
    config_path: Path,
    created_at: float | None = None,
    active: bool = False,
) -> ModelConfigVersionInfo:
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="模型配置副本文件不存在")

    stat = config_path.stat()
    valid = True
    error: str | None = None
    try:
        _validate_model_config_file(config_path)
    except HTTPException as exc:
        valid = False
        error = str(exc.detail)

    return ModelConfigVersionInfo(
        id=version_id,
        label=label,
        created_at=float(created_at or stat.st_mtime),
        modified_at=stat.st_mtime,
        size=stat.st_size,
        active=active,
        inner_config_version=_read_model_config_version_inner_version(config_path),
        valid=valid,
        error=error,
    )


def _list_model_config_versions() -> List[ModelConfigVersionInfo]:
    versions_dir = _model_config_versions_dir()
    metadata_by_id = _model_version_metadata_by_id()
    if not versions_dir.exists():
        return []

    versions: List[ModelConfigVersionInfo] = []
    for version_path in sorted(versions_dir.glob("*.toml"), key=lambda path: path.stat().st_mtime, reverse=True):
        version_id = version_path.stem
        metadata = metadata_by_id.get(version_id, {})
        versions.append(
            _build_model_config_version_info(
                version_id=version_id,
                label=str(metadata.get("label") or version_id),
                config_path=version_path,
                created_at=metadata.get("created_at") if isinstance(metadata.get("created_at"), (int, float)) else None,
            )
        )

    return versions


def _copy_model_config_to_version(source_path: Path, label: str) -> ModelConfigVersionInfo:
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="当前模型配置文件不存在")

    _validate_model_config_file(source_path)
    versions_dir = _model_config_versions_dir()
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_id = _create_model_config_version_id()
    version_path = _model_config_version_path(version_id)
    shutil.copy2(source_path, version_path)
    created_at = time.time()
    _upsert_model_version_metadata(version_id, label, created_at)
    return _build_model_config_version_info(
        version_id=version_id,
        label=label.strip() or _default_model_config_version_label(),
        config_path=version_path,
        created_at=created_at,
    )


def _create_prompt_version_id(language: str, filename: str) -> str:
    base_version_id = time.strftime("v%Y%m%d%H%M%S")
    version_id = base_version_id
    suffix = 2
    while _prompt_version_file_path(language, filename, version_id).exists():
        version_id = f"{base_version_id}-{suffix}"
        suffix += 1
    return version_id


def _default_prompt_version_label(filename: str) -> str:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    prompt_name = filename.removesuffix(".prompt")
    return f"{prompt_name} 自定义版本 {timestamp}"


def _normalize_prompt_version_label(filename: str, label: str) -> str:
    normalized_label = label.strip()
    return normalized_label or _default_prompt_version_label(filename)


def _build_prompt_validation(default_content: str, custom_content: str) -> PromptValidationResult:
    try:
        default_placeholders = extract_prompt_placeholders(default_content)
        custom_placeholders = extract_prompt_placeholders(custom_content)
    except ValueError as exc:
        return PromptValidationResult(valid=False, message=f"Prompt 参数格式错误: {exc}")

    missing_placeholders = sorted(default_placeholders - custom_placeholders)
    extra_placeholders = sorted(custom_placeholders - default_placeholders)
    if not missing_placeholders and not extra_placeholders:
        return PromptValidationResult()

    message_parts: List[str] = []
    if missing_placeholders:
        message_parts.append(f"缺少参数: {', '.join(missing_placeholders)}")
    if extra_placeholders:
        message_parts.append(f"多余参数: {', '.join(extra_placeholders)}")
    return PromptValidationResult(
        valid=False,
        missing_placeholders=missing_placeholders,
        extra_placeholders=extra_placeholders,
        message="自定义 Prompt 参数必须与默认 Prompt 完全一致，" + "；".join(message_parts),
    )


def _ensure_prompt_parameters_match(prompt_path: Path, custom_content: str) -> PromptValidationResult:
    default_content = prompt_path.read_text(encoding="utf-8")
    validation = _build_prompt_validation(default_content, custom_content)
    if not validation.valid:
        raise HTTPException(status_code=400, detail=validation.message)
    return validation


def _list_prompt_versions(language: str, filename: str) -> List[PromptVersionInfo]:
    manifest = _read_prompt_version_manifest(language, filename)
    active_version_id = manifest.get("active_version_id")
    versions: List[PromptVersionInfo] = []
    for raw_version in manifest["versions"]:
        version_id = raw_version.get("id")
        if not isinstance(version_id, str):
            continue
        version_path = _prompt_version_file_path(language, filename, version_id)
        if not version_path.exists():
            continue
        stat = version_path.stat()
        versions.append(
            PromptVersionInfo(
                id=version_id,
                label=raw_version.get("label") if isinstance(raw_version.get("label"), str) else version_id,
                created_at=float(raw_version.get("created_at") or stat.st_ctime),
                modified_at=stat.st_mtime,
                size=stat.st_size,
                active=version_id == active_version_id,
            )
        )

    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if custom_prompt_path.exists() and not active_version_id and not versions:
        stat = custom_prompt_path.stat()
        versions.append(
            PromptVersionInfo(
                id=_LEGACY_CUSTOM_PROMPT_VERSION_ID,
                label="当前自定义（旧格式）",
                created_at=stat.st_ctime,
                modified_at=stat.st_mtime,
                size=stat.st_size,
                active=True,
            )
        )
    return sorted(versions, key=lambda version: version.modified_at, reverse=True)


def _get_active_prompt_version_id(language: str, filename: str) -> str | None:
    manifest = _read_prompt_version_manifest(language, filename)
    active_version_id = manifest.get("active_version_id")
    if isinstance(active_version_id, str):
        return active_version_id
    if _safe_custom_prompt_path(language, filename).exists():
        return _LEGACY_CUSTOM_PROMPT_VERSION_ID
    return None


def _save_prompt_version(
    language: str,
    filename: str,
    content: str,
    version_id: str | None,
    label: str,
    create_version: bool,
) -> str:
    manifest = _read_prompt_version_manifest(language, filename)
    versions = manifest["versions"]
    existing_version_ids = {
        raw_version["id"] for raw_version in versions if isinstance(raw_version.get("id"), str)
    }

    normalized_version_id = version_id.strip() if isinstance(version_id, str) else ""
    if normalized_version_id:
        _safe_prompt_version_id(normalized_version_id)
    if (
        normalized_version_id
        and not create_version
        and normalized_version_id != _LEGACY_CUSTOM_PROMPT_VERSION_ID
        and normalized_version_id not in existing_version_ids
    ):
        raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")

    should_create_version = (
        create_version
        or not normalized_version_id
        or normalized_version_id == _LEGACY_CUSTOM_PROMPT_VERSION_ID
    )
    if should_create_version:
        normalized_version_id = _create_prompt_version_id(language, filename)
        now = time.time()
        versions.append(
            {
                "id": normalized_version_id,
                "label": _normalize_prompt_version_label(filename, label),
                "created_at": now,
            }
        )
    version_path = _prompt_version_file_path(language, filename, normalized_version_id)
    version_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text(content, encoding="utf-8", newline="\n")

    for raw_version in versions:
        if raw_version.get("id") != normalized_version_id:
            continue
        raw_version["label"] = _normalize_prompt_version_label(
            filename,
            label if label.strip() else str(raw_version.get("label") or ""),
        )
        raw_version["modified_at"] = time.time()
        break

    manifest["active_version_id"] = normalized_version_id
    manifest["versions"] = versions
    _write_prompt_version_manifest(language, filename, manifest)
    return normalized_version_id


def _set_active_prompt_version(language: str, filename: str, version_id: str | None) -> None:
    manifest = _read_prompt_version_manifest(language, filename)
    manifest["active_version_id"] = version_id
    _write_prompt_version_manifest(language, filename, manifest)


def _safe_maisaka_prompt_preview_path(relative_path: str) -> Path:
    """校验并解析 MaiSaka Prompt 预览路径。"""

    normalized_path = relative_path.strip().replace("\\", "/")
    if not normalized_path or normalized_path.startswith("/") or ".." in Path(normalized_path).parts:
        raise HTTPException(status_code=400, detail="无效的 Prompt 预览路径")

    preview_path = (MAISAKA_PROMPT_PREVIEW_DIR / normalized_path).resolve()
    try:
        preview_path.relative_to(MAISAKA_PROMPT_PREVIEW_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Prompt 预览路径越界") from exc

    if preview_path.suffix.lower() not in {".html", ".json", ".txt"}:
        raise HTTPException(status_code=400, detail="只允许打开 Prompt 预览文件")
    return preview_path


def _toml_to_plain_dict(obj: Any) -> Any:
    """递归转换 tomlkit 文档/Table 为纯 Python 字典，避免 from_dict 触发 tomlkit __setitem__"""
    if isinstance(obj, dict):
        return {str(k): _toml_to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_toml_to_plain_dict(v) for v in obj]
    return obj


def _coerce_numeric_value(value: Any, target_type: Any) -> Any:
    """根据配置字段类型，把旧 WebUI 可能写入的数字字符串还原为数字。"""
    if target_type is str:
        if isinstance(value, (int, float)):
            return str(value)
        return value

    if target_type is int:
        if isinstance(value, str):
            try:
                parsed_value = float(value.strip())
            except ValueError:
                return value
            if parsed_value.is_integer():
                return int(parsed_value)
        return value

    if target_type is float:
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return value
        return value

    return value


def _coerce_value_by_annotation(value: Any, annotation: Any) -> Any:
    """递归按 ConfigBase 字段注解修正数据类型，避免保存时把数字写成字符串。"""
    value = _coerce_numeric_value(value, annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in {Union, types.UnionType}:
        for candidate_type in args:
            if candidate_type is type(None):
                continue
            coerced_value = _coerce_value_by_annotation(value, candidate_type)
            if coerced_value != value or type(coerced_value) is not type(value):
                return coerced_value
        return value

    if origin in {list, List} and isinstance(value, list) and args:
        item_type = args[0]
        return [_coerce_value_by_annotation(item, item_type) for item in value]

    if origin in {dict, Dict} and isinstance(value, dict) and len(args) >= 2:
        value_type = args[1]
        return {key: _coerce_value_by_annotation(item, value_type) for key, item in value.items()}

    if isinstance(value, dict) and isinstance(annotation, type) and issubclass(annotation, ConfigBase):
        return _coerce_config_numeric_values(value, annotation)

    return value


def _coerce_config_numeric_values(data: Dict[str, Any], config_type: type[ConfigBase]) -> Dict[str, Any]:
    """按配置类 schema 统一修正所有数字字段类型。"""
    for field_name, field_info in config_type.model_fields.items():
        if field_name in data:
            data[field_name] = _coerce_value_by_annotation(data[field_name], field_info.annotation)
    return data


def _collect_orphaned_model_api_providers(config_data: Dict[str, Any]) -> Dict[str, str]:
    """收集引用了不存在 API Provider 的模型。"""
    providers = config_data.get("api_providers", [])
    provider_names = {provider.get("name") for provider in providers if isinstance(provider, dict)}
    orphaned_models: Dict[str, str] = {}

    for model in config_data.get("models", []):
        if not isinstance(model, dict):
            continue
        model_name = model.get("name")
        api_provider = model.get("api_provider")
        if model_name is None or not api_provider:
            continue
        if api_provider not in provider_names:
            orphaned_models[str(model_name)] = str(api_provider)

    return orphaned_models


def _validate_api_provider_section(section_data: Any) -> None:
    """只校验 api_providers 小节本身，避免历史坏模型引用阻断 Provider 修复。"""
    if not isinstance(section_data, list) or not section_data:
        raise HTTPException(status_code=400, detail="API 提供商列表不能为空")

    coerced_providers = [
        _coerce_config_numeric_values(copy.deepcopy(provider), APIProvider)
        for provider in section_data
        if isinstance(provider, dict)
    ]
    if len(coerced_providers) != len(section_data):
        raise HTTPException(status_code=400, detail="API 提供商配置格式无效")

    provider_names: List[str] = []
    try:
        for provider_data in coerced_providers:
            provider = APIProvider.from_dict(AttributeData(), provider_data)
            provider_names.append(provider.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"API 提供商配置验证失败: {str(exc)}") from exc

    if len(provider_names) != len(set(provider_names)):
        raise HTTPException(status_code=400, detail="API 提供商名称不能重复")


# ===== 架构获取接口 =====


@router.get("/prompts", response_model=PromptCatalogResponse)
def list_prompt_files():
    """列出 prompts 目录下的语言和 Prompt 文件。"""

    try:
        if not PROMPTS_DIR.exists():
            return PromptCatalogResponse(languages=[], files={})

        languages: List[str] = []
        files: Dict[str, List[PromptFileInfo]] = {}
        for language_dir in sorted(PROMPTS_DIR.iterdir(), key=lambda item: item.name):
            if not language_dir.is_dir():
                continue

            language = language_dir.name
            prompt_template_infos = list_prompt_templates(locale=language, prompts_root=PROMPTS_DIR)
            prompt_files: List[PromptFileInfo] = []
            for prompt_file in sorted(language_dir.glob("*.prompt"), key=lambda item: item.name):
                custom_prompt_file = _safe_custom_prompt_path(language, prompt_file.name)
                effective_prompt_file = custom_prompt_file if custom_prompt_file.exists() else prompt_file
                stat = effective_prompt_file.stat()
                template_info = prompt_template_infos.get(prompt_file.stem)
                metadata = template_info.metadata if template_info and template_info.path == prompt_file else None
                versions = _list_prompt_versions(language, prompt_file.name)
                prompt_files.append(
                    PromptFileInfo(
                        name=prompt_file.name,
                        size=stat.st_size,
                        modified_at=stat.st_mtime,
                        display_name=metadata.display_name if metadata else "",
                        advanced=metadata.advanced if metadata else False,
                        description=metadata.description if metadata else "",
                        customized=custom_prompt_file.exists(),
                        custom_version_count=len(versions),
                    )
                )

            languages.append(language)
            files[language] = prompt_files

        return PromptCatalogResponse(languages=languages, files=files)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"列出 Prompt 文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"列出 Prompt 文件失败: {str(e)}") from e


@router.get("/prompts/{language}/{filename}", response_model=PromptFileResponse)
def get_prompt_file(language: str, filename: str):
    """读取指定语言下的 Prompt 文件内容。"""

    prompt_path = _safe_prompt_path(language, filename)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    try:
        effective_prompt_path = custom_prompt_path if custom_prompt_path.exists() else prompt_path
        content = effective_prompt_path.read_text(encoding="utf-8")
        default_content = prompt_path.read_text(encoding="utf-8")
        validation = (
            _build_prompt_validation(default_content, content) if custom_prompt_path.exists() else PromptValidationResult()
        )
        return PromptFileResponse(
            language=language,
            filename=filename,
            content=content,
            customized=custom_prompt_path.exists(),
            active_version_id=_get_active_prompt_version_id(language, filename),
            versions=_list_prompt_versions(language, filename),
            validation=validation,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取 Prompt 文件失败: {prompt_path} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取 Prompt 文件失败: {str(e)}") from e


@router.get("/prompts/{language}/{filename}/default", response_model=PromptFileResponse)
def get_default_prompt_file(language: str, filename: str):
    """只读获取内置 Prompt 模板内容，不读取或修改用户自定义覆盖。"""

    prompt_path = _safe_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    try:
        content = prompt_path.read_text(encoding="utf-8")
        return PromptFileResponse(
            language=language,
            filename=filename,
            content=content,
            customized=False,
            active_version_id=_get_active_prompt_version_id(language, filename),
            versions=_list_prompt_versions(language, filename),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取默认 Prompt 文件失败: {prompt_path} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取默认 Prompt 文件失败: {str(e)}") from e


@router.get("/prompts/{language}/{filename}/versions", response_model=PromptVersionListResponse)
def list_prompt_versions(language: str, filename: str):
    """列出指定 Prompt 的自定义版本。"""

    prompt_path = _safe_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    return PromptVersionListResponse(
        language=language,
        filename=filename,
        active_version_id=_get_active_prompt_version_id(language, filename),
        versions=_list_prompt_versions(language, filename),
    )


@router.get("/prompts/{language}/{filename}/versions/{version_id}", response_model=PromptVersionFileResponse)
def get_prompt_version_file(language: str, filename: str, version_id: str):
    """读取指定 Prompt 自定义版本内容。"""

    prompt_path = _safe_prompt_path(language, filename)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    normalized_version_id = _safe_prompt_version_id(version_id)
    if normalized_version_id == _LEGACY_CUSTOM_PROMPT_VERSION_ID:
        if not custom_prompt_path.exists():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")
        content = custom_prompt_path.read_text(encoding="utf-8")
    else:
        version_path = _prompt_version_file_path(language, filename, normalized_version_id)
        if not version_path.exists() or not version_path.is_file():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")
        content = version_path.read_text(encoding="utf-8")

    validation = _build_prompt_validation(prompt_path.read_text(encoding="utf-8"), content)
    return PromptVersionFileResponse(
        language=language,
        filename=filename,
        version_id=normalized_version_id,
        content=content,
        customized=True,
        active_version_id=_get_active_prompt_version_id(language, filename),
        versions=_list_prompt_versions(language, filename),
        validation=validation,
    )


@router.post("/prompts/{language}/{filename}/versions/{version_id}/activate", response_model=PromptFileResponse)
def activate_prompt_version(language: str, filename: str, version_id: str):
    """启用指定 Prompt 自定义版本。"""

    prompt_path = _safe_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    normalized_version_id = _safe_prompt_version_id(version_id)
    if normalized_version_id == _LEGACY_CUSTOM_PROMPT_VERSION_ID:
        custom_prompt_path = _safe_custom_prompt_path(language, filename)
        if not custom_prompt_path.exists():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")
        content = custom_prompt_path.read_text(encoding="utf-8")
    else:
        version_path = _prompt_version_file_path(language, filename, normalized_version_id)
        if not version_path.exists() or not version_path.is_file():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")
        content = version_path.read_text(encoding="utf-8")

    validation = _ensure_prompt_parameters_match(prompt_path, content)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    custom_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    custom_prompt_path.write_text(content, encoding="utf-8", newline="\n")
    if normalized_version_id != _LEGACY_CUSTOM_PROMPT_VERSION_ID:
        _set_active_prompt_version(language, filename, normalized_version_id)
    clear_prompt_cache()
    return PromptFileResponse(
        language=language,
        filename=filename,
        content=content,
        customized=True,
        active_version_id=_get_active_prompt_version_id(language, filename),
        versions=_list_prompt_versions(language, filename),
        validation=validation,
    )


@router.delete("/prompts/{language}/{filename}/versions/{version_id}", response_model=PromptFileResponse)
def delete_prompt_version(language: str, filename: str, version_id: str):
    """删除指定 Prompt 自定义版本；删除当前启用版本时恢复默认 Prompt。"""

    prompt_path = _safe_prompt_path(language, filename)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    normalized_version_id = _safe_prompt_version_id(version_id)
    manifest = _read_prompt_version_manifest(language, filename)
    active_version_id = _get_active_prompt_version_id(language, filename)

    if normalized_version_id == _LEGACY_CUSTOM_PROMPT_VERSION_ID:
        if not custom_prompt_path.exists():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")
        custom_prompt_path.unlink()
        if active_version_id == normalized_version_id:
            manifest["active_version_id"] = None
            _write_prompt_version_manifest(language, filename, manifest)
            clear_prompt_cache()
    else:
        version_path = _prompt_version_file_path(language, filename, normalized_version_id)
        version_exists = any(
            raw_version.get("id") == normalized_version_id for raw_version in manifest["versions"]
        )
        if not version_exists or not version_path.exists() or not version_path.is_file():
            raise HTTPException(status_code=404, detail="Prompt 自定义版本不存在")

        version_path.unlink()
        manifest["versions"] = [
            raw_version
            for raw_version in manifest["versions"]
            if raw_version.get("id") != normalized_version_id
        ]
        if active_version_id == normalized_version_id:
            manifest["active_version_id"] = None
            if custom_prompt_path.exists():
                custom_prompt_path.unlink()
            clear_prompt_cache()
        _write_prompt_version_manifest(language, filename, manifest)

    if custom_prompt_path.exists():
        content = custom_prompt_path.read_text(encoding="utf-8")
        customized = True
    else:
        content = prompt_path.read_text(encoding="utf-8")
        customized = False
    validation = _build_prompt_validation(prompt_path.read_text(encoding="utf-8"), content)
    return PromptFileResponse(
        language=language,
        filename=filename,
        content=content,
        customized=customized,
        active_version_id=_get_active_prompt_version_id(language, filename),
        versions=_list_prompt_versions(language, filename),
        validation=validation,
    )


@router.put("/prompts/{language}/{filename}", response_model=PromptFileResponse)
def update_prompt_file(language: str, filename: str, request: PromptUpdateRequest):
    """更新指定语言下的 Prompt 文件内容。"""

    prompt_path = _safe_prompt_path(language, filename)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if not prompt_path.parent.exists() or not prompt_path.parent.is_dir():
        raise HTTPException(status_code=404, detail="Prompt 语言目录不存在")
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    try:
        validation = _ensure_prompt_parameters_match(prompt_path, request.content)
        custom_prompt_path.parent.mkdir(parents=True, exist_ok=True)
        active_version_id = _save_prompt_version(
            language=language,
            filename=filename,
            content=request.content,
            version_id=request.version_id,
            label=request.label,
            create_version=request.create_version,
        )
        custom_prompt_path.write_text(request.content, encoding="utf-8", newline="\n")
        clear_prompt_cache()
        return PromptFileResponse(
            language=language,
            filename=filename,
            content=request.content,
            customized=True,
            active_version_id=active_version_id,
            versions=_list_prompt_versions(language, filename),
            validation=validation,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存 Prompt 文件失败: {prompt_path} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存 Prompt 文件失败: {str(e)}") from e


@router.delete("/prompts/{language}/{filename}", response_model=PromptFileResponse)
def reset_prompt_file(language: str, filename: str):
    """删除用户自定义覆盖，恢复使用内置 Prompt 模板。"""

    prompt_path = _safe_prompt_path(language, filename)
    custom_prompt_path = _safe_custom_prompt_path(language, filename)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 文件不存在")

    try:
        if custom_prompt_path.exists():
            custom_prompt_path.unlink()
            _set_active_prompt_version(language, filename, None)
            clear_prompt_cache()
        content = prompt_path.read_text(encoding="utf-8")
        return PromptFileResponse(
            language=language,
            filename=filename,
            content=content,
            customized=False,
            active_version_id=None,
            versions=_list_prompt_versions(language, filename),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复 Prompt 默认模板失败: {prompt_path} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"恢复 Prompt 默认模板失败: {str(e)}") from e


@router.get("/maisaka-prompt-preview", response_class=FileResponse)
def get_maisaka_prompt_preview(path: str = Query(..., description="logs/maisaka_prompt 下的相对预览路径")):
    """打开 MaiSaka 监控中生成的 Prompt 预览。"""

    preview_path = _safe_maisaka_prompt_preview_path(path)
    if not preview_path.exists() or not preview_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt 预览文件不存在")
    media_type = {
        ".html": "text/html",
        ".json": "application/json",
        ".txt": "text/plain",
    }.get(preview_path.suffix.lower(), "application/octet-stream")
    return FileResponse(preview_path, media_type=media_type)


def _apply_retro_prompt_schema_lock(schema: Dict[str, Any]) -> None:
    """按当前模型状态锁定复古开关，不污染缓存的基础 schema。"""
    for field in schema["fields"]:
        if field["name"] == "replyer_retro_prompt":
            field["readOnly"] = config_manager.retro_prompt_forced


def _validate_retro_prompt_setting(config_data: Dict[str, Any]) -> None:
    """模型要求复古模板时，所有主配置写入入口均禁止手动关闭。"""
    if config_manager.retro_prompt_forced and config_data.get("debug", {}).get("replyer_retro_prompt") is False:
        raise HTTPException(status_code=400, detail="当前模型需要复古回复提示词，移除对应模型后才能关闭")


@router.get("/schema/bot")
def get_bot_config_schema():
    """获取麦麦主程序配置架构"""
    try:
        # Config 类包含所有子配置
        schema = copy.deepcopy(_get_cached_schema("bot", Config))
        _apply_retro_prompt_schema_lock(schema["nested"]["debug"])
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置架构失败: {str(e)}") from e


@compat_router.get("/schema")
def get_compat_bot_config_schema():
    """兼容旧版 /api/config/schema，返回主程序配置架构。"""
    return get_bot_config_schema()


@compat_router.get("/schema/bot")
def get_compat_bot_config_schema_alias():
    """兼容旧版 /api/config/schema/bot。"""
    return get_bot_config_schema()


@router.get("/schema/model")
def get_model_config_schema():
    """获取模型配置架构（包含提供商和模型任务配置）"""
    try:
        schema = _get_cached_schema("model", ModelConfig)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取模型配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取模型配置架构失败: {str(e)}") from e


# ===== 子配置架构获取接口 =====


@router.get("/schema/section/{section_name}")
def get_config_section_schema(section_name: str):
    """
    获取指定配置节的架构

    支持的section_name:
    - bot: BotConfig
    - personality: PersonalityConfig
    - chat: ChatConfig
    - visual: VisualConfig
    - message_receive: MessageReceiveConfig
    - emoji: EmojiConfig
    - expression: ExpressionConfig
    - jargon: JargonConfig
    - keyword_reaction: KeywordReactionConfig
    - chinese_typo: ChineseTypoConfig
    - response_post_process: ResponsePostProcessConfig
    - response_splitter: ResponseSplitterConfig
    - telemetry: TelemetryConfig
    - log: LogConfig
    - maim_message: MaimMessageConfig
    - webui: WebUIConfig
    - database: DatabaseConfig
    - mcp: MCPConfig
    - plugin: PluginConfig
    - plugin_runtime: PluginRuntimeConfig
    - a_memorix: AMemorixConfig
    - debug: DebugConfig
    - voice: VoiceConfig
    - model_task_config: ModelTaskConfig
    - api_provider: APIProvider
    - model_info: ModelInfo
    """
    section_map = {
        "bot": BotConfig,
        "personality": PersonalityConfig,
        "chat": ChatConfig,
        "visual": VisualConfig,
        "message_receive": MessageReceiveConfig,
        "emoji": EmojiConfig,
        "expression": ExpressionConfig,
        "jargon": JargonConfig,
        "keyword_reaction": KeywordReactionConfig,
        "chinese_typo": ChineseTypoConfig,
        "response_post_process": ResponsePostProcessConfig,
        "response_splitter": ResponseSplitterConfig,
        "telemetry": TelemetryConfig,
        "log": LogConfig,
        "maim_message": MaimMessageConfig,
        "webui": WebUIConfig,
        "database": DatabaseConfig,
        "mcp": MCPConfig,
        "plugin": PluginConfig,
        "plugin_runtime": PluginRuntimeConfig,
        "a_memorix": AMemorixConfig,
        "debug": DebugConfig,
        "voice": VoiceConfig,
        "model_task_config": ModelTaskConfig,
        "api_provider": APIProvider,
        "model_info": ModelInfo,
    }

    if section_name not in section_map:
        raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

    try:
        config_class = section_map[section_name]
        schema = copy.deepcopy(_get_cached_schema(f"section:{section_name}", config_class, include_nested=False))
        if section_name == "debug":
            _apply_retro_prompt_schema_lock(schema)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置节架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置节架构失败: {str(e)}") from e


# ===== 配置读取接口 =====


@router.get("/bot")
def get_bot_config():
    """获取麦麦主程序配置"""
    try:
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        return {"success": True, "config": config_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


@router.get("/model")
def get_model_config():
    """获取模型配置（包含提供商和模型任务配置）"""
    try:
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        return {"success": True, "config": config_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


@router.get("/model/versions", response_model=ModelConfigVersionListResponse)
def list_model_config_versions():
    """列出模型配置文件副本。"""

    active_path = _active_model_config_path()
    if not active_path.exists():
        raise HTTPException(status_code=404, detail="当前模型配置文件不存在")

    active_version = _build_model_config_version_info(
        version_id="active",
        label=_get_active_model_config_label(),
        config_path=active_path,
        active=True,
    )
    return ModelConfigVersionListResponse(
        active_version=active_version,
        versions=_list_model_config_versions(),
    )


@router.post("/model/versions", response_model=ModelConfigVersionResponse)
def create_model_config_version(request: ModelConfigVersionCreateRequest):
    """将当前启用的模型配置保存为一个未启用副本。"""

    active_path = _active_model_config_path()
    version = _copy_model_config_to_version(active_path, request.label)
    logger.info(f"已创建模型配置副本: {version.id} ({version.label})")
    return ModelConfigVersionResponse(version=version, message="模型配置副本已创建")


@router.patch("/model/versions/{version_id}", response_model=ModelConfigVersionResponse)
def update_model_config_version(version_id: str, request: ModelConfigVersionUpdateRequest):
    """更新模型配置文件副本展示名称。"""

    normalized_version_id = _safe_model_config_version_id(version_id)
    version_path = _model_config_version_path(normalized_version_id)
    if not version_path.exists():
        raise HTTPException(status_code=404, detail="模型配置副本不存在")

    _upsert_model_version_metadata(normalized_version_id, request.label)
    version = _build_model_config_version_info(
        version_id=normalized_version_id,
        label=request.label.strip(),
        config_path=version_path,
    )
    return ModelConfigVersionResponse(version=version, message="模型配置副本已更新")


@router.delete("/model/versions/{version_id}")
def delete_model_config_version(version_id: str):
    """删除未启用的模型配置文件副本。"""

    normalized_version_id = _safe_model_config_version_id(version_id)
    version_path = _model_config_version_path(normalized_version_id)
    if not version_path.exists():
        raise HTTPException(status_code=404, detail="模型配置副本不存在")

    version_path.unlink()
    _remove_model_version_metadata(normalized_version_id)
    logger.info(f"已删除模型配置副本: {normalized_version_id}")
    return {"success": True, "message": "模型配置副本已删除"}


@router.post("/model/versions/{version_id}/activate", response_model=ModelConfigVersionResponse)
async def activate_model_config_version(version_id: str, request: ModelConfigVersionSwitchRequest):
    """切换当前启用的模型配置文件副本。"""

    normalized_version_id = _safe_model_config_version_id(version_id)
    active_path = _active_model_config_path()
    version_path = _model_config_version_path(normalized_version_id)
    if not version_path.exists():
        raise HTTPException(status_code=404, detail="模型配置副本不存在")
    if not active_path.exists():
        raise HTTPException(status_code=404, detail="当前模型配置文件不存在")

    selected_metadata = _model_version_metadata_by_id().get(normalized_version_id, {})
    selected_label = str(selected_metadata.get("label") or normalized_version_id)

    _validate_model_config_file(version_path)
    if request.archive_current:
        archive_label = request.archive_label.strip() or _default_model_config_archive_label()
        _copy_model_config_to_version(active_path, archive_label)

    temp_path = active_path.with_name(f".{active_path.name}.{normalized_version_id}.tmp")
    try:
        shutil.copy2(version_path, temp_path)
        os.replace(temp_path, active_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    try:
        version_path.unlink()
        _remove_model_version_metadata(normalized_version_id)
    except OSError as exc:
        logger.warning(f"模型配置副本已切换，但删除已启用副本文件失败: {version_path}，原因: {exc}")

    _set_active_model_config_label(selected_label)

    if active_path == config_manager.model_config_path.resolve():
        await config_manager.reload_config(changed_scopes=["model"])

    active_version = _build_model_config_version_info(
        version_id="active",
        label=_get_active_model_config_label(),
        config_path=active_path,
        active=True,
    )
    logger.info(f"已切换模型配置副本: {normalized_version_id}")
    return ModelConfigVersionResponse(version=active_version, message="模型配置副本已切换")


# ===== 配置更新接口 =====


@router.post("/bot")
def update_bot_config(config_data: ConfigBody):
    """更新麦麦主程序配置"""
    try:
        _validate_retro_prompt_setting(config_data)
        config_data = _coerce_config_numeric_values(config_data, Config)

        # 验证配置数据
        try:
            Config.from_dict(AttributeData(), copy.deepcopy(config_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件（自动保留注释和格式）
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        save_toml_with_format(config_data, config_path)

        logger.info("麦麦主程序配置已更新")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


@router.post("/model")
async def update_model_config(config_data: ConfigBody):
    """更新模型配置"""
    try:
        config_data = _coerce_config_numeric_values(config_data, ModelConfig)

        # 验证配置数据
        try:
            ModelConfig.from_dict(AttributeData(), copy.deepcopy(config_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件（自动保留注释和格式）
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        save_toml_with_format(config_data, config_path)
        await _reload_model_config_after_save()

        logger.info("模型配置已更新")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


# ===== 配置节更新接口 =====


@router.post("/bot/section/{section_name}")
def update_bot_config_section(section_name: str, section_data: SectionBody):
    """更新麦麦主程序配置的指定节（保留注释和格式）"""
    try:
        if section_name == "debug" and isinstance(section_data, dict):
            _validate_retro_prompt_setting({"debug": section_data})
        # 读取现有配置
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        # 更新指定节
        if section_name not in config_data:
            raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

        # 使用递归合并保留注释（对于字典类型）
        # 对于数组类型（如 platforms, aliases），直接替换
        if isinstance(section_data, list):
            # 列表直接替换
            config_data[section_name] = section_data
        elif isinstance(section_data, dict) and isinstance(config_data[section_name], dict):
            # 字典递归合并
            _update_toml_doc(config_data[section_name], section_data)
        else:
            # 其他类型直接替换
            config_data[section_name] = section_data

        # 验证完整配置
        try:
            plain_config_data = _coerce_config_numeric_values(_toml_to_plain_dict(config_data), Config)
            Config.from_dict(AttributeData(), copy.deepcopy(plain_config_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        config_data = plain_config_data

        # 保存配置（格式化数组为多行，保留注释）
        save_toml_with_format(config_data, config_path)

        logger.info(f"配置节 '{section_name}' 已更新（保留注释）")
        return {"success": True, "message": f"配置节 '{section_name}' 已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新配置节失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置节失败: {str(e)}") from e


# ===== 原始 TOML 文件操作接口 =====


@router.get("/bot/raw")
def get_bot_config_raw():
    """获取麦麦主程序配置的原始 TOML 内容"""
    try:
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            raw_content = f.read()

        return {"success": True, "content": raw_content}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


@router.post("/bot/raw")
def update_bot_config_raw(raw_content: RawContentBody):
    """更新麦麦主程序配置（直接保存原始 TOML 内容，会先验证格式）"""
    try:
        # 验证 TOML 格式
        try:
            config_data = tomlkit.loads(raw_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        # 验证配置数据结构
        _validate_retro_prompt_setting(config_data)
        try:
            Config.from_dict(AttributeData(), _toml_to_plain_dict(config_data))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(raw_content)

        logger.info("麦麦主程序配置已更新（原始模式）")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


@compat_router.get("/raw")
def get_compat_bot_config_raw():
    """兼容旧版 /api/config/raw，读取主程序原始 TOML。"""
    return get_bot_config_raw()


@compat_router.post("/raw")
def update_compat_bot_config_raw(raw_content: RawContentBody):
    """兼容旧版 /api/config/raw，写入主程序原始 TOML。"""
    return update_bot_config_raw(raw_content)


def _save_web_config_section(section_data: Any) -> None:
    """整体替换联网配置，避免递归合并使已删除或改名的服务复活。"""
    if not isinstance(section_data, dict):
        raise HTTPException(status_code=400, detail="联网配置必须是对象")
    config_path = Path(CONFIG_DIR) / "model_config.toml"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    doc = tomlkit.loads(config_path.read_text(encoding="utf-8"))
    plain = _toml_to_plain_dict(doc)
    plain["web"] = section_data
    plain = _coerce_config_numeric_values(plain, ModelConfig)
    try:
        ModelConfig.from_dict(AttributeData(), copy.deepcopy(plain))
    except ValidationError as exc:
        # 不把 Pydantic 的输入快照返回给前端，其中可能包含服务密钥。
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        detail = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in errors)
        raise HTTPException(status_code=400, detail=f"配置数据验证失败: {detail}") from exc
    # 直接保存 TOMLDocument，保留其他配置节的注释，且不再次与磁盘旧字典合并。
    doc["web"] = plain["web"]
    save_toml_with_format(doc, str(config_path))


@router.post("/model/section/{section_name}")
async def update_model_config_section(section_name: str, section_data: SectionBody):
    """更新模型配置的指定节（保留注释和格式）"""
    try:
        if section_name == "web":
            await asyncio.to_thread(_save_web_config_section, section_data)
            await _reload_model_config_after_save()
            return {"success": True, "message": "配置节 'web' 已保存"}

        # 读取现有配置
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)
        original_plain_config_data = _coerce_config_numeric_values(_toml_to_plain_dict(config_data), ModelConfig)

        # 更新指定节
        if section_name not in config_data:
            raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

        # 使用递归合并保留注释（对于字典类型）
        # 对于数组表（如 [[models]], [[api_providers]]），直接替换
        if isinstance(section_data, list):
            # 列表直接替换
            config_data[section_name] = section_data
        elif isinstance(section_data, dict) and isinstance(config_data[section_name], dict):
            # 字典递归合并
            _update_toml_doc(config_data[section_name], section_data)
        else:
            # 其他类型直接替换
            config_data[section_name] = section_data

        # 验证完整配置
        try:
            plain_config_data = _coerce_config_numeric_values(_toml_to_plain_dict(config_data), ModelConfig)
            ModelConfig.from_dict(AttributeData(), copy.deepcopy(plain_config_data))
        except Exception as e:
            logger.error(f"配置数据验证失败，详细错误: {str(e)}")
            allow_incomplete_provider_save = False
            # 更新 api_providers 时只校验该小节，允许用户从模型列表为空等不完整状态中恢复配置。
            if section_name == "api_providers":
                _validate_api_provider_section(section_data)
                original_orphaned = _collect_orphaned_model_api_providers(original_plain_config_data)
                orphaned_models = _collect_orphaned_model_api_providers(plain_config_data)
                introduced_orphaned_models = [
                    model_name
                    for model_name, api_provider in orphaned_models.items()
                    if original_orphaned.get(model_name) != api_provider
                ]

                if introduced_orphaned_models:
                    error_msg = (
                        "以下模型引用了已删除的提供商: "
                        f"{', '.join(introduced_orphaned_models)}。"
                        "请先在模型管理页面删除这些模型，或重新分配它们的提供商。"
                    )
                    raise HTTPException(status_code=400, detail=error_msg) from e

                if orphaned_models:
                    logger.warning(
                        "api_providers 已保存，但模型配置中仍存在历史无效引用: "
                        + ", ".join(
                            f"{model_name} -> {api_provider}"
                            for model_name, api_provider in orphaned_models.items()
                        )
                    )
                    allow_incomplete_provider_save = True
                elif not plain_config_data.get("models"):
                    logger.warning("api_providers 已保存，但模型列表仍为空，请继续添加模型")
                    allow_incomplete_provider_save = True
                else:
                    raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e
            if not allow_incomplete_provider_save:
                raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        config_data = plain_config_data

        # 保存配置（格式化数组为多行，保留注释）
        save_toml_with_format(config_data, config_path)
        await _reload_model_config_after_save()

        logger.info(f"配置节 '{section_name}' 已更新（保留注释）")
        return {"success": True, "message": f"配置节 '{section_name}' 已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新配置节失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置节失败: {str(e)}") from e


# ===== 适配器配置管理接口 =====


def _normalize_adapter_path(path: str) -> str:
    """将路径转换为绝对路径（如果是相对路径，则相对于项目根目录）"""
    if not path:
        return path

    # 如果已经是绝对路径，直接返回
    if os.path.isabs(path):
        return path

    # 相对路径，转换为相对于项目根目录的绝对路径
    return os.path.normpath(os.path.join(PROJECT_ROOT, path))


def _get_allowed_adapter_config_roots() -> Tuple[Path, ...]:
    project_root = Path(PROJECT_ROOT).resolve()
    return (
        project_root,
        (project_root.parent / "MaiBot-Napcat-Adapter").resolve(),
        Path("/MaiMBot/adapters-config").resolve(),
    )


def _resolve_safe_adapter_config_path(path: str) -> Path:
    normalized_path = _normalize_adapter_path(path)
    candidate_path = Path(normalized_path).expanduser().resolve()

    if candidate_path.suffix.lower() != ".toml":
        raise HTTPException(status_code=400, detail="只支持 .toml 格式的配置文件")

    for allowed_root in _get_allowed_adapter_config_roots():
        try:
            candidate_path.relative_to(allowed_root)
            return candidate_path
        except ValueError:
            continue

    raise HTTPException(status_code=400, detail="适配器配置路径超出允许范围")


def _to_relative_path(path: str) -> str:
    """尝试将绝对路径转换为相对于项目根目录的相对路径，如果无法转换则返回原路径"""
    if not path or not os.path.isabs(path):
        return path

    try:
        # 尝试获取相对路径
        rel_path = os.path.relpath(path, PROJECT_ROOT)
        # 如果相对路径不是以 .. 开头（说明文件在项目目录内），则返回相对路径
        if not rel_path.startswith(".."):
            return rel_path
    except (ValueError, TypeError):
        # 在 Windows 上，如果路径在不同驱动器，relpath 会抛出 ValueError
        pass

    # 无法转换为相对路径，返回绝对路径
    return path


@router.get("/adapter-config/path")
def get_adapter_config_path():
    """获取保存的适配器配置文件路径"""
    try:
        # 从 data/webui.json 读取路径偏好
        webui_data_path = os.path.join("data", "webui.json")
        if not os.path.exists(webui_data_path):
            return {"success": True, "path": None}

        import json

        with open(webui_data_path, "r", encoding="utf-8") as f:
            webui_data = json.load(f)

        adapter_config_path = webui_data.get("adapter_config_path")
        if not adapter_config_path:
            return {"success": True, "path": None}

        try:
            abs_path = str(_resolve_safe_adapter_config_path(adapter_config_path))
        except HTTPException:
            logger.warning(f"已忽略不安全的适配器配置路径: {adapter_config_path}")
            return {"success": True, "path": None}

        # 检查文件是否存在并返回最后修改时间
        if os.path.exists(abs_path):
            import datetime

            mtime = os.path.getmtime(abs_path)
            last_modified = datetime.datetime.fromtimestamp(mtime).isoformat()
            # 返回相对路径（如果可能）
            display_path = _to_relative_path(abs_path)
            return {"success": True, "path": display_path, "lastModified": last_modified}
        else:
            # 文件不存在，返回原路径
            return {"success": True, "path": adapter_config_path, "lastModified": None}

    except Exception as e:
        logger.error(f"获取适配器配置路径失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置路径失败: {str(e)}") from e


@router.post("/adapter-config/path")
def save_adapter_config_path(data: PathBody):
    """保存适配器配置文件路径偏好"""
    try:
        path = data.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="路径不能为空")

        # 保存到 data/webui.json
        webui_data_path = os.path.join("data", "webui.json")
        import json

        # 读取现有数据
        if os.path.exists(webui_data_path):
            with open(webui_data_path, "r", encoding="utf-8") as f:
                webui_data = json.load(f)
        else:
            webui_data = {}

        abs_path = str(_resolve_safe_adapter_config_path(path))

        # 尝试转换为相对路径保存（如果文件在项目目录内）
        save_path = _to_relative_path(abs_path)

        # 更新路径
        webui_data["adapter_config_path"] = save_path

        # 保存
        os.makedirs("data", exist_ok=True)
        with open(webui_data_path, "w", encoding="utf-8") as f:
            json.dump(webui_data, f, ensure_ascii=False, indent=2)

        logger.info(f"适配器配置路径已保存: {save_path}（绝对路径: {abs_path}）")
        return {"success": True, "message": "路径已保存"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存适配器配置路径失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存路径失败: {str(e)}") from e


@router.get("/adapter-config")
def get_adapter_config(path: str):
    """从指定路径读取适配器配置文件"""
    try:
        if not path:
            raise HTTPException(status_code=400, detail="路径参数不能为空")

        abs_path = str(_resolve_safe_adapter_config_path(path))

        # 检查文件是否存在
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail=f"配置文件不存在: {path}")

        # 读取文件内容
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()

        logger.info(f"已读取适配器配置: {path} (绝对路径: {abs_path})")
        return {"success": True, "content": content}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取适配器配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置失败: {str(e)}") from e


@router.post("/adapter-config")
def save_adapter_config(data: PathBody):
    """保存适配器配置到指定路径"""
    try:
        path = data.get("path")
        content = data.get("content")

        if not path:
            raise HTTPException(status_code=400, detail="路径不能为空")
        if content is None:
            raise HTTPException(status_code=400, detail="配置内容不能为空")

        abs_path = str(_resolve_safe_adapter_config_path(path))

        # 验证 TOML 格式
        try:
            tomlkit.loads(content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        # 确保目录存在
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # 保存文件
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"适配器配置已保存: {path} (绝对路径: {abs_path})")
        return {"success": True, "message": "配置已保存"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存适配器配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}") from e

"""
配置管理API路由
"""

from pathlib import Path
from typing import Annotated, Any, Dict, List, Tuple, Union, get_args, get_origin
import copy
import json
import os
import re
import time
import types

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import tomlkit

from src.common.logger import get_logger
from src.common.prompt_i18n import clear_prompt_cache, extract_prompt_placeholders, list_prompt_templates
from src.config.config import CONFIG_DIR, Config, ModelConfig, PROJECT_ROOT, config_manager
from src.config.config_base import AttributeData, ConfigBase
from src.config.model_configs import (
    APIProvider,
    ModelInfo,
    ModelTaskConfig,
    TaskConfig,
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
from src.llm_models.utils_model import LLMOrchestrator
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

PROMPTS_DIR = PROJECT_ROOT / "prompts"
CUSTOM_PROMPTS_DIR = PROJECT_ROOT / "data" / "custom_prompts"
MAISAKA_PROMPT_PREVIEW_DIR = (PROJECT_ROOT / "logs" / "maisaka_prompt").resolve()
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_VERSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEGACY_CUSTOM_PROMPT_VERSION_ID = "legacy-current"


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


class PromptGeneratorChatPrompt(BaseModel):
    """单个聊天流额外 Prompt。"""

    platform: str = Field(default="", description="平台名")
    item_id: str = Field(default="", description="目标 ID")
    rule_type: str = Field(default="group", description="规则类型：group/private")
    prompt: str = Field(default="", description="额外 Prompt 内容")


class PromptGeneratorParsedResult(BaseModel):
    """LLM 生成的 MaiBot 人设配置结构。"""

    personality: str = Field(default="", description="对应 [personality].personality")
    reply_style: str = Field(default="", description="对应 [personality].reply_style")
    multiple_reply_style: List[str] = Field(default_factory=list, description="对应 multiple_reply_style")
    group_chat_prompt: str = Field(default="", description="对应 [chat.reply_style].group_chat_prompt")
    private_chat_prompts: str = Field(default="", description="对应 [chat.reply_style].private_chat_prompts")
    chat_prompts: List[PromptGeneratorChatPrompt] = Field(
        default_factory=list,
        description="对应 [[chat.reply_style.chat_prompts]]",
    )
    notes: List[str] = Field(default_factory=list, description="生成说明或人工检查建议")


class PromptGeneratorConfigBlock(BaseModel):
    """可直接写入 bot_config.toml 的单个配置块。"""

    id: str = Field(..., description="配置块 ID")
    section: str = Field(..., description="目标配置节")
    field: str = Field(..., description="目标字段")
    title: str = Field(..., description="展示标题")
    description: str = Field(default="", description="展示说明")
    value: Any = Field(..., description="字段值")
    toml: str = Field(..., description="单块 TOML 片段")


class PromptGeneratorRequest(BaseModel):
    """Prompt 生成请求。"""

    model_name: str = Field(..., min_length=1, description="model_config.toml 中定义的模型名称")
    source_text: str = Field(..., min_length=1, max_length=20000, description="任意人设、角色卡或风格描述")
    target_scene: str = Field(default="group", description="目标场景：group/private/both")
    language: str = Field(default="简体中文", max_length=32, description="生成语言")
    extra_requirements: str = Field(default="", max_length=4000, description="额外生成要求")
    temperature: float = Field(default=0.3, ge=0, le=2, description="生成温度")
    max_tokens: int = Field(default=1800, ge=256, le=8192, description="最大输出 token 数")


class PromptGeneratorResponse(BaseModel):
    """Prompt 生成响应。"""

    success: bool = True
    model_name: str
    result: PromptGeneratorParsedResult
    config_blocks: List[PromptGeneratorConfigBlock]
    toml_snippet: str
    raw_response: str
    reasoning: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class PromptGeneratorApplyRequest(BaseModel):
    """人设生成器配置块写入请求。"""

    blocks: List[PromptGeneratorConfigBlock] = Field(default_factory=list, description="要写入的配置块")


class PromptGeneratorApplyResponse(BaseModel):
    """人设生成器配置块写入响应。"""

    success: bool = True
    message: str
    applied_blocks: int
    sections: List[str]


class _SingleModelPromptOrchestrator(LLMOrchestrator):
    """复用现有 LLM 调度器，但把本次请求限制到一个已定义模型。"""

    def __init__(self, model_name: str, temperature: float, max_tokens: int) -> None:
        self._prompt_generator_task_config = TaskConfig(
            model_list=[model_name],
            max_tokens=max_tokens,
            temperature=temperature,
            slow_threshold=30.0,
            selection_strategy="sequential",
            hard_timeout=180.0,
        )
        super().__init__(task_name="webui_prompt_generator", request_type="webui_prompt_generator")

    def _get_task_config_or_raise(self) -> TaskConfig:
        return self._prompt_generator_task_config


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


def _ensure_prompt_generator_model_exists(model_name: str) -> None:
    """确认请求模型存在于 model_config.toml 的 models 中。"""

    normalized_model_name = model_name.strip()
    if not any(model.name == normalized_model_name for model in config_manager.get_model_config().models):
        raise HTTPException(status_code=404, detail=f"未找到模型: {normalized_model_name}")


_PROMPT_GENERATOR_REFERENCE_CONFIG: Dict[str, Any] = {
    "personality": "是一个大二女大学生，现在正在上网和群友聊天。包容且善良",
    "reply_style": (
        "你的风格平淡简短。可以参考贴吧，知乎的回复风格。不浮夸不过分修辞，不使用复杂句。"
        "只回复简短的内容就好。\n"
        "（禁止生成固定化、模板化的怼人、威胁或玩梗句式，尤其避免“你搁这…呢”“你才…/你全家…”"
        "“…你个头”“…了是吧”“再…就禁言/踢/封”“技术群不是让你…”“v我/转我…”“排队…”“子曰…”"
        "等相似结构；需要反驳、提醒或调侃时，改用自然、具体、低攻击性的当场回应。)"
    ),
    "multiple_reply_style": [
        "如果有人对你有敌意，或者很无聊的玩笑，尝试讽刺并且之后不会理他了，十分无语",
        "用1-2个字进行回复",
        "用1-2个符号进行回复",
        "言辭凝練古雅，穿插《論語》經句卻不晦澀，以文言短句為基，輔以淺白語意，持長者溫和風範，全用繁體字表達，具先秦儒者談吐韻致。",
        "你的风格平淡简短。你很慵懒，打算随便回几句应付一下.不浮夸不长篇大论，不要过分修辞和复杂句。",
    ],
    "group_chat_prompt": (
        "你正在qq群里聊天，下面是群里正在聊的内容，其中包含聊天记录和聊天中的图片和表情包。\n"
        "回复尽量简短一些。最好一次对一个话题进行回复，但必须考虑不同群友发言之间的交互，"
        "免得啰嗦或者回复内容太乱。请注意把握聊天内容。\n"
        "不要总是提及自己的身份背景，根据聊天内容自由发挥，但是要日常不浮夸，不要刻意找话题，。\n"
        "不用刻意回复其他人发送的表情包，只要关注表情包表达的含义。你可以适当发送表情包表达情绪。"
        "控制回复的频率，意思是如果有人不喜欢你或者不理你，就不要强行回复，回复前读空气。"
        "不要每个人的消息都回复，优先回复你感兴趣的或者主动提及你的，适当回复其他话题。不要硬找话题。\n"
    ),
    "private_chat_prompts": (
        "你正在聊天，下面是正在聊的内容，其中包含聊天记录和聊天中的图片。\n"
        "回复尽量简短一些。请注意把握聊天内容。\n"
        "请考虑对方的发言频率，想法，思考自己何时回复以及回复内容。\n"
    ),
}


def _build_prompt_generator_reference_config() -> str:
    """构建固定的人设参考快照，不读取运行时配置。"""

    lines = [
        "[personality]",
        f"personality = {_toml_string(_PROMPT_GENERATOR_REFERENCE_CONFIG['personality'])}",
        f"reply_style = {_toml_string(_PROMPT_GENERATOR_REFERENCE_CONFIG['reply_style'])}",
        "multiple_reply_style = [",
    ]
    lines.extend(f"  {_toml_string(item)}," for item in _PROMPT_GENERATOR_REFERENCE_CONFIG["multiple_reply_style"])
    lines.extend(
        [
            "]",
            "",
            "[chat.reply_style]",
            f"group_chat_prompt = {_toml_string(_PROMPT_GENERATOR_REFERENCE_CONFIG['group_chat_prompt'])}",
            f"private_chat_prompts = {_toml_string(_PROMPT_GENERATOR_REFERENCE_CONFIG['private_chat_prompts'])}",
        ]
    )
    return "\n".join(lines)


def _build_prompt_generator_instruction(request: PromptGeneratorRequest) -> str:
    """构建给 LLM 的人设解析提示词。"""

    target_scene_label = {
        "group": "群聊",
        "private": "私聊",
        "both": "群聊和私聊",
    }.get(request.target_scene.strip().lower(), "群聊")
    reference_config = _build_prompt_generator_reference_config()

    return f"""你是 MaiBot/MaiM 的配置人设解析助手。请把用户提供的任意文段、角色卡、人设、说话风格或聊天要求，改写成可以直接放入 bot_config.toml 的麦麦人设配置。

目标场景：{target_scene_label}
主要输出语言：{request.language.strip() or "简体中文"}

默认人设参考：
下面是用于本功能的固定默认参考人设，只用于理解麦麦默认语气、字段职责和通用聊天边界；生成时必须以用户原文为主，不要逐字照抄。如果用户原文缺少场景规则，可以沿用这些设定的精神。
{reference_config}

必须只输出一个 JSON 对象，不要 Markdown，不要代码块，不要额外解释。JSON 结构如下：
{{
  "personality": "对应 [personality].personality。使用第二人称描述稳定人格、身份和长期特质，建议 80-220 字，不要写成小说设定。",
  "reply_style": "对应 [personality].reply_style。描述麦麦说话方式、回复长度、语气、互动习惯和禁用表达。",
  "multiple_reply_style": ["可选备用表达风格，每项一段，最多 5 项"],
  "group_chat_prompt": "对应 [chat.reply_style].group_chat_prompt。只写群聊场景规则，不要重复人格设定。",
  "private_chat_prompts": "对应 [chat.reply_style].private_chat_prompts。只写私聊场景规则，不要重复人格设定。",
  "chat_prompts": [
    {{"platform": "", "item_id": "", "rule_type": "group", "prompt": "如果原文明确提到某个平台或群/私聊专属规则，才生成此项；否则返回空数组"}}
  ],
  "notes": ["需要人工检查或迁移到配置时注意的事项"]
}}

生成要求：
1. 输出要适合聊天型 bot，像真实聊天参与者，不要像客服、旁白、小说角色卡或系统公告。
2. personality 放稳定身份与人格；reply_style 放表达风格和边界；chat prompt 放聊天场景规则。不要三处重复同一段话。
3. 除非特别提到，reply_style 和 multiple_reply_style 最好不要是特别具体的句式，而是描述性的风格要求，方便覆盖不同话题和场景的回复。
4. 默认回复应日常、自然、不过度展开；可以保留原文中的鲜明风格，但要改成可维护的配置文字。
5. 如果信息不足，请根据原文谨慎补全通用聊天规则，并在 notes 中说明需要人工确认，不要反问用户。
6. 字段值必须都是字符串、字符串数组或对象数组，不能为 null。

额外要求：
{request.extra_requirements.strip() or "无"}

用户原文：
{request.source_text.strip()}"""


def _extract_json_object(raw_response: str) -> Dict[str, Any]:
    """从模型输出中解析 JSON 对象。"""

    text = raw_response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start_index = text.find("{")
        end_index = text.rfind("}")
        if start_index < 0 or end_index <= start_index:
            raise ValueError("模型没有返回可解析的 JSON 对象") from None
        parsed = json.loads(text[start_index : end_index + 1])

    if not isinstance(parsed, dict):
        raise ValueError("模型返回的 JSON 顶层必须是对象")
    return parsed


def _coerce_prompt_generator_string(value: Any) -> str:
    """把模型输出的任意字段安全转为字符串。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(_coerce_prompt_generator_string(item) for item in value if item is not None).strip()
    return str(value).strip()


def _coerce_prompt_generator_string_list(value: Any, max_items: int = 8) -> List[str]:
    """把模型输出字段转成字符串列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = [value]

    items: List[str] = []
    for item in candidates:
        normalized_item = _coerce_prompt_generator_string(item)
        if normalized_item:
            items.append(normalized_item)
    return items[:max_items]


def _normalize_prompt_generator_result(raw_data: Dict[str, Any]) -> PromptGeneratorParsedResult:
    """规范化模型 JSON 输出，避免前端收到不稳定结构。"""

    chat_prompts: List[PromptGeneratorChatPrompt] = []
    raw_chat_prompts = raw_data.get("chat_prompts")
    if isinstance(raw_chat_prompts, list):
        for item in raw_chat_prompts:
            if not isinstance(item, dict):
                continue
            prompt = _coerce_prompt_generator_string(item.get("prompt"))
            platform = _coerce_prompt_generator_string(item.get("platform"))
            item_id = _coerce_prompt_generator_string(item.get("item_id"))
            if not prompt or not platform or not item_id:
                continue
            rule_type = _coerce_prompt_generator_string(item.get("rule_type")) or "group"
            chat_prompts.append(
                PromptGeneratorChatPrompt(
                    platform=platform,
                    item_id=item_id,
                    rule_type=rule_type if rule_type in {"group", "private"} else "group",
                    prompt=prompt,
                )
            )

    return PromptGeneratorParsedResult(
        personality=_coerce_prompt_generator_string(raw_data.get("personality")),
        reply_style=_coerce_prompt_generator_string(raw_data.get("reply_style")),
        multiple_reply_style=_coerce_prompt_generator_string_list(raw_data.get("multiple_reply_style"), max_items=5),
        group_chat_prompt=_coerce_prompt_generator_string(raw_data.get("group_chat_prompt")),
        private_chat_prompts=_coerce_prompt_generator_string(raw_data.get("private_chat_prompts")),
        chat_prompts=chat_prompts[:8],
        notes=_coerce_prompt_generator_string_list(raw_data.get("notes"), max_items=8),
    )


def _toml_string(value: str) -> str:
    """生成可嵌入 TOML basic string 的值。"""

    return json.dumps(value, ensure_ascii=False)


def _build_prompt_generator_toml(result: PromptGeneratorParsedResult) -> str:
    """把结构化结果转换为 bot_config.toml 片段。"""

    lines = [
        "[personality]",
        f"personality = {_toml_string(result.personality)}",
        f"reply_style = {_toml_string(result.reply_style)}",
        "multiple_reply_style = [",
    ]
    lines.extend(f"  {_toml_string(item)}," for item in result.multiple_reply_style)
    lines.extend(
        [
            "]",
            "multiple_probability = 0.15",
            "",
            "[chat]",
        ]
    )

    if result.group_chat_prompt:
        lines.append(f"group_chat_prompt = {_toml_string(result.group_chat_prompt)}")
    if result.private_chat_prompts:
        lines.append(f"private_chat_prompts = {_toml_string(result.private_chat_prompts)}")

    for chat_prompt in result.chat_prompts:
        lines.extend(
            [
                "",
                "[[chat.reply_style.chat_prompts]]",
                f"platform = {_toml_string(chat_prompt.platform)}",
                f"item_id = {_toml_string(chat_prompt.item_id)}",
                f"rule_type = {_toml_string(chat_prompt.rule_type)}",
                f"prompt = {_toml_string(chat_prompt.prompt)}",
            ]
        )

    return "\n".join(lines)


def _prompt_generator_chat_prompt_to_dict(chat_prompt: PromptGeneratorChatPrompt) -> Dict[str, str]:
    """把额外 Prompt 项转换成 bot_config.toml 可保存的普通字典。"""

    return {
        "platform": chat_prompt.platform,
        "item_id": chat_prompt.item_id,
        "rule_type": chat_prompt.rule_type,
        "prompt": chat_prompt.prompt,
    }


def _build_prompt_generator_block_toml(section: str, field: str, value: Any) -> str:
    """生成单个配置块的 TOML 预览。"""

    if field == "chat_prompts" and isinstance(value, list):
        lines: List[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    "[[chat.reply_style.chat_prompts]]",
                    f"platform = {_toml_string(_coerce_prompt_generator_string(item.get('platform')))}",
                    f"item_id = {_toml_string(_coerce_prompt_generator_string(item.get('item_id')))}",
                    f"rule_type = {_toml_string(_coerce_prompt_generator_string(item.get('rule_type')) or 'group')}",
                    f"prompt = {_toml_string(_coerce_prompt_generator_string(item.get('prompt')))}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    lines = [f"[{section}]"]
    if isinstance(value, list):
        lines.append(f"{field} = [")
        lines.extend(f"  {_toml_string(_coerce_prompt_generator_string(item))}," for item in value)
        lines.append("]")
    else:
        lines.append(f"{field} = {_toml_string(_coerce_prompt_generator_string(value))}")
    return "\n".join(lines)


def _build_prompt_generator_config_blocks(result: PromptGeneratorParsedResult) -> List[PromptGeneratorConfigBlock]:
    """把生成结果拆成可单独写入的配置块。"""

    blocks: List[PromptGeneratorConfigBlock] = []

    def add_block(
        block_id: str,
        section: str,
        field: str,
        title: str,
        description: str,
        value: Any,
    ) -> None:
        blocks.append(
            PromptGeneratorConfigBlock(
                id=block_id,
                section=section,
                field=field,
                title=title,
                description=description,
                value=value,
                toml=_build_prompt_generator_block_toml(section, field, value),
            )
        )

    add_block(
        "personality.personality",
        "personality",
        "personality",
        "人格设定",
        "写入 bot_config.toml 的 [personality].personality，会覆盖当前人格设定字段。",
        result.personality,
    )
    add_block(
        "personality.reply_style",
        "personality",
        "reply_style",
        "表达风格",
        "写入 bot_config.toml 的 [personality].reply_style，会覆盖当前表达风格字段。",
        result.reply_style,
    )
    if result.multiple_reply_style:
        add_block(
            "personality.multiple_reply_style",
            "personality",
            "multiple_reply_style",
            "备用表达风格",
            "写入 bot_config.toml 的 [personality].multiple_reply_style，会替换当前备用表达风格列表。",
            result.multiple_reply_style,
        )
    if result.group_chat_prompt:
        add_block(
            "chat.reply_style.group_chat_prompt",
            "chat.reply_style",
            "group_chat_prompt",
            "群聊提示词",
            "写入 bot_config.toml 的 [chat.reply_style].group_chat_prompt，会覆盖当前群聊提示词。",
            result.group_chat_prompt,
        )
    if result.private_chat_prompts:
        add_block(
            "chat.reply_style.private_chat_prompts",
            "chat.reply_style",
            "private_chat_prompts",
            "私聊提示词",
            "写入 bot_config.toml 的 [chat.reply_style].private_chat_prompts，会覆盖当前私聊提示词。",
            result.private_chat_prompts,
        )
    if result.chat_prompts:
        add_block(
            "chat.reply_style.chat_prompts",
            "chat.reply_style",
            "chat_prompts",
            "额外聊天流 Prompt",
            "写入 bot_config.toml 的 [[chat.reply_style.chat_prompts]]，会替换当前额外 Prompt 列表。",
            [_prompt_generator_chat_prompt_to_dict(item) for item in result.chat_prompts],
        )

    return blocks


_PROMPT_GENERATOR_ALLOWED_BLOCK_FIELDS = {
    ("personality", "personality"),
    ("personality", "reply_style"),
    ("personality", "multiple_reply_style"),
    ("chat.reply_style", "group_chat_prompt"),
    ("chat.reply_style", "private_chat_prompts"),
    ("chat.reply_style", "chat_prompts"),
}


def _normalize_prompt_generator_block_value(block: PromptGeneratorConfigBlock) -> Tuple[str, str, Any]:
    """校验并规范化单个配置块，避免人设生成器写入非人设字段。"""

    section = block.section.strip()
    field = block.field.strip()
    if (section, field) not in _PROMPT_GENERATOR_ALLOWED_BLOCK_FIELDS:
        raise HTTPException(status_code=400, detail=f"不允许写入配置字段: {section}.{field}")

    if field in {"personality", "reply_style", "group_chat_prompt", "private_chat_prompts"}:
        value = _coerce_prompt_generator_string(block.value)
        if not value:
            raise HTTPException(status_code=400, detail=f"配置块 {section}.{field} 不能为空")
        return section, field, value

    if field == "multiple_reply_style":
        value = _coerce_prompt_generator_string_list(block.value, max_items=5)
        if not value:
            raise HTTPException(status_code=400, detail="备用表达风格配置块不能为空")
        return section, field, value

    if field == "chat_prompts":
        if not isinstance(block.value, list):
            raise HTTPException(status_code=400, detail="额外聊天流 Prompt 必须是数组")

        chat_prompts: List[Dict[str, str]] = []
        for item in block.value:
            if not isinstance(item, dict):
                continue
            platform = _coerce_prompt_generator_string(item.get("platform"))
            item_id = _coerce_prompt_generator_string(item.get("item_id"))
            prompt = _coerce_prompt_generator_string(item.get("prompt"))
            rule_type = _coerce_prompt_generator_string(item.get("rule_type")) or "group"
            if not platform or not item_id or not prompt:
                raise HTTPException(status_code=400, detail="额外聊天流 Prompt 需要包含 platform、item_id 和 prompt")
            chat_prompts.append(
                {
                    "platform": platform,
                    "item_id": item_id,
                    "rule_type": rule_type if rule_type in {"group", "private"} else "group",
                    "prompt": prompt,
                }
            )
        if not chat_prompts:
            raise HTTPException(status_code=400, detail="额外聊天流 Prompt 配置块不能为空")
        return section, field, chat_prompts[:8]

    raise HTTPException(status_code=400, detail=f"无法识别配置字段: {section}.{field}")


def _resolve_prompt_generator_section(config_data: Dict[str, Any], section: str) -> Dict[str, Any]:
    """按点分配置节定位可写入的 TOML 表。"""

    current: Any = config_data
    for section_part in section.split("."):
        if not isinstance(current, dict) or section_part not in current:
            raise HTTPException(status_code=404, detail=f"配置节 '{section}' 不存在")
        current = current[section_part]

    if not isinstance(current, dict):
        raise HTTPException(status_code=400, detail=f"配置节 '{section}' 不是可写对象")
    return current


def _apply_prompt_generator_config_blocks(blocks: List[PromptGeneratorConfigBlock]) -> PromptGeneratorApplyResponse:
    """把选中的人设生成器配置块写入 bot_config.toml。"""

    if not blocks:
        raise HTTPException(status_code=400, detail="请选择要注入的配置块")

    config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="配置文件不存在")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = tomlkit.load(f)

    section_updates: Dict[str, Dict[str, Any]] = {}
    for block in blocks:
        section, field, value = _normalize_prompt_generator_block_value(block)
        section_updates.setdefault(section, {})[field] = value

    for section, section_data in section_updates.items():
        _update_toml_doc(_resolve_prompt_generator_section(config_data, section), section_data)

    try:
        plain_config_data = _coerce_config_numeric_values(_toml_to_plain_dict(config_data), Config)
        Config.from_dict(AttributeData(), copy.deepcopy(plain_config_data))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

    save_toml_with_format(plain_config_data, config_path)
    applied_sections = sorted(section_updates)
    logger.info(f"人设生成器已注入 {len(blocks)} 个配置块: {', '.join(applied_sections)}")
    return PromptGeneratorApplyResponse(
        message=f"已注入 {len(blocks)} 个配置块",
        applied_blocks=len(blocks),
        sections=applied_sections,
    )


# ===== 架构获取接口 =====


@router.get("/prompts", response_model=PromptCatalogResponse)
async def list_prompt_files():
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
async def get_prompt_file(language: str, filename: str):
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
async def get_default_prompt_file(language: str, filename: str):
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
async def list_prompt_versions(language: str, filename: str):
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
async def get_prompt_version_file(language: str, filename: str, version_id: str):
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
async def activate_prompt_version(language: str, filename: str, version_id: str):
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


@router.put("/prompts/{language}/{filename}", response_model=PromptFileResponse)
async def update_prompt_file(language: str, filename: str, request: PromptUpdateRequest):
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
async def reset_prompt_file(language: str, filename: str):
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
async def get_maisaka_prompt_preview(path: str = Query(..., description="logs/maisaka_prompt 下的相对预览路径")):
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


@router.post("/prompt-generator/generate", response_model=PromptGeneratorResponse)
async def generate_prompt_persona(request: PromptGeneratorRequest):
    """使用已定义模型把任意文段解析为 MaiBot 人设配置片段。"""

    model_name = request.model_name.strip()
    _ensure_prompt_generator_model_exists(model_name)

    prompt = _build_prompt_generator_instruction(request)
    try:
        orchestrator = _SingleModelPromptOrchestrator(
            model_name=model_name,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        llm_result = await orchestrator.generate_response_async(
            prompt=prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        raw_response = llm_result.response.strip()
        parsed_data = _extract_json_object(raw_response)
        parsed_result = _normalize_prompt_generator_result(parsed_data)
        if not parsed_result.personality or not parsed_result.reply_style:
            raise ValueError("模型返回缺少 personality 或 reply_style 字段")

        return PromptGeneratorResponse(
            model_name=llm_result.model_name or model_name,
            result=parsed_result,
            config_blocks=_build_prompt_generator_config_blocks(parsed_result),
            toml_snippet=_build_prompt_generator_toml(parsed_result),
            raw_response=raw_response,
            reasoning=llm_result.reasoning,
            prompt_tokens=llm_result.prompt_tokens,
            completion_tokens=llm_result.completion_tokens,
            total_tokens=llm_result.total_tokens,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Prompt 生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Prompt 生成失败: {str(e)}") from e


@router.post("/prompt-generator/apply", response_model=PromptGeneratorApplyResponse)
async def apply_prompt_generator_blocks(request: PromptGeneratorApplyRequest):
    """把人设生成器产出的配置块写入 bot_config.toml。"""

    try:
        return _apply_prompt_generator_config_blocks(request.blocks)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Prompt 配置块注入失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Prompt 配置块注入失败: {str(e)}") from e


@router.get("/schema/bot")
async def get_bot_config_schema():
    """获取麦麦主程序配置架构"""
    try:
        # Config 类包含所有子配置
        schema = _get_cached_schema("bot", Config)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置架构失败: {str(e)}") from e


@compat_router.get("/schema")
async def get_compat_bot_config_schema():
    """兼容旧版 /api/config/schema，返回主程序配置架构。"""
    return await get_bot_config_schema()


@compat_router.get("/schema/bot")
async def get_compat_bot_config_schema_alias():
    """兼容旧版 /api/config/schema/bot。"""
    return await get_bot_config_schema()


@router.get("/schema/model")
async def get_model_config_schema():
    """获取模型配置架构（包含提供商和模型任务配置）"""
    try:
        schema = _get_cached_schema("model", ModelConfig)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取模型配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取模型配置架构失败: {str(e)}") from e


# ===== 子配置架构获取接口 =====


@router.get("/schema/section/{section_name}")
async def get_config_section_schema(section_name: str):
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
        schema = _get_cached_schema(f"section:{section_name}", config_class, include_nested=False)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置节架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置节架构失败: {str(e)}") from e


# ===== 配置读取接口 =====


@router.get("/bot")
async def get_bot_config():
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
async def get_model_config():
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


# ===== 配置更新接口 =====


@router.post("/bot")
async def update_bot_config(config_data: ConfigBody):
    """更新麦麦主程序配置"""
    try:
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

        logger.info("模型配置已更新")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


# ===== 配置节更新接口 =====


@router.post("/bot/section/{section_name}")
async def update_bot_config_section(section_name: str, section_data: SectionBody):
    """更新麦麦主程序配置的指定节（保留注释和格式）"""
    try:
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
async def get_bot_config_raw():
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
async def update_bot_config_raw(raw_content: RawContentBody):
    """更新麦麦主程序配置（直接保存原始 TOML 内容，会先验证格式）"""
    try:
        # 验证 TOML 格式
        try:
            config_data = tomlkit.loads(raw_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        # 验证配置数据结构
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
async def get_compat_bot_config_raw():
    """兼容旧版 /api/config/raw，读取主程序原始 TOML。"""
    return await get_bot_config_raw()


@compat_router.post("/raw")
async def update_compat_bot_config_raw(raw_content: RawContentBody):
    """兼容旧版 /api/config/raw，写入主程序原始 TOML。"""
    return await update_bot_config_raw(raw_content)


@router.post("/model/section/{section_name}")
async def update_model_config_section(section_name: str, section_data: SectionBody):
    """更新模型配置的指定节（保留注释和格式）"""
    try:
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
            allow_orphaned_provider_save = False
            # 特殊处理：如果是更新 api_providers，检查是否有模型引用了已删除的provider
            if section_name == "api_providers" and "api_provider" in str(e):
                _validate_api_provider_section(section_data)
                original_orphaned = _collect_orphaned_model_api_providers(original_plain_config_data)
                orphaned_models = _collect_orphaned_model_api_providers(plain_config_data)
                introduced_orphaned_models = [
                    model_name
                    for model_name, api_provider in orphaned_models.items()
                    if original_orphaned.get(model_name) != api_provider
                ]

                if orphaned_models and not introduced_orphaned_models:
                    logger.warning(
                        "api_providers 已保存，但模型配置中仍存在历史无效引用: "
                        + ", ".join(
                            f"{model_name} -> {api_provider}"
                            for model_name, api_provider in orphaned_models.items()
                        )
                    )
                    allow_orphaned_provider_save = True
                elif introduced_orphaned_models:
                    error_msg = (
                        "以下模型引用了已删除的提供商: "
                        f"{', '.join(introduced_orphaned_models)}。"
                        "请先在模型管理页面删除这些模型，或重新分配它们的提供商。"
                    )
                    raise HTTPException(status_code=400, detail=error_msg) from e
                else:
                    raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e
            if not allow_orphaned_provider_save:
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
async def get_adapter_config_path():
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
async def save_adapter_config_path(data: PathBody):
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
async def get_adapter_config(path: str):
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
async def save_adapter_config(data: PathBody):
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

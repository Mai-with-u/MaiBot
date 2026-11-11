"""宿主共享供应商、模型与联网服务定义；任务配置由 Bot 独立保存。"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import copy
import hashlib
import os
import tomllib

SHARED_MODELS_ENV = "MAIBOT_SHARED_MODEL_CATALOG"
CATALOG_FILENAME = "model_catalog.toml"
CATALOG_VERSION = "3.0.0"
CATALOG_SECTIONS = ("api_providers", "models")


def shared_models_path() -> Optional[Path]:
    value = os.environ.get(SHARED_MODELS_ENV)
    return Path(value) if value else None


def catalog_revision() -> str:
    path = shared_models_path()
    return hashlib.sha256(path.read_bytes() if path is not None and path.exists() else b"").hexdigest()


def read_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path if path is not None else shared_models_path()
    if path is None or not path.exists():
        return {"api_providers": [], "models": []}
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if data.get("inner", {}).get("version") != CATALOG_VERSION:
        raise ValueError("共享模型目录版本不支持")
    return data


def merge_catalog(local: Dict[str, Any], catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """运行时供应商和模型只取公共目录；没有宿主契约的初始化工具使用模板。"""
    if catalog is None and shared_models_path() is None:
        return copy.deepcopy(local)
    shared = read_catalog() if catalog is None else catalog
    result = copy.deepcopy(local)
    for section in CATALOG_SECTIONS:
        result[section] = copy.deepcopy(shared.get(section, []))
    result.setdefault("web", {})["services"] = copy.deepcopy(shared.get("web", {}).get("services", {}))
    return result


def read_effective_config(path: Path) -> Dict[str, Any]:
    with path.open("rb") as stream:
        return merge_catalog(tomllib.load(stream))


def extract_bot_model_settings(submitted: Dict[str, Any]) -> Dict[str, Any]:
    """实例文件只保存任务等独立设置，公共模型定义由统一写入服务保存。"""
    result = copy.deepcopy(submitted)
    result.pop("_shared_catalog", None)
    result.pop("_warnings", None)
    for section in CATALOG_SECTIONS:
        result.pop(section, None)
    if "web" in result:
        result["web"].pop("services", None)
        if not result["web"]:
            result.pop("web")
    return result


def extract_catalog(submitted: Dict[str, Any]) -> Dict[str, Any]:
    """公共定义统一提取；联网预览参数和任务选择留在 Bot。"""
    return {
        "inner": {"version": CATALOG_VERSION},
        **{section: copy.deepcopy(submitted[section]) for section in CATALOG_SECTIONS},
        "web": {"services": copy.deepcopy(submitted.get("web", {}).get("services", {}))},
    }


def validate_catalog(data: Dict[str, Any]) -> None:
    from src.web_search.config import WebConfig

    from .model_configs import APIProvider, ModelInfo

    for section, model_type in (("api_providers", APIProvider), ("models", ModelInfo)):
        items = data.get(section, [])
        if not isinstance(items, list):
            raise ValueError(f"{section} 必须是列表")
        names = set()
        for item in items:
            name = model_type.model_validate(item).name
            if name in names:
                raise ValueError(f"{section} 存在重复名称")
            names.add(name)
    WebConfig.model_validate(data.get("web", {}))


def missing_references(data: Dict[str, Any]) -> Set[str]:
    """收集具体失效引用，目录修改不能扩大已有配置错误。"""
    errors: Set[str] = set()
    providers = {item.get("name", "") for item in data.get("api_providers", []) if item.get("name", "").strip()}
    models = {item["name"] for item in data.get("models", [])}
    for model in data.get("models", []):
        provider = model.get("api_provider", "")
        if provider not in providers:
            errors.add(f"模型 {model['name']} 引用的供应商 {provider!r} 不存在")

    def check(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "model_list" and isinstance(value, list):
                    missing = {name for name in value if name} - models
                    for name in missing:
                        errors.add(f"任务 {path} 引用的模型 {name} 不存在")
                else:
                    check(value, f"{path}.{key}")

    for task_name, task in data.get("model_task_config", {}).items():
        if task_name == "web_search":
            services = data.get("web", {}).get("services", {})
            for name in task.get("model_list", []):
                if name not in services:
                    errors.add(f"联网任务引用的服务 {name} 不存在")
        else:
            check(task, f"model_task_config.{task_name}")
    return errors


def model_config_warnings(data: Dict[str, Any]) -> List[str]:
    """统一诊断模型目录和全部任务引用，不改写用户配置。"""
    from src.web_search.config import WebConfig, WebSearchConfigError, require_web_service

    from .model_configs import APIProvider, provider_configuration_errors

    warnings = sorted(missing_references(data))
    if not data.get("models"):
        warnings.append("模型列表为空，请在 WebUI 添加模型")
    if not data.get("api_providers"):
        warnings.append("供应商列表为空，请在 WebUI 添加供应商")
    for item in data.get("api_providers", []):
        provider = APIProvider.model_validate(item)
        warnings.extend(f"供应商 {provider.name!r}：{error}" for error in provider_configuration_errors(provider))
    web = WebConfig.model_validate(data.get("web", {}))
    for name in data.get("model_task_config", {}).get("web_search", {}).get("model_list", []):
        if name in web.services:
            try:
                require_web_service(web, name)
            except WebSearchConfigError as exc:
                warnings.append(str(exc))
    return warnings

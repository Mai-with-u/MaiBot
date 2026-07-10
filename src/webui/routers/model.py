"""
模型列表获取API路由

提供从各个 AI 厂商 API 获取可用模型列表的代理接口
"""

from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import os
import time

import httpx
import tomlkit

from src.common.logger import get_logger
from src.config.config import CONFIG_DIR
from src.config.model_configs import APIProvider, TaskConfig
from src.llm_models.model_client import ensure_client_type_loaded
from src.llm_models.model_client.base_client import client_registry
from src.llm_models.openai_compat import build_openai_compatible_client_config, normalize_openai_base_url
from src.llm_models.payload_content.message import Message, MessageBuilder
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.utils_model import LLMOrchestrator, LLMResponseResult
from src.webui.dependencies import require_auth
from src.webui.utils.network_security import validate_public_url

logger = get_logger("webui")

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_auth)])
# 模型获取器配置
MODEL_FETCHER_CONFIG = {
    # OpenAI 兼容格式的提供商
    "openai": {
        "endpoint": "/models",
        "parser": "openai",
    },
    # Gemini 格式
    "gemini": {
        "endpoint": "/models",
        "parser": "gemini",
    },
}

MODEL_TEST_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAB"
    "lklEQVR4nA3L0QBAIQxA0RCGEMIQhjCEEIYwhBBC2McFCCGE"
    "EEJ47/yf1hrS6A1tWMMboxGNbMzGalRjN07jNl6jNUGELqhg"
    "ggtDCCGFKSyhhC0c4QpP/tCRTu9oxzreGZ3oZGd2Vqc6u3M6"
    "t/P6HxRRuqKKKa4MJZRUprKUUrZylKs8/YMhRjfUMMONYYSR"
    "xjSWUcY2jnGNZ39wxOmOOua4M5xw0pnOcsrZznGu8/wPAxn0"
    "gQ5s4IMxiEEO5mANarAHZ3AHb/whkKAHGljgwQgiyGAGK6hg"
    "Bye4wYs/JJL0RBNLPBlJJJnMZCWV7OQkN3n5h4lM+kQnNvHJ"
    "mMQkJ3OyJjXZkzO5kzf/sJBFX+jCFr4Yi1jkYi7WohZ7cRZ3"
    "8dYfCil6oYUVXowiiixmsYoqdnGKW7z6w0Y2faMb2/hmbGKT"
    "m7lZm9rszdnczdt/OMihH/RgBz+MQxzyMA/rUId9OId7eOcP"
    "F7n0i17s4pdxiUte5mVd6rIv53Iv7/7hIY/+0Ic9/DEe8cj"
    "HfKxHPfbjPO7jPT74o6QQdaP0PQAAAABJRU5ErkJggg=="
)
MODEL_TEST_TOOL_NAME = "maibot_model_test_report"


class ModelTestRequest(BaseModel):
    """单个模型测试请求。"""

    model_name: str = Field(..., min_length=1, description="model_config.toml 中定义的模型名称")


class ModelTestToolCall(BaseModel):
    """模型测试返回的工具调用摘要。"""

    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ModelTestResponse(BaseModel):
    """单个模型测试响应。"""

    success: bool
    model_name: str
    visual_tested: bool
    tool_call_ok: bool
    response: str = ""
    reasoning: str = ""
    tool_calls: List[ModelTestToolCall] = Field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class _SingleModelTestOrchestrator(LLMOrchestrator):
    """复用 LLM 调度器，但将 WebUI 测试请求限制到单个模型。"""

    def __init__(self, model_name: str) -> None:
        self._model_test_task_config = TaskConfig(
            model_list=[model_name],
            max_tokens=512,
            temperature=0.0,
            slow_threshold=30.0,
            selection_strategy="sequential",
            hard_timeout=90.0,
        )
        super().__init__(task_name="webui_model_test", request_type="webui_model_test")

    def _get_task_config_or_raise(self) -> TaskConfig:
        return self._model_test_task_config


@router.get("/client-types")
async def get_registered_client_types():
    """返回当前主程序与插件已注册的 LLM Provider client_type。"""
    for client_type in MODEL_FETCHER_CONFIG:
        ensure_client_type_loaded(client_type)

    client_types = []
    for registration in client_registry.client_registry.values():
        client_types.append(
            {
                "client_type": registration.client_type,
                "owner_plugin_id": registration.owner_plugin_id,
                "version": registration.version,
                "description": registration.description,
                "builtin": registration.builtin,
            }
        )

    client_types.sort(key=lambda item: (not item["builtin"], item["client_type"]))
    return {
        "success": True,
        "client_types": client_types,
        "count": len(client_types),
    }


@router.post("/test-model", response_model=ModelTestResponse)
async def test_model_capability(request: ModelTestRequest):
    """测试单个模型的文本、tool call 与可选视觉能力。"""
    model_name = request.model_name.strip()
    model_config = _get_model_config(model_name)
    if model_config is None:
        raise HTTPException(status_code=404, detail=f"未找到模型: {model_name}")

    # 嵌入模型不支持 chat/completions 接口，需改用嵌入接口测试
    if model_name in _get_embedding_task_model_names():
        return await _test_embedding_model(model_name)

    visual_enabled = bool(model_config.get("visual", False))
    start_time = time.time()
    try:
        orchestrator = _SingleModelTestOrchestrator(model_name=model_name)
        result = await orchestrator.generate_response_with_message_async(
            message_factory=_build_model_test_message_factory(visual_enabled),
            temperature=0.0,
            max_tokens=512,
            model_name=model_name,
            tools=_build_model_test_tools(),
        )
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return _build_model_test_response(
            model_name=model_name,
            visual_tested=visual_enabled,
            latency_ms=latency_ms,
            result=result,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"模型测试失败: model={model_name}, error={e}", exc_info=True)
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return _build_model_test_response(
            model_name=model_name,
            visual_tested=visual_enabled,
            latency_ms=latency_ms,
            error=str(e),
        )


def _normalize_url(url: str) -> str:
    """规范化 URL（去掉尾部斜杠）。"""
    return normalize_openai_base_url(url) if url else ""


def _parse_openai_response(data: Dict) -> List[Dict]:
    """
    解析 OpenAI 格式的模型列表响应

    格式: { "data": [{ "id": "gpt-4", "object": "model", ... }] }
    """
    if "data" not in data or not isinstance(data["data"], list):
        return []

    return [
        {
            "id": model["id"],
            "name": model.get("name") or model["id"],
            "owned_by": model.get("owned_by", ""),
        }
        for model in data["data"]
        if isinstance(model, dict) and "id" in model
    ]


def _parse_gemini_response(data: Dict) -> List[Dict]:
    """
    解析 Gemini 格式的模型列表响应

    格式: { "models": [{ "name": "models/gemini-pro", "displayName": "Gemini Pro", ... }] }
    """
    models = []
    if "models" in data and isinstance(data["models"], list):
        for model in data["models"]:
            if isinstance(model, dict) and "name" in model:
                # Gemini 的 name 格式是 "models/gemini-pro"，我们只取后面部分
                model_id = model["name"]
                if model_id.startswith("models/"):
                    model_id = model_id[7:]  # 去掉 "models/" 前缀
                models.append(
                    {
                        "id": model_id,
                        "name": model.get("displayName") or model_id,
                        "owned_by": "google",
                    }
                )
    return models


async def _fetch_models_from_provider(
    base_url: str,
    api_key: str,
    endpoint: str,
    parser: str,
    client_type: str = "openai",
    auth_type: str = "bearer",
    auth_header_name: str = "Authorization",
    auth_header_prefix: str = "Bearer",
    auth_query_name: str = "api_key",
    default_headers: Optional[Dict[str, str]] = None,
    default_query: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """从提供商 API 获取模型列表。

    Args:
        base_url: 提供商的基础 URL。
        api_key: API 密钥。
        endpoint: 获取模型列表的端点。
        parser: 响应解析器类型。
        client_type: 客户端类型。
        auth_type: OpenAI 兼容接口的鉴权方式。
        auth_header_name: Header 鉴权时使用的请求头名称。
        auth_header_prefix: Header 鉴权时使用的请求头前缀。
        auth_query_name: Query 鉴权时使用的查询参数名称。
        default_headers: 默认附带的请求头。
        default_query: 默认附带的查询参数。

    Returns:
        List[Dict]: 解析后的模型列表。
    """
    try:
        base_url = validate_public_url(_normalize_url(base_url))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    url = f"{base_url}{endpoint}"

    # 根据客户端类型设置请求头
    headers = {}
    params = {}

    if client_type == "gemini":
        # Gemini 使用 URL 参数传递 API Key
        params["key"] = api_key
    else:
        provider = APIProvider(
            name="webui-openai-compatible-fetcher",
            base_url=base_url,
            api_key=api_key,
            client_type="openai",
            auth_type=auth_type,
            auth_header_name=auth_header_name,
            auth_header_prefix=auth_header_prefix,
            auth_query_name=auth_query_name,
            default_headers=default_headers or {},
            default_query=default_query or {},
        )
        client_config = build_openai_compatible_client_config(provider)
        headers.update(client_config.default_headers)
        params.update(client_config.default_query)
        # build_openai_compatible_client_config 在“默认 Bearer”场景下，
        # 会把 api_key 留在 client_config.api_key 中交给 OpenAI SDK 自行注入 Authorization 头，
        # 而不会写入 default_headers。这里我们用 httpx 直接发请求，需要手动补上鉴权头/参数。
        if client_config.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {client_config.api_key}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="请求超时，请稍后重试") from e
    except httpx.HTTPStatusError as e:
        # 注意：使用 502 Bad Gateway 而不是原始的 401/403，
        # 因为前端的 fetchWithAuth 会把 401 当作 WebUI 认证失败处理
        if e.response.status_code == 401:
            raise HTTPException(status_code=502, detail="API Key 无效或已过期") from e
        elif e.response.status_code == 403:
            raise HTTPException(status_code=502, detail="没有权限访问模型列表，请检查 API Key 权限") from e
        elif e.response.status_code == 404:
            raise HTTPException(status_code=502, detail="该提供商不支持获取模型列表") from e
        else:
            raise HTTPException(
                status_code=502, detail=f"上游服务请求失败 ({e.response.status_code}): {e.response.text[:200]}"
            ) from e
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}") from e

    # 根据解析器类型解析响应
    if parser == "openai":
        return _parse_openai_response(data)
    elif parser == "gemini":
        return _parse_gemini_response(data)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的解析器类型: {parser}")


def _get_provider_config(provider_name: str) -> Optional[Dict]:
    """
    从 model_config.toml 获取指定提供商的配置

    Args:
        provider_name: 提供商名称

    Returns:
        提供商配置，如果未找到则返回 None
    """
    config_path = os.path.join(CONFIG_DIR, "model_config.toml")
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        providers = config_data.get("api_providers", [])
        provider = next((provider for provider in providers if provider.get("name") == provider_name), None)
        return dict(provider) if provider is not None else None
    except Exception as e:
        logger.error(f"读取提供商配置失败: {e}")
        return None


def _get_model_config(model_name: str) -> Optional[Dict]:
    """
    从 model_config.toml 获取指定模型的配置。

    Args:
        model_name: 模型名称。

    Returns:
        模型配置，如果未找到则返回 None。
    """
    config_path = os.path.join(CONFIG_DIR, "model_config.toml")
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        models = config_data.get("models", [])
        model = next((model for model in models if model.get("name") == model_name), None)
        return dict(model) if model is not None else None
    except Exception as e:
        logger.error(f"读取模型配置失败: {e}")
        return None


def _get_embedding_task_model_names() -> Set[str]:
    """从 model_config.toml 获取嵌入任务配置的模型名称集合。

    Returns:
        嵌入任务 model_list 中的模型名称集合，读取失败时返回空集合。
    """
    config_path = os.path.join(CONFIG_DIR, "model_config.toml")
    if not os.path.exists(config_path):
        return set()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        task_config = config_data.get("model_task_config", {}).get("embedding", {})
        return {str(name) for name in task_config.get("model_list", [])}
    except Exception as e:
        logger.error(f"读取嵌入任务配置失败: {e}")
        return set()


async def _test_embedding_model(model_name: str) -> ModelTestResponse:
    """对嵌入任务中的模型执行嵌入测试。

    嵌入模型不支持 chat/completions 接口，直接发对话测试会被服务商拒绝，
    因此改为调用嵌入接口验证可用性。
    """
    start_time = time.time()
    try:
        orchestrator = _SingleModelTestOrchestrator(model_name=model_name)
        result = await orchestrator.get_embedding("MaiBot 模型可用性测试")
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return ModelTestResponse(
            success=True,
            model_name=result.model_name or model_name,
            visual_tested=False,
            tool_call_ok=False,
            response=f"嵌入向量维度: {len(result.embedding)}",
            latency_ms=latency_ms,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"嵌入模型测试失败: model={model_name}, error={e}", exc_info=True)
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return ModelTestResponse(
            success=False,
            model_name=model_name,
            visual_tested=False,
            tool_call_ok=False,
            latency_ms=latency_ms,
            error=str(e),
        )


def _build_model_test_prompt(visual_enabled: bool) -> str:
    """构造单模型测试提示词。"""
    image_instruction = (
        "本次消息还附带了一张测试图片，请在工具参数 saw_image 中填 true，并简要描述图片。"
        if visual_enabled
        else "本次消息没有附带图片，请在工具参数 saw_image 中填 false。"
    )
    return (
        "你正在执行 MaiBot WebUI 的单模型能力测试。\n"
        "测试目标：确认模型可以读取普通文本，并可以按工具定义发起 tool call。\n"
        f"{image_instruction}\n"
        f"请必须调用工具 {MODEL_TEST_TOOL_NAME}，不要只用普通文本回答。\n"
        "工具参数要求：status 填 ok，echo 填 maibot model test。"
    )


def _build_model_test_tools() -> List[Dict[str, Any]]:
    """构造模型测试使用的工具定义。"""
    return [
        {
            "name": MODEL_TEST_TOOL_NAME,
            "description": "回报 MaiBot WebUI 模型测试结果。",
            "parameters_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "测试状态，成功时填 ok。",
                        "enum": ["ok"],
                    },
                    "echo": {
                        "type": "string",
                        "description": "文本回显，固定填 maibot model test。",
                    },
                    "saw_image": {
                        "type": "boolean",
                        "description": "本次请求中是否包含并识别了测试图片。",
                    },
                    "image_summary": {
                        "type": "string",
                        "description": "如果包含图片，简短描述图片内容；未包含图片时留空。",
                    },
                },
                "required": ["status", "echo", "saw_image"],
            },
        }
    ]


def _build_model_test_message_factory(visual_enabled: bool):
    """构造可按客户端图片格式能力生成消息的工厂。"""

    def message_factory(client) -> List[Message]:
        builder = MessageBuilder().add_text_content(_build_model_test_prompt(visual_enabled))
        if visual_enabled:
            builder.add_image_content(
                image_format="png",
                image_base64=MODEL_TEST_IMAGE_BASE64,
                support_formats=client.get_support_image_formats(),
            )
        return [builder.build()]

    return message_factory


def _serialize_model_test_tool_calls(tool_calls: List[ToolCall] | None) -> List[ModelTestToolCall]:
    """将内部工具调用对象转换为 WebUI 响应结构。"""
    return [
        ModelTestToolCall(
            id=tool_call.call_id,
            name=tool_call.func_name,
            arguments=tool_call.args or {},
        )
        for tool_call in (tool_calls or [])
    ]


def _build_model_test_response(
    *,
    model_name: str,
    visual_tested: bool,
    latency_ms: float | None,
    result: LLMResponseResult | None = None,
    error: str | None = None,
) -> ModelTestResponse:
    """根据 LLM 调用结果构造模型测试响应。"""
    tool_calls = _serialize_model_test_tool_calls(result.tool_calls if result is not None else None)
    tool_call_ok = any(tool_call.name == MODEL_TEST_TOOL_NAME for tool_call in tool_calls)
    success = result is not None and tool_call_ok and not error
    if result is not None and not tool_call_ok and not error:
        error = f"模型未按要求调用测试工具 {MODEL_TEST_TOOL_NAME}"

    return ModelTestResponse(
        success=success,
        model_name=result.model_name if result is not None and result.model_name else model_name,
        visual_tested=visual_tested,
        tool_call_ok=tool_call_ok,
        response=result.response if result is not None else "",
        reasoning=result.reasoning if result is not None else "",
        tool_calls=tool_calls,
        latency_ms=latency_ms,
        error=error,
        prompt_tokens=result.prompt_tokens if result is not None else 0,
        completion_tokens=result.completion_tokens if result is not None else 0,
        total_tokens=result.total_tokens if result is not None else 0,
    )


@router.get("/list")
async def get_provider_models(
    provider_name: str = Query(..., description="提供商名称"),
    parser: str = Query("openai", description="响应解析器类型 (openai | gemini)"),
    endpoint: str = Query("/models", description="获取模型列表的端点"),
):
    """获取指定提供商的可用模型列表。

    通过提供商名称查找配置，然后请求对应的模型列表端点。
    """
    # 获取提供商配置
    provider_config = _get_provider_config(provider_name)
    if not provider_config:
        raise HTTPException(status_code=404, detail=f"未找到提供商: {provider_name}")

    base_url = provider_config.get("base_url")
    api_key = provider_config.get("api_key")
    client_type = provider_config.get("client_type", "openai")

    if not base_url:
        raise HTTPException(status_code=400, detail="提供商配置缺少 base_url")
    if not api_key:
        raise HTTPException(status_code=400, detail="提供商配置缺少 api_key")

    resolved_endpoint = provider_config.get("model_list_endpoint", endpoint) if endpoint == "/models" else endpoint

    # 获取模型列表
    models = await _fetch_models_from_provider(
        base_url=base_url,
        api_key=api_key,
        endpoint=resolved_endpoint,
        parser=parser,
        client_type=client_type,
        auth_type=provider_config.get("auth_type", "bearer"),
        auth_header_name=provider_config.get("auth_header_name", "Authorization"),
        auth_header_prefix=provider_config.get("auth_header_prefix", "Bearer"),
        auth_query_name=provider_config.get("auth_query_name", "api_key"),
        default_headers=provider_config.get("default_headers", {}),
        default_query=provider_config.get("default_query", {}),
    )

    return {
        "success": True,
        "models": models,
        "provider": provider_name,
        "count": len(models),
    }


@router.get("/list-by-url")
async def get_models_by_url(
    base_url: str = Query(..., description="提供商的基础 URL"),
    api_key: str = Query(..., description="API Key"),
    parser: str = Query("openai", description="响应解析器类型 (openai | gemini)"),
    endpoint: str = Query("/models", description="获取模型列表的端点"),
    client_type: str = Query("openai", description="客户端类型 (openai | gemini)"),
    auth_type: str = Query("bearer", description="鉴权方式 (bearer | header | query | none)"),
    auth_header_name: str = Query("Authorization", description="Header 鉴权名称"),
    auth_header_prefix: str = Query("Bearer", description="Header 鉴权前缀"),
    auth_query_name: str = Query("api_key", description="Query 鉴权参数名"),
):
    """通过 URL 直接获取模型列表。"""
    models = await _fetch_models_from_provider(
        base_url=base_url,
        api_key=api_key,
        endpoint=endpoint,
        parser=parser,
        client_type=client_type,
        auth_type=auth_type,
        auth_header_name=auth_header_name,
        auth_header_prefix=auth_header_prefix,
        auth_query_name=auth_query_name,
    )

    return {
        "success": True,
        "models": models,
        "count": len(models),
    }


@router.get("/test-connection")
async def test_provider_connection(
    base_url: str = Query(..., description="提供商的基础 URL"),
    api_key: Optional[str] = Query(None, description="API Key（可选，用于验证 Key 有效性）"),
    client_type: str = Query("openai", description="客户端类型 (openai | gemini)"),
):
    """
    测试提供商连接状态

    分两步测试：
    1. 网络连通性测试：向 base_url 发送请求，检查是否能连接
    2. API Key 验证（可选）：如果提供了 api_key，尝试获取模型列表验证 Key 是否有效

    返回：
    - network_ok: 网络是否连通
    - api_key_valid: API Key 是否有效（仅在提供 api_key 时返回）
    - latency_ms: 响应延迟（毫秒）
    - error: 错误信息（如果有）
    """
    import time

    base_url = _normalize_url(base_url)
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url 不能为空")

    try:
        base_url = validate_public_url(base_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = {
        "network_ok": False,
        "api_key_valid": None,
        "latency_ms": None,
        "error": None,
        "http_status": None,
    }

    # 第一步：测试网络连通性
    try:
        start_time = time.time()
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # 尝试 GET 请求 base_url（不需要 API Key）
            response = await client.get(base_url)
            latency = (time.time() - start_time) * 1000

            result["network_ok"] = True
            result["latency_ms"] = round(latency, 2)
            result["http_status"] = response.status_code

    except httpx.ConnectError as e:
        result["error"] = f"连接失败：无法连接到服务器 ({str(e)})"
        return result
    except httpx.TimeoutException:
        result["error"] = "连接超时：服务器响应时间过长"
        return result
    except httpx.RequestError as e:
        result["error"] = f"请求错误：{str(e)}"
        return result
    except Exception as e:
        result["error"] = f"未知错误：{str(e)}"
        return result

    # 第二步：如果提供了 API Key，验证其有效性
    if api_key:
        try:
            start_time = time.time()
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                headers = {"Content-Type": "application/json"}
                params = {}

                if client_type == "gemini":
                    # Gemini 使用 URL 参数传递 API Key
                    params["key"] = api_key
                else:
                    # OpenAI 兼容格式使用 Authorization 头
                    headers["Authorization"] = f"Bearer {api_key}"

                # 尝试获取模型列表
                models_url = f"{base_url}/models"
                response = await client.get(models_url, headers=headers, params=params)

                if response.status_code == 200:
                    result["api_key_valid"] = True
                elif response.status_code in (401, 403):
                    result["api_key_valid"] = False
                    result["error"] = "API Key 无效或已过期"
                else:
                    # 其他状态码，可能是端点不支持，但 Key 可能是有效的
                    result["api_key_valid"] = None

        except Exception as e:
            # API Key 验证失败不影响网络连通性结果
            logger.warning(f"API Key 验证失败: {e}")
            result["api_key_valid"] = None

    return result


@router.post("/test-connection-by-name")
async def test_provider_connection_by_name(
    provider_name: str = Query(..., description="提供商名称"),
):
    """
    通过提供商名称测试连接（从配置文件读取信息）
    """
    # 读取配置文件
    model_config_path = os.path.join(CONFIG_DIR, "model_config.toml")
    if not os.path.exists(model_config_path):
        raise HTTPException(status_code=404, detail="配置文件不存在")

    with open(model_config_path, "r", encoding="utf-8") as f:
        config = tomlkit.load(f)

    # 查找提供商
    providers = config.get("api_providers", [])
    provider = next((item for item in providers if item.get("name") == provider_name), None)

    if not provider:
        raise HTTPException(status_code=404, detail=f"未找到提供商: {provider_name}")

    base_url = provider.get("base_url", "")
    api_key = provider.get("api_key", "")
    client_type = provider.get("client_type", "openai")

    if not base_url:
        raise HTTPException(status_code=400, detail="提供商配置缺少 base_url")

    # 调用测试接口
    return await test_provider_connection(
        base_url=base_url,
        api_key=api_key if api_key else None,
        client_type=client_type,
    )

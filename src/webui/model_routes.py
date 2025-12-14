"""
模型列表获取API路由

提供从各个 AI 厂商 API 获取可用模型列表的代理接口
"""

import os
import httpx
from fastapi import APIRouter, HTTPException, Query, Depends, Cookie, Header
from typing import Optional
import tomlkit

from src.common.logger import get_logger
from src.config.config import CONFIG_DIR
from src.webui.auth import verify_auth_token_from_cookie_or_header

logger = get_logger("webui")

router = APIRouter(prefix="/models", tags=["models"])


def require_auth(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """认证依赖：验证用户是否已登录"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


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


def _normalize_url(url: str) -> str:
    """规范化 URL（去掉尾部斜杠）"""
    if not url:
        return ""
    return url.rstrip("/")


def _parse_openai_response(data: dict) -> list[dict]:
    """
    解析 OpenAI 格式的模型列表响应

    格式: { "data": [{ "id": "gpt-4", "object": "model", ... }] }
    """
    models = []
    if "data" in data and isinstance(data["data"], list):
        for model in data["data"]:
            if isinstance(model, dict) and "id" in model:
                models.append(
                    {
                        "id": model["id"],
                        "name": model.get("name") or model["id"],
                        "owned_by": model.get("owned_by", ""),
                    }
                )
    return models


def _parse_gemini_response(data: dict) -> list[dict]:
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
) -> list[dict]:
    """
    从提供商 API 获取模型列表

    Args:
        base_url: 提供商的基础 URL
        api_key: API 密钥
        endpoint: 获取模型列表的端点
        parser: 响应解析器类型 ('openai' | 'gemini')
        client_type: 客户端类型 ('openai' | 'gemini')

    Returns:
        模型列表
    """
    url = f"{_normalize_url(base_url)}{endpoint}"

    # 根据客户端类型设置请求头
    headers = {}
    params = {}

    if client_type == "gemini":
        # Gemini 使用 URL 参数传递 API Key
        params["key"] = api_key
    else:
        # OpenAI 兼容格式使用 Authorization 头
        headers["Authorization"] = f"Bearer {api_key}"

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


def _get_provider_config(provider_name: str) -> Optional[dict]:
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
        for provider in providers:
            if provider.get("name") == provider_name:
                return dict(provider)

        return None
    except Exception as e:
        logger.error(f"读取提供商配置失败: {e}")
        return None


@router.get("/list")
async def get_provider_models(
    provider_name: str = Query(..., description="提供商名称"),
    parser: str = Query("openai", description="响应解析器类型 (openai | gemini)"),
    endpoint: str = Query("/models", description="获取模型列表的端点"),
    _auth: bool = Depends(require_auth),
):
    """
    获取指定提供商的可用模型列表

    通过提供商名称查找配置，然后请求对应的模型列表端点
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

    # 获取模型列表
    models = await _fetch_models_from_provider(
        base_url=base_url,
        api_key=api_key,
        endpoint=endpoint,
        parser=parser,
        client_type=client_type,
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
    _auth: bool = Depends(require_auth),
):
    """
    通过 URL 直接获取模型列表（用于自定义提供商）
    """
    models = await _fetch_models_from_provider(
        base_url=base_url,
        api_key=api_key,
        endpoint=endpoint,
        parser=parser,
        client_type=client_type,
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
    _auth: bool = Depends(require_auth),
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
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                # 尝试获取模型列表
                models_url = f"{base_url}/models"
                response = await client.get(models_url, headers=headers)

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
    _auth: bool = Depends(require_auth),
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
    provider = None
    for p in providers:
        if p.get("name") == provider_name:
            provider = p
            break

    if not provider:
        raise HTTPException(status_code=404, detail=f"未找到提供商: {provider_name}")

    base_url = provider.get("base_url", "")
    api_key = provider.get("api_key", "")

    if not base_url:
        raise HTTPException(status_code=400, detail="提供商配置缺少 base_url")

    # 调用测试接口
    return await test_provider_connection(base_url=base_url, api_key=api_key if api_key else None)

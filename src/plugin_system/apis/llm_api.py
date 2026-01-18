"""LLM API模块

提供了与LLM模型交互的功能
使用方式：
    from src.plugin_system.apis import llm_api
    models = llm_api.get_available_models()
    success, response, reasoning, model_name = await llm_api.generate_with_model(prompt, model_config)
"""

from typing import Tuple, Dict, List, Any, Optional, Callable
from src.common.logger import get_logger
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.payload_content.message import Message
from src.llm_models.model_client.base_client import BaseClient
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.config.model_configs import TaskConfig

logger = get_logger("llm_api")

# =============================================================================
# LLM模型API函数
# =============================================================================


def get_available_models() -> Dict[str, TaskConfig]:
    """获取所有可用的模型配置

    Returns:
        Dict[str, Any]: 模型配置字典，key为模型名称，value为模型配置
    """
    try:
        # 自动获取所有属性并转换为字典形式
        models = model_config.model_task_config
        attrs = dir(models)
        rets: Dict[str, TaskConfig] = {}
        for attr in attrs:
            if not attr.startswith("__"):
                try:
                    value = getattr(models, attr)
                    if not callable(value) and isinstance(value, TaskConfig):
                        rets[attr] = value
                except Exception as e:
                    logger.debug(f"[LLMAPI] 获取属性 {attr} 失败: {e}")
                    continue
        return rets

    except Exception as e:
        logger.error(f"[LLMAPI] 获取可用模型失败: {e}")
        return {}


async def generate_with_model(
    prompt: str,
    model_config: TaskConfig,
    request_type: str = "plugin.generate",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[bool, str, str, str]:
    """使用指定模型生成内容

    Args:
        prompt: 提示词
        model_config: 模型配置（从 get_available_models 获取的模型配置）
        request_type: 请求类型标识

    Returns:
        Tuple[bool, str, str, str]: (是否成功, 生成的内容, 推理过程, 模型名称)
    """
    try:
        # model_name_list = model_config.model_list
        # logger.info(f"[LLMAPI] 使用模型集合 {model_name_list} 生成内容")
        logger.debug(f"[LLMAPI] 完整提示词: {prompt}")

        llm_request = LLMRequest(model_set=model_config, request_type=request_type)

        response, (reasoning_content, model_name, _) = await llm_request.generate_response_async(
            prompt, temperature=temperature, max_tokens=max_tokens
        )
        return True, response, reasoning_content, model_name

    except Exception as e:
        error_msg = f"生成内容时出错: {str(e)}"
        logger.error(f"[LLMAPI] {error_msg}")
        return False, error_msg, "", ""


async def generate_with_model_with_tools(
    prompt: str,
    model_config: TaskConfig,
    tool_options: List[Dict[str, Any]] | None = None,
    request_type: str = "plugin.generate",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[bool, str, str, str, List[ToolCall] | None]:
    """使用指定模型和工具生成内容

    Args:
        prompt: 提示词
        model_config: 模型配置（从 get_available_models 获取的模型配置）
        tool_options: 工具选项列表
        request_type: 请求类型标识
        temperature: 温度参数
        max_tokens: 最大token数

    Returns:
        Tuple[bool, str, str, str]: (是否成功, 生成的内容, 推理过程, 模型名称)
    """
    try:
        model_name_list = model_config.model_list
        logger.info(f"使用模型{model_name_list}生成内容")
        logger.debug(f"完整提示词: {prompt}")

        llm_request = LLMRequest(model_set=model_config, request_type=request_type)

        response, (reasoning_content, model_name, tool_call) = await llm_request.generate_response_async(
            prompt, tools=tool_options, temperature=temperature, max_tokens=max_tokens
        )
        return True, response, reasoning_content, model_name, tool_call

    except Exception as e:
        error_msg = f"生成内容时出错: {str(e)}"
        logger.error(f"[LLMAPI] {error_msg}")
        return False, error_msg, "", "", None


async def generate_with_model_with_tools_by_message_factory(
    message_factory: Callable[[BaseClient], List[Message]],
    model_config: TaskConfig,
    tool_options: List[Dict[str, Any]] | None = None,
    request_type: str = "plugin.generate",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[bool, str, str, str, List[ToolCall] | None]:
    """使用指定模型和工具生成内容（通过消息工厂构建消息列表）

    Args:
        message_factory: 消息工厂函数
        model_config: 模型配置
        tool_options: 工具选项列表
        request_type: 请求类型标识
        temperature: 温度参数
        max_tokens: 最大token数

    Returns:
        Tuple[bool, str, str, str, List[ToolCall] | None]: (是否成功, 生成的内容, 推理过程, 模型名称, 工具调用列表)
    """
    try:
        model_name_list = model_config.model_list
        logger.info(f"使用模型 {model_name_list} 生成内容")

        llm_request = LLMRequest(model_set=model_config, request_type=request_type)

        response, (reasoning_content, model_name, tool_call) = await llm_request.generate_response_with_message_async(
            message_factory=message_factory,
            tools=tool_options,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return True, response, reasoning_content, model_name, tool_call

    except Exception as e:
        error_msg = f"生成内容时出错: {str(e)}"
        logger.error(f"[LLMAPI] {error_msg}")
        return False, error_msg, "", "", None

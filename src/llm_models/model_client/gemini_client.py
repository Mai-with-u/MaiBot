import asyncio
import io
import base64
from typing import Callable, AsyncIterator, Optional, Coroutine, Any, List, Dict

from google import genai
from google.genai.types import (
    Content,
    Part,
    FunctionDeclaration,
    GenerateContentResponse,
    ContentListUnion,
    ContentUnion,
    ThinkingConfig,
    Tool,
    GoogleSearch,
    GenerateContentConfig,
    EmbedContentResponse,
    EmbedContentConfig,
    SafetySetting,
    HttpOptions,
    HarmCategory,
    HarmBlockThreshold,
)
from google.genai.errors import (
    ClientError,
    ServerError,
    UnknownFunctionCallArgumentError,
    UnsupportedFunctionError,
    FunctionInvocationError,
)

from src.config.api_ada_configs import ModelInfo, APIProvider
from src.common.logger import get_logger

from .base_client import APIResponse, UsageRecord, BaseClient, client_registry
from ..exceptions import (
    RespParseException,
    NetworkConnectionError,
    RespNotOkException,
    ReqAbortException,
    EmptyResponseException,
)
from ..payload_content.message import Message, RoleType
from ..payload_content.resp_format import RespFormat, RespFormatType
from ..payload_content.tool_option import ToolOption, ToolParam, ToolCall

logger = get_logger("Gemini客户端")

# gemini_thinking参数（默认范围）
# 不同模型的思考预算范围配置
THINKING_BUDGET_LIMITS = {
    "gemini-2.5-flash": {"min": 1, "max": 24576, "can_disable": True},
    "gemini-2.5-flash-lite": {"min": 512, "max": 24576, "can_disable": True},
    "gemini-2.5-pro": {"min": 128, "max": 32768, "can_disable": False},
}
# 思维预算特殊值
THINKING_BUDGET_AUTO = -1  # 自动调整思考预算，由模型决定
THINKING_BUDGET_DISABLED = 0  # 禁用思考预算（如果模型允许禁用）

gemini_safe_settings = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY, threshold=HarmBlockThreshold.BLOCK_NONE),
]


def _convert_messages(
    messages: list[Message],
) -> tuple[ContentListUnion, list[str] | None]:
    """
    转换消息格式 - 将消息转换为Gemini API所需的格式
    :param messages: 消息列表
    :return: 转换后的消息列表(和可能存在的system消息)
    """

    def _convert_message_item(message: Message) -> Content:
        """
        转换单个消息格式，除了system和tool类型的消息
        :param message: 消息对象
        :return: 转换后的消息字典
        """

        # 将openai格式的角色重命名为gemini格式的角色
        if message.role == RoleType.Assistant:
            role = "model"
        elif message.role == RoleType.User:
            role = "user"
        else:
            raise ValueError(f"Unsupported role: {message.role}")

        # 添加Content
        if isinstance(message.content, str):
            content = [Part.from_text(text=message.content)]
        elif isinstance(message.content, list):
            content: List[Part] = []
            for item in message.content:
                if isinstance(item, tuple):
                    image_format = "jpeg" if item[0].lower() == "jpg" else item[0].lower()
                    content.append(Part.from_bytes(data=base64.b64decode(item[1]), mime_type=f"image/{image_format}"))
                elif isinstance(item, str):
                    content.append(Part.from_text(text=item))
        else:
            raise RuntimeError("无法触及的代码：请使用MessageBuilder类构建消息对象")

        return Content(role=role, parts=content)

    temp_list: list[ContentUnion] = []
    system_instructions: list[str] = []
    for message in messages:
        if message.role == RoleType.System:
            if isinstance(message.content, str):
                system_instructions.append(message.content)
            else:
                raise ValueError("你tm怎么往system里面塞图片base64？")
        elif message.role == RoleType.Tool:
            if not message.tool_call_id:
                raise ValueError("无法触及的代码：请使用MessageBuilder类构建消息对象")
        else:
            temp_list.append(_convert_message_item(message))
    if system_instructions:
        # 如果有system消息，就把它加上去
        ret: tuple = (temp_list, system_instructions)
    else:
        # 如果没有system消息，就直接返回
        ret: tuple = (temp_list, None)

    return ret


def _convert_tool_options(tool_options: list[ToolOption]) -> list[FunctionDeclaration]:
    """
    转换工具选项格式 - 将工具选项转换为Gemini API所需的格式
    :param tool_options: 工具选项列表
    :return: 转换后的工具对象列表
    """

    def _convert_tool_param(tool_option_param: ToolParam) -> dict:
        """
        转换单个工具参数格式
        :param tool_option_param: 工具参数对象
        :return: 转换后的工具参数字典
        """
        # JSON Schema要求使用"boolean"而不是"bool"
        param_type_value = tool_option_param.param_type.value
        if param_type_value == "bool":
            param_type_value = "boolean"

        return_dict: dict[str, Any] = {
            "type": param_type_value,
            "description": tool_option_param.description,
        }
        if tool_option_param.enum_values:
            return_dict["enum"] = tool_option_param.enum_values
        return return_dict

    def _convert_tool_option_item(tool_option: ToolOption) -> FunctionDeclaration:
        """
        转换单个工具项格式
        :param tool_option: 工具选项对象
        :return: 转换后的Gemini工具选项对象
        """
        ret: dict[str, Any] = {
            "name": tool_option.name,
            "description": tool_option.description,
        }
        if tool_option.params:
            ret["parameters"] = {
                "type": "object",
                "properties": {param.name: _convert_tool_param(param) for param in tool_option.params},
                "required": [param.name for param in tool_option.params if param.required],
            }
        ret1 = FunctionDeclaration(**ret)
        return ret1

    return [_convert_tool_option_item(tool_option) for tool_option in tool_options]


def _process_delta(
    delta: GenerateContentResponse,
    fc_delta_buffer: io.StringIO,
    tool_calls_buffer: list[tuple[str, str, dict[str, Any]]],
    resp: APIResponse | None = None,
):
    if not hasattr(delta, "candidates") or not delta.candidates:
        raise RespParseException(delta, "响应解析失败，缺失candidates字段")

    # 处理 thought（Gemini 的特殊字段）
    for c in getattr(delta, "candidates", []):
        if c.content and getattr(c.content, "parts", None):
            for p in c.content.parts:
                if getattr(p, "thought", False) and getattr(p, "text", None):
                    # 保存到 reasoning_content
                    if resp is not None:
                        resp.reasoning_content = (resp.reasoning_content or "") + p.text
                elif getattr(p, "text", None):
                    # 正常输出写入 buffer
                    fc_delta_buffer.write(p.text)

    if delta.function_calls:  # 为什么不用hasattr呢，是因为这个属性一定有，即使是个空的
        for call in delta.function_calls:
            try:
                if not isinstance(call.args, dict):  # gemini返回的function call参数就是dict格式的了
                    raise RespParseException(delta, "响应解析失败，工具调用参数无法解析为字典类型")
                if not call.id or not call.name:
                    raise RespParseException(delta, "响应解析失败，工具调用缺失id或name字段")
                tool_calls_buffer.append(
                    (
                        call.id,
                        call.name,
                        call.args or {},  # 如果args是None，则转换为一个空字典
                    )
                )
            except Exception as e:
                raise RespParseException(delta, "响应解析失败，无法解析工具调用参数") from e


def _build_stream_api_resp(
    _fc_delta_buffer: io.StringIO,
    _tool_calls_buffer: list[tuple[str, str, dict]],
    last_resp: GenerateContentResponse | None = None,  # 传入 last_resp
    resp: APIResponse | None = None,
) -> APIResponse:
    # sourcery skip: simplify-len-comparison, use-assigned-variable
    if resp is None:
        resp = APIResponse()

    if _fc_delta_buffer.tell() > 0:
        # 如果正式内容缓冲区不为空，则将其写入APIResponse对象
        resp.content = _fc_delta_buffer.getvalue()
    _fc_delta_buffer.close()
    if len(_tool_calls_buffer) > 0:
        # 如果工具调用缓冲区不为空，则将其解析为ToolCall对象列表
        resp.tool_calls = []
        for call_id, function_name, arguments_buffer in _tool_calls_buffer:
            if arguments_buffer is not None:
                arguments = arguments_buffer
                if not isinstance(arguments, dict):
                    raise RespParseException(
                        None,
                        f"响应解析失败，工具调用参数无法解析为字典类型。工具调用参数原始响应：\n{arguments_buffer}",
                    )
            else:
                arguments = None

            resp.tool_calls.append(ToolCall(call_id, function_name, arguments))

    # 检查是否因为 max_tokens 截断
    reason = None
    if last_resp and getattr(last_resp, "candidates", None):
        for c in last_resp.candidates:
            fr = getattr(c, "finish_reason", None) or getattr(c, "finishReason", None)
            if fr:
                reason = str(fr)
                break

    if str(reason).endswith("MAX_TOKENS"):
        has_visible_output = bool(resp.content and resp.content.strip())
        if has_visible_output:
            logger.warning(
                "⚠ Gemini 响应因达到 max_tokens 限制被部分截断，\n"
                "    可能会对回复内容造成影响，建议修改模型 max_tokens 配置！"
            )
        else:
            logger.warning("⚠ Gemini 响应因达到 max_tokens 限制被截断，\n    请修改模型 max_tokens 配置！")

    if not resp.content and not resp.tool_calls:
        if not getattr(resp, "reasoning_content", None):
            raise EmptyResponseException()

    return resp


async def _default_stream_response_handler(
    resp_stream: AsyncIterator[GenerateContentResponse],
    interrupt_flag: asyncio.Event | None,
) -> tuple[APIResponse, Optional[tuple[int, int, int]]]:
    """
    流式响应处理函数 - 处理Gemini API的流式响应
    :param resp_stream: 流式响应对象,是一个神秘的iterator，我完全不知道这个玩意能不能跑，不过遍历一遍之后它就空了，如果跑不了一点的话可以考虑改成别的东西
    :return: APIResponse对象
    """
    _fc_delta_buffer = io.StringIO()  # 正式内容缓冲区，用于存储接收到的正式内容
    _tool_calls_buffer: list[tuple[str, str, dict]] = []  # 工具调用缓冲区，用于存储接收到的工具调用
    _usage_record = None  # 使用情况记录
    last_resp: GenerateContentResponse | None = None  # 保存最后一个 chunk
    resp = APIResponse()

    def _insure_buffer_closed():
        if _fc_delta_buffer and not _fc_delta_buffer.closed:
            _fc_delta_buffer.close()

    async for chunk in resp_stream:
        last_resp = chunk  # 保存最后一个响应
        # 检查是否有中断量
        if interrupt_flag and interrupt_flag.is_set():
            # 如果中断量被设置，则抛出ReqAbortException
            raise ReqAbortException("请求被外部信号中断")

        _process_delta(
            chunk,
            _fc_delta_buffer,
            _tool_calls_buffer,
            resp=resp,
        )

        if chunk.usage_metadata:
            # 如果有使用情况，则将其存储在APIResponse对象中
            _usage_record = (
                chunk.usage_metadata.prompt_token_count or 0,
                (chunk.usage_metadata.candidates_token_count or 0) + (chunk.usage_metadata.thoughts_token_count or 0),
                chunk.usage_metadata.total_token_count or 0,
            )

    try:
        return _build_stream_api_resp(
            _fc_delta_buffer,
            _tool_calls_buffer,
            last_resp=last_resp,
            resp=resp,
        ), _usage_record
    except Exception:
        # 确保缓冲区被关闭
        _insure_buffer_closed()
        raise


def _default_normal_response_parser(
    resp: GenerateContentResponse,
) -> tuple[APIResponse, Optional[tuple[int, int, int]]]:
    """
    解析对话补全响应 - 将Gemini API响应解析为APIResponse对象
    :param resp: 响应对象
    :return: APIResponse对象
    """
    api_response = APIResponse()

    # 解析思考内容
    try:
        if candidates := resp.candidates:
            if candidates[0].content and candidates[0].content.parts:
                for part in candidates[0].content.parts:
                    if not part.text:
                        continue
                    if part.thought:
                        api_response.reasoning_content = (
                            api_response.reasoning_content + part.text if api_response.reasoning_content else part.text
                        )
    except Exception as e:
        logger.warning(f"解析思考内容时发生错误: {e}，跳过解析")

    # 解析响应内容
    api_response.content = resp.text

    # 解析工具调用
    if function_calls := resp.function_calls:
        api_response.tool_calls = []
        for call in function_calls:
            try:
                if not isinstance(call.args, dict):
                    raise RespParseException(resp, "响应解析失败，工具调用参数无法解析为字典类型")
                if not call.name:
                    raise RespParseException(resp, "响应解析失败，工具调用缺失name字段")
                api_response.tool_calls.append(ToolCall(call.id or "gemini-tool_call", call.name, call.args or {}))
            except Exception as e:
                raise RespParseException(resp, "响应解析失败，无法解析工具调用参数") from e

    # 解析使用情况
    if usage_metadata := resp.usage_metadata:
        _usage_record = (
            usage_metadata.prompt_token_count or 0,
            (usage_metadata.candidates_token_count or 0) + (usage_metadata.thoughts_token_count or 0),
            usage_metadata.total_token_count or 0,
        )
    else:
        _usage_record = None

    api_response.raw_data = resp

    # 检查是否因为 max_tokens 截断
    try:
        if resp.candidates:
            c0 = resp.candidates[0]
            reason = getattr(c0, "finish_reason", None) or getattr(c0, "finishReason", None)
            if reason and "MAX_TOKENS" in str(reason):
                # 检查第二个及之后的 parts 是否有内容
                has_real_output = False
                if getattr(c0, "content", None) and getattr(c0.content, "parts", None):
                    for p in c0.content.parts[1:]:  # 跳过第一个 thought
                        if getattr(p, "text", None) and p.text.strip():
                            has_real_output = True
                            break

                if not has_real_output and getattr(resp, "text", None):
                    has_real_output = True

                if has_real_output:
                    logger.warning(
                        "⚠ Gemini 响应因达到 max_tokens 限制被部分截断，\n"
                        "    可能会对回复内容造成影响，建议修改模型 max_tokens 配置！"
                    )
                else:
                    logger.warning("⚠ Gemini 响应因达到 max_tokens 限制被截断，\n    请修改模型 max_tokens 配置！")

                return api_response, _usage_record
    except Exception as e:
        logger.debug(f"检查 MAX_TOKENS 截断时异常: {e}")

    # 最终的、唯一的空响应检查
    if not api_response.content and not api_response.tool_calls:
        raise EmptyResponseException("响应中既无文本内容也无工具调用")

    return api_response, _usage_record


@client_registry.register_client_class("gemini")
class GeminiClient(BaseClient):
    client: genai.Client

    def __init__(self, api_provider: APIProvider):
        super().__init__(api_provider)

        # 增加传入参数处理
        http_options_kwargs: Dict[str, Any] = {}

        # 秒转换为毫秒传入
        if api_provider.timeout is not None:
            http_options_kwargs["timeout"] = int(api_provider.timeout * 1000)

        # 传入并处理地址和版本(必须为Gemini格式)
        if api_provider.base_url:
            parts = api_provider.base_url.rstrip("/").rsplit("/", 1)
            if len(parts) == 2 and parts[1].startswith("v"):
                http_options_kwargs["base_url"] = f"{parts[0]}/"
                http_options_kwargs["api_version"] = parts[1]
            else:
                http_options_kwargs["base_url"] = api_provider.base_url
                http_options_kwargs["api_version"] = None
        self.client = genai.Client(
            http_options=HttpOptions(**http_options_kwargs),
            api_key=api_provider.api_key,
        )  # 这里和openai不一样，gemini会自己决定自己是否需要retry

    @staticmethod
    def clamp_thinking_budget(extra_params: dict[str, Any] | None, model_id: str) -> int:
        """
        按模型限制思考预算范围，仅支持指定的模型（支持带数字后缀的新版本）
        """
        limits = None

        # 参数传入处理
        tb = THINKING_BUDGET_AUTO
        if extra_params and "thinking_budget" in extra_params:
            try:
                tb = int(extra_params["thinking_budget"])
            except (ValueError, TypeError):
                logger.warning(
                    f"无效的 thinking_budget 值 {extra_params['thinking_budget']}，将使用模型自动预算模式 {tb}"
                )

        # 优先尝试精确匹配
        if model_id in THINKING_BUDGET_LIMITS:
            limits = THINKING_BUDGET_LIMITS[model_id]
        else:
            # 按 key 长度倒序，保证更长的（更具体的，如 -lite）优先
            sorted_keys = sorted(THINKING_BUDGET_LIMITS.keys(), key=len, reverse=True)
            for key in sorted_keys:
                # 必须满足：完全等于 或者 前缀匹配（带 "-" 边界）
                if model_id == key or model_id.startswith(f"{key}-"):
                    limits = THINKING_BUDGET_LIMITS[key]
                    break

        # 预算值处理
        if tb == THINKING_BUDGET_AUTO:
            return THINKING_BUDGET_AUTO
        if tb == THINKING_BUDGET_DISABLED:
            if limits and limits.get("can_disable", False):
                return THINKING_BUDGET_DISABLED
            if limits:
                logger.warning(f"模型 {model_id} 不支持禁用思考预算，已回退到最小值 {limits['min']}")
                return limits["min"]
            return THINKING_BUDGET_AUTO

        # 已知模型范围裁剪 + 提示
        if limits:
            if tb < limits["min"]:
                logger.warning(f"模型 {model_id} 的 thinking_budget={tb} 过小，已调整为最小值 {limits['min']}")
                return limits["min"]
            if tb > limits["max"]:
                logger.warning(f"模型 {model_id} 的 thinking_budget={tb} 过大，已调整为最大值 {limits['max']}")
                return limits["max"]
            return tb

        # 未知模型 → 默认自动模式
        logger.warning(f"模型 {model_id} 未在 THINKING_BUDGET_LIMITS 中定义，已启用模型自动预算兼容")
        return THINKING_BUDGET_AUTO

    async def get_response(
        self,
        model_info: ModelInfo,
        message_list: list[Message],
        tool_options: list[ToolOption] | None = None,
        max_tokens: Optional[int] = 1024,
        temperature: Optional[float] = 0.4,
        response_format: RespFormat | None = None,
        stream_response_handler: Optional[
            Callable[
                [AsyncIterator[GenerateContentResponse], asyncio.Event | None],
                Coroutine[Any, Any, tuple[APIResponse, Optional[tuple[int, int, int]]]],
            ]
        ] = None,
        async_response_parser: Optional[
            Callable[[GenerateContentResponse], tuple[APIResponse, Optional[tuple[int, int, int]]]]
        ] = None,
        interrupt_flag: asyncio.Event | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> APIResponse:
        """
        获取对话响应
        Args:
            model_info: 模型信息
            message_list: 对话体
            tool_options: 工具选项（可选，默认为None）
            max_tokens: 最大token数（可选，默认为1024）
            temperature: 温度（可选，默认为0.7）
            response_format: 响应格式（默认为text/plain,如果是输入的JSON Schema则必须遵守OpenAPI3.0格式,理论上和openai是一样的，暂不支持其它相应格式输入）
            stream_response_handler: 流式响应处理函数（可选，默认为default_stream_response_handler）
            async_response_parser: 响应解析函数（可选，默认为default_response_parser）
            interrupt_flag: 中断信号量（可选，默认为None）
        Returns:
            APIResponse对象，包含响应内容、推理内容、工具调用等信息
        """
        if stream_response_handler is None:
            stream_response_handler = _default_stream_response_handler

        if async_response_parser is None:
            async_response_parser = _default_normal_response_parser

        # 将messages构造为Gemini API所需的格式
        messages = _convert_messages(message_list)
        # 将tool_options转换为Gemini API所需的格式
        tools = _convert_tool_options(tool_options) if tool_options else None
        # 解析并裁剪 thinking_budget
        tb = self.clamp_thinking_budget(extra_params, model_info.model_identifier)
        # 检测是否为带 -search 的模型
        enable_google_search = False
        model_identifier = model_info.model_identifier
        if model_identifier.endswith("-search"):
            enable_google_search = True
            # 去掉后缀并更新模型ID
            model_identifier = model_identifier.removesuffix("-search")
            model_info.model_identifier = model_identifier
            logger.info(f"模型已启用 GoogleSearch 功能：{model_identifier}")

        # 将response_format转换为Gemini API所需的格式
        generation_config_dict = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_modalities": ["TEXT"],
            "thinking_config": ThinkingConfig(
                include_thoughts=True,
                thinking_budget=tb,
            ),
            "safety_settings": gemini_safe_settings,  # 防止空回复问题
        }
        if tools:
            generation_config_dict["tools"] = Tool(function_declarations=tools)
        if messages[1]:
            # 如果有system消息，则将其添加到配置中
            generation_config_dict["system_instructions"] = messages[1]
        if response_format and response_format.format_type == RespFormatType.TEXT:
            generation_config_dict["response_mime_type"] = "text/plain"
        elif response_format and response_format.format_type in (RespFormatType.JSON_OBJ, RespFormatType.JSON_SCHEMA):
            generation_config_dict["response_mime_type"] = "application/json"
            generation_config_dict["response_schema"] = response_format.to_dict()
        # 自动启用 GoogleSearch grounding_tool
        if enable_google_search:
            grounding_tool = Tool(google_search=GoogleSearch())
            if "tools" in generation_config_dict:
                existing = generation_config_dict["tools"]
                if isinstance(existing, list):
                    existing.append(grounding_tool)
                else:
                    generation_config_dict["tools"] = [existing, grounding_tool]
            else:
                generation_config_dict["tools"] = [grounding_tool]

        generation_config = GenerateContentConfig(**generation_config_dict)

        try:
            if model_info.force_stream_mode:
                req_task = asyncio.create_task(
                    self.client.aio.models.generate_content_stream(
                        model=model_info.model_identifier,
                        contents=messages[0],
                        config=generation_config,
                    )
                )
                while not req_task.done():
                    if interrupt_flag and interrupt_flag.is_set():
                        # 如果中断量存在且被设置，则取消任务并抛出异常
                        req_task.cancel()
                        raise ReqAbortException("请求被外部信号中断")
                    await asyncio.sleep(0.1)  # 等待0.1秒后再次检查任务&中断信号量状态
                resp, usage_record = await stream_response_handler(req_task.result(), interrupt_flag)
            else:
                req_task = asyncio.create_task(
                    self.client.aio.models.generate_content(
                        model=model_info.model_identifier,
                        contents=messages[0],
                        config=generation_config,
                    )
                )
                while not req_task.done():
                    if interrupt_flag and interrupt_flag.is_set():
                        # 如果中断量存在且被设置，则取消任务并抛出异常
                        req_task.cancel()
                        raise ReqAbortException("请求被外部信号中断")
                    await asyncio.sleep(0.5)  # 等待0.5秒后再次检查任务&中断信号量状态

                resp, usage_record = async_response_parser(req_task.result())
        except (ClientError, ServerError) as e:
            # 重封装 ClientError 和 ServerError 为 RespNotOkException
            raise RespNotOkException(e.code, e.message) from None
        except (
            UnknownFunctionCallArgumentError,
            UnsupportedFunctionError,
            FunctionInvocationError,
        ) as e:
            # 工具调用相关错误
            raise RespParseException(None, f"工具调用参数错误: {str(e)}") from None
        except EmptyResponseException as e:
            # 保持原始异常，便于区分“空响应”和网络异常
            raise e
        except Exception as e:
            # 其他未预料的错误，才归为网络连接类
            raise NetworkConnectionError() from e

        if usage_record:
            resp.usage = UsageRecord(
                model_name=model_info.name,
                provider_name=model_info.api_provider,
                prompt_tokens=usage_record[0],
                completion_tokens=usage_record[1],
                total_tokens=usage_record[2],
            )

        return resp

    async def get_embedding(
        self,
        model_info: ModelInfo,
        embedding_input: str,
        extra_params: dict[str, Any] | None = None,
    ) -> APIResponse:
        """
        获取文本嵌入
        :param model_info: 模型信息
        :param embedding_input: 嵌入输入文本
        :return: 嵌入响应
        """
        try:
            raw_response: EmbedContentResponse = await self.client.aio.models.embed_content(
                model=model_info.model_identifier,
                contents=embedding_input,
                config=EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
            )
        except (ClientError, ServerError) as e:
            # 重封装ClientError和ServerError为RespNotOkException
            raise RespNotOkException(e.code) from None
        except Exception as e:
            raise NetworkConnectionError() from e

        response = APIResponse()

        # 解析嵌入响应和使用情况
        if hasattr(raw_response, "embeddings") and raw_response.embeddings:
            response.embedding = raw_response.embeddings[0].values
        else:
            raise RespParseException(raw_response, "响应解析失败，缺失embeddings字段")

        response.usage = UsageRecord(
            model_name=model_info.name,
            provider_name=model_info.api_provider,
            prompt_tokens=len(embedding_input),
            completion_tokens=0,
            total_tokens=len(embedding_input),
        )

        return response

    async def get_audio_transcriptions(
        self,
        model_info: ModelInfo,
        audio_base64: str,
        max_tokens: Optional[int] = 2048,
        extra_params: dict[str, Any] | None = None,
    ) -> APIResponse:
        """
        获取音频转录
        :param model_info: 模型信息
        :param audio_base64: 音频文件的Base64编码字符串
        :param max_tokens: 最大输出token数（默认2048）
        :param extra_params: 额外参数（可选）
        :return: 转录响应
        """
        # 解析并裁剪 thinking_budget
        tb = self.clamp_thinking_budget(extra_params, model_info.model_identifier)

        # 构造 prompt + 音频输入
        prompt = "Generate a transcript of the speech. The language of the transcript should **match the language of the speech**."
        contents = [
            Content(
                role="user",
                parts=[
                    Part.from_text(text=prompt),
                    Part.from_bytes(data=base64.b64decode(audio_base64), mime_type="audio/wav"),
                ],
            )
        ]

        generation_config_dict = {
            "max_output_tokens": max_tokens,
            "response_modalities": ["TEXT"],
            "thinking_config": ThinkingConfig(
                include_thoughts=True,
                thinking_budget=tb,
            ),
            "safety_settings": gemini_safe_settings,
        }
        generate_content_config = GenerateContentConfig(**generation_config_dict)

        try:
            raw_response: GenerateContentResponse = await self.client.aio.models.generate_content(
                model=model_info.model_identifier,
                contents=contents,
                config=generate_content_config,
            )
            resp, usage_record = _default_normal_response_parser(raw_response)
        except (ClientError, ServerError) as e:
            # 重封装ClientError和ServerError为RespNotOkException
            raise RespNotOkException(e.code) from None
        except Exception as e:
            raise NetworkConnectionError() from e

        if usage_record:
            resp.usage = UsageRecord(
                model_name=model_info.name,
                provider_name=model_info.api_provider,
                prompt_tokens=usage_record[0],
                completion_tokens=usage_record[1],
                total_tokens=usage_record[2],
            )

        return resp

    def get_support_image_formats(self) -> list[str]:
        """
        获取支持的图片格式
        :return: 支持的图片格式列表
        """
        return ["png", "jpg", "jpeg", "webp", "heic", "heif"]

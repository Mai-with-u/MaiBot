from enum import Enum
from typing import Any

from src.common.i18n import t

from .config_base import ConfigBase, Field


class OpenAICompatibleAuthType(str, Enum):
    """OpenAI 兼容接口的鉴权方式。"""

    BEARER = "bearer"
    HEADER = "header"
    QUERY = "query"
    NONE = "none"


class ReasoningParseMode(str, Enum):
    """推理内容解析策略。"""

    AUTO = "auto"
    NATIVE = "native"
    THINK_TAG = "think_tag"
    NONE = "none"


class ToolArgumentParseMode(str, Enum):
    """工具调用参数的解析策略。"""

    AUTO = "auto"
    STRICT = "strict"
    REPAIR = "repair"
    DOUBLE_DECODE = "double_decode"


class APIProvider(ConfigBase):
    """API提供商配置类"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """API服务商名称 (可随意命名, 在models的api-provider中需使用这个命名)"""

    base_url: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """API服务商的BaseURL"""

    api_key: str = Field(
        default_factory=str,
        repr=False,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "key",
        },
    )
    """API密钥。对于不需要鉴权的兼容端点，可将 `auth_type` 设为 `none`。"""

    client_type: str = Field(
        default="openai",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "settings",
        },
    )
    """客户端类型 (可选: openai/google, 默认为openai)"""

    auth_type: str = Field(
        default=OpenAICompatibleAuthType.BEARER.value,
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shield",
        },
    )
    """OpenAI 兼容接口的鉴权方式。可选值：`bearer`、`header`、`query`、`none`。"""

    auth_header_name: str = Field(
        default="Authorization",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "header",
        },
    )
    """当 `auth_type` 为 `header` 时使用的请求头名称。"""

    auth_header_prefix: str = Field(
        default="Bearer",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "shield-check",
        },
    )
    """当 `auth_type` 为 `header` 时使用的请求头前缀。留空表示直接发送原始密钥。"""

    auth_query_name: str = Field(
        default="api_key",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """当 `auth_type` 为 `query` 时使用的查询参数名称。"""

    default_headers: dict[str, str] = Field(
        default_factory=dict,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "header",
        },
    )
    """所有请求默认附带的 HTTP Header。"""

    default_query: dict[str, str] = Field(
        default_factory=dict,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list-filter",
        },
    )
    """所有请求默认附带的查询参数。"""

    organization: str | None = Field(
        default=None,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "building-2",
        },
    )
    """OpenAI 官方接口可选的 `organization`。"""

    project: str | None = Field(
        default=None,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "folder-kanban",
        },
    )
    """OpenAI 官方接口可选的 `project`。"""

    model_list_endpoint: str = Field(
        default="/models",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "list",
        },
    )
    """模型列表端点路径。适用于 OpenAI 兼容接口的探测与管理。"""

    reasoning_parse_mode: str = Field(
        default=ReasoningParseMode.AUTO.value,
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "brain",
        },
    )
    """推理内容解析模式。可选值：`auto`、`native`、`think_tag`、`none`。"""

    tool_argument_parse_mode: str = Field(
        default=ToolArgumentParseMode.AUTO.value,
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "braces",
        },
    )
    """工具参数解析模式。可选值：`auto`、`strict`、`repair`、`double_decode`。"""

    max_retry: int = Field(
        default=3,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "repeat",
        },
    )
    """最大重试次数 (单个模型API调用失败, 最多重试的次数)"""

    timeout: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
            "step": 1,
        },
    )
    """API调用的超时时长 (超过这个时长, 本次请求将被视为"请求超时", 单位: 秒)"""

    retry_interval: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "timer",
            "step": 1,
        },
    )
    """重试间隔 (如果API调用失败, 重试的间隔时间, 单位: 秒)"""

    def model_post_init(self, context: Any = None) -> None:
        """执行 API 提供商配置的后置校验。

        Args:
            context: Pydantic 传入的上下文对象。

        Raises:
            ValueError: 当配置项缺失或组合不合法时抛出。
        """
        if self.auth_type != OpenAICompatibleAuthType.NONE and not self.api_key:
            raise ValueError(t("config.api_key_empty"))
        if not self.base_url and self.client_type != "gemini":  # TODO: 允许gemini使用base_url
            raise ValueError(t("config.api_base_url_empty"))
        if not self.name:
            raise ValueError(t("config.api_provider_name_empty"))
        if self.auth_type == OpenAICompatibleAuthType.HEADER and not self.auth_header_name.strip():
            raise ValueError("当 auth_type=header 时，auth_header_name 不能为空")
        if self.auth_type == OpenAICompatibleAuthType.QUERY and not self.auth_query_name.strip():
            raise ValueError("当 auth_type=query 时，auth_query_name 不能为空")
        super().model_post_init(context)


class ModelInfo(ConfigBase):
    """单个模型信息配置类"""

    _validate_any: bool = False
    suppress_any_warning: bool = True

    model_identifier: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "package",
        },
    )
    """模型标识符 (API服务商提供的模型标识符)"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """模型名称 (可随意命名, 在models中需使用这个命名)"""

    api_provider: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "link",
        },
    )
    """API服务商名称 (对应在api_providers中配置的服务商名称)"""

    price_in: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "dollar-sign",
            "step": 0.001,
        },
    )
    """输入价格 (用于API调用统计, 单位：元/ M token) (可选, 若无该字段, 默认值为0)"""

    cache: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """是否启用模型输入缓存计费。开启后命中缓存的输入 token 使用 cache_price_in 计费。"""

    cache_price_in: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "database-zap",
            "step": 0.001,
        },
    )
    """缓存命中输入价格 (用于API调用统计, 单位：元/ M token)。仅当 cache=true 时使用。"""

    price_out: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "dollar-sign",
            "step": 0.001,
        },
    )
    """输出价格 (用于API调用统计, 单位：元/ M token) (可选, 若无该字段, 默认值为0)"""

    temperature: float | None = Field(
        default=None,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "thermometer",
        },
    )
    """模型级别温度（可选），会覆盖任务配置和调用方显式传入的温度"""

    max_tokens: int | None = Field(
        default=None,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
        },
    )
    """模型级别最大token数（可选），会覆盖任务配置中的max_tokens"""

    force_stream_mode: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "zap",
        },
    )
    """强制流式输出模式 (若模型不支持非流式输出, 请设置为true启用强制流式输出, 默认值为false)"""

    visual: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "image",
        },
    )
    """是否为多模态模型。开启后表示该模型支持视觉输入。"""

    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "sliders",
        },
    )
    """额外参数 (用于API调用时的额外配置)。
    OpenAI 兼容客户端会将该字典拆分为请求附加项：headers 会作为请求头传入，query 会作为 URL 查询参数传入，body 会合并到请求体。
    未放入 headers/query/body 的普通键，也会作为请求体额外字段传入；例如 {enable_thinking = "false"} 会传为请求体字段 enable_thinking。
    该字段不会以 extra_params 这个键整体发送给模型服务商。
    temperature 和 max_tokens 也可写在此处作为模型级默认值，但更推荐使用同名独立配置项。
    Gemini 客户端会按自身支持的字段筛选并映射到 GenerateContentConfig、EmbedContentConfig 或音频请求配置中。"""

    def model_post_init(self, context: Any = None):
        self.model_identifier = self.model_identifier.strip()
        if not self.model_identifier:
            raise ValueError(t("config.model_identifier_empty_generic"))
        if not self.name:
            raise ValueError(t("config.model_name_empty"))
        if not self.api_provider:
            raise ValueError(t("config.model_api_provider_empty"))
        return super().model_post_init(context)


class TaskConfig(ConfigBase):
    """任务配置类"""

    model_list: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """使用的模型列表, 每个元素对应上面的模型名称(name)"""

    max_tokens: int = Field(
        default=4096,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
            "step": 1,
        },
    )
    """任务最大输出token数"""

    temperature: float = Field(
        default=0.3,
        ge=0,
        le=2,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "thermometer",
            "step": 0.1,
        },
    )
    """模型温度"""

    slow_threshold: float = Field(
        default=15.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "alert-circle",
            "step": 0.1,
            "advanced": True,
        },
    )
    """超时警告时间（秒），超过此时间会输出警告日志"""

    selection_strategy: str = Field(
        default="balance",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shuffle",
            "options": ["balance", "random", "sequential"],
        },
    )
    """模型选择策略：balance（负载均衡）、random（随机选择）或 sequential（按配置顺序优先选择）"""

    hard_timeout: float = Field(
        default=240.0,
        ge=1.0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
            "step": 1.0,
            "advanced": True,
        },
    )
    """任务硬超时（秒），到点未返回则取消请求并尝试切换下一个模型；防止上游代理静默排队导致主循环饥饿"""


class ModelTaskConfig(ConfigBase):
    """模型配置类"""

    replyer: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "message-square",
        },
    )
    """回复模型，影响麦麦的回复表现"""

    planner: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "map",
        },
    )
    """规划模型，决定麦麦的行动，需要有一定Agent能力的模型"""

    memory: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "brain",
            "advanced": True,
        },
    )
    """记忆模型配置，用于长期记忆总结、抽取、写回等高记忆任务；留空时由调用方按需回退"""

    mid_memory: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "archive",
            "advanced": True,
        },
    )
    """聊天回想模型配置；留空时自动继用 planner 模型"""

    utils: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "wrench",
        },
    )
    """执行文本概括，整理等小任务，是麦麦必须的模型。可以选择速度快的小尺寸模型"""

    learner: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "graduation-cap",
            "advanced": True,
        },
    )
    """学习模型配置，用于表达方式学习和黑话学习；留空时用 utils 模型"""

    expression_use: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "message-circle-more",
            "advanced": True,
        },
    )
    """表达方式使用模型配置；留空时用 utils 模型"""

    emoji: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "smile",
            "advanced": True,
        },
    )
    """表情包发送模型配置；留空时保持原有 planner/vlm 选择逻辑"""

    vlm: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "image",
        },
    )
    """视觉模型，需要能够识图的模型"""

    voice: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "volume-2",
            "advanced": True,
        },
    )
    """语音识别模型"""

    embedding: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "database",
        },
    )
    """嵌入模型，需要文本嵌入类型的模型，不可使用LLM"""

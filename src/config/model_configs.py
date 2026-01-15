from typing import Any
from .config_base import ConfigBase, Field


class APIProvider(ConfigBase):
    """API提供商配置类"""

    name: str = ""
    """API服务商名称 (可随意命名, 在models的api-provider中需使用这个命名)"""

    base_url: str = ""
    """API服务商的BaseURL"""

    api_key: str = Field(default_factory=str, repr=False)
    """API密钥"""

    client_type: str = Field(default="openai")
    """客户端类型 (可选: openai/google, 默认为openai)"""

    max_retry: int = Field(default=2)
    """最大重试次数 (单个模型API调用失败, 最多重试的次数)"""

    timeout: int = 10
    """API调用的超时时长 (超过这个时长, 本次请求将被视为"请求超时", 单位: 秒)"""

    retry_interval: int = 10
    """重试间隔 (如果API调用失败, 重试的间隔时间, 单位: 秒)"""

    def model_post_init(self, context: Any = None):
        """确保api_key在repr中不被显示"""
        if not self.api_key:
            raise ValueError("API密钥不能为空, 请在配置中设置有效的API密钥。")
        if not self.base_url and self.client_type != "gemini":  # TODO: 允许gemini使用base_url
            raise ValueError("API基础URL不能为空, 请在配置中设置有效的基础URL。")
        if not self.name:
            raise ValueError("API提供商名称不能为空, 请在配置中设置有效的名称。")
        return super().model_post_init(context)


class ModelInfo(ConfigBase):
    """单个模型信息配置类"""
    _validate_any: bool = False
    suppress_any_warning: bool = True

    model_identifier: str = ""
    """模型标识符 (API服务商提供的模型标识符)"""

    name: str = ""
    """模型名称 (可随意命名, 在models中需使用这个命名)"""

    api_provider: str = ""
    """API服务商名称 (对应在api_providers中配置的服务商名称)"""

    price_in: float = Field(default=0.0)
    """输入价格 (用于API调用统计, 单位：元/ M token) (可选, 若无该字段, 默认值为0)"""

    price_out: float = Field(default=0.0)
    """输出价格 (用于API调用统计, 单位：元/ M token) (可选, 若无该字段, 默认值为0)"""
    
    temperature: float | None = Field(default=None)
    """模型级别温度（可选），会覆盖任务配置中的温度"""

    max_tokens: int | None = Field(default=None)
    """模型级别最大token数（可选），会覆盖任务配置中的max_tokens"""

    force_stream_mode: bool = Field(default=False)
    """强制流式输出模式 (若模型不支持非流式输出, 请设置为true启用强制流式输出, 默认值为false)"""

    extra_params: dict[str, Any] = Field(default_factory=dict)
    """额外参数 (用于API调用时的额外配置)"""

    def model_post_init(self, context: Any = None):
        if not self.model_identifier:
            raise ValueError("模型标识符不能为空, 请在配置中设置有效的模型标识符。")
        if not self.name:
            raise ValueError("模型名称不能为空, 请在配置中设置有效的模型名称。")
        if not self.api_provider:
            raise ValueError("API提供商不能为空, 请在配置中设置有效的API提供商。")
        return super().model_post_init(context)


class TaskConfig(ConfigBase):
    """任务配置类"""

    model_list: list[str] = Field(default_factory=list)
    """使用的模型列表, 每个元素对应上面的模型名称(name)"""

    max_tokens: int = 1024
    """任务最大输出token数"""

    temperature: float = 0.3
    """模型温度"""
    
    slow_threshold: float = 15.0
    """慢请求阈值（秒），超过此值会输出警告日志"""

    selection_strategy: str = Field(default="balance")
    """模型选择策略：balance（负载均衡）或 random（随机选择）"""


class ModelTaskConfig(ConfigBase):
    """模型配置类"""

    utils: TaskConfig = Field(default_factory=TaskConfig)
    """组件使用的模型, 例如表情包模块, 取名模块, 关系模块, 麦麦的情绪变化等，是麦麦必须的模型"""

    replyer: TaskConfig = Field(default_factory=TaskConfig)
    """首要回复模型配置, 还用于表达器和表达方式学习"""

    vlm: TaskConfig = Field(default_factory=TaskConfig)
    """视觉模型配置"""

    voice: TaskConfig = Field(default_factory=TaskConfig)
    """语音识别模型配置"""

    tool_use: TaskConfig = Field(default_factory=TaskConfig)
    """工具使用模型配置, 需要使用支持工具调用的模型"""

    planner: TaskConfig = Field(default_factory=TaskConfig)
    """规划模型配置"""

    embedding: TaskConfig = Field(default_factory=TaskConfig)
    """嵌入模型配置"""

    lpmm_entity_extract: TaskConfig = Field(default_factory=TaskConfig)
    """LPMM实体提取模型配置"""

    lpmm_rdf_build: TaskConfig = Field(default_factory=TaskConfig)
    """LPMM RDF构建模型配置"""

from dataclasses import dataclass, field

from .config_base import ConfigBase


@dataclass
class APIProvider(ConfigBase):
    """API提供商配置类"""

    name: str
    """API提供商名称"""

    base_url: str
    """API基础URL"""

    api_key: str = field(default_factory=str, repr=False)
    """API密钥列表"""

    client_type: str = field(default="openai")
    """客户端类型（如openai/google等，默认为openai）"""

    max_retry: int = 2
    """最大重试次数（单个模型API调用失败，最多重试的次数）"""

    timeout: int = 10
    """API调用的超时时长（超过这个时长，本次请求将被视为“请求超时”，单位：秒）"""

    retry_interval: int = 10
    """重试间隔（如果API调用失败，重试的间隔时间，单位：秒）"""

    def get_api_key(self) -> str:
        return self.api_key

    def __post_init__(self):
        """确保api_key在repr中不被显示"""
        if not self.api_key:
            raise ValueError("API密钥不能为空，请在配置中设置有效的API密钥。")
        if not self.base_url and self.client_type != "gemini":
            raise ValueError("API基础URL不能为空，请在配置中设置有效的基础URL。")
        if not self.name:
            raise ValueError("API提供商名称不能为空，请在配置中设置有效的名称。")


@dataclass
class ModelInfo(ConfigBase):
    """单个模型信息配置类"""

    model_identifier: str
    """模型标识符（用于URL调用）"""

    name: str
    """模型名称（用于模块调用）"""

    api_provider: str
    """API提供商（如OpenAI、Azure等）"""

    price_in: float = field(default=0.0)
    """每M token输入价格"""

    price_out: float = field(default=0.0)
    """每M token输出价格"""

    temperature: float | None = field(default=None)
    """模型级别温度（可选），会覆盖任务配置中的温度"""

    max_tokens: int | None = field(default=None)
    """模型级别最大token数（可选），会覆盖任务配置中的max_tokens"""

    force_stream_mode: bool = field(default=False)
    """是否强制使用流式输出模式"""

    extra_params: dict = field(default_factory=dict)
    """额外参数（用于API调用时的额外配置）"""

    def __post_init__(self):
        if not self.model_identifier:
            raise ValueError("模型标识符不能为空，请在配置中设置有效的模型标识符。")
        if not self.name:
            raise ValueError("模型名称不能为空，请在配置中设置有效的模型名称。")
        if not self.api_provider:
            raise ValueError("API提供商不能为空，请在配置中设置有效的API提供商。")


@dataclass
class TaskConfig(ConfigBase):
    """任务配置类"""

    model_list: list[str] = field(default_factory=list)
    """任务使用的模型列表"""

    max_tokens: int = 1024
    """任务最大输出token数"""

    temperature: float = 0.3
    """模型温度"""

    slow_threshold: float = 15.0
    """慢请求阈值（秒），超过此值会输出警告日志"""

    selection_strategy: str = field(default="balance")
    """模型选择策略：balance（负载均衡）或 random（随机选择）"""


@dataclass
class ModelTaskConfig(ConfigBase):
    """模型配置类"""

    utils: TaskConfig
    """组件模型配置"""

    replyer: TaskConfig
    """normal_chat首要回复模型模型配置"""

    vlm: TaskConfig
    """视觉语言模型配置"""

    voice: TaskConfig
    """语音识别模型配置"""

    tool_use: TaskConfig
    """专注工具使用模型配置"""

    planner: TaskConfig
    """规划模型配置"""

    embedding: TaskConfig
    """嵌入模型配置"""

    lpmm_entity_extract: TaskConfig
    """LPMM实体提取模型配置"""

    lpmm_rdf_build: TaskConfig
    """LPMM RDF构建模型配置"""

    def get_task(self, task_name: str) -> TaskConfig:
        """获取指定任务的配置"""
        if hasattr(self, task_name):
            return getattr(self, task_name)
        raise ValueError(f"任务 '{task_name}' 未找到对应的配置")

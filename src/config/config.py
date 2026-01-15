from pathlib import Path
from typing import TypeVar
from datetime import datetime
from typing import Any

import tomlkit
import sys

from .official_configs import (
    BotConfig,
    PersonalityConfig,
    ExpressionConfig,
    ChatConfig,
    EmojiConfig,
    KeywordReactionConfig,
    ChineseTypoConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    ExperimentalConfig,
    MessageReceiveConfig,
    MaimMessageConfig,
    LPMMKnowledgeConfig,
    RelationshipConfig,
    ToolConfig,
    VoiceConfig,
    MemoryConfig,
    DebugConfig,
    DreamConfig,
    WebUIConfig,
)
from .model_configs import ModelInfo, ModelTaskConfig, APIProvider
from .config_base import ConfigBase, Field, AttributeData
from .config_utils import recursive_parse_item_to_table, output_config_changes, compare_versions

from src.common.logger import get_logger

"""
如果你想要修改配置文件，请递增version的值

版本格式：主版本号.次版本号.修订号，版本号递增规则如下：
    主版本号：MMC版本更新
    次版本号：配置文件内容大更新
    修订号：配置文件内容小更新
"""

PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.absolute().resolve()
CONFIG_DIR: Path = PROJECT_ROOT / "config"
BOT_CONFIG_PATH: Path = (CONFIG_DIR / "bot_config.toml").resolve().absolute()
MODEL_CONFIG_PATH: Path = (CONFIG_DIR / "model_config.toml").resolve().absolute()
MMC_VERSION: str = "0.13.0"
CONFIG_VERSION: str = "8.0.0"
MODEL_CONFIG_VERSION: str = "1.12.0"

logger = get_logger("config")

T = TypeVar("T", bound="ConfigBase")


class Config(ConfigBase):
    """总配置类"""

    bot: BotConfig = Field(default_factory=BotConfig)
    """机器人配置类"""

    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    """人格配置类"""

    expression: ExpressionConfig = Field(default_factory=ExpressionConfig)
    """表达配置类"""

    chat: ChatConfig = Field(default_factory=ChatConfig)
    """聊天配置类"""

    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    """记忆配置类"""

    relationship: RelationshipConfig = Field(default_factory=RelationshipConfig)
    """关系配置类"""

    message_receive: MessageReceiveConfig = Field(default_factory=MessageReceiveConfig)
    """消息接收配置类"""

    dream: DreamConfig = Field(default_factory=DreamConfig)
    """做梦配置类"""

    tool: ToolConfig = Field(default_factory=ToolConfig)
    """工具配置类"""

    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    """语音配置类"""

    emoji: EmojiConfig = Field(default_factory=EmojiConfig)
    """表情包配置类"""

    keyword_reaction: KeywordReactionConfig = Field(default_factory=KeywordReactionConfig)
    """关键词反应配置类"""

    response_post_process: ResponsePostProcessConfig = Field(default_factory=ResponsePostProcessConfig)
    """回复后处理配置类"""

    chinese_typo: ChineseTypoConfig = Field(default_factory=ChineseTypoConfig)
    """中文错别字生成器配置类"""

    response_splitter: ResponseSplitterConfig = Field(default_factory=ResponseSplitterConfig)
    """回复分割器配置类"""

    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    """遥测配置类"""

    debug: DebugConfig = Field(default_factory=DebugConfig)
    """调试配置类"""

    experimental: ExperimentalConfig = Field(default_factory=ExperimentalConfig)
    """实验性功能配置类"""

    maim_message: MaimMessageConfig = Field(default_factory=MaimMessageConfig)
    """maim_message配置类"""

    lpmm_knowledge: LPMMKnowledgeConfig = Field(default_factory=LPMMKnowledgeConfig)
    """LPMM知识库配置类"""

    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    """WebUI配置类"""


class ModelConfig(ConfigBase):
    """模型配置类"""

    models: list[ModelInfo] = Field(default_factory=list)
    """模型配置列表"""

    model_task_config: ModelTaskConfig = Field(default_factory=ModelTaskConfig)
    """模型任务配置"""

    api_providers: list[APIProvider] = Field(default_factory=list)
    """API提供商列表"""

    def model_post_init(self, context: Any = None):
        if not self.models:
            raise ValueError("模型列表不能为空，请在配置中设置有效的模型列表。")
        if not self.api_providers:
            raise ValueError("API提供商列表不能为空，请在配置中设置有效的API提供商列表。")

        # 检查API提供商名称是否重复
        provider_names = [provider.name for provider in self.api_providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("API提供商名称存在重复，请检查配置文件。")

        # 检查模型名称是否重复
        model_names = [model.name for model in self.models]
        if len(model_names) != len(set(model_names)):
            raise ValueError("模型名称存在重复，请检查配置文件。")

        api_providers_dict = {provider.name: provider for provider in self.api_providers}

        for model in self.models:
            if not model.model_identifier:
                raise ValueError(f"模型 '{model.name}' 的 model_identifier 不能为空")
            if not model.api_provider or model.api_provider not in api_providers_dict:
                raise ValueError(f"模型 '{model.name}' 的 api_provider '{model.api_provider}' 不存在")
        return super().model_post_init(context)


class ConfigManager:
    """总配置管理类"""

    def __init__(self):
        self.bot_config_path: Path = BOT_CONFIG_PATH
        self.model_config_path: Path = MODEL_CONFIG_PATH
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        logger.info(f"MaiCore当前版本: {MMC_VERSION}")
        logger.info("正在品鉴配置文件...")
        self.global_config: Config = self._load_global_config()
        self.model_config: ModelConfig = self._load_model_config()
        logger.info("非常的新鲜，非常的美味！")

    def _load_global_config(self) -> Config:
        config, updated = load_config_from_file(Config, self.bot_config_path, CONFIG_VERSION)
        if updated:
            sys.exit(0)  # 先直接退出
        return config

    def _load_model_config(self) -> ModelConfig:
        config, updated = load_config_from_file(ModelConfig, self.model_config_path, MODEL_CONFIG_VERSION, True)
        if updated:
            sys.exit(0)  # 先直接退出
        return config

    def get_global_config(self) -> Config:
        return self.global_config

    def get_model_config(self) -> ModelConfig:
        return self.model_config


def generate_new_config_file(config_class: type[T], config_path: Path, inner_config_version: str) -> None:
    """生成新的配置文件

    :param config_class: 配置类
    :param config_path: 配置文件路径
    :param inner_config_version: 配置文件版本号
    """
    config = config_class()
    write_config_to_file(config, config_path, inner_config_version)


def load_config_from_file(
    config_class: type[T], config_path: Path, new_ver: str, override_repr: bool = False
) -> tuple[T, bool]:
    attribute_data = AttributeData()
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = tomlkit.load(f)
    old_ver: str = config_data["inner"]["version"]  # type: ignore
    config_data.remove("inner")  # 移除 inner 部分，避免干扰后续处理
    config_data = config_data.unwrap()  # 转换为普通字典，方便后续处理
    try:
        updated: bool = False
        target_config = config_class.from_dict(attribute_data, config_data)
        if compare_versions(old_ver, new_ver):
            output_config_changes(attribute_data, logger, old_ver, new_ver, config_path.name)
            write_config_to_file(target_config, config_path, new_ver, override_repr)
            updated = True
        return target_config, updated
    except Exception as e:
        logger.critical(f"配置文件{config_path.name}解析失败")
        raise e


def write_config_to_file(
    config: ConfigBase, config_path: Path, inner_config_version: str, override_repr: bool = False
) -> None:
    """将配置写入文件

    :param config: 配置对象
    :param config_path: 配置文件路径
    """
    # 创建空TOMLDocument
    full_config_data = tomlkit.document()

    # 首先写入配置文件版本信息
    version_table = tomlkit.table()
    version_table.add("version", inner_config_version)
    full_config_data.add("inner", version_table)

    # 递归解析配置项为表格
    for config_item_name, config_item in type(config).model_fields.items():
        if not config_item.repr and not override_repr:
            continue
        if config_item_name in ["field_docs", "_validate_any", "suppress_any_warning"]:
            continue
        config_field = getattr(config, config_item_name)
        if isinstance(config_field, ConfigBase):
            full_config_data.add(
                config_item_name, recursive_parse_item_to_table(config_field, override_repr=override_repr)
            )
        elif isinstance(config_field, list):
            aot = tomlkit.aot()
            for item in config_field:
                if not isinstance(item, ConfigBase):
                    raise TypeError("配置写入只支持ConfigBase子类")
                aot.append(recursive_parse_item_to_table(item, override_repr=override_repr))
            full_config_data.add(config_item_name, aot)
        else:
            raise TypeError("配置写入只支持ConfigBase子类")

    # 备份旧文件
    if config_path.exists():
        backup_root = config_path.parent / "old"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_root / f"{config_path.stem}_{timestamp}.toml"
        config_path.replace(backup_path)

    # 写入文件
    with open(config_path, "w", encoding="utf-8") as f:
        tomlkit.dump(full_config_data, f)


# generate_new_config_file(Config, BOT_CONFIG_PATH, CONFIG_VERSION)
config_manager = ConfigManager()
config_manager.initialize()
# global_config = config_manager.get_global_config()

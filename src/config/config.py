from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

import asyncio
import copy
import inspect
import sys

import tomlkit

from .config_base import AttributeData, ConfigBase, Field
from .config_utils import compare_versions, output_config_changes, recursive_parse_item_to_table
from .file_watcher import FileChange, FileWatcher
from .legacy_migration import try_migrate_legacy_bot_config_dict
from .model_configs import APIProvider, ModelInfo, ModelTaskConfig
from .official_configs import (
    BotConfig,
    ChatConfig,
    ChineseTypoConfig,
    DatabaseConfig,
    DebugConfig,
    EmojiConfig,
    ExperimentalConfig,
    ExpressionConfig,
    KeywordReactionConfig,
    LPMMKnowledgeConfig,
    MaiSakaConfig,
    MaimMessageConfig,
    PluginRuntimeConfig,
    MemoryConfig,
    MessageReceiveConfig,
    PersonalityConfig,
    RelationshipConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    ToolConfig,
    VoiceConfig,
    WebUIConfig,
)
from src.common.i18n import t
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
MMC_VERSION: str = "1.0.0"
CONFIG_VERSION: str = "8.1.0"
MODEL_CONFIG_VERSION: str = "1.12.0"

logger = get_logger("config")

T = TypeVar("T", bound="ConfigBase")
ConfigReloadCallback = Callable[[Sequence[str]], object] | Callable[[], object]


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

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    """数据库配置类"""

    maisaka: MaiSakaConfig = Field(default_factory=MaiSakaConfig)
    """MaiSaka对话系统配置类"""

    plugin_runtime: PluginRuntimeConfig = Field(default_factory=PluginRuntimeConfig)
    """插件运行时配置类"""


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
            raise ValueError(t("config.models_empty"))
        if not self.api_providers:
            raise ValueError(t("config.api_providers_empty"))

        # 检查API提供商名称是否重复
        provider_names = [provider.name for provider in self.api_providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError(t("config.api_provider_name_duplicate"))

        # 检查模型名称是否重复
        model_names = [model.name for model in self.models]
        if len(model_names) != len(set(model_names)):
            raise ValueError(t("config.model_name_duplicate"))

        api_providers_dict = {provider.name: provider for provider in self.api_providers}

        for model in self.models:
            if not model.model_identifier:
                raise ValueError(t("config.model_identifier_empty", model_name=model.name))
            if not model.api_provider or model.api_provider not in api_providers_dict:
                raise ValueError(
                    t(
                        "config.model_api_provider_missing",
                        api_provider=model.api_provider,
                        model_name=model.name,
                    )
                )
        return super().model_post_init(context)


class ConfigManager:
    """总配置管理类"""

    def __init__(self):
        self.bot_config_path: Path = BOT_CONFIG_PATH
        self.model_config_path: Path = MODEL_CONFIG_PATH
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.global_config: Config | None = None
        self.model_config: ModelConfig | None = None
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        self._reload_callbacks: list[ConfigReloadCallback] = []
        self._file_watcher: FileWatcher | None = None
        self._file_watcher_subscription_id: str | None = None
        self._hot_reload_min_interval_s: float = 1.0
        self._hot_reload_timeout_s: float = 20.0
        self._last_hot_reload_monotonic: float = 0.0

    def initialize(self):
        logger.info(t("config.current_version", version=MMC_VERSION))
        logger.info(t("config.loading"))
        self.global_config = self.load_global_config()
        self.model_config = self.load_model_config()
        logger.info(t("config.loaded"))

    def load_global_config(self) -> Config:
        config, updated = load_config_from_file(Config, self.bot_config_path, CONFIG_VERSION)
        if updated:
            sys.exit(0)  # 先直接退出
        return config

    def load_model_config(self) -> ModelConfig:
        config, updated = load_config_from_file(ModelConfig, self.model_config_path, MODEL_CONFIG_VERSION, True)
        if updated:
            sys.exit(0)  # 先直接退出
        return config

    def get_global_config(self) -> Config:
        if self.global_config is None:
            raise RuntimeError(t("config.global_not_initialized"))
        return self.global_config

    def get_model_config(self) -> ModelConfig:
        if self.model_config is None:
            raise RuntimeError(t("config.model_not_initialized"))
        return self.model_config

    def register_reload_callback(self, callback: ConfigReloadCallback) -> None:
        """注册配置热重载回调。

        Args:
            callback: 配置热重载回调。允许无参回调，也允许接收
                ``Sequence[str]`` 类型的变更范围列表。
        """

        self._reload_callbacks.append(callback)

    def unregister_reload_callback(self, callback: ConfigReloadCallback) -> None:
        """注销配置热重载回调。

        Args:
            callback: 先前注册过的回调对象。
        """

        try:
            self._reload_callbacks.remove(callback)
        except ValueError:
            return

    @staticmethod
    def _normalize_changed_scopes(changed_scopes: Sequence[str] | None) -> tuple[str, ...]:
        """规范化配置变更范围列表。

        Args:
            changed_scopes: 原始配置变更范围。

        Returns:
            tuple[str, ...]: 去重后的配置变更范围元组。
        """

        if not changed_scopes:
            return ("bot", "model")

        normalized_scopes: list[str] = []
        for scope in changed_scopes:
            normalized_scope = str(scope or "").strip().lower()
            if normalized_scope not in {"bot", "model"}:
                continue
            if normalized_scope not in normalized_scopes:
                normalized_scopes.append(normalized_scope)
        return tuple(normalized_scopes)

    @staticmethod
    def _resolve_changed_scopes(changes: Sequence[FileChange]) -> tuple[str, ...]:
        """根据文件变更列表推断配置变更范围。

        Args:
            changes: 文件监听器返回的变更列表。

        Returns:
            tuple[str, ...]: 命中的配置变更范围元组。
        """

        changed_scopes: list[str] = []
        for change in changes:
            file_name = change.path.name
            if file_name == "bot_config.toml" and "bot" not in changed_scopes:
                changed_scopes.append("bot")
            if file_name == "model_config.toml" and "model" not in changed_scopes:
                changed_scopes.append("model")
        return tuple(changed_scopes)

    @staticmethod
    def _callback_accepts_scopes(callback: ConfigReloadCallback) -> bool:
        """判断回调是否接收配置变更范围参数。

        Args:
            callback: 待检测的回调对象。

        Returns:
            bool: 若回调可接收一个位置参数或可变位置参数，则返回 ``True``。
        """

        try:
            parameters = inspect.signature(callback).parameters.values()
        except (TypeError, ValueError):
            return False

        positional_params = {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        for parameter in parameters:
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                return True
            if parameter.kind in positional_params:
                return True
        return False

    async def _invoke_reload_callback(
        self,
        callback: ConfigReloadCallback,
        changed_scopes: Sequence[str],
    ) -> None:
        """执行单个配置热重载回调。

        Args:
            callback: 要执行的回调对象。
            changed_scopes: 本次热重载命中的配置范围。
        """

        result = callback(changed_scopes) if self._callback_accepts_scopes(callback) else callback()
        if asyncio.iscoroutine(result):
            await result

    async def reload_config(self, changed_scopes: Sequence[str] | None = None) -> bool:
        """重新加载主配置和模型配置。

        Args:
            changed_scopes: 本次触发热重载的配置范围。

        Returns:
            bool: 是否重载成功。
        """

        normalized_scopes = self._normalize_changed_scopes(changed_scopes)
        async with self._reload_lock:
            try:
                global_config_new, global_updated = load_config_from_file(
                    Config,
                    self.bot_config_path,
                    CONFIG_VERSION,
                )
                model_config_new, model_updated = load_config_from_file(
                    ModelConfig,
                    self.model_config_path,
                    MODEL_CONFIG_VERSION,
                    True,
                )
            except Exception as exc:
                logger.error(t("config.reload_failed", error=exc))
                return False

            if global_updated or model_updated:
                logger.warning(t("config.version_update_detected"))

            self.global_config = global_config_new
            self.model_config = model_config_new
            global global_config, model_config
            global_config = global_config_new
            model_config = model_config_new
            logger.info(t("config.hot_reload_completed"))

            for callback in list(self._reload_callbacks):
                try:
                    await self._invoke_reload_callback(callback, normalized_scopes)
                except Exception as exc:
                    logger.warning(t("config.reload_callback_failed", error=exc))
            return True

    async def start_file_watcher(self) -> None:
        if self._file_watcher is not None and self._file_watcher.running:
            return
        self._file_watcher = FileWatcher(
            paths=[self.bot_config_path, self.model_config_path],
            debounce_ms=600,
            callback_timeout_s=15.0,
            callback_failure_threshold=3,
            callback_cooldown_s=30.0,
        )
        self._file_watcher_subscription_id = self._file_watcher.subscribe(
            self._handle_file_changes,
            paths=[self.bot_config_path, self.model_config_path],
        )
        await self._file_watcher.start()
        logger.info(t("config.file_watcher_started"))

    async def stop_file_watcher(self) -> None:
        if self._file_watcher is None:
            return
        if self._file_watcher_subscription_id is not None:
            self._file_watcher.unsubscribe(self._file_watcher_subscription_id)
            self._file_watcher_subscription_id = None
        watcher_stats = self._file_watcher.stats
        logger.info(
            t(
                "config.file_watcher_stop_stats",
                batches=watcher_stats.batches_seen,
                changes=watcher_stats.changes_seen,
                cooldown_skip=watcher_stats.callbacks_skipped_cooldown,
                failed=watcher_stats.callbacks_failed,
                ok=watcher_stats.callbacks_succeeded,
                restart=watcher_stats.restart_count,
                timeout=watcher_stats.callbacks_timed_out,
            )
        )
        await self._file_watcher.stop()
        self._file_watcher = None

    async def _handle_file_changes(self, changes: Sequence[FileChange]) -> None:
        """处理主配置与模型配置文件变更。

        Args:
            changes: 当前批次收集到的文件变更列表。
        """

        if not changes:
            return
        now_monotonic = asyncio.get_running_loop().time()
        if now_monotonic - self._last_hot_reload_monotonic < self._hot_reload_min_interval_s:
            logger.debug(t("config.reload_skipped_too_frequent"))
            return
        self._last_hot_reload_monotonic = now_monotonic
        logger.info(t("config.file_change_detected"))
        try:
            changed_scopes = self._resolve_changed_scopes(changes)
            await asyncio.wait_for(
                self.reload_config(changed_scopes=changed_scopes),
                timeout=self._hot_reload_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.error(t("config.reload_timeout", timeout_seconds=self._hot_reload_timeout_s))


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
    inner_table = config_data.get("inner")
    if not isinstance(inner_table, Mapping):
        raise TypeError(t("config.missing_inner_version"))
    inner_version = inner_table.get("version")
    if not isinstance(inner_version, str):
        raise TypeError(t("config.invalid_inner_version"))
    old_ver: str = inner_version
    config_data.remove("inner")  # 移除 inner 部分，避免干扰后续处理
    config_data = config_data.unwrap()  # 转换为普通字典，方便后续处理
    # 保留一份“干净”的原始数据副本，避免第一次 from_dict 过程中对 dict 的就地修改
    original_data: dict[str, Any] = copy.deepcopy(config_data)
    try:
        updated: bool = False
        try:
            target_config = config_class.from_dict(attribute_data, config_data)
        except TypeError as e:
            # 可拔插的旧配置修复（仅针对 bot_config.toml 的已知结构变更）
            if config_path.name == "bot_config.toml" and config_class.__name__ == "Config":
                # 基于未被部分构造污染的 original_data 做迁移尝试
                mig = try_migrate_legacy_bot_config_dict(original_data)
                if mig.migrated:
                    logger.warning(t("config.legacy_migrated", reason=mig.reason))
                    migrated_data = mig.data
                    target_config = config_class.from_dict(attribute_data, migrated_data)
                else:
                    raise e
            else:
                raise e
        if compare_versions(old_ver, new_ver):
            output_config_changes(attribute_data, logger, old_ver, new_ver, config_path.name)
            write_config_to_file(target_config, config_path, new_ver, override_repr)
            updated = True
        return target_config, updated
    except Exception as e:
        logger.critical(t("config.parse_failed", file_name=config_path.name))
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
                    raise TypeError(t("config.write_unsupported_type"))
                aot.append(recursive_parse_item_to_table(item, override_repr=override_repr))
            full_config_data.add(config_item_name, aot)
        else:
            raise TypeError(t("config.write_unsupported_type"))

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
global_config = config_manager.get_global_config()
model_config = config_manager.get_model_config()

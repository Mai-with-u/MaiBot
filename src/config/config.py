import asyncio
import copy
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

import tomlkit

from src.common.i18n import t
from src.common.logger import get_logger
from src.common.version import read_project_version

from .config_base import AttributeData, ConfigBase, Field
from .config_upgrade_hooks import apply_config_upgrade_hooks
from .config_utils import compare_versions, output_config_changes, recursive_parse_item_to_table
from .default_model_config import create_default_model_config
from .file_watcher import FileChange, FileWatcher
from .legacy_migration import (
    mark_legacy_config_migration_completed,
    migrate_legacy_bind_env_to_bot_config_dict,
    should_apply_legacy_migration,
    try_migrate_legacy_bot_config_dict,
)
from .model_configs import APIProvider, ModelInfo, ModelTaskConfig
from .official_configs import (
    AMemorixConfig,
    BotConfig,
    ChatConfig,
    ChineseTypoConfig,
    DatabaseConfig,
    DebugConfig,
    EmojiConfig,
    ExperimentalConfig,
    ExpressionConfig,
    JargonConfig,
    KeywordReactionConfig,
    LogConfig,
    MaimMessageConfig,
    MCPConfig,
    MessageReceiveConfig,
    PersonalityConfig,
    PluginConfig,
    PluginRuntimeConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    VisualConfig,
    VoiceConfig,
    WebUIConfig,
)

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
LEGACY_ENV_PATH: Path = (PROJECT_ROOT / ".env").resolve().absolute()
A_MEMORIX_LEGACY_CONFIG_PATH: Path = (CONFIG_DIR / "a_memorix.toml").resolve().absolute()
MMC_VERSION: str = read_project_version(PROJECT_ROOT)
CONFIG_VERSION: str = "8.14.26"
MODEL_CONFIG_VERSION: str = "1.17.6"

logger = get_logger("config")

T = TypeVar("T", bound="ConfigBase")
ConfigReloadCallback = Callable[[Sequence[str]], object] | Callable[[], object]


class Config(ConfigBase):
    """总配置类"""

    bot: BotConfig = Field(default_factory=BotConfig)
    """机器人配置类"""

    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    """人格配置类"""

    chat: ChatConfig = Field(default_factory=ChatConfig)
    """聊天配置类"""

    experimental: ExperimentalConfig = Field(default_factory=ExperimentalConfig)
    """实验性功能配置类"""

    visual: VisualConfig = Field(default_factory=VisualConfig)
    """视觉配置类"""

    expression: ExpressionConfig = Field(default_factory=ExpressionConfig)
    """表达配置类"""

    jargon: JargonConfig = Field(default_factory=JargonConfig)
    """黑话配置类"""

    a_memorix: AMemorixConfig = Field(default_factory=AMemorixConfig)
    """A_Memorix 长期记忆子系统配置"""

    message_receive: MessageReceiveConfig = Field(default_factory=MessageReceiveConfig)
    """消息接收配置类"""

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

    log: LogConfig = Field(default_factory=LogConfig)
    """日志配置类"""

    debug: DebugConfig = Field(default_factory=DebugConfig)
    """调试配置类"""

    maim_message: MaimMessageConfig = Field(default_factory=MaimMessageConfig)
    """maim_message配置类"""

    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    """WebUI配置类"""

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    """数据库配置类"""

    mcp: MCPConfig = Field(default_factory=MCPConfig)
    """MCP 配置类"""

    plugin: PluginConfig = Field(default_factory=PluginConfig)
    """插件管理配置类"""

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


def _normalize_a_memorix_legacy_config(config_data: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(config_data)
    web_config = normalized.get("web")
    if isinstance(web_config, dict) and "import" in web_config and "import_config" not in web_config:
        web_config["import_config"] = web_config.pop("import")
    return normalized


def _migrate_legacy_a_memorix_config(config_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if isinstance(config_data.get("a_memorix"), dict):
        return config_data, False
    if not A_MEMORIX_LEGACY_CONFIG_PATH.exists():
        return config_data, False

    try:
        with A_MEMORIX_LEGACY_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            legacy_data = tomlkit.load(handle).unwrap()
    except Exception as exc:
        logger.warning(f"读取旧版 A_Memorix 配置失败，已使用主配置默认值: {A_MEMORIX_LEGACY_CONFIG_PATH}，原因: {exc}")
        return config_data, False

    if not isinstance(legacy_data, dict):
        logger.warning(f"旧版 A_Memorix 配置内容无效，已使用主配置默认值: {A_MEMORIX_LEGACY_CONFIG_PATH}")
        return config_data, False

    migrated_data = copy.deepcopy(config_data)
    migrated_data["a_memorix"] = _normalize_a_memorix_legacy_config(legacy_data)
    logger.warning(
        f"检测到旧版 A_Memorix 配置，已迁移到 bot_config.toml 的 [a_memorix]: {A_MEMORIX_LEGACY_CONFIG_PATH}"
    )
    return migrated_data, True


def _normalize_loaded_bot_config_dict(config_data: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(config_data)
    a_memorix_config = normalized.get("a_memorix")
    if isinstance(a_memorix_config, dict):
        normalized["a_memorix"] = _normalize_a_memorix_legacy_config(a_memorix_config)
    return normalized


class ConfigManager:
    """总配置管理类"""

    VLM_NOT_CONFIGURED_WARNING: str = "未配置视觉识图模型，部分图片理解可能受限，请在webui或model_config中配置"

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
        self.reload_revision: int = 0

    def initialize(self):
        logger.info(t("config.current_version", version=MMC_VERSION))
        logger.info(t("config.loading"))
        self.global_config, global_updated = load_config_from_file(Config, self.bot_config_path, CONFIG_VERSION)
        self.model_config, model_updated = load_config_from_file(
            ModelConfig,
            self.model_config_path,
            MODEL_CONFIG_VERSION,
            True,
        )
        if global_updated or model_updated:
            logger.info("配置已自动升级，将继续使用更新后的配置启动")
        self._warn_if_vlm_not_configured(self.model_config)
        logger.info(t("config.loaded"))

    @classmethod
    def _warn_if_vlm_not_configured(cls, model_config: ModelConfig) -> None:
        if any(model_name.strip() for model_name in model_config.model_task_config.vlm.model_list):
            return
        logger.warning(cls.VLM_NOT_CONFIGURED_WARNING)

    def load_global_config(self) -> Config:
        config, updated = load_config_from_file(Config, self.bot_config_path, CONFIG_VERSION)
        if updated:
            logger.info("bot_config.toml 已自动升级，将继续使用更新后的配置")
        return config

    def load_model_config(self) -> ModelConfig:
        config, updated = load_config_from_file(ModelConfig, self.model_config_path, MODEL_CONFIG_VERSION, True)
        if updated:
            logger.info("model_config.toml 已自动升级，将继续使用更新后的配置")
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

        if self._callback_accepts_scopes(callback):
            callback_with_scopes = cast(Callable[[Sequence[str]], object], callback)
            result = callback_with_scopes(changed_scopes)
        else:
            callback_without_scopes = cast(Callable[[], object], callback)
            result = callback_without_scopes()
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
        if not normalized_scopes:
            logger.debug("配置热重载未命中有效范围，已跳过")
            return True

        async with self._reload_lock:
            try:
                global_config_new = self.global_config
                model_config_new = self.model_config
                global_updated = False
                model_updated = False
                if "bot" in normalized_scopes or global_config_new is None:
                    global_config_new, global_updated = load_config_from_file(
                        Config,
                        self.bot_config_path,
                        CONFIG_VERSION,
                    )
                if "model" in normalized_scopes or model_config_new is None:
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
            self.reload_revision += 1
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


def generate_new_config_file(
    config_class: type[T], config_path: Path, inner_config_version: str, override_repr: bool = False
) -> None:
    """生成新的配置文件

    :param config_class: 配置类
    :param config_path: 配置文件路径
    :param inner_config_version: 配置文件版本号
    """
    if config_class is ModelConfig:
        config = create_default_model_config(config_class)
    else:
        config = config_class()
    write_config_to_file(config, config_path, inner_config_version, override_repr)


def remove_legacy_env_file(env_path: Path) -> None:
    """删除已完成迁移的旧版 `.env` 文件。"""

    if not env_path.exists():
        return

    try:
        env_path.unlink()
    except OSError as exc:
        logger.warning(f"旧版 .env 配置文件删除失败，请手动删除: {env_path}，原因: {exc}")
    else:
        logger.warning(f"检测到旧版环境变量绑定配置迁移成功，已删除旧版 .env 文件: {env_path}")


def load_config_from_file(
    config_class: type[T], config_path: Path, new_ver: str, override_repr: bool = False
) -> tuple[T, bool]:
    attribute_data = AttributeData()
    if not config_path.exists():
        logger.warning(f"配置文件缺失，正在生成默认配置: {config_path}")
        generate_new_config_file(config_class, config_path, new_ver, override_repr)
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = tomlkit.load(f)
    inner_table = config_data.get("inner")
    if not isinstance(inner_table, Mapping):
        raise TypeError(t("config.missing_inner_version"))
    inner_version = inner_table.get("version")
    if not isinstance(inner_version, str):
        raise TypeError(t("config.invalid_inner_version"))
    old_ver: str = inner_version
    env_migration_applied: bool = False
    legacy_config_migration_applied: bool = False
    a_memorix_migration_applied: bool = False
    upgrade_hook_applied: bool = False
    legacy_config_migration_reasons: list[str] = []
    config_data.remove("inner")  # 移除 inner 部分，避免干扰后续处理
    config_data = config_data.unwrap()  # 转换为普通字典，方便后续处理
    legacy_migration_enabled = config_class.__name__ == "Config" and should_apply_legacy_migration(config_path.name)
    if legacy_migration_enabled:
        env_migration = migrate_legacy_bind_env_to_bot_config_dict(config_data)
        env_migration_applied = env_migration.migrated
        if env_migration.migrated:
            logger.warning(f"检测到旧版环境变量绑定配置，已迁移到主配置: {env_migration.reason}")
            legacy_config_migration_reasons.append(env_migration.reason)
        config_data = env_migration.data
        legacy_migration = try_migrate_legacy_bot_config_dict(config_data)
        if legacy_migration.migrated:
            logger.warning(t("config.legacy_migrated", reason=legacy_migration.reason))
            legacy_config_migration_applied = True
            legacy_config_migration_reasons.append(legacy_migration.reason)
        config_data = legacy_migration.data
        config_data, a_memorix_migration_applied = _migrate_legacy_a_memorix_config(config_data)
        if a_memorix_migration_applied:
            legacy_config_migration_reasons.append("a_memorix_legacy_config")
        config_data = _normalize_loaded_bot_config_dict(config_data)
    hook_result = apply_config_upgrade_hooks(config_data, config_path.name, old_ver, new_ver)
    upgrade_hook_applied = hook_result.migrated
    if hook_result.migrated:
        logger.warning(f"检测到配置升级钩子变更，已应用: {hook_result.reason}")
    config_data = hook_result.data
    # 保留一份“干净”的原始数据副本，避免第一次 from_dict 过程中对 dict 的就地修改
    original_data: dict[str, Any] = copy.deepcopy(config_data)
    try:
        updated: bool = False
        try:
            target_config = config_class.from_dict(attribute_data, config_data)
        except TypeError as e:
            # 可拔插的旧配置修复（仅针对 bot_config.toml 的已知结构变更）
            if legacy_migration_enabled:
                # 基于未被部分构造污染的 original_data 做迁移尝试
                mig = try_migrate_legacy_bot_config_dict(original_data)
                if mig.migrated:
                    logger.warning(t("config.legacy_migrated", reason=mig.reason))
                    legacy_config_migration_applied = True
                    legacy_config_migration_reasons.append(mig.reason)
                    migrated_data = mig.data
                    target_config = config_class.from_dict(attribute_data, migrated_data)
                else:
                    raise e
            else:
                raise e
        if (
            compare_versions(old_ver, new_ver)
            or env_migration_applied
            or legacy_config_migration_applied
            or a_memorix_migration_applied
            or upgrade_hook_applied
        ):
            output_config_changes(attribute_data, logger, old_ver, new_ver, config_path.name)
            write_config_to_file(target_config, config_path, new_ver, override_repr)
            if env_migration_applied:
                remove_legacy_env_file(LEGACY_ENV_PATH)
            updated = True
        if legacy_migration_enabled:
            mark_legacy_config_migration_completed(
                migrated=env_migration_applied or legacy_config_migration_applied or a_memorix_migration_applied,
                reason=",".join(reason for reason in legacy_config_migration_reasons if reason),
            )
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

    if isinstance(config, Config):
        try:
            a_memorix_web = full_config_data["a_memorix"]["web"]
            if "import_config" in a_memorix_web and "import" not in a_memorix_web:
                a_memorix_web["import"] = a_memorix_web.pop("import_config")
        except Exception:
            logger.debug("A_Memorix 配置写出时转换 web.import_config 失败", exc_info=True)

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


class _ConfigProxy:
    """稳定配置代理，确保热重载后旧导入也能读取最新配置。"""

    def __init__(self, getter: Callable[[], ConfigBase]) -> None:
        self._getter = getter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)

    def __getitem__(self, key: str) -> Any:
        return self._getter()[key]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_getter":
            object.__setattr__(self, name, value)
            return
        setattr(self._getter(), name, value)

    def __repr__(self) -> str:
        return repr(self._getter())


# generate_new_config_file(Config, BOT_CONFIG_PATH, CONFIG_VERSION)
config_manager = ConfigManager()
config_manager.initialize()
global_config: Config = cast(Config, _ConfigProxy(config_manager.get_global_config))
model_config: ModelConfig = cast(ModelConfig, _ConfigProxy(config_manager.get_model_config))

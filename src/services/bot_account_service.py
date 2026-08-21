"""持久化并缓存适配器确认的 Bot 平台身份。"""

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Dict, List, Optional, Set, Tuple

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import BotPlatformAccount
from src.common.logger import get_logger
from src.core.local_operator import LOCAL_PLATFORM_BOT_IDS

logger = get_logger("bot_account_service")

BOT_ACCOUNT_SOURCE_READY = "ready"
BOT_ACCOUNT_SOURCE_INBOUND = "inbound"
WEBUI_BOT_USER_ID = "self"


@dataclass(frozen=True)
class AdapterAccountIdentity:
    """适配器上报账号时携带的来源信息。"""

    platform: str
    account_id: str
    adapter_id: Optional[str] = None
    plugin_id: Optional[str] = None
    gateway_name: Optional[str] = None


@dataclass(frozen=True)
class RecordResult:
    """账号上报的持久化与身份启用结果。"""

    persisted: bool
    enabled: bool
    account: Optional[BotPlatformAccount]


def normalize_platform(platform: str) -> str:
    """规范化平台名，不在主程序中合并任何平台别名。"""

    return str(platform or "").strip().lower()


def normalize_account_id(account_id: str) -> str:
    """规范化平台账号。"""

    return str(account_id or "").strip()


class BotAccountService:
    """维护数据库身份事实及供热路径读取的内存快照。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._initialized = False
        self._enabled_accounts: Dict[str, Set[str]] = {}
        self._disabled_accounts: Dict[str, Set[str]] = {}
        self._observed_platforms: Set[str] = set()
        self._volatile_accounts: Dict[str, Set[str]] = {}

    def _ensure_initialized(self) -> None:
        with self._lock:
            if self._initialized:
                return
            with get_db_session(auto_commit=False) as session:
                accounts = list(session.exec(select(BotPlatformAccount)).all())
            self._enabled_accounts.clear()
            self._disabled_accounts.clear()
            self._observed_platforms.clear()
            for account in accounts:
                platform = normalize_platform(account.platform)
                account_id = normalize_account_id(account.account_id)
                if not platform or not account_id:
                    continue
                self._observed_platforms.add(platform)
                target = self._disabled_accounts if account.disabled else self._enabled_accounts
                target.setdefault(platform, set()).add(account_id)
            self._initialized = True

    @staticmethod
    def _configured_accounts() -> Dict[str, Set[str]]:
        """读取备用配置；调用方不得把结果写入数据库。"""

        from src.config.config import global_config

        configured: Dict[str, Set[str]] = {}
        qq_account = normalize_account_id(global_config.bot.qq_account)
        if qq_account and qq_account != "0":
            primary_platform = normalize_platform(global_config.bot.platform) or "qq"
            configured.setdefault(primary_platform, set()).add(qq_account)
        for entry in global_config.bot.platforms:
            platform, separator, account_id = str(entry).partition(":")
            normalized_platform = normalize_platform(platform)
            normalized_account = normalize_account_id(account_id)
            if separator and normalized_platform and normalized_account:
                configured.setdefault(normalized_platform, set()).add(normalized_account)
        return configured

    def get_bot_accounts(self, platform: str) -> Set[str]:
        """返回平台下全部已确认身份；无事实记录时才使用配置。"""

        normalized_platform = normalize_platform(platform)
        if not normalized_platform:
            return set()
        if normalized_platform == "webui":
            return {WEBUI_BOT_USER_ID}
        if normalized_platform in LOCAL_PLATFORM_BOT_IDS:
            return {LOCAL_PLATFORM_BOT_IDS[normalized_platform]}

        self._ensure_initialized()
        with self._lock:
            accounts = set(self._enabled_accounts.get(normalized_platform, set()))
            accounts.update(self._volatile_accounts.get(normalized_platform, set()))
            if normalized_platform in self._observed_platforms:
                return accounts
        return set(self._configured_accounts().get(normalized_platform, set()))

    def get_all_bot_account_pairs(self) -> Set[Tuple[str, str]]:
        """返回全部可用于身份判断的平台账号对。"""

        self._ensure_initialized()
        with self._lock:
            platforms = set(self._observed_platforms)
            platforms.update(self._enabled_accounts)
            platforms.update(self._volatile_accounts)
        platforms.update(self._configured_accounts())
        platforms.update(LOCAL_PLATFORM_BOT_IDS)
        platforms.add("webui")
        return {
            (platform, account_id)
            for platform in platforms
            for account_id in self.get_bot_accounts(platform)
        }

    def is_bot_self(self, platform: str, user_id: str) -> bool:
        """判断账号是否属于当前 MaiBot 实例。"""

        account_id = normalize_account_id(user_id)
        return bool(account_id and account_id in self.get_bot_accounts(platform))

    def record_adapter_account(self, identity: AdapterAccountIdentity, source: str) -> RecordResult:
        """记录适配器上报账号；写库失败时保留本进程临时身份。"""

        platform = normalize_platform(identity.platform)
        account_id = normalize_account_id(identity.account_id)
        if not platform or not account_id:
            raise ValueError("适配器上报的 platform 和 account_id 不能为空")
        if source not in {BOT_ACCOUNT_SOURCE_READY, BOT_ACCOUNT_SOURCE_INBOUND}:
            raise ValueError(f"不支持的 Bot 账号来源: {source}")

        self._ensure_initialized()
        now = datetime.now()
        try:
            with get_db_session() as session:
                account = session.exec(
                    select(BotPlatformAccount).where(
                        BotPlatformAccount.platform == platform,
                        BotPlatformAccount.account_id == account_id,
                    )
                ).first()
                if account is None:
                    account = BotPlatformAccount(
                        platform=platform,
                        account_id=account_id,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(account)
                account.last_seen_at = now
                account.last_source = source
                account.last_adapter_id = normalize_account_id(identity.adapter_id or "") or None
                account.last_plugin_id = normalize_account_id(identity.plugin_id or "") or None
                account.last_gateway_name = normalize_account_id(identity.gateway_name or "") or None
                session.flush()
                disabled = account.disabled
            with self._lock:
                self._observed_platforms.add(platform)
                self._volatile_accounts.get(platform, set()).discard(account_id)
                if disabled:
                    self._enabled_accounts.get(platform, set()).discard(account_id)
                    self._disabled_accounts.setdefault(platform, set()).add(account_id)
                else:
                    self._disabled_accounts.get(platform, set()).discard(account_id)
                    self._enabled_accounts.setdefault(platform, set()).add(account_id)
            if disabled:
                logger.warning(f"已禁用的 Bot 平台账号仍被适配器上报: platform={platform}, account_id={account_id}")
            return RecordResult(persisted=True, enabled=not disabled, account=account)
        except Exception:
            with self._lock:
                disabled = account_id in self._disabled_accounts.get(platform, set())
                self._observed_platforms.add(platform)
                if not disabled:
                    self._volatile_accounts.setdefault(platform, set()).add(account_id)
            logger.exception(
                f"Bot 平台账号写入数据库失败，当前进程继续使用临时身份: platform={platform}, account_id={account_id}"
            )
            return RecordResult(persisted=False, enabled=not disabled, account=None)

    def list_accounts(self) -> List[BotPlatformAccount]:
        """列出持久化账号，按平台和账号排序。"""

        self._ensure_initialized()
        with get_db_session(auto_commit=False) as session:
            return list(
                session.exec(
                    select(BotPlatformAccount).order_by(
                        BotPlatformAccount.platform,
                        BotPlatformAccount.account_id,
                    )
                ).all()
            )

    def _save_disabled_state(self, account: BotPlatformAccount, disabled: bool) -> BotPlatformAccount:
        """保存禁用状态，并立即刷新内存快照。"""

        with get_db_session() as session:
            account.disabled = disabled
            account.disabled_at = datetime.now() if disabled else None
            session.add(account)
            session.flush()
        platform = normalize_platform(account.platform)
        normalized_account = normalize_account_id(account.account_id)
        with self._lock:
            self._observed_platforms.add(platform)
            if disabled:
                self._enabled_accounts.get(platform, set()).discard(normalized_account)
                self._volatile_accounts.get(platform, set()).discard(normalized_account)
                self._disabled_accounts.setdefault(platform, set()).add(normalized_account)
            else:
                self._disabled_accounts.get(platform, set()).discard(normalized_account)
                self._enabled_accounts.setdefault(platform, set()).add(normalized_account)
        return account

    def set_disabled_by_id(self, record_id: int, disabled: bool) -> BotPlatformAccount:
        """按数据库主键软禁用或恢复账号。"""

        self._ensure_initialized()
        with get_db_session(auto_commit=False) as session:
            account = session.get(BotPlatformAccount, record_id)
            if account is None:
                raise LookupError(f"Bot 平台账号不存在: id={record_id}")
            session.expunge(account)
        return self._save_disabled_state(account, disabled)

    def set_disabled(self, platform: str, account_id: str, disabled: bool) -> BotPlatformAccount:
        """按平台账号软禁用或恢复身份。"""

        normalized_platform = normalize_platform(platform)
        normalized_account = normalize_account_id(account_id)
        self._ensure_initialized()
        with get_db_session(auto_commit=False) as session:
            account = session.exec(
                select(BotPlatformAccount).where(
                    BotPlatformAccount.platform == normalized_platform,
                    BotPlatformAccount.account_id == normalized_account,
                )
            ).first()
            if account is None:
                raise LookupError(
                    f"Bot 平台账号不存在: platform={normalized_platform}, account_id={normalized_account}"
                )
            session.expunge(account)
        return self._save_disabled_state(account, disabled)

    def reset_cache_for_test(self) -> None:
        """仅供测试在替换数据库后重置单例状态。"""

        with self._lock:
            self._initialized = False
            self._enabled_accounts.clear()
            self._disabled_accounts.clear()
            self._observed_platforms.clear()
            self._volatile_accounts.clear()


bot_account_service = BotAccountService()


def get_bot_accounts(platform: str) -> Set[str]:
    return bot_account_service.get_bot_accounts(platform)


def get_all_bot_account_pairs() -> Set[Tuple[str, str]]:
    return bot_account_service.get_all_bot_account_pairs()


def is_bot_self(platform: str, user_id: str) -> bool:
    return bot_account_service.is_bot_self(platform, user_id)


def record_adapter_account(identity: AdapterAccountIdentity, source: str) -> RecordResult:
    return bot_account_service.record_adapter_account(identity, source)


def disable_bot_account(platform: str, account_id: str) -> BotPlatformAccount:
    return bot_account_service.set_disabled(platform, account_id, True)


def restore_bot_account(platform: str, account_id: str) -> BotPlatformAccount:
    return bot_account_service.set_disabled(platform, account_id, False)

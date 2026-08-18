from contextlib import contextmanager

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from src.common.database.database_model import BotPlatformAccount
from src.services import bot_account_service as service_module
from src.services.bot_account_service import (
    BOT_ACCOUNT_SOURCE_INBOUND,
    BOT_ACCOUNT_SOURCE_READY,
    AdapterAccountIdentity,
    BotAccountService,
)


@pytest.fixture
def account_service(monkeypatch: pytest.MonkeyPatch) -> tuple[BotAccountService, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    BotPlatformAccount.__table__.create(engine)

    @contextmanager
    def get_test_session(auto_commit: bool = True):
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                if auto_commit:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(service_module, "get_db_session", get_test_session)
    service = BotAccountService()
    monkeypatch.setattr(service, "_configured_accounts", lambda: {"qq": {"configured"}, "tg": {"tg-config"}})
    return service, engine


def test_multiple_reported_accounts_replace_platform_fallback(account_service) -> None:
    service, _ = account_service

    assert service.get_bot_accounts(" QQ ") == {"configured"}
    service.record_adapter_account(AdapterAccountIdentity(platform=" QQ ", account_id="bot-a"), BOT_ACCOUNT_SOURCE_READY)
    service.record_adapter_account(AdapterAccountIdentity(platform="qq", account_id="bot-b"), BOT_ACCOUNT_SOURCE_INBOUND)

    assert service.get_bot_accounts("qq") == {"bot-a", "bot-b"}
    assert service.is_bot_self("qq", "bot-a")
    assert service.is_bot_self("qq", "bot-b")
    assert not service.is_bot_self("qq", "configured")
    assert service.get_bot_accounts("tg") == {"tg-config"}
    assert service.get_bot_accounts("telegram") == set()


def test_configured_platform_quietly_rejects_regular_user(account_service) -> None:
    service, _ = account_service

    assert service.get_bot_accounts("qq") == {"configured"}
    assert not service.is_bot_self("qq", "regular-user")


def test_disabled_account_stays_disabled_when_reported_again(account_service) -> None:
    service, engine = account_service
    result = service.record_adapter_account(
        AdapterAccountIdentity(platform="qq", account_id="bot-a"),
        BOT_ACCOUNT_SOURCE_READY,
    )
    assert result.account is not None and result.account.id is not None

    service.set_disabled("qq", "bot-a", True)
    repeated = service.record_adapter_account(
        AdapterAccountIdentity(platform="qq", account_id="bot-a"),
        BOT_ACCOUNT_SOURCE_INBOUND,
    )

    assert repeated.persisted
    assert not repeated.enabled
    assert service.get_bot_accounts("qq") == set()
    with Session(engine) as session:
        stored = session.exec(select(BotPlatformAccount)).one()
        assert stored.disabled
        assert stored.last_source == BOT_ACCOUNT_SOURCE_INBOUND

    service.set_disabled("qq", "bot-a", False)
    assert service.get_bot_accounts("qq") == {"bot-a"}


def test_database_failure_keeps_temporary_identity(account_service, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = account_service
    service.get_bot_accounts("qq")

    @contextmanager
    def failing_session(auto_commit: bool = True):
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(service_module, "get_db_session", failing_session)
    result = service.record_adapter_account(
        AdapterAccountIdentity(platform="qq", account_id="temporary"),
        BOT_ACCOUNT_SOURCE_READY,
    )

    assert not result.persisted
    assert result.enabled
    assert service.get_bot_accounts("qq") == {"temporary"}

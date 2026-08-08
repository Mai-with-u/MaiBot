"""Bot 平台账号管理 API。"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.platform_io import DriverKind, get_platform_io_manager
from src.services.bot_account_service import (
    bot_account_service,
    normalize_platform,
)
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/bot-accounts", tags=["Bot Accounts"], dependencies=[Depends(require_auth)])


class BotPlatformAccountItem(BaseModel):
    id: int
    platform: str
    account_id: str
    disabled: bool
    first_seen_at: datetime
    last_seen_at: datetime
    disabled_at: Optional[datetime] = None
    last_source: str
    last_adapter_id: Optional[str] = None
    last_plugin_id: Optional[str] = None
    last_gateway_name: Optional[str] = None
    online: bool


class BotPlatformAccountListResponse(BaseModel):
    success: bool = True
    data: list[BotPlatformAccountItem]


class BotPlatformAccountMutationResponse(BaseModel):
    success: bool = True
    data: BotPlatformAccountItem


def _online_account_pairs() -> set[tuple[str, str]]:
    manager = get_platform_io_manager()
    return {
        (normalize_platform(driver.descriptor.platform), str(driver.descriptor.account_id or "").strip())
        for driver in manager.driver_registry.list(kind=DriverKind.PLUGIN)
        if driver.descriptor.account_id
    }


def _serialize_account(account, online_pairs: set[tuple[str, str]]) -> BotPlatformAccountItem:
    platform = normalize_platform(account.platform)
    return BotPlatformAccountItem(
        id=int(account.id),
        platform=platform,
        account_id=account.account_id,
        disabled=account.disabled,
        first_seen_at=account.first_seen_at,
        last_seen_at=account.last_seen_at,
        disabled_at=account.disabled_at,
        last_source=account.last_source,
        last_adapter_id=account.last_adapter_id,
        last_plugin_id=account.last_plugin_id,
        last_gateway_name=account.last_gateway_name,
        online=(platform, account.account_id) in online_pairs,
    )


@router.get("", response_model=BotPlatformAccountListResponse)
async def list_bot_platform_accounts() -> BotPlatformAccountListResponse:
    online_pairs = _online_account_pairs()
    return BotPlatformAccountListResponse(
        data=[_serialize_account(account, online_pairs) for account in bot_account_service.list_accounts()]
    )


def _mutate_account(account_id: int, *, disabled: bool) -> BotPlatformAccountMutationResponse:
    try:
        account = bot_account_service.set_disabled_by_id(account_id, disabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BotPlatformAccountMutationResponse(data=_serialize_account(account, _online_account_pairs()))


@router.post("/{account_id}/disable", response_model=BotPlatformAccountMutationResponse)
async def disable_bot_platform_account(account_id: int) -> BotPlatformAccountMutationResponse:
    return _mutate_account(account_id, disabled=True)


@router.post("/{account_id}/restore", response_model=BotPlatformAccountMutationResponse)
async def restore_bot_platform_account(account_id: int) -> BotPlatformAccountMutationResponse:
    return _mutate_account(account_id, disabled=False)

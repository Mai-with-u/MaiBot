from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, List

from fastapi import HTTPException

import pytest

from src.common.database.database_model import ChatSession
from src.platform_io import AdapterPolicyManager, DriverKind
from src.platform_io.registry import DriverRegistry
from src.platform_io.routing import RouteTable
from src.platform_io.types import DriverDescriptor, RouteBinding
from src.webui.routers.chat import routes


class _DetachedGuardPerson:
    def __init__(self) -> None:
        self.closed = False
        self.person_id = "person-1"
        self.user_id = "user-1"
        self.person_name = "Test User"
        self.user_nickname = "Test Nickname"
        self.is_known = True
        self.platform = "qq"

    def __getattribute__(self, name: str) -> Any:
        if name not in {"closed", "__dict__", "__class__", "__getattribute__"}:
            if object.__getattribute__(self, "closed"):
                raise RuntimeError("person attribute accessed after session closed")
        return object.__getattribute__(self, name)


class _FakeExecResult:
    def __init__(self, person: _DetachedGuardPerson) -> None:
        self.person = person

    def all(self) -> list[_DetachedGuardPerson]:
        return [self.person]


class _FakeSession:
    def __init__(self, person: _DetachedGuardPerson) -> None:
        self.person = person

    def exec(self, statement: Any) -> _FakeExecResult:
        del statement
        return _FakeExecResult(self.person)


@pytest.mark.asyncio
async def test_get_persons_by_platform_serializes_before_session_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    person = _DetachedGuardPerson()

    @contextmanager
    def fake_get_db_session() -> Iterator[_FakeSession]:
        try:
            yield _FakeSession(person)
        finally:
            person.closed = True

    monkeypatch.setattr(routes, "get_db_session", fake_get_db_session)

    response = routes.get_persons_by_platform(platform="qq", limit=50)

    assert response == {
        "success": True,
        "persons": [
            {
                "person_id": "person-1",
                "user_id": "user-1",
                "person_name": "Test User",
                "nickname": "Test Nickname",
                "is_known": True,
                "platform": "qq",
                "display_name": "Test User",
            }
        ],
        "total": 1,
    }


def test_group_display_name_ignores_private_latest_message_identity() -> None:
    chat_session = ChatSession(
        session_id="group-session",
        platform="qq",
        group_id="571780722",
        group_name="麦麦脑电图｜技术交流群｜部署/配置",
    )
    latest_message = SimpleNamespace(
        group_id=None,
        group_name=None,
        user_id="2814567326",
        user_nickname="麦麦",
        user_cardname=None,
    )

    assert routes._get_chat_display_name(chat_session, latest_message) == "麦麦脑电图｜技术交流群｜部署/配置"


@pytest.fixture
def adapter_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    registry = DriverRegistry()
    send_routes = RouteTable()
    manager = AdapterPolicyManager(tmp_path / "adapter_policy.toml")
    manager.set_default_action("group", "block")
    for driver_id, platform in [("qq-bound", "qq"), ("qq-unbound", "qq"), ("tg", "telegram")]:
        descriptor = DriverDescriptor(
            driver_id=driver_id,
            kind=DriverKind.PLUGIN,
            platform=platform,
            plugin_id=driver_id,
            metadata={"plugin_type": "adapter"},
        )
        driver = SimpleNamespace(driver_id=driver_id, descriptor=descriptor)
        registry.register(driver)
        if driver_id != "qq-unbound":
            send_routes.bind(RouteBinding(descriptor.route_key, driver_id, DriverKind.PLUGIN))
        if platform == "telegram":
            manager.set_chat_override(
                routes._get_driver_adapter_identity(driver), chat_type="group", target_id="123", action="allow"
            )
    runtime = SimpleNamespace(
        driver_registry=registry,
        send_route_table=send_routes,
        receive_route_table=RouteTable(),
        policy_manager=manager,
    )
    monkeypatch.setattr(routes, "get_platform_io_manager", lambda: runtime)
    monkeypatch.setattr(routes, "get_adapter_policy_manager", lambda: manager)
    return runtime


@pytest.mark.parametrize(
    ("platform", "expected_ids", "allowed"),
    [(" QQ ", ["qq-bound", "qq-unbound"], False), ("telegram", ["tg"], True), ("webui", [], True)],
)
def test_chat_adapter_details_only_include_session_platform(
    adapter_runtime: SimpleNamespace, platform: str, expected_ids: List[str], allowed: bool
) -> None:
    chat_session = ChatSession(session_id="session", platform=platform, group_id="123")

    details = routes._get_adapter_policy_details(chat_session)

    assert [adapter["adapter_id"] for adapter in details] == expected_ids
    assert all(adapter["policy"]["allowed"] is allowed for adapter in details)
    assert routes._summarize_session_adapter_allowance(chat_session)["allowed"] is allowed


@pytest.mark.parametrize("action", ["allow", "block", "inherit"])
def test_chat_adapter_policy_rejects_other_platform_without_writing(
    adapter_runtime: SimpleNamespace, action: str
) -> None:
    chat_session = ChatSession(session_id="session", platform="qq", group_id="123")
    manager = adapter_runtime.policy_manager
    original_policy = manager.policy_path.read_bytes()

    with pytest.raises(HTTPException, match="平台不匹配") as exc_info:
        routes._save_adapter_policy_rule(chat_session, routes.AdapterPolicyUpdateRequest(adapter_id="tg", action=action))

    assert exc_info.value.status_code == 400
    assert manager.policy_path.read_bytes() == original_policy


@pytest.mark.parametrize("action", ["allow", "block", "inherit"])
def test_chat_adapter_policy_still_updates_same_platform(adapter_runtime: SimpleNamespace, action: str) -> None:
    chat_session = ChatSession(session_id="session", platform=" QQ ", group_id="123")
    routes._save_adapter_policy_rule(
        chat_session, routes.AdapterPolicyUpdateRequest(adapter_id="qq-bound", action=action)
    )

    details = routes._get_adapter_policy_details(chat_session)
    assert details[0]["adapter_id"] == "qq-bound"
    assert details[0]["policy"]["allowed"] is (action == "allow")
    telegram_session = ChatSession(session_id="tg-session", platform="telegram", group_id="123")
    assert routes._get_adapter_policy_details(telegram_session)[0]["policy"]["allowed"] is True

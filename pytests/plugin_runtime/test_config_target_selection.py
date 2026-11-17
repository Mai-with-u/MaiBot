from contextlib import contextmanager

from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

import pytest

from src.common.database.database_model import ChatSession, Individual, PersonInfo
from src.plugins.built_in.qq_official_adapter.plugin import QQOfficialAdapterPlugin
from src.plugins.built_in.qq_official_adapter.settings import AdapterSettings
from src.webui.routers.chat import routes


def test_target_schema_and_legacy_configuration():
    config = AdapterSettings.model_validate({"kick": {"allowed_groups": [" 123 ", ""], "admin_users": ["openid"]}})
    assert config.kick.allowed_groups[0].model_dump() == {"platform": "qq", "type": "group", "id": "123", "name": ""}
    assert config.kick.admin_users[0].id == "openid"
    schema = QQOfficialAdapterPlugin.build_config_schema()
    field = schema["sections"]["kick"]["fields"]["allowed_groups"]
    assert field["ui_type"] == "target"
    assert field["item_fields"]["type"]["default"] == "group"
    assert field["item_fields"]["platform"]["default"] == "qq"
    assert AdapterSettings.model_validate(config.model_dump()).kick.allowed_groups == config.kick.allowed_groups


@pytest.mark.parametrize("target", [
    {"platform": "telegram", "type": "group", "id": "1"},
    {"platform": "qq", "type": "user", "id": "1"},
    {"platform": "qq", "type": "group", "id": ""},
    {"platform": "qq", "type": "group"},
])
def test_invalid_target_rejected(target):
    with pytest.raises(ValidationError):
        AdapterSettings.model_validate({"kick": {"allowed_groups": [target]}})


def test_target_search_includes_users_without_private_sessions(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Individual.__table__, PersonInfo.__table__, ChatSession.__table__])
    with Session(engine) as session:
        session.add(PersonInfo(person_id="person", platform="qq", user_id="user-openid", user_nickname="群成员"))
        session.add(ChatSession(session_id="group-1", platform="qq", group_id="group-openid", group_name="测试群"))
        session.add(ChatSession(session_id="group-2", platform="qq", group_id="group-openid", group_name="测试群"))
        session.commit()
    @contextmanager
    def isolated_session():
        with Session(engine) as session:
            yield session
    monkeypatch.setattr(routes, "get_db_session", isolated_session)
    result = routes.search_config_targets("user", "qq", "群成员", 50)
    assert result["targets"] == [{"platform": "qq", "type": "user", "id": "user-openid", "name": "群成员"}]
    assert len(routes.search_config_targets("group", "qq", "测试群", 50)["targets"]) == 1
    assert routes.search_config_targets("user", "telegram", "", 50)["targets"] == []
    engine.dispose()

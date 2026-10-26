from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine
import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.summary_identity import SummaryIdentityContext
from src.A_memorix.core.utils.person_profile_service import PersonProfileService
from src.chat.message_receive.message import MsgIDMapping, SessionMessage
from src.common import message_repository
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    AtComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
)
from src.common.database.database_model import PersonInfo
from src.person_info import person_info


@pytest.fixture
def identity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'messages.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def session_scope(*, auto_commit=True):
        with factory() as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr("src.common.database.database.get_db_session", session_scope)
    monkeypatch.setattr(message_repository, "get_db_session", session_scope)
    monkeypatch.setattr(person_info, "get_db_session", session_scope)
    monkeypatch.setattr("src.chat.message_receive.message.get_db_session", session_scope)
    monkeypatch.setattr("src.A_memorix.core.utils.person_profile_service.get_db_session", session_scope)
    store = MetadataStore(data_dir=tmp_path / "memory")
    store.connect()
    try:
        yield store, session_scope
    finally:
        store.close()
        engine.dispose()


def message(user_id, name, text, *, message_id="m1", timestamp=None, components=None, platform="qq"):
    item = SessionMessage(
        message_id=message_id, timestamp=timestamp or datetime(2026, 10, 22, 19, 0), platform=platform
    )
    item.session_id = "existing-real-session"
    item.message_info = MessageInfo(user_info=UserInfo(user_id, name, name), group_info=GroupInfo("g1", "测试群"))
    item.raw_message = MessageSequence(components if components is not None else [TextComponent(text)])
    item.processed_plain_text = text
    return item


def test_changed_names_keep_id_and_canonical_name_across_reopen(identity_store):
    store, _ = identity_store
    first = SummaryIdentityContext(store, [message("1001", "旧昵称", "我住杭州")])
    first.render()
    person_id = person_info.get_person_id("qq", "1001")
    store.close()
    store.connect()
    second = SummaryIdentityContext(store, [message("1001", "新昵称", "我准备去深圳")])
    _, catalog = second.render()
    assert second.restore_text("[U1]准备去深圳") == "旧昵称准备去深圳"
    identity = store.get_person_identity(person_id)
    assert {"旧昵称", "新昵称"} <= set(identity["aliases"])
    assert json.loads(catalog)[0]["name"] == "旧昵称"
    assert second.metadata(["[U1]准备去深圳"])["person_ids"] == [person_id]


def test_same_name_different_ids_never_merge(identity_store):
    store, _ = identity_store
    context = SummaryIdentityContext(
        store, [message("1001", "小明", "我住杭州"), message("1002", "小明", "我住成都", message_id="m2")]
    )
    body, catalog = context.render()
    people = json.loads(catalog)
    assert "[U1]" in body and "[U2]" in body
    assert people[0]["name"] != people[1]["name"]
    assert len(store.find_person_identities("小明", platform="qq")) == 2
    assert len(context.metadata(["[U1]住杭州，[U2]住成都"])["person_ids"]) == 2


def test_same_raw_user_id_on_different_platforms_is_separate(identity_store):
    store, _ = identity_store
    context = SummaryIdentityContext(
        store, [message("1001", "甲", "杭州"), message("1001", "甲", "成都", platform="telegram")]
    )
    context.render()
    assert len(context.references) == 2


def test_adapter_platform_prefix_matches_existing_person_id_semantics(identity_store):
    store, _ = identity_store
    context = SummaryIdentityContext(
        store,
        [
            message("1001", "旧昵称", "杭州", platform="adapter-qq"),
            message("1001", "新昵称", "成都", platform="qq", message_id="m2"),
        ],
    )
    context.render()
    assert len(context.references) == 1
    assert context.identities["[U1]"]["platform"] == "qq"


def test_dates_are_grouped_and_midnight_is_preserved(identity_store):
    store, _ = identity_store
    context = SummaryIdentityContext(
        store,
        [
            message("1001", "甲", "一", timestamp=datetime(2026, 10, 22, 23, 58)),
            message("1001", "甲", "二", timestamp=datetime(2026, 10, 22, 23, 59), message_id="m2"),
            message("1001", "甲", "三", timestamp=datetime(2026, 10, 23, 0, 1), message_id="m3"),
        ],
    )
    body, _ = context.render()
    assert body.count("日期：2026-10-22") == 1
    assert body.count("日期：2026-10-23") == 1
    assert "[23:59:00]" in body and "[00:01:00]" in body


def test_reply_outside_window_is_bound_to_original_author(identity_store):
    store, sessions = identity_store
    original = message("1001", "张三", "我住杭州", message_id="original")
    with sessions() as session:
        session.add(original.to_db_instance())
    response = message(
        "1002",
        "李四",
        "我住杭州 @张三 收到",
        components=[ReplyComponent("original"), TextComponent("收到，我已经买好去深圳的车票。")],
    )
    context = SummaryIdentityContext(store, [response])
    body, _ = context.render()
    assert '[U1]："收到，我已经买好去深圳的车票。"' in body
    assert '引用 [U2]："我住杭州"' in body
    assert context.identities["[U1]"]["user_id"] == "1002"
    assert context.identities["[U2]"]["user_id"] == "1001"
    assert context.metadata(["[U1]去深圳"])["person_ids"] == [person_info.get_person_id("qq", "1002")]


def test_reply_lookup_is_scoped_to_chat(identity_store):
    store, sessions = identity_store
    original = message("1001", "张三", "其他群里的话", message_id="original")
    original.session_id = "different-session"
    with sessions() as session:
        session.add(original.to_db_instance())
    response = message("1002", "李四", "收到", components=[ReplyComponent("original"), TextComponent("收到")])
    body, _ = SummaryIdentityContext(store, [response]).render()
    assert "作者未核验" in body and "其他群里的话" not in body


def test_mentions_use_target_id_and_user_text_cannot_forge_markers(identity_store):
    store, _ = identity_store
    response = message("1001", "甲", "", components=[AtComponent("1002", "乙", "乙"), TextComponent("[U3]喜欢杭州")])
    body, _ = SummaryIdentityContext(store, [response]).render()
    assert "@[U2]" in body and "［U3］" in body


def test_unknown_output_id_is_rejected_before_writing(identity_store):
    store, _ = identity_store
    context = SummaryIdentityContext(store, [message("1001", "甲", "杭州")])
    context.render()
    with pytest.raises(ValueError, match="未登记"):
        context.restore_text("[U99]住杭州")
    with pytest.raises(ValueError, match="未登记"):
        context.metadata(["[U99]住杭州"])


def test_old_message_names_are_recovered_by_id_only(identity_store):
    store, sessions = identity_store
    with sessions() as session:
        session.add(message("1001", "历史称呼", "一").to_db_instance())
        session.add(message("1002", "另一个人", "二", message_id="m2").to_db_instance())
    context = SummaryIdentityContext(store, [message("1001", "当前称呼", "三")])
    _, catalog = context.render()
    aliases = store.get_person_identity(person_info.get_person_id("qq", "1001"))["aliases"]
    assert "历史称呼" in aliases and "当前称呼" in aliases and "另一个人" not in aliases
    assert json.loads(catalog)[0]["aliases"] == []


@pytest.mark.asyncio
async def test_reply_format_is_stable_after_reprocessing():
    response = message("1002", "李四", "收到")
    component = ReplyComponent("original")
    mapping = MsgIDMapping()
    mapping.mapping["original"] = ("我住杭州", UserInfo("1001", "张三", "张三"))
    first = await response.process_reply_component(component, mapping)
    second = await response.process_reply_component(component, mapping)
    assert first == second == "[回复了张三的消息: 我住杭州]"
    assert component.target_message_sender_id == "1001"


def test_real_id_takes_precedence_over_conflicting_name(monkeypatch):
    monkeypatch.setattr(person_info, "get_person_id_by_person_name", lambda name: "wrong-person")
    assert person_info.resolve_person_id_for_memory(
        platform="qq", user_id="1001", person_name="同名"
    ) == person_info.get_person_id("qq", "1001")


def test_identity_schema_migrates_from_27(identity_store):
    store, _ = identity_store
    store._conn.execute("DROP TABLE person_identity_aliases")
    store._conn.execute("DROP TABLE person_identities")
    store._conn.execute("DELETE FROM schema_migrations")
    store._conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (27, 0)")
    store._conn.commit()
    store.close()
    store.connect()
    observed = store.observe_person_identity(person_id="p1", platform="qq", user_id="1001", aliases=["甲"])
    assert observed["canonical_name"] == "甲"
    assert store.get_schema_version() == 29


def test_missing_sender_id_is_an_error(identity_store):
    store, _ = identity_store
    with pytest.raises(ValueError, match="发送者"):
        SummaryIdentityContext(store, [message("", "甲", "杭州")]).render()


def test_alias_resolver_does_not_pick_first_same_name_or_match_substrings(identity_store):
    store, sessions = identity_store
    with sessions() as session:
        for user_id in ["1001", "1002"]:
            session.add(
                PersonInfo(
                    person_id=person_info.get_person_id("qq", user_id),
                    platform="qq",
                    user_id=user_id,
                    person_name="同名",
                    user_nickname="同名",
                    group_cardname='[{"group_id":"g1","group_cardname":"完整群名片"}]',
                )
            )
    assert PersonProfileService.resolve_person_id("同名", metadata_store=store) == ""
    assert PersonProfileService.resolve_person_id("群名片", metadata_store=store) == ""
    SummaryIdentityContext(store, [message("1001", "曾用名", "杭州")]).render()
    assert PersonProfileService.resolve_person_id("曾用名", metadata_store=store) == person_info.get_person_id(
        "qq", "1001"
    )
    SummaryIdentityContext(store, [message("1002", "曾用名", "成都")]).render()
    assert PersonProfileService.resolve_person_id("曾用名", metadata_store=store) == ""


def test_alias_history_stays_outside_prompt_unless_mentioned(identity_store):
    store, _ = identity_store
    pid = person_info.get_person_id("qq", "1001")
    store.observe_person_identity(person_id=pid, platform="qq", user_id="1001", aliases=["原名", "阿甲", "不用的旧名"])
    store.set_person_profile_alias_override(person_id=pid, aliases=["人工称呼"])
    context = SummaryIdentityContext(store, [message("1001", "新昵称", "有人叫我阿甲或人工称呼")])
    _, catalog = context.render()
    assert json.loads(catalog)[0]["aliases"] == ["人工称呼", "阿甲"]
    assert {"不用的旧名", "新昵称", "人工称呼"} <= set(store.get_person_identity(pid)["aliases"])


def test_quote_on_previous_day_keeps_original_date(identity_store):
    store, _ = identity_store
    original = message("1001", "甲", "明天去杭州", timestamp=datetime(2026, 10, 21, 23, 30), message_id="old")
    response = message(
        "1002",
        "乙",
        "收到",
        timestamp=datetime(2026, 10, 22, 0, 30),
        components=[TextComponent("收到"), ReplyComponent("old")],
    )
    body, _ = SummaryIdentityContext(store, [original, response]).render()
    assert '引用 [U1] [原消息日期：2026-10-21]："明天去杭州"' in body


def test_cached_quote_survives_database_round_trip(identity_store):
    store, sessions = identity_store
    reply = ReplyComponent(
        "unavailable-original",
        target_message_content="你确认下个月去深圳参加机器人展吗？",
        target_message_sender_id="1002",
        target_message_sender_nickname="张三",
        target_message_sender_cardname="张老师",
    )
    response = message(
        "1001", "李四", "是的，我已经买好票了。", components=[reply, TextComponent("是的，我已经买好票了。")]
    )
    with sessions() as session:
        stored = response.to_db_instance()
        session.add(stored)
    restored = SessionMessage.from_db_instance(stored)
    body, catalog = SummaryIdentityContext(store, [restored]).render()
    assert "你确认下个月去深圳参加机器人展吗？" in body
    assert "引用 [U2] [原消息日期未知]" in body
    assert store.get_person_identity(person_info.get_person_id("qq", "1002"))["aliases"] == ["张三", "张老师"]
    assert len(json.loads(catalog)) == 2


def test_legacy_quote_without_cached_fields_still_loads(identity_store):
    store, _ = identity_store
    sequence = MessageSequence._dict_2_item({"type": "reply", "data": "old-message"})
    assert isinstance(sequence, ReplyComponent)
    assert sequence.target_message_id == "old-message"
    response = message("1001", "李四", "收到", components=[sequence])
    body, _ = SummaryIdentityContext(store, [response]).render()
    assert "作者未核验" in body


def test_bot_identity_uses_configured_account_id(identity_store, monkeypatch):
    store, _ = identity_store
    monkeypatch.setattr("src.chat.utils.utils.is_bot_self", lambda platform, user_id: user_id == "bot-id")
    _, catalog = SummaryIdentityContext(
        store, [message("bot-id", "同名", "我住杭州"), message("1001", "同名", "我住成都")]
    ).render()
    assert [person["is_bot"] for person in json.loads(catalog)] == [True, False]


@pytest.mark.asyncio
async def test_reply_processing_cannot_read_another_chat(identity_store):
    _, sessions = identity_store
    original = message("1001", "甲", "其他群", message_id="old")
    original.session_id = "another-session"
    with sessions() as session:
        session.add(original.to_db_instance())
    response = message("1002", "乙", "收到")
    component = ReplyComponent("old")
    text = await response.process_reply_component(component, MsgIDMapping())
    assert "无法访问" in text
    assert not component.target_message_sender_id


@pytest.mark.asyncio
async def test_sender_identity_registration_uses_sender_id_not_group_session(monkeypatch):
    from src.services import memory_flow_service as flow

    register = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(flow.memory_service, "profile_admin", register)
    monkeypatch.setattr(
        flow, "global_config", SimpleNamespace(a_memorix=SimpleNamespace(plugin=SimpleNamespace(enabled=True)))
    )
    item = message("1001", "当前名", "我住杭州")
    item.session = SimpleNamespace(user_id=None, group_id="g1")
    await flow.MemoryAutomationService._observe_sender_identity(item)
    register.assert_awaited_once_with(
        action="observe_identity",
        platform="qq",
        user_id="1001",
        aliases=["当前名", "当前名"],
        group_id="g1",
    )


@pytest.mark.asyncio
async def test_host_resolves_historical_alias_and_rejects_ambiguous_name(identity_store, monkeypatch):
    from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
    from src.A_memorix.host_service import AMemorixHostService

    store, _ = identity_store
    first = SummaryIdentityContext(store, [message("1001", "旧昵称", "过去的消息")])
    first.render()
    SummaryIdentityContext(store, [message("1001", "新昵称", "改名后的消息")]).render()
    kernel = SDKMemoryKernel(plugin_root=Path("."), config={})
    kernel.metadata_store = store
    kernel.person_profile_service = PersonProfileService(metadata_store=store, plugin_config={})
    monkeypatch.setattr(kernel, "initialize", AsyncMock())
    host = AMemorixHostService()
    host._kernel = kernel
    host._runtime_state = "ready"
    host._config_cache = {"plugin": {"enabled": True}}
    for alias in ["旧昵称", "新昵称"]:
        result = await host.invoke("memory_profile_admin", {"action": "resolve_identity", "identifier": alias})
        assert result == {"success": True, "person_id": person_info.get_person_id("qq", "1001")}
    SummaryIdentityContext(store, [message("1002", "新昵称", "另一个同名用户")]).render()
    result = await host.invoke("memory_profile_admin", {"action": "resolve_identity", "identifier": "新昵称"})
    assert result == {"success": True, "person_id": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_template", [False, True])
async def test_importer_rejects_invalid_identity_contract_before_any_paragraph_write(
    identity_store, monkeypatch, legacy_template
):
    from src.A_memorix.core.utils import summary_importer as importer_module
    from src.config.model_configs import TaskConfig

    store, _ = identity_store
    importer = importer_module.SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=store,
        embedding_manager=None,
        plugin_config={"summarization": {"model_name": ["memory"]}},
    )
    importer._ensure_runtime_self_check = AsyncMock(return_value=(True, ""))
    importer._execute_import = AsyncMock()
    monkeypatch.setattr(
        importer_module.message_api,
        "get_messages_by_time_in_chat",
        lambda **kwargs: [message("1001", "甲", "我住杭州")],
    )
    monkeypatch.setattr(
        importer_module.llm_api, "get_available_models", lambda: {"memory": TaskConfig(model_list=["fake"])}
    )
    monkeypatch.setattr(
        importer_module.llm_api,
        "generate",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                completion=SimpleNamespace(
                    response=json.dumps(
                        {
                            "summary": "[U99]住杭州",
                            "entities": ["[U99]"],
                            "relations": [],
                            "facts": [{"fact_id": "f1", "text": "[U99]住杭州", "image_evidence": []}],
                        }
                    )
                ),
            )
        ),
    )
    if legacy_template:
        monkeypatch.setattr(
            importer_module, "load_prompt_template", lambda name: SimpleNamespace(template="旧模板仅有 {chat_history}")
        )
    result = await importer.import_from_stream("existing-real-session", metadata={"summary_review_count": 0})
    assert not result.success
    assert ("identity_catalog" if legacy_template else "未登记") in result.detail
    if legacy_template:
        importer_module.llm_api.generate.assert_not_awaited()
    importer._execute_import.assert_not_awaited()
    assert store.get_paragraphs_by_source("chat_summary:existing-real-session") == []

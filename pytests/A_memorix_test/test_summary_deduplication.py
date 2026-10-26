from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.summary_importer import SummaryImporter
from src.services import memory_service
from src.services.memory_flow_service import ChatSummaryWritebackService


@pytest.fixture
def store(tmp_path):
    metadata = MetadataStore(data_dir=tmp_path / "memory")
    metadata.connect()
    try:
        yield metadata
    finally:
        metadata.close()


def importer_for(store):
    return SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=store,
        embedding_manager=None,
        plugin_config={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("external_ids", [("window-1", "window-2"), (None, "window-2")])
async def test_different_window_ids_still_serialize_same_stream(external_ids):
    importer = importer_for(None)
    active = 0
    maximum = 0

    async def generate(stream_id, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    importer._import_from_stream_unlocked = generate
    await asyncio.gather(
        *[importer.import_from_stream("chat-a", metadata={"external_id": value}) for value in external_ids]
    )
    assert maximum == 1


def test_shared_content_keeps_window_origin_on_retry_and_review(store):
    paragraph = store.add_paragraph(content="项目统一在周五验收。", source="chat_summary:chat-a")
    repeated = store.add_paragraph(content="项目统一在周五验收。", source="chat_summary:chat-b")
    assert paragraph == repeated
    store.upsert_external_memory_ref(
        external_id="window-b",
        paragraph_hash=paragraph,
        source_type="chat_summary",
        metadata={"chat_id": "chat-b"},
    )
    importer = importer_for(store)
    result = importer._existing_summary_result(
        stream_id="chat-b",
        metadata={"external_id": "window-b", "trigger_message_count": 40},
    )
    assert result.success and result.paragraph_hash == paragraph
    assert store.get_summary_checkpoint_count("chat-b") == 40
    assert "周五验收" in importer._build_previous_summary_context("chat-b", limit=2)
    assert importer._build_previous_summary_context("chat-c", limit=2) == ""
    with pytest.raises((RuntimeError, ValueError), match="其他聊天流"):
        importer._existing_summary_result(stream_id="chat-c", metadata={"external_id": "window-b"})


def test_checkpoint_for_nonempty_window_keeps_paragraph_on_retry(store):
    paragraph = store.add_paragraph(content="验收已经完成。", source="chat_summary:chat-a")
    store.upsert_external_memory_ref(
        external_id="window-a",
        paragraph_hash=paragraph,
        source_type="chat_summary",
        metadata={"chat_id": "chat-a"},
    )
    store.record_summary_checkpoint(external_id="window-a", chat_id="chat-a", trigger_message_count=60)
    result = importer_for(store)._existing_summary_result(
        stream_id="chat-a",
        metadata={"external_id": "window-a"},
    )
    assert result.paragraph_hash == paragraph
    assert not result.skipped


@pytest.mark.asyncio
async def test_restart_uses_highest_cursor_even_if_old_content_reappears(store, monkeypatch):
    first = store.add_paragraph(
        content="部署安排在周五。",
        source="chat_summary:chat-a",
        metadata={"trigger_message_count": 10},
    )
    store.add_paragraph(
        content="测试已经通过。",
        source="chat_summary:chat-a",
        metadata={"trigger_message_count": 20},
    )
    assert (
        store.add_paragraph(
            content="部署安排在周五。",
            source="chat_summary:chat-a",
            metadata={"trigger_message_count": 30},
        )
        == first
    )
    monkeypatch.setattr(
        memory_service,
        "a_memorix_host_service",
        SimpleNamespace(
            _ensure_kernel=AsyncMock(return_value=SimpleNamespace(metadata_store=store)),
        ),
    )
    restored = await ChatSummaryWritebackService()._load_last_trigger_message_count(
        session_id="chat-a",
        total_message_count=35,
    )
    assert restored == 30


def test_old_fact_outside_review_window_is_not_available_for_semantic_dedup(store):
    for index, content in enumerate(["甲长期使用青轴键盘。", "乙报名了游泳课。", "丙的航班已确定。"]):
        paragraph = store.add_paragraph(content=content, source="chat_summary:chat-a")
        with store.transaction() as database:
            database.execute("UPDATE paragraphs SET created_at=? WHERE hash=?", (float(index + 1), paragraph))
    review = importer_for(store)._build_previous_summary_context("chat-a", limit=2)
    assert "青轴键盘" not in review
    assert "游泳课" in review and "航班" in review
    assert store.add_paragraph(content="甲一直在用青轴的键盘。", source="chat_summary:chat-a") != store.add_paragraph(
        content="甲长期使用青轴键盘。",
        source="chat_summary:chat-a",
    )


@pytest.mark.asyncio
async def test_shared_paragraph_does_not_replace_another_chats_cursor(store, monkeypatch):
    store.add_paragraph(
        content="项目已完成。",
        source="chat_summary:chat-a",
        metadata={"chat_id": "chat-a", "trigger_message_count": 10},
    )
    store.record_summary_checkpoint(external_id="a-10", chat_id="chat-a", trigger_message_count=10)
    store.add_paragraph(
        content="项目已完成。",
        source="chat_summary:chat-b",
        metadata={"chat_id": "chat-b", "trigger_message_count": 30},
    )
    # 重新打开数据库并新建服务，验证进度来自各窗口的持久记录。
    store.close()
    store.connect()
    monkeypatch.setattr(
        memory_service,
        "a_memorix_host_service",
        SimpleNamespace(
            _ensure_kernel=AsyncMock(return_value=SimpleNamespace(metadata_store=store)),
        ),
    )
    restored = await ChatSummaryWritebackService()._load_last_trigger_message_count(
        session_id="chat-a",
        total_message_count=35,
    )
    assert restored == 10

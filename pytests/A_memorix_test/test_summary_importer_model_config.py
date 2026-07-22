import asyncio

import pytest

from src.A_memorix.core.utils.summary_importer import (
    SummaryImporter,
    _message_timestamp,
    _normalize_entity_items,
    _normalize_relation_items,
)
from src.config.model_configs import TaskConfig
from src.services import llm_service as llm_api


def _fake_available_models() -> dict[str, TaskConfig]:
    return {
        "memory": TaskConfig(
            model_list=["memory-model"],
            max_tokens=512,
            temperature=0.4,
            selection_strategy="random",
        ),
        "utils": TaskConfig(
            model_list=["utils-model"],
            max_tokens=256,
            temperature=0.5,
            selection_strategy="random",
        ),
        "replyer": TaskConfig(
            model_list=["replyer-model"],
            max_tokens=128,
            temperature=0.7,
            selection_strategy="random",
        ),
    }


def test_resolve_summary_model_config_uses_auto_list_when_summarization_missing(monkeypatch):
    monkeypatch.setattr(llm_api, "get_available_models", _fake_available_models)

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={},
    )

    resolved = importer._resolve_summary_model_config()

    assert resolved is not None
    assert resolved.model_list == ["memory-model"]


def test_resolve_summary_model_config_auto_falls_back_to_utils_then_planner(monkeypatch):
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={},
    )

    monkeypatch.setattr(
        llm_api,
        "get_available_models",
        lambda: {
            "utils": TaskConfig(model_list=["utils-model"]),
            "planner": TaskConfig(model_list=["planner-model"]),
            "replyer": TaskConfig(model_list=["replyer-model"]),
        },
    )
    resolved = importer._resolve_summary_model_config()
    assert resolved is not None
    assert resolved.model_list == ["utils-model"]

    monkeypatch.setattr(
        llm_api,
        "get_available_models",
        lambda: {
            "planner": TaskConfig(model_list=["planner-model"]),
            "replyer": TaskConfig(model_list=["replyer-model"]),
        },
    )
    resolved = importer._resolve_summary_model_config()
    assert resolved is not None
    assert resolved.model_list == ["planner-model"]


def test_resolve_summary_model_config_auto_does_not_fallback_to_replyer(monkeypatch):
    monkeypatch.setattr(
        llm_api,
        "get_available_models",
        lambda: {
            "replyer": TaskConfig(model_list=["replyer-model"]),
            "embedding": TaskConfig(model_list=["embedding-model"]),
        },
    )

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={},
    )

    assert importer._resolve_summary_model_config() is None


def test_resolve_summary_model_config_rejects_legacy_string_selector(monkeypatch):
    monkeypatch.setattr(llm_api, "get_available_models", _fake_available_models)

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={"summarization": {"model_name": "auto"}},
    )

    with pytest.raises(ValueError, match="List\\[str\\]"):
        importer._resolve_summary_model_config()


def test_resolve_summary_model_config_skips_task_with_invalid_model(monkeypatch):
    monkeypatch.setattr(llm_api, "get_available_models", _fake_available_models)
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={
            "summarization": {
                "model_name": ["memory:not-a-memory-model", "utils:utils-model"],
            }
        },
    )

    resolved = importer._resolve_summary_model_config()

    assert resolved is not None
    assert resolved.model_list == ["utils-model"]


def test_summary_importer_normalizes_llm_entities_and_relations():
    assert _normalize_entity_items(["Alice", {"name": "地图"}, ["bad"], "Alice"]) == ["Alice", "地图"]
    assert _normalize_entity_items("Alice") == []
    assert _normalize_relation_items(
        [
            {"subject": "Alice", "predicate": "持有", "object": "地图"},
            {"subject": "Alice", "predicate": "", "object": "地图"},
            ["bad"],
        ]
    ) == [{"subject": "Alice", "predicate": "持有", "object": "地图"}]


def test_summary_importer_message_timestamp_accepts_time_fallback():
    class Message:
        time = 123.5

    assert _message_timestamp(Message()) == 123.5


@pytest.mark.parametrize(
    "text",
    [
        "用户不是素食主义者。",
        "用户的职业是测试工程师。",
        "用户居住在旧金山。",
        "用户喜欢绿茶。",
    ],
)
def test_summary_review_normalization_preserves_legal_semantics(text: str) -> None:
    assert SummaryImporter._clean_review_summary(text) == text


@pytest.mark.asyncio
async def test_summary_external_id_short_circuits_before_runtime_or_model() -> None:
    class ExistingSummaryStore:
        @staticmethod
        def get_external_memory_ref(external_id: str):
            assert external_id == "summary-1"
            return {"external_id": external_id, "paragraph_hash": "paragraph-1"}

        @staticmethod
        def get_paragraph(paragraph_hash: str):
            assert paragraph_hash == "paragraph-1"
            return {"hash": paragraph_hash, "source": "chat_summary:stream-1", "is_deleted": 0}

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=ExistingSummaryStore(),
        embedding_manager=None,
        plugin_config={},
    )

    async def fail_self_check():
        raise AssertionError("external ID 命中后不应执行运行时检查或模型调用")

    importer._ensure_runtime_self_check = fail_self_check
    result = await importer.import_from_stream(
        "stream-1",
        metadata={"external_id": "summary-1"},
    )

    assert result.success is True
    assert result.paragraph_hash == "paragraph-1"
    assert result.source == "chat_summary:stream-1"


def test_summary_external_id_cannot_be_reused_across_streams() -> None:
    class ExistingSummaryStore:
        @staticmethod
        def get_external_memory_ref(external_id: str):
            return {"external_id": external_id, "paragraph_hash": "paragraph-1"}

        @staticmethod
        def get_paragraph(paragraph_hash: str):
            return {
                "hash": paragraph_hash,
                "source": "chat_summary:stream-original",
                "is_deleted": 0,
            }

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=ExistingSummaryStore(),
        embedding_manager=None,
        plugin_config={},
    )

    with pytest.raises(RuntimeError, match="其他聊天流"):
        importer._existing_summary_result(
            stream_id="stream-other",
            metadata={"external_id": "summary-shared"},
        )


def test_summary_review_uses_explicit_supersession_instead_of_keywords() -> None:
    class SummaryStore:
        @staticmethod
        def get_live_paragraphs_by_source(source: str):
            assert source == "chat_summary:stream-1"
            return [
                {
                    "content": "此前的错误值已经失效。",
                    "created_at": 2.0,
                    "metadata": {"memory_change": {"valid_to": 1.0}},
                },
                {
                    "content": "用户的职业是测试工程师。",
                    "created_at": 1.0,
                    "metadata": {},
                },
            ]

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=SummaryStore(),
        embedding_manager=None,
        plugin_config={},
    )

    context = importer._build_previous_summary_context("stream-1", limit=2)

    assert "测试工程师" in context
    assert "错误值" not in context


@pytest.mark.asyncio
async def test_summary_import_serializes_same_stream_concurrency() -> None:
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={},
    )
    active = 0
    max_active = 0

    async def fake_import(stream_id: str, **kwargs):
        nonlocal active, max_active
        del kwargs
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return stream_id

    importer._import_from_stream_unlocked = fake_import

    results = await asyncio.gather(
        importer.import_from_stream("stream-1"),
        importer.import_from_stream("stream-1"),
    )

    assert results == ["stream-1", "stream-1"]
    assert max_active == 1


@pytest.mark.asyncio
async def test_summary_import_serializes_same_external_id_across_streams() -> None:
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={},
    )
    active = 0
    max_active = 0

    async def fake_import(stream_id: str, **kwargs):
        nonlocal active, max_active
        del kwargs
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return stream_id

    importer._import_from_stream_unlocked = fake_import

    results = await asyncio.gather(
        importer.import_from_stream("stream-1", metadata={"external_id": "summary-shared"}),
        importer.import_from_stream("stream-2", metadata={"external_id": "summary-shared"}),
    )

    assert results == ["stream-1", "stream-2"]
    assert max_active == 1


@pytest.mark.asyncio
async def test_generated_summary_uses_common_ingest_when_external_id_is_available() -> None:
    class SummaryStore:
        ref = None

        def get_external_memory_ref(self, external_id: str):
            assert external_id == "summary-common-1"
            return self.ref

        @staticmethod
        def add_paragraph(**kwargs):
            del kwargs
            raise AssertionError("存在公共 ingest 时不应直接写段落")

    class PluginInstance:
        def __init__(self, store: SummaryStore) -> None:
            self.store = store
            self.calls = []

        async def ingest_text(self, **kwargs):
            self.calls.append(dict(kwargs))
            self.store.ref = {
                "external_id": kwargs["external_id"],
                "paragraph_hash": "paragraph-common-1",
            }
            return {"stored_ids": ["paragraph-common-1"]}

    store = SummaryStore()
    plugin = PluginInstance(store)
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=store,
        embedding_manager=None,
        plugin_config={"plugin_instance": plugin},
    )

    paragraph_hash = await importer._execute_import(
        "测试摘要",
        ["测试用户"],
        [{"subject": "测试用户", "predicate": "喜欢", "object": "猫"}],
        "stream-1",
        time_meta={"event_time_start": 10.0, "event_time_end": 20.0},
        metadata={"external_id": "summary-common-1"},
    )

    assert paragraph_hash == "paragraph-common-1"
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["source_type"] == "chat_summary"
    assert plugin.calls[0]["respect_filter"] is False

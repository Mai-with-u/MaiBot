from types import SimpleNamespace

import asyncio

import pytest

from src.A_memorix.core.utils.summary_importer import (
    SUMMARY_PROMPT_TEMPLATE,
    SummaryImporter,
    _message_timestamp,
    _normalize_entity_items,
    _normalize_relation_items,
)
from src.config.model_configs import TaskConfig
from src.services import llm_service as llm_api


def test_persist_vector_store_delegates_to_runtime_facade() -> None:
    persisted: list[object] = []
    plugin = SimpleNamespace(persist_vector_store=lambda store: persisted.append(store))
    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=None,
        embedding_manager=None,
        plugin_config={"plugin_instance": plugin},
    )
    store = object()

    importer._persist_vector_store(store)  # type: ignore[arg-type]

    assert persisted == [store]


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


def test_summary_prompt_requires_incremental_output_from_current_window() -> None:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        bot_name="测试机器人",
        personality_context="",
        previous_summary_context="\n历史净化摘要回顾：\n- 用户喜欢绿茶。\n",
        chat_history="用户：今天天气不错。",
    )

    assert "只输出当前聊天窗口新引入或明确变更" in prompt
    assert "不得把未变化的历史事实再次写入 summary" in prompt
    assert '"summary": ""' in prompt


@pytest.mark.asyncio
async def test_empty_incremental_summary_is_successful_noop(monkeypatch) -> None:
    class SummaryStore:
        @staticmethod
        def get_external_memory_ref(external_id: str):
            assert external_id == "summary-noop-1"
            return None

        @staticmethod
        def get_live_paragraphs_by_source(source: str):
            assert source == "chat_summary:stream-1"
            return [{"content": "用户喜欢绿茶。", "created_at": 1.0, "metadata": {}}]

    importer = SummaryImporter(
        vector_store=None,
        graph_store=None,
        metadata_store=SummaryStore(),
        embedding_manager=None,
        plugin_config={"summarization": {"model_name": ["memory"]}},
    )

    async def fake_self_check():
        return True, ""

    async def fake_generate(request):
        assert "用户喜欢绿茶" in request.prompt
        return SimpleNamespace(
            success=True,
            completion=SimpleNamespace(
                response='{"summary": "", "entities": [], "relations": []}',
            ),
        )

    importer._ensure_runtime_self_check = fake_self_check
    monkeypatch.setattr(
        "src.A_memorix.core.utils.summary_importer.message_api.get_messages_by_time_in_chat",
        lambda **kwargs: [SimpleNamespace(timestamp=2.0)],
    )
    monkeypatch.setattr(
        "src.A_memorix.core.utils.summary_importer.message_api.build_readable_messages",
        lambda messages: "用户：今天天气不错。",
    )
    monkeypatch.setattr(llm_api, "get_available_models", _fake_available_models)
    monkeypatch.setattr(llm_api, "generate", fake_generate)

    result = await importer.import_from_stream(
        "stream-1",
        metadata={
            "external_id": "summary-noop-1",
            "summary_review_count": 1,
        },
    )

    assert result.success is True
    assert result.skipped is True
    assert result.paragraph_hash == ""


@pytest.mark.asyncio
async def test_history_only_followup_windows_do_not_write_repeated_paragraphs(monkeypatch) -> None:
    class SummaryStore:
        def __init__(self) -> None:
            self.paragraphs = []
            self.refs = {}

        def get_external_memory_ref(self, external_id: str):
            return self.refs.get(external_id)

        def get_live_paragraphs_by_source(self, source: str):
            assert source == "chat_summary:stream-1"
            return list(self.paragraphs)

        def upsert_external_memory_ref(self, *, external_id: str, paragraph_hash: str, **kwargs):
            del kwargs
            self.refs[external_id] = {
                "external_id": external_id,
                "paragraph_hash": paragraph_hash,
            }

    class GraphStore:
        def save(self) -> None:
            return None

    store = SummaryStore()
    importer = SummaryImporter(
        vector_store=None,
        graph_store=GraphStore(),
        metadata_store=store,
        embedding_manager=None,
        plugin_config={"summarization": {"model_name": ["memory"]}},
    )
    prompts = []
    paragraph_writes = []

    async def fake_self_check():
        return True, ""

    async def fake_generate(request):
        prompts.append(request.prompt)
        summary = "用户愿意和阿九一起组队游戏。" if len(prompts) == 1 else ""
        return SimpleNamespace(
            success=True,
            completion=SimpleNamespace(
                response=f'{{"summary": "{summary}", "entities": [], "relations": []}}',
            ),
        )

    async def fake_execute_import(summary, entities, relations, stream_id, **kwargs):
        del entities, relations, kwargs
        paragraph_hash = f"paragraph-{len(paragraph_writes) + 1}"
        paragraph_writes.append(summary)
        store.paragraphs.append(
            {
                "hash": paragraph_hash,
                "content": summary,
                "source": f"chat_summary:{stream_id}",
                "created_at": float(len(paragraph_writes)),
                "metadata": {},
            }
        )
        return paragraph_hash

    importer._ensure_runtime_self_check = fake_self_check
    importer._execute_import = fake_execute_import
    importer._persist_vector_store = lambda store: None
    monkeypatch.setattr(
        "src.A_memorix.core.utils.summary_importer.message_api.get_messages_by_time_in_chat",
        lambda **kwargs: [SimpleNamespace(timestamp=2.0)],
    )
    monkeypatch.setattr(
        "src.A_memorix.core.utils.summary_importer.message_api.build_readable_messages",
        lambda messages: "用户：继续聊刚才的话题。",
    )
    monkeypatch.setattr(llm_api, "get_available_models", _fake_available_models)
    monkeypatch.setattr(llm_api, "generate", fake_generate)

    results = [
        await importer.import_from_stream(
            "stream-1",
            metadata={
                "external_id": f"summary-window-{index}",
                "summary_review_count": 1,
            },
        )
        for index in range(3)
    ]

    assert paragraph_writes == ["用户愿意和阿九一起组队游戏。"]
    assert [result.skipped for result in results] == [False, True, True]
    assert "历史净化摘要回顾（只用于理解上下文" not in prompts[0]
    assert "历史净化摘要回顾（只用于理解上下文" in prompts[1]
    assert "用户愿意和阿九一起组队游戏" in prompts[1]
    assert "不得直接复制到本轮增量" in prompts[1]


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

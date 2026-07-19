from types import SimpleNamespace

import pytest

from src.A_memorix.core.retrieval.dual_path import DualPathRetriever, RetrievalResult
from src.A_memorix.core.utils.metadata import coerce_metadata_dict
from src.A_memorix.core.utils.person_profile_service import PersonProfileService
from src.A_memorix.core.utils.search_execution_service import SearchExecutionRequest, SearchExecutionService


class FakeMetadataStore:
    @staticmethod
    def get_paragraphs_by_source(source: str):
        return [
            {
                "hash": "person-fact-1",
                "content": "测试用户喜欢猫。",
                "source": source,
                "metadata": ["not", "a", "dict"],
                "is_deleted": 0,
                "created_at": 1.0,
                "updated_at": 1.0,
            }
        ]

    @staticmethod
    def get_paragraph_stale_relation_marks_batch(paragraph_hashes):
        del paragraph_hashes
        return {}

    @staticmethod
    def get_relation_status_batch(relation_hashes):
        del relation_hashes
        return {}


def test_person_fact_evidence_ignores_non_dict_metadata():
    service = PersonProfileService(metadata_store=FakeMetadataStore(), retriever=None)

    evidence = service._collect_person_fact_evidence("person-1")

    assert evidence[0]["metadata"] == {}


def test_search_serialization_ignores_non_dict_metadata():
    item = RetrievalResult(
        hash_value="paragraph-1",
        content="content",
        score=1.0,
        result_type="paragraph",
        source="test",
        metadata=["not", "a", "dict"],
    )

    payload = SearchExecutionService.to_serializable_results([item])

    assert payload[0]["metadata"] == {"time_meta": {}}


def test_dual_path_clone_ignores_non_dict_metadata():
    item = RetrievalResult(
        hash_value="relation-1",
        content="a likes b",
        score=1.0,
        result_type="relation",
        source="test",
        metadata=["not", "a", "dict"],
    )

    cloned = DualPathRetriever._clone_retrieval_result(item)

    assert cloned.metadata == {}


def test_dual_path_graph_merge_ignores_non_dict_metadata():
    retriever = object.__new__(DualPathRetriever)
    retriever.metadata_store = SimpleNamespace(
        get_paragraphs_by_relation_hashes=lambda relation_hashes: {
            relation_hash: [] for relation_hash in relation_hashes
        }
    )
    retriever._build_minmax_score_map = lambda results: {item.hash_value: 1.0 for item in results}

    merged = DualPathRetriever._merge_relation_results_graph_enhanced(
        retriever,
        [RetrievalResult("relation-1", "a likes b", 1.0, "relation", "vector", {})],
        [RetrievalResult("relation-1", "a likes b", 0.5, "relation", "sparse", ["not", "a", "dict"])],
        [],
    )

    assert merged[0].metadata["supporting_paragraph_count"] == 0
    assert merged[0].source == "relation_fusion"


def test_coerce_metadata_dict_ignores_non_mapping_metadata():
    assert coerce_metadata_dict(["not", "a", "dict"]) == {}
    assert coerce_metadata_dict({"kind": "chat_summary"}) == {"kind": "chat_summary"}


@pytest.mark.asyncio
async def test_search_execution_does_not_require_enable_ppr_config():
    class FakeRetriever:
        config = SimpleNamespace()

        def __init__(self):
            self.called = False

        async def retrieve(self, *, query, top_k, temporal, enable_ppr):
            del query, top_k, temporal, enable_ppr
            self.called = True
            return [
                RetrievalResult(
                    hash_value="paragraph-1",
                    content="content",
                    score=1.0,
                    result_type="paragraph",
                    source="test",
                    metadata={},
                )
            ]

    retriever = FakeRetriever()

    result = await SearchExecutionService.execute(
        retriever=retriever,
        threshold_filter=None,
        plugin_config={
            "retrieval": {
                "search": {
                    "smart_fallback": {"enabled": False},
                    "safe_content_dedup": {"enabled": False},
                }
            }
        },
        request=SearchExecutionRequest(
            caller="test",
            stream_id=None,
            group_id=None,
            user_id=None,
            query_type="search",
            query="content",
            top_k=1,
            time_from=None,
            time_to=None,
            person=None,
            source=None,
            use_threshold=True,
            enable_ppr=False,
        ),
        enforce_chat_filter=False,
    )

    assert result.success is True
    assert retriever.called is True
    assert len(result.results) == 1

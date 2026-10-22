import pytest

from src.A_memorix.core.utils.episode_retrieval_service import EpisodeRetrievalService


class _FakeMetadataStore:
    def __init__(self, lexical_rows):
        self.lexical_rows = lexical_rows
        self.query_episode_limits = []
        self.paragraph_evidence_requests = []
        self.relation_evidence_requests = []

    def query_episodes(self, **kwargs):
        self.query_episode_limits.append(kwargs["limit"])
        return list(self.lexical_rows)

    def get_episode_rows_by_paragraph_hashes(self, hashes, **kwargs):
        self.paragraph_evidence_requests.append((list(hashes), kwargs))
        return []

    def get_episode_rows_by_relation_hashes(self, hashes, **kwargs):
        self.relation_evidence_requests.append((list(hashes), kwargs))
        return []


class _Result:
    def __init__(self, hash_value: str, result_type: str):
        self.hash_value = hash_value
        self.result_type = result_type


class _FakeRetriever:
    def __init__(self):
        self.top_k_values = []

    async def retrieve(self, *, query, top_k, temporal):
        del query, temporal
        self.top_k_values.append(top_k)
        return [
            _Result("p-1", "paragraph"),
            _Result("r-1", "relation"),
        ]




@pytest.mark.asyncio
async def test_episode_relation_evidence_is_conditionally_projected() -> None:
    metadata_store = _FakeMetadataStore(
        [{"episode_id": "e-1", "updated_at": 1.0}],
    )
    retriever = _FakeRetriever()
    service = EpisodeRetrievalService(metadata_store=metadata_store, retriever=retriever)

    await service.query(query="普通聊天回忆", top_k=1)

    assert metadata_store.relation_evidence_requests == []

    await service.query(query="艾宝和稀疏检索有什么关系", top_k=1)

    assert metadata_store.relation_evidence_requests == [(["r-1"], {"source": None})]

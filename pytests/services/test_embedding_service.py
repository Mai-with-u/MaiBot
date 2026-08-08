from types import SimpleNamespace

import pytest

from src.services.embedding_service import EmbeddingServiceClient


@pytest.mark.asyncio
async def test_embed_texts_can_return_parallel_item_failures_in_input_order(monkeypatch) -> None:
    """允许局部失败时，并发批量结果应保留输入顺序和单条异常。"""

    client = object.__new__(EmbeddingServiceClient)

    async def fake_embed_text(_self, embedding_input, **_kwargs):
        if embedding_input == "bad":
            raise ValueError("局部失败")
        return SimpleNamespace(embedding=[float(len(embedding_input))])

    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fake_embed_text)

    results = await client.embed_texts(
        ["first", "bad", "last"],
        max_concurrent=3,
        return_exceptions=True,
    )

    assert results[0].embedding == [5.0]
    assert isinstance(results[1], ValueError)
    assert results[2].embedding == [4.0]


@pytest.mark.asyncio
async def test_embed_texts_keeps_fail_fast_default(monkeypatch) -> None:
    """未显式启用局部失败时，原有批量接口仍应向上抛错。"""

    client = object.__new__(EmbeddingServiceClient)

    async def fail_embed_text(_self, _embedding_input, **_kwargs):
        raise ValueError("批量失败")

    monkeypatch.setattr(EmbeddingServiceClient, "embed_text", fail_embed_text)

    with pytest.raises(ValueError, match="批量失败"):
        await client.embed_texts(["bad"], return_exceptions=False)

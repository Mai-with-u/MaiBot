from unittest.mock import AsyncMock

import httpx
import pytest

from src.webui.services import ai_search_documents


@pytest.mark.asyncio
async def test_official_docs_use_new_bundle_and_citation_host(monkeypatch):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            text=(
                "---\nurl: 'https://docs.maibot.chat/manual/configuration/model-config.md'\n---\n"
                "\n# Model configuration\n\nConfigure model providers here.\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(ai_search_documents.httpx, "AsyncClient", lambda **_: client)
    monkeypatch.setattr(ai_search_documents, "get_shared_ssl_context_async", AsyncMock())
    store = ai_search_documents.AISearchDocumentStore()

    [match] = await store.search_official_docs("model", limit=1)
    [document] = await store.read_official_docs([match["path"]])
    [source] = store.build_sources([document["source_id"]])

    assert requests == ["https://docs.maibot.chat/llms-full.txt"]
    assert "Configure model providers here." in document["content"]
    assert (
        match["url"]
        == document["url"]
        == source["url"]
        == ("https://docs.maibot.chat/manual/configuration/model-config")
    )


@pytest.mark.asyncio
async def test_html_migration_notice_is_not_cached_as_a_document_bundle(monkeypatch):
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<html><a href="https://docs.maibot.chat/">Documentation has moved</a></html>',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(ai_search_documents.httpx, "AsyncClient", lambda **_: client)
    monkeypatch.setattr(ai_search_documents, "get_shared_ssl_context_async", AsyncMock())
    store = ai_search_documents.AISearchDocumentStore()

    with pytest.raises(ValueError, match="官方文档包端点返回了网页"):
        await store.search_official_docs("model", limit=1)

    assert store.build_sources(["/manual/configuration/model-config.md"]) == []

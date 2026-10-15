from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.plugin_runtime.host.supervisor import PluginSupervisor
from src.plugin_runtime.webui_schema import MAX_CUSTOM_DOCUMENT_BYTES, WebUIExtension, load_webui_extension


def test_document_reload_updates_revision_without_replacing_bindings(tmp_path):
    (tmp_path / "webui.json").write_text(
        '{"required_capabilities":["custom_pages"],"pages":[{"id":"images","title":"Images","mode":"custom","entry":"images.html"}]}',
        encoding="utf-8",
    )
    (tmp_path / "images.html").write_text("old", encoding="utf-8")
    original = load_webui_extension(str(tmp_path))
    registration = SimpleNamespace(webui=original)
    supervisor = object.__new__(PluginSupervisor)
    supervisor._registered_plugins = {"test.plugin": registration}
    (tmp_path / "images.html").write_text("new", encoding="utf-8")
    updated = load_webui_extension(str(tmp_path))
    supervisor._rpc_server = SimpleNamespace(
        send_request=AsyncMock(
            return_value=SimpleNamespace(
                error=None,
                payload={"documents": [{"id": p.id, "entry": p.entry, "document": p.document} for p in updated.pages]},
            )
        )
    )
    asyncio.run(supervisor.reload_webui_documents("test.plugin"))
    assert registration.webui.pages[0].document == "new"
    assert registration.webui.pages[0].document_revision != original.pages[0].document_revision
    assert registration.webui.pages[0].actions == original.pages[0].actions
    supervisor._rpc_server.send_request.assert_awaited_once_with(
        "plugin.webui.reload_documents",
        plugin_id="test.plugin",
        timeout_ms=10000,
    )


def test_failed_document_reload_preserves_snapshot():
    registration = SimpleNamespace(webui=object())
    supervisor = object.__new__(PluginSupervisor)
    supervisor._registered_plugins = {"test.plugin": registration}
    supervisor._rpc_server = SimpleNamespace(
        send_request=AsyncMock(
            return_value=SimpleNamespace(
                error={"message": "invalid HTML path"},
            )
        )
    )
    original = registration.webui
    with pytest.raises(ValueError, match="invalid HTML path"):
        asyncio.run(supervisor.reload_webui_documents("test.plugin"))
    assert registration.webui is original


@pytest.mark.parametrize(
    "change", ["oversized", "utf8", "empty", "missing", "duplicate", "unknown", "extra_field", "total"]
)
def test_host_validates_untrusted_reload_before_atomic_replacement(change):
    original = WebUIExtension.model_validate(
        {
            "required_capabilities": ["custom_pages"],
            "pages": [
                {"id": f"page{i}", "title": "Page", "mode": "custom", "entry": f"{i}.html", "document": "old"}
                for i in range(5 if change == "total" else 1)
            ],
        }
    )
    documents = [{"id": page.id, "entry": page.entry, "document": "new"} for page in original.pages]
    if change == "oversized":
        documents[0]["document"] = "x" * (MAX_CUSTOM_DOCUMENT_BYTES + 1)
    elif change == "utf8":
        documents[0]["document"] = "界" * (MAX_CUSTOM_DOCUMENT_BYTES // 3 + 1)
    elif change == "empty":
        documents[0]["document"] = "   "
    elif change == "missing":
        documents.clear()
    elif change == "duplicate":
        documents.append(documents[0])
    elif change == "unknown":
        documents[0]["entry"] = "other.html"
    elif change == "extra_field":
        documents[0]["actions"] = {}
    else:
        for document in documents:
            document["document"] = "x" * MAX_CUSTOM_DOCUMENT_BYTES
    registration = SimpleNamespace(webui=original)
    supervisor = object.__new__(PluginSupervisor)
    supervisor._registered_plugins = {"test.plugin": registration}
    supervisor._rpc_server = SimpleNamespace(
        send_request=AsyncMock(
            return_value=SimpleNamespace(
                error=None,
                payload={"documents": documents},
            )
        )
    )
    with pytest.raises(ValueError):
        asyncio.run(supervisor.reload_webui_documents("test.plugin"))
    assert registration.webui is original

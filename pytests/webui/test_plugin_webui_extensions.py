from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.plugin_runtime.host.api_registry import APIRegistry
from src.plugin_runtime.host.supervisor import PluginSupervisor
from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RegisterPluginPayload
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.webui_schema import MAX_CUSTOM_DOCUMENT_BYTES, WebUIExtension, load_webui_extension
from src.webui.routers.plugin import webui_extensions as routes


def declaration():
    return {
        "schema_version": 2,
        "workspace_title": "统计",
        "pages": [
            {
                "id": "overview",
                "title": "概览",
                "placement": "workspace",
                "queries": {"summary": {"api": "summary"}},
                "actions": {
                    "reset": {
                        "api": "reset",
                        "confirmation": "确认重置？",
                        "parameters": {
                            "count": {"type": "integer", "required": True, "minimum": 1, "maximum": 10},
                        },
                    }
                },
                "content": [
                    {"type": "stat", "label": "今日消息", "value": {"source": "summary", "field": "count"}},
                    {"type": "button", "label": "重置", "action": "reset"},
                ],
            }
        ],
    }


def test_pagination_requires_bound_query_and_page_parameter():
    raw = declaration()
    raw["pages"][0]["auto_refresh"] = True
    node = {"type": "pagination", "name": "page", "value": {"source": "summary", "field": "pagination"}}
    raw["pages"][0]["content"] = [node]
    assert WebUIExtension.model_validate(raw).pages[0].auto_refresh
    del node["name"]
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


@pytest.mark.parametrize("property_name", ["html", "script", "style", "className", "onClick", "href"])
def test_rejects_executable_and_arbitrary_style_properties(property_name):
    raw = declaration()
    raw["pages"][0]["content"][0][property_name] = "arbitrary"
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


def test_rejects_unknown_bindings_and_duplicate_pages():
    raw = declaration()
    raw["pages"][0]["content"][0]["value"]["source"] = "other"
    with pytest.raises(ValidationError, match="未声明的数据上下文"):
        WebUIExtension.model_validate(raw)
    raw = declaration()
    raw["pages"].append(deepcopy(raw["pages"][0]))
    with pytest.raises(ValidationError, match="页面 ID 重复"):
        WebUIExtension.model_validate(raw)


def test_rejects_deep_component_trees():
    raw = declaration()
    node = {"type": "text", "value": "deep"}
    for _ in range(8):
        node = {"type": "stack", "children": [node]}
    raw["pages"][0]["content"] = [node]
    with pytest.raises(ValidationError, match="8 层"):
        WebUIExtension.model_validate(raw)


def detail_declaration():
    raw = declaration()
    page = raw["pages"][0]
    page["actions"]["reset"]["arguments"] = {"count": {"scope": "selection", "source": "selectedRow", "field": "count"}}
    page["content"] = [
        {
            "type": "table",
            "value": {"source": "summary", "field": "rows"},
            "columns": [{"field": "count", "label": "数量"}],
            "selection": "selectedRow",
            "detail": "details",
        },
        {
            "type": "dialog",
            "name": "details",
            "label": "详情",
            "children": [
                {"type": "text", "value": {"scope": "selection", "source": "selectedRow", "field": "count"}},
                {"type": "button", "label": "重置", "action": "reset", "variant": "danger"},
            ],
        },
    ]
    return raw


def test_details_and_action_binding_roundtrip():
    extension = WebUIExtension.model_validate(detail_declaration())
    assert WebUIExtension.model_validate(extension.model_dump()) == extension
    # Row fields still go through the same strict gateway scalar validation.
    binding = extension.pages[0].actions["reset"]
    binding.validate_args({"count": 2})
    with pytest.raises(ValueError):
        binding.validate_args({"count": {"count": 2}})


def test_loader_and_registration_roundtrip(tmp_path):
    assert load_webui_extension(str(tmp_path)) is None
    (tmp_path / "webui.json").write_bytes(json.dumps(declaration()).encode())
    extension = load_webui_extension(str(tmp_path))
    payload = RegisterPluginPayload(plugin_id="test.plugin", webui=extension)
    assert RegisterPluginPayload.model_validate(payload.model_dump()).webui == extension
    (tmp_path / "webui.json").write_bytes(b" " * 131073)
    with pytest.raises(ValueError, match="128 KiB"):
        load_webui_extension(str(tmp_path))


@pytest.fixture
def supervisor(monkeypatch):
    extension = WebUIExtension.model_validate(declaration())
    registry = APIRegistry()
    registry.register_api("summary", "test.plugin", {"handler_name": "get_summary"})
    registry.register_api("reset", "test.plugin", {"handler_name": "reset_counts"})
    fake = SimpleNamespace(
        api_registry=registry,
        get_webui_extensions=lambda: {"test.plugin": extension},
        invoke_api=AsyncMock(
            return_value=SimpleNamespace(error=None, payload={"success": True, "result": {"count": 3}})
        ),
    )
    monkeypatch.setattr(routes.component_query_service, "_iter_supervisors", lambda: [fake])
    routes._inflight.clear()
    return fake


@pytest.mark.asyncio
async def test_invokes_only_registered_owner_and_handler(supervisor):
    result = await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert result == {"success": True, "result": {"count": 3}}
    supervisor.invoke_api.assert_awaited_once_with("test.plugin", "get_summary", args={}, timeout_ms=10000)
    for plugin_id, kind, name in [
        ("other.plugin", "queries", "summary"),
        ("test.plugin", "actions", "summary"),
        ("test.plugin", "queries", "other.api"),
    ]:
        with pytest.raises(HTTPException) as exc:
            await routes._invoke(plugin_id, "overview", kind, name, routes.Invocation())
        assert exc.value.status_code in {403, 404}
    assert supervisor.invoke_api.await_count == 1


@pytest.mark.asyncio
async def test_requires_confirmation_and_validates_parameters(supervisor):
    requests = [
        (routes.Invocation(args={"count": 2}), 409),
        (routes.Invocation(args={"count": True}, confirmed=True), 422),
        (routes.Invocation(args={"count": "2"}, confirmed=True), 422),
        (routes.Invocation(args={"count": 11}, confirmed=True), 422),
        (routes.Invocation(confirmed=True), 422),
        (routes.Invocation(args={"count": 2, "extra": "value"}, confirmed=True), 422),
    ]
    for request, status in requests:
        with pytest.raises(HTTPException) as exc:
            await routes._invoke("test.plugin", "overview", "actions", "reset", request)
        assert exc.value.status_code == status
    supervisor.invoke_api.assert_not_awaited()
    await routes._invoke(
        "test.plugin", "overview", "actions", "reset", routes.Invocation(args={"count": 2}, confirmed=True)
    )
    assert routes._inflight == {}


@pytest.mark.asyncio
async def test_disabled_and_dynamic_apis_are_not_callable(supervisor):
    entry = supervisor.api_registry.get_api("test.plugin", "summary")
    entry.enabled = False
    with pytest.raises(HTTPException) as exc:
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert exc.value.status_code == 404
    entry.enabled = True
    entry.dynamic = True
    with pytest.raises(HTTPException):
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    supervisor.invoke_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrency_and_cancellation_release_slots(supervisor):
    entered = asyncio.Event()

    async def wait_for_cancel(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    supervisor.invoke_api.side_effect = wait_for_cancel
    routes._inflight["test.plugin"] = 1
    task = asyncio.create_task(routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation()))
    await entered.wait()
    with pytest.raises(HTTPException) as exc:
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert exc.value.status_code == 429
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert routes._inflight == {"test.plugin": 1}
    routes._inflight.clear()


@pytest.mark.asyncio
async def test_errors_are_exposed_and_slots_released(supervisor):
    supervisor.invoke_api.return_value = SimpleNamespace(error=None, payload={"success": False})
    with pytest.raises(HTTPException) as exc:
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert exc.value.status_code == 502
    assert not routes._inflight


@pytest.mark.asyncio
async def test_rpc_timeout_is_reported_as_timeout(supervisor):
    supervisor.invoke_api.side_effect = RPCError(ErrorCode.E_TIMEOUT)
    with pytest.raises(HTTPException) as exc:
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert exc.value.status_code == 504
    assert not routes._inflight
    supervisor.invoke_api.side_effect = TimeoutError
    with pytest.raises(HTTPException) as exc:
        await routes._invoke(
            "test.plugin", "overview", "actions", "reset", routes.Invocation(args={"count": 1}, confirmed=True)
        )
    assert exc.value.status_code == 504
    assert not routes._inflight


@pytest.mark.parametrize(
    "result", [{"value": float("nan")}, {"value": b"bytes"}, "x" * 524289], ids=["nan", "bytes", "oversized"]
)
def test_rejects_non_json_and_oversized_results(result):
    with pytest.raises(ValueError):
        routes._validate_result(result)


def test_requires_authenticated_webui_session(monkeypatch):
    monkeypatch.setattr(routes, "require_plugin_token", lambda token: (_ for _ in ()).throw(HTTPException(401)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_webui_extensions())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unload_removes_pages(supervisor, monkeypatch):
    assert (await routes._list_extensions())["extensions"][0]["plugin_id"] == "test.plugin"
    monkeypatch.setattr(supervisor, "get_webui_extensions", lambda: {})
    assert (await routes._list_extensions())["extensions"] == []
    with pytest.raises(HTTPException) as exc:
        await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert exc.value.status_code == 404


def test_authenticated_http_gateway(supervisor, monkeypatch):
    def authorize(token):
        if token != "valid-session":
            raise HTTPException(401)

    monkeypatch.setattr(routes, "require_plugin_token", authorize)
    app = FastAPI()
    app.include_router(routes.router, prefix="/plugins")
    with TestClient(app) as client:
        assert client.get("/plugins/runtime/webui").status_code == 401
        client.cookies.set("maibot_session", "valid-session")
        listed = client.get("/plugins/runtime/webui")
        assert listed.status_code == 200
        assert listed.json()["extensions"][0]["pages"][0]["content"][0]["type"] == "stat"
        path = "/plugins/runtime/webui/test.plugin/overview"
        assert client.post(f"{path}/queries/summary", json={"args": {}}).json()["result"] == {"count": 3}
        assert client.post(f"{path}/actions/reset", json={"args": {"count": 2}}).status_code == 409
        assert client.post(f"{path}/queries/summary", json={"args": {}, "api": "other.plugin.api"}).status_code == 422
        assert client.post(f"{path}/actions/reset", json={"args": {"count": 2}, "confirmed": True}).status_code == 200


@pytest.mark.asyncio
async def test_host_rejects_pages_bound_to_unregistered_apis(monkeypatch):
    supervisor = object.__new__(PluginSupervisor)
    monkeypatch.setattr(supervisor, "_build_granted_capabilities", lambda payload: [])
    payload = RegisterPluginPayload(plugin_id="test.plugin", webui=WebUIExtension.model_validate(declaration()))
    envelope = Envelope(request_id=1, message_type=MessageType.REQUEST, payload=payload.model_dump())
    response = await supervisor._handle_register_plugin(envelope)
    assert response.error is not None
    assert "summary@1" in response.error["message"]


def custom_declaration():
    raw = declaration()
    raw["required_capabilities"] = ["custom_pages"]
    raw["pages"][0].update(mode="custom", entry="webui/index.html", document="<h1>Hello</h1>")
    del raw["pages"][0]["content"]
    return raw


def test_custom_page_registration_roundtrip_and_capability():
    raw = custom_declaration()
    extension = WebUIExtension.model_validate(raw)
    assert WebUIExtension.model_validate(extension.model_dump()) == extension
    payload = RegisterPluginPayload(plugin_id="test.plugin", webui=extension)
    assert RegisterPluginPayload.model_validate(payload.model_dump()).webui == extension
    raw["required_capabilities"] = []
    with pytest.raises(ValidationError, match="custom_pages"):
        WebUIExtension.model_validate(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"content": [{"type": "text"}]},
        {"document": ""},
        {"entry": None},
        {"auto_refresh": True},
        {"poll_interval_seconds": 3},
        {"document": "界" * (MAX_CUSTOM_DOCUMENT_BYTES // 3 + 1)},
    ],
)
def test_custom_page_rejects_invalid_or_oversized_declarations(changes):
    raw = custom_declaration()
    raw["pages"][0].update(changes)
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


def test_custom_loader_reads_bounded_utf8_snapshot(tmp_path):
    raw = custom_declaration()
    del raw["pages"][0]["document"]
    (tmp_path / "webui").mkdir()
    entry = tmp_path / "webui/index.html"
    entry.write_text("<script>console.log('自由页面')</script>", encoding="utf-8")
    (tmp_path / "webui.json").write_text(json.dumps(raw), encoding="utf-8")
    extension = load_webui_extension(str(tmp_path))
    assert "自由页面" in extension.pages[0].document
    revision = extension.pages[0].document_revision
    entry.write_text("<h1>Changed</h1>", encoding="utf-8")
    assert load_webui_extension(str(tmp_path)).pages[0].document_revision != revision
    entry.write_bytes(b"x" * (MAX_CUSTOM_DOCUMENT_BYTES + 1))
    with pytest.raises(ValueError, match="2 MiB"):
        load_webui_extension(str(tmp_path))


@pytest.mark.parametrize("entry", ["../outside.html", "C:/outside.html", "/outside.html", "webui/config.toml"])
def test_custom_loader_rejects_outside_paths(tmp_path, entry):
    raw = custom_declaration()
    del raw["pages"][0]["document"]
    raw["pages"][0]["entry"] = entry
    (tmp_path / "webui.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="相对 HTML"):
        load_webui_extension(str(tmp_path))


def test_custom_document_is_authenticated_json_and_unload_revokes_access(supervisor, monkeypatch):
    extension = WebUIExtension.model_validate(custom_declaration())
    monkeypatch.setattr(supervisor, "get_webui_extensions", lambda: {"test.plugin": extension})

    def authorize(token):
        if token != "valid-session":
            raise HTTPException(401)

    monkeypatch.setattr(routes, "require_plugin_token", authorize)
    app = FastAPI()
    app.include_router(routes.router, prefix="/plugins")
    path = "/plugins/runtime/webui/test.plugin/overview/document"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        client.cookies.set("maibot_session", "valid-session")
        listed = client.get("/plugins/runtime/webui").json()
        page = listed["extensions"][0]["pages"][0]
        assert "document" not in page
        assert page["document_revision"] == extension.pages[0].document_revision
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.json()["document"] == "<h1>Hello</h1>"
        assert client.get(path.replace("test.plugin", "other.plugin")).status_code == 404
        assert client.get(path.replace("overview", "other")).status_code == 404
        monkeypatch.setattr(supervisor, "get_webui_extensions", lambda: {})
        assert client.get(path).status_code == 404


def test_custom_loader_bounds_page_count_before_reading_files(tmp_path):
    raw = custom_declaration()
    del raw["pages"][0]["document"]
    raw["pages"] *= 21
    (tmp_path / "webui.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="20 个页面"):
        load_webui_extension(str(tmp_path))


def test_custom_loader_bounds_total_documents_before_registration(tmp_path):
    raw = custom_declaration()
    del raw["pages"][0]["document"]
    raw["pages"] = [{**raw["pages"][0], "id": f"page{index}"} for index in range(5)]
    (tmp_path / "webui").mkdir()
    (tmp_path / "webui/index.html").write_bytes(b"x" * MAX_CUSTOM_DOCUMENT_BYTES)
    (tmp_path / "webui.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="总量超过 8 MiB"):
        load_webui_extension(str(tmp_path))


@pytest.mark.parametrize("component", ["repeat", "multi_select", "checkbox", "gallery", "upload"])
def test_removed_components_are_rejected_without_compatibility_conversion(component):
    raw = declaration()
    raw["pages"][0]["content"] = [{"type": component}]
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


def test_component_models_reject_unrelated_fields_even_at_old_default_values():
    raw = declaration()
    raw["pages"][0]["content"] = [{"type": "text", "value": "hello", "columns": None}]
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)
    raw["schema_version"] = 1
    with pytest.raises(ValidationError, match="迁移声明"):
        WebUIExtension.model_validate(raw)


def test_structured_arguments_are_typed_bounded_and_restricted_to_custom_pages():
    raw = custom_declaration()
    binding = raw["pages"][0]["actions"]["reset"]
    binding["parameters"] = {
        "rows": {
            "type": "array",
            "required": True,
            "max_items": 2,
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "required": True}},
            },
        },
    }
    with pytest.raises(ValidationError, match="structured_arguments"):
        WebUIExtension.model_validate(raw)
    raw["required_capabilities"].append("structured_arguments")
    extension = WebUIExtension.model_validate(raw)
    api = extension.pages[0].actions["reset"]
    api.validate_args({"rows": [{"id": "with,comma"}]})
    for args in [
        {"rows": [1]},
        {"rows": [{}]},
        {"rows": [{"id": "x", "unknown": 1}]},
        {"rows": [{"id": "x"}] * 3},
        {"rows": "[]"},
    ]:
        with pytest.raises(ValueError):
            api.validate_args(args)
    raw = declaration()
    raw["pages"][0]["actions"]["reset"] = binding
    with pytest.raises(ValidationError, match="预置表单"):
        WebUIExtension.model_validate(raw)
    with pytest.raises(ValidationError):
        routes.Invocation(args={"payload": "界" * 22000})
    nested = None
    for _ in range(14):
        nested = [nested]
    with pytest.raises(ValidationError):
        routes.Invocation(args={"payload": nested})


@pytest.mark.asyncio
async def test_business_result_fields_are_opaque_to_the_gateway(supervisor):
    result = {"success": False, "error": "task did not succeed", "navigate_page": "another"}
    supervisor.invoke_api.return_value = SimpleNamespace(error=None, payload={"success": True, "result": result})
    response = await routes._invoke("test.plugin", "overview", "queries", "summary", routes.Invocation())
    assert response == {"success": True, "result": result}

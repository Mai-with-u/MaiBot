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
from src.plugin_runtime.webui_schema import WebUIExtension, load_webui_extension
from src.webui.routers.plugin import webui_extensions as routes


def declaration():
    return {
        "schema_version": 1,
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


def test_generic_dialog_trigger_and_select_presentation():
    raw = declaration()
    raw["pages"][0]["content"] = [
        {"type": "button", "label": "上传", "detail": "upload",
         "disabled_when": {"reference": {"source": "summary", "field": "ready"}, "operator": "equals", "expected": False},
         "disabled_reason": "环境尚未准备好"},
        {"type": "dialog", "name": "upload", "label": "上传", "children": [
            {"type": "select", "name": "identity", "presentation": "buttons", "value": "self",
             "options": [{"label": "是自己", "value": "self"}, {"label": "不是自己", "value": "other"}]}]},
    ]
    assert WebUIExtension.model_validate(raw).pages[0].content[0].detail == "upload"
    raw["pages"][0]["content"][0]["action"] = "reset"
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)
    del raw["pages"][0]["content"][0]["action"]
    raw["pages"][0]["content"][0]["detail"] = "missing"
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


@pytest.mark.parametrize("columns", [0, 5])
def test_gallery_requires_bounded_columns(columns):
    raw = declaration()
    raw["pages"][0]["content"] = [{"type": "gallery", "columns": columns, "value": {"source": "summary", "field": "images"}}]
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


def test_gallery_accepts_declared_query_and_rejects_inline_images():
    raw = declaration()
    gallery = {"type": "gallery", "columns": 3, "value": {"source": "summary", "field": "images"}}
    raw["pages"][0]["content"] = [gallery]
    assert WebUIExtension.model_validate(raw).pages[0].content[0].type == "gallery"
    gallery["columns"] = None
    assert WebUIExtension.model_validate(raw).pages[0].content[0].columns is None
    gallery["value"] = "data:image/jpeg;base64,aGVsbG8="
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


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
    with pytest.raises(ValidationError, match="未声明的查询"):
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
    page["actions"]["reset"]["arguments"] = {
        "count": {"scope": "selection", "source": "selectedRow", "field": "count"}
    }
    page["content"] = [
        {"type": "table", "value": {"source": "summary", "field": "rows"},
         "columns": [{"field": "count", "label": "数量"}], "selection": "selectedRow", "detail": "details"},
        {"type": "dialog", "name": "details", "label": "详情", "children": [
            {"type": "text", "value": {"scope": "selection", "source": "selectedRow", "field": "count"}},
            {"type": "button", "label": "重置", "action": "reset", "variant": "danger"},
        ]},
        {"type": "repeat", "name": "row", "value": {"source": "summary", "field": "rows"}, "max_items": 20,
         "when": {"reference": {"source": "summary", "field": "rows"}, "operator": "not_empty"},
         "children": [{"type": "text", "value": {"scope": "item", "source": "row", "field": "count"}}]},
    ]
    return raw


def test_details_loop_and_action_binding_roundtrip():
    extension = WebUIExtension.model_validate(detail_declaration())
    assert WebUIExtension.model_validate(extension.model_dump()) == extension
    # Row fields still go through the same strict gateway scalar validation.
    binding = extension.pages[0].actions["reset"]
    binding.validate_args({"count": 2})
    with pytest.raises(ValueError):
        binding.validate_args({"count": {"count": 2}})


@pytest.mark.parametrize("mutation", [
    "unknown_dialog", "unknown_selection", "item_outside_loop", "loop_input", "duplicate_selection",
    "undeclared_argument", "query_arguments", "loop_limit", "condition_source", "wrong_property",
])
def test_rejects_invalid_detail_and_loop_declarations(mutation):
    raw = detail_declaration()
    page = raw["pages"][0]
    table, dialog, repeat = page["content"]
    if mutation == "unknown_dialog":
        table["detail"] = "missing"
    elif mutation == "unknown_selection":
        dialog["children"][0]["value"]["source"] = "missing"
    elif mutation == "item_outside_loop":
        dialog["children"][0]["value"] = {"scope": "item", "source": "row", "field": "count"}
    elif mutation == "loop_input":
        repeat["children"] = [{"type": "input", "name": "repeated", "value": ""}]
    elif mutation == "duplicate_selection":
        page["content"].append(deepcopy(table))
    elif mutation == "undeclared_argument":
        page["actions"]["reset"]["arguments"]["extra"] = {"source": "summary", "field": "count"}
    elif mutation == "query_arguments":
        page["queries"]["summary"]["arguments"] = {"count": {"scope": "selection", "source": "selectedRow"}}
        page["queries"]["summary"]["parameters"] = {"count": {"type": "integer"}}
    elif mutation == "loop_limit":
        repeat["max_items"] = 101
    elif mutation == "condition_source":
        repeat["when"]["reference"]["source"] = "missing"
    elif mutation == "wrong_property":
        dialog["selection"] = "unsupported"
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)


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

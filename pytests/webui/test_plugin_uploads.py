from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import io
import time

import pytest
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.plugin_runtime import upload_store as storage
from src.plugin_runtime.host.api_registry import APIRegistry
from src.plugin_runtime.webui_schema import WebUIExtension
from src.webui.routers.plugin import webui_extensions as routes


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("format,suffix", [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp")])
def test_supported_static_formats(format, suffix):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format=format)
    assert storage.validate_image(buffer.getvalue()) == suffix


def declaration():
    return dict(
        required_capabilities=["custom_pages", "image_upload"],
        pages=[
            dict(
                id="images",
                title="Images",
                mode="custom",
                entry="index.html",
                document="<h1>Images</h1>",
                actions={"add": dict(api="add", parameters={"upload_id": dict(type="string", required=True)})},
                uploads={"library": {"action": "add", "image_max_edge": 4000}},
            )
        ],
    )


def test_image_upload_binding_constraints():
    raw = declaration()
    assert WebUIExtension.model_validate(raw).pages[0].uploads["library"].action == "add"
    raw["required_capabilities"].remove("image_upload")
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)
    raw = declaration()
    raw["pages"][0]["uploads"]["library"]["action"] = "undeclared"
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)
    raw = declaration()
    raw["pages"][0]["actions"]["add"]["confirmation"] = "Confirm?"
    assert WebUIExtension.model_validate(raw).pages[0].actions["add"].confirmation == "Confirm?"


def test_token_ownership_expiry_and_single_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECT_ROOT", tmp_path)
    store = storage.UploadStore(tmp_path / "stage")
    token = store.stage("test.plugin", png())
    with pytest.raises(ValueError):
        store.claim("other.plugin", token)
    result = store.claim("test.plugin", token)
    assert Path(result["path"]).read_bytes() == png()
    with pytest.raises(ValueError):
        store.claim("test.plugin", token)
    expired = store.stage("test.plugin", png())
    with store.connect() as db:
        db.execute("UPDATE uploads SET expires=? WHERE token=?", (time.time() - 1, expired))
    with pytest.raises(ValueError):
        store.claim("test.plugin", expired)


@pytest.mark.parametrize("raw", [b"PK archive", b"GIF89a", b""])
def test_invalid_files(raw):
    with pytest.raises((ValueError, OSError)):
        storage.validate_image(raw)


def test_size_pixel_and_animation_limits(monkeypatch):
    monkeypatch.setattr(storage, "MAX_BYTES", 4)
    with pytest.raises(ValueError):
        storage.validate_image(png())
    monkeypatch.setattr(storage, "MAX_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(storage, "MAX_PIXELS", 2)
    with pytest.raises(ValueError):
        storage.validate_image(png())
    monkeypatch.setattr(storage, "MAX_PIXELS", 40_000_000)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(
        buffer, format="WEBP", save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0
    )
    with pytest.raises(ValueError):
        storage.validate_image(buffer.getvalue())


def test_authenticated_multipart_and_owner_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECT_ROOT", tmp_path)
    store = storage.UploadStore(tmp_path / "stage")
    monkeypatch.setattr(routes, "upload_store", lambda: store)

    def auth(token):
        if token != "valid":
            raise HTTPException(401)

    monkeypatch.setattr(routes, "require_plugin_token", auth)
    extension = WebUIExtension.model_validate(declaration())
    registry = APIRegistry()
    registry.register_api("add", "test.plugin", {"handler_name": "add_image"})
    supervisor = SimpleNamespace(
        api_registry=registry,
        get_webui_extensions=lambda: {"test.plugin": extension},
        invoke_api=AsyncMock(
            return_value=SimpleNamespace(error=None, payload={"success": True, "result": {"added": True}})
        ),
    )
    monkeypatch.setattr(routes.component_query_service, "_iter_supervisors", lambda: [supervisor])
    app = FastAPI()
    app.include_router(routes.router, prefix="/plugins")
    path = "/plugins/runtime/webui/test.plugin/images/uploads/library"
    with TestClient(app) as client:
        assert client.post(path, files={"file": ("fake.exe", png())}).status_code == 401
        client.cookies.set("maibot_session", "valid")
        assert (
            client.post(
                path.replace("/uploads/library", "/uploads/undeclared"), files={"file": ("x.png", png())}
            ).status_code
            == 403
        )
        assert (
            client.post(path.replace("test.plugin", "other.plugin"), files={"file": ("x.png", png())}).status_code
            == 404
        )
        assert client.post(path, files={"file": ("x.zip", b"PK archive")}).status_code == 422
        assert (
            client.post(path, headers={"content-length": str(storage.MAX_BYTES + 65537)}, content=b"x").status_code
            == 413
        )
        result = client.post(path, files={"file": ("../../evil.exe", png(), "image/png")}, data={"args": "{}"})
        assert result.status_code == 200, result.text
        token = supervisor.invoke_api.call_args.kwargs["args"]["upload_id"]
        claimed = store.claim("test.plugin", token)
        assert Path(claimed["path"]).is_relative_to(tmp_path / "data" / "plugins" / "test.plugin")
        assert Path(claimed["path"]).suffix == ".png"
        # File content never reaches the plugin invocation.
        assert set(supervisor.invoke_api.call_args.kwargs["args"]) == {"upload_id"}


def test_upload_admission_happens_before_multipart_parsing(monkeypatch):
    budget = storage.UploadBudget()
    monkeypatch.setattr(routes, "upload_budget", budget)
    monkeypatch.setattr(routes, "require_plugin_token", lambda _token: "valid")
    monkeypatch.setattr(routes, "_authorize_upload", AsyncMock())
    app = FastAPI()
    app.include_router(routes.router, prefix="/plugins")
    with TestClient(app) as client, budget.reserve("test.plugin"), budget.reserve("test.plugin"):
        response = client.post("/plugins/runtime/webui/test.plugin/images/uploads/library", content=b"not multipart")
        assert response.status_code == 429
    # Releasing either successful or failed requests makes capacity available again.
    with budget.reserve("test.plugin"):
        pass


def test_global_and_per_plugin_receive_budgets():
    budget = storage.UploadBudget()
    with budget.reserve("one"), budget.reserve("one"):
        with pytest.raises(storage.UploadCapacityError):
            with budget.reserve("one"):
                pass
        with budget.reserve("two"), budget.reserve("three"):
            with pytest.raises(storage.UploadCapacityError):
                with budget.reserve("four"):
                    pass
    with budget.reserve("four"):
        pass


def test_pending_quota_cleanup_and_claim_release(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(storage, "MAX_PENDING_PER_PLUGIN", 2)
    monkeypatch.setattr(storage, "MAX_PENDING_TOTAL", 3)
    store = storage.UploadStore(tmp_path / "stage")
    first = store.stage("one.plugin", png())
    expired = store.stage("one.plugin", png())
    with pytest.raises(storage.UploadCapacityError):
        store.stage("one.plugin", png())
    store.stage("two.plugin", png())
    with pytest.raises(storage.UploadCapacityError):
        store.stage("three.plugin", png())
    claimed = store.claim("one.plugin", first)
    store.stage("three.plugin", png())
    with store.connect() as db:
        db.execute("UPDATE uploads SET expires = ? WHERE token = ?", (time.time() - 1, expired))
    store.cleanup()
    assert not (store.root / expired).exists()
    assert Path(claimed["path"]).is_file()
    store.stage("one.plugin", png())


@pytest.mark.asyncio
async def test_cancelling_upload_holds_budget_until_worker_finishes(monkeypatch):
    entered, release = Event(), Event()
    budget = storage.UploadBudget()
    monkeypatch.setattr(storage, "MAX_UPLOADS_PER_PLUGIN", 1)

    def stage(_owner, _raw):
        entered.set()
        assert release.wait(5)
        return "token"

    monkeypatch.setattr(routes, "upload_store", lambda: SimpleNamespace(stage=stage))

    async def upload():
        with budget.reserve("test.plugin"):
            return await routes._stage_upload("test.plugin", b"image")

    task = asyncio.create_task(upload())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(storage.UploadCapacityError):
            with budget.reserve("test.plugin"):
                pass
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    with budget.reserve("test.plugin"):
        pass

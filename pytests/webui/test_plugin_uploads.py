from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import io
import time

import pytest
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.plugin_runtime import upload_store as storage
from src.plugin_runtime.host.api_registry import APIRegistry
from src.plugin_runtime.webui_schema import WebUIExtension, load_webui_extension
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
        required_capabilities=["file_upload"],
        pages=[
            dict(
                id="images",
                title="Images",
                actions={"add": dict(api="add", parameters={"upload_id": dict(type="string", required=True)})},
                content=[dict(type="upload", label="Upload", action="add")],
            )
        ],
    )


def test_upload_declaration_and_legacy_compatibility():
    raw = declaration()
    assert WebUIExtension.model_validate(raw).required_capabilities == ["file_upload"]
    del raw["required_capabilities"]
    with pytest.raises(ValidationError):
        WebUIExtension.model_validate(raw)
    raw["pages"][0]["content"] = [dict(type="text", value="legacy")]
    assert WebUIExtension.model_validate(raw).required_capabilities == []
    assert len(load_webui_extension("plugins/mai_recog_self").pages) == 4


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
    path = "/plugins/runtime/webui/test.plugin/images/uploads/add"
    with TestClient(app) as client:
        assert client.post(path, files={"file": ("fake.exe", png())}).status_code == 401
        client.cookies.set("maibot_session", "valid")
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

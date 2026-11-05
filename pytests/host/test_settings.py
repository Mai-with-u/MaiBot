"""公共设置必须通过实际响应序列化完成读取和保存。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import tomlkit

from src.config.shared_settings import RETENTION_DEFAULTS, WEBUI_DEFAULTS
from src.host.config import HostConfig
from src.host.layout import Workspace
from src.host.registry import Registry
from src.host.settings import register_common_settings


def test_retention_settings_roundtrip(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    path = host / "config.toml"
    path.write_text(tomlkit.dumps({"webui": {"enabled": True, **WEBUI_DEFAULTS}, "retention": RETENTION_DEFAULTS}), encoding="utf-8")
    workspace = Workspace(root=tmp_path, registry=Registry(tmp_path), config=HostConfig())
    app = FastAPI()
    register_common_settings(app, workspace, lambda: None)
    with TestClient(app) as client:
        response = client.get("/api/host/settings")
        assert response.status_code == 200
        settings = response.json()
        assert settings["retention"] == RETENTION_DEFAULTS
        assert settings["restart_required"] is False
        original = path.read_bytes()
        missing_policy = {key: value for key, value in settings.items() if key != "retention"}
        assert client.put("/api/host/settings", json=missing_policy).status_code == 422
        incomplete_network = {**settings, "webui": {"enabled": True}}
        assert client.put("/api/host/settings", json=incomplete_network).status_code == 422
        assert path.read_bytes() == original
        settings["retention"]["images"]["enabled"] = False
        settings["retention"]["emoji"]["check_interval_hours"] = 2.5
        settings["retention"]["logs"]["log_cleanup_days"] = 60
        saved = client.put("/api/host/settings", json=settings)
        assert saved.status_code == 200
        assert saved.json()["restart_required"] is False
        assert client.put("/api/host/settings", json=settings).status_code == 409
        assert saved.json()["retention"] == settings["retention"]
        assert client.get("/api/host/settings").json()["retention"] == settings["retention"]
        assert tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()["retention"] == settings["retention"]
        appearance = client.patch("/api/host/settings/appearance", json={"webui_style": 1})
        assert appearance.status_code == 200
        assert appearance.json()["webui"]["webui_style"] == 1
        assert appearance.json()["webui"]["enforce_public_outbound_url"] is True
        assert appearance.json()["retention"] == settings["retention"]
        assert appearance.json()["restart_required"] is False
        assert client.patch("/api/host/settings/appearance", json={"webui_style": True}).status_code == 422
        assert client.put("/api/host/settings", json=saved.json()).status_code == 409
        network_settings = appearance.json()
        network_settings["webui"]["public_base_url"] = "  https://example.com/  "
        updated = client.put("/api/host/settings", json=network_settings)
        assert updated.status_code == 200
        assert updated.json()["webui"]["public_base_url"] == "https://example.com"
        assert updated.json()["restart_required"] is True
        assert client.get("/api/host/settings").json()["restart_required"] is True

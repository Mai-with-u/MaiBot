from pathlib import Path
from types import SimpleNamespace

import pytest

from src.webui.routers import config as config_routes


def _write_model_config(path: Path, model_name: str) -> None:
    path.write_text(
        f"""
[inner]
version = "1.17.6"

[[api_providers]]
name = "openai"
base_url = "https://api.openai.com/v1"
api_key = "sk-test"
client_type = "openai"

[[models]]
name = "{model_name}"
model_identifier = "{model_name}"
api_provider = "openai"
""".strip(),
        encoding="utf-8",
    )


@pytest.fixture()
def model_config_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    active_path = config_dir / "model_config.toml"
    _write_model_config(active_path, "active-model")

    reload_calls: list[list[str]] = []

    class FakeConfigManager:
        model_config_path = active_path.resolve()

        async def reload_config(self, changed_scopes=None) -> bool:
            reload_calls.append(list(changed_scopes or []))
            return True

    monkeypatch.setattr(config_routes, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_routes, "config_manager", FakeConfigManager())
    return SimpleNamespace(config_dir=config_dir, active_path=active_path, reload_calls=reload_calls)


@pytest.mark.asyncio
async def test_create_and_list_model_config_version(model_config_workspace) -> None:
    response = await config_routes.create_model_config_version(
        config_routes.ModelConfigVersionCreateRequest(label="测试副本")
    )

    assert response.success is True
    assert response.version.label == "测试副本"
    assert response.version.active is False

    versions_response = await config_routes.list_model_config_versions()

    assert versions_response.active_version.active is True
    assert versions_response.active_version.label == "默认配置"
    assert versions_response.versions[0].id == response.version.id
    assert versions_response.versions[0].label == "测试副本"
    assert (model_config_workspace.config_dir / "versions" / "model" / f"{response.version.id}.toml").exists()


@pytest.mark.asyncio
async def test_activate_model_config_version_archives_current_and_reloads(model_config_workspace) -> None:
    version_response = await config_routes.create_model_config_version(
        config_routes.ModelConfigVersionCreateRequest(label="备用副本")
    )
    _write_model_config(model_config_workspace.active_path, "changed-active-model")

    switch_response = await config_routes.activate_model_config_version(
        version_response.version.id,
        config_routes.ModelConfigVersionSwitchRequest(),
    )

    assert switch_response.success is True
    assert "active-model" in model_config_workspace.active_path.read_text(encoding="utf-8")
    assert model_config_workspace.reload_calls == [["model"]]

    versions_response = await config_routes.list_model_config_versions()
    assert versions_response.active_version.label == "备用副本"
    labels = {version.label for version in versions_response.versions}
    assert "备用副本" not in labels
    assert "默认配置" in labels
    assert not (
        model_config_workspace.config_dir / "versions" / "model" / f"{version_response.version.id}.toml"
    ).exists()


@pytest.mark.asyncio
async def test_activate_model_config_version_rejects_invalid_version(model_config_workspace) -> None:
    versions_dir = model_config_workspace.config_dir / "versions" / "model"
    versions_dir.mkdir(parents=True)
    (versions_dir / "broken.toml").write_text("not = [valid", encoding="utf-8")

    with pytest.raises(config_routes.HTTPException) as exc_info:
        await config_routes.activate_model_config_version(
            "broken",
            config_routes.ModelConfigVersionSwitchRequest(),
        )

    assert exc_info.value.status_code == 400
    assert "模型配置副本无效" in str(exc_info.value.detail)
    assert model_config_workspace.reload_calls == []

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
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
    response = config_routes.create_model_config_version(
        config_routes.ModelConfigVersionCreateRequest(label="测试副本")
    )

    assert response.success is True
    assert response.version.label == "测试副本"
    assert response.version.active is False

    versions_response = config_routes.list_model_config_versions()

    assert versions_response.active_version.active is True
    assert versions_response.active_version.label == "默认配置"
    assert versions_response.versions[0].id == response.version.id
    assert versions_response.versions[0].label == "测试副本"
    assert (model_config_workspace.config_dir / "versions" / "model" / f"{response.version.id}.toml").exists()


def test_concurrent_model_version_edits_preserve_both_labels(model_config_workspace, monkeypatch) -> None:
    first, second = [
        config_routes.create_model_config_version(config_routes.ModelConfigVersionCreateRequest(label=label)).version.id
        for label in ("副本一", "副本二")
    ]
    original_write = config_routes._write_model_version_manifest
    first_held, release_first, second_started, second_committing = Event(), Event(), Event(), Event()

    def hold_first_write(manifest):
        labels = {version["label"] for version in manifest["versions"]}
        if "更新一" in labels and not first_held.is_set():
            first_held.set()
            assert release_first.wait(5)
        if "更新二" in labels:
            second_committing.set()
        original_write(manifest)

    def update_second():
        second_started.set()
        return config_routes.update_model_config_version(second, config_routes.ModelConfigVersionUpdateRequest(label="更新二"))

    monkeypatch.setattr(config_routes, "_write_model_version_manifest", hold_first_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_update = executor.submit(
            config_routes.update_model_config_version, first, config_routes.ModelConfigVersionUpdateRequest(label="更新一"),
        )
        try:
            assert first_held.wait(5)
            second_update = executor.submit(update_second)
            assert second_started.wait(5)
            assert not second_committing.wait(0.2)
        finally:
            release_first.set()
        first_update.result()
        second_update.result()

    assert {item.label for item in config_routes.list_model_config_versions().versions} == {"更新一", "更新二"}


@pytest.mark.parametrize("contents", [b"{broken", b"\xff", b"[]"])
def test_delete_model_config_version_with_corrupt_manifest(model_config_workspace, contents: bytes) -> None:
    first, second = [
        config_routes.create_model_config_version(config_routes.ModelConfigVersionCreateRequest(label=label)).version.id
        for label in ("副本一", "副本二")
    ]
    versions_dir = model_config_workspace.config_dir / "versions" / "model"
    (versions_dir / "manifest.json").write_bytes(contents)

    assert config_routes.delete_model_config_version(first)["success"] is True
    assert not (versions_dir / f"{first}.toml").exists()
    (backup,) = versions_dir.glob("manifest.json.corrupt-*")
    assert backup.read_bytes() == contents
    assert [(item.id, item.label) for item in config_routes.list_model_config_versions().versions] == [(second, second)]


def test_list_model_config_versions_with_corrupt_manifest(model_config_workspace) -> None:
    version = config_routes.create_model_config_version(
        config_routes.ModelConfigVersionCreateRequest(label="备用副本")
    ).version
    versions_dir = model_config_workspace.config_dir / "versions" / "model"
    (versions_dir / "manifest.json").write_bytes(b"{broken")

    assert [(item.id, item.label) for item in config_routes.list_model_config_versions().versions] == [
        (version.id, version.id)
    ]


@pytest.mark.asyncio
async def test_activate_model_config_version_archives_current_and_reloads(model_config_workspace) -> None:
    version_response = config_routes.create_model_config_version(
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

    versions_response = config_routes.list_model_config_versions()
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

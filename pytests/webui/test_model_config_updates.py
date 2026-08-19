from pathlib import Path

import pytest
import tomlkit

from src.webui.routers import config as config_routes


@pytest.fixture()
def reload_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """记录模型配置保存后触发的运行时重载。"""

    calls: list[list[str]] = []

    async def reload_config(changed_scopes=None) -> bool:
        calls.append(list(changed_scopes or []))
        return True

    monkeypatch.setattr(config_routes.config_manager, "reload_config", reload_config)
    return calls


def _write_empty_model_config(path: Path) -> None:
    path.write_text(
        """
models = []
api_providers = []

[inner]
version = "1.17.6"

[model_task_config]
""".strip(),
        encoding="utf-8",
    )


def _write_complete_model_config(path: Path) -> None:
    path.write_text(
        """
[[api_providers]]
name = "openai"
base_url = "https://api.openai.com/v1"
api_key = "sk-test"
client_type = "openai"

[[models]]
name = "gpt-test"
model_identifier = "gpt-test"
api_provider = "openai"

[inner]
version = "1.17.6"
""".strip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_update_api_providers_recovers_from_empty_model_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_calls: list[list[str]]
) -> None:
    config_path = tmp_path / "model_config.toml"
    _write_empty_model_config(config_path)
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)

    provider = {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "client_type": "openai",
    }
    response = await config_routes.update_model_config_section("api_providers", [provider])

    assert response == {"success": True, "message": "配置节 'api_providers' 已保存"}
    saved_config = tomlkit.loads(config_path.read_text(encoding="utf-8")).unwrap()
    assert saved_config["api_providers"][0]["name"] == "openai"
    assert saved_config["models"] == []
    assert reload_calls == [["model"]]


@pytest.mark.asyncio
async def test_update_model_config_reloads_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_calls: list[list[str]]
) -> None:
    config_path = tmp_path / "model_config.toml"
    _write_complete_model_config(config_path)
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)
    config_data = tomlkit.loads(config_path.read_text(encoding="utf-8")).unwrap()

    response = await config_routes.update_model_config(config_data)

    assert response == {"success": True, "message": "配置已保存"}
    assert reload_calls == [["model"]]


@pytest.mark.asyncio
async def test_update_api_providers_still_rejects_empty_provider_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_calls: list[list[str]]
) -> None:
    config_path = tmp_path / "model_config.toml"
    _write_empty_model_config(config_path)
    original_content = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)

    with pytest.raises(config_routes.HTTPException) as exc_info:
        await config_routes.update_model_config_section("api_providers", [])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "API 提供商列表不能为空"
    assert config_path.read_text(encoding="utf-8") == original_content
    assert reload_calls == []


@pytest.mark.asyncio
async def test_update_api_providers_still_rejects_new_orphaned_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_calls: list[list[str]]
) -> None:
    config_path = tmp_path / "model_config.toml"
    _write_complete_model_config(config_path)
    original_content = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)

    replacement_provider = {
        "name": "replacement",
        "base_url": "https://replacement.example.com/v1",
        "api_key": "sk-test",
        "client_type": "openai",
    }
    with pytest.raises(config_routes.HTTPException) as exc_info:
        await config_routes.update_model_config_section("api_providers", [replacement_provider])

    assert exc_info.value.status_code == 400
    assert "gpt-test" in str(exc_info.value.detail)
    assert config_path.read_text(encoding="utf-8") == original_content
    assert reload_calls == []

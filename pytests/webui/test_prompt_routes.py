from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.common.prompt_i18n import clear_prompt_cache
from src.webui.dependencies import require_auth
from src.webui.routers import config as config_router_module


@pytest.fixture(name="client")
def client_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    prompts_dir = tmp_path / "prompts"
    custom_prompts_dir = tmp_path / "data" / "custom_prompts"
    source_dir = prompts_dir / "zh-CN"
    source_dir.mkdir(parents=True)
    (source_dir / "replyer.prompt").write_text("Hello {name}", encoding="utf-8")

    monkeypatch.setattr(config_router_module, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(config_router_module, "CUSTOM_PROMPTS_DIR", custom_prompts_dir)
    clear_prompt_cache()

    app = FastAPI()
    app.include_router(config_router_module.router, prefix="/api/webui")
    app.dependency_overrides[require_auth] = lambda: "test-token"
    return TestClient(app)


def test_update_prompt_file_saves_custom_version(client: TestClient) -> None:
    response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "Hi {name}", "create_version": True, "label": "测试版本"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "Hi {name}"
    assert payload["customized"] is True
    assert payload["active_version_id"]
    assert payload["validation"]["valid"] is True
    assert payload["versions"][0]["label"] == "测试版本"
    assert payload["versions"][0]["active"] is True

    catalog_response = client.get("/api/webui/config/prompts")
    assert catalog_response.status_code == 200
    [file_info] = catalog_response.json()["files"]["zh-CN"]
    assert file_info["customized"] is True
    assert file_info["custom_version_count"] == 1


def test_prompt_catalog_lists_emoji_content_analysis_metadata(client: TestClient) -> None:
    prompt_dir = config_router_module.PROMPTS_DIR / "zh-CN"
    (prompt_dir / "emoji_content_analysis.prompt").write_text(
        "输入类型：{image_type}",
        encoding="utf-8",
    )
    (prompt_dir / ".meta.toml").write_text(
        """
[emoji_content_analysis]
display_name = "表情包内容分析"
advanced = true
description = "用于分析表情包图片内容的模板。"
""".strip(),
        encoding="utf-8",
    )
    clear_prompt_cache()

    response = client.get("/api/webui/config/prompts")

    assert response.status_code == 200
    file_info = next(
        item
        for item in response.json()["files"]["zh-CN"]
        if item["name"] == "emoji_content_analysis.prompt"
    )
    assert file_info["display_name"] == "表情包内容分析"
    assert file_info["advanced"] is True
    assert file_info["description"] == "用于分析表情包图片内容的模板。"


def test_update_prompt_file_rejects_placeholder_mismatch(client: TestClient) -> None:
    response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "Hi {other}", "create_version": True},
    )

    assert response.status_code == 400
    assert "缺少参数: name" in response.json()["detail"]
    assert "多余参数: other" in response.json()["detail"]


def test_activate_prompt_version_rejects_placeholder_mismatch(client: TestClient) -> None:
    save_response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "Hi {name}", "create_version": True, "label": "有效版本"},
    )
    version_id = save_response.json()["active_version_id"]

    custom_root = config_router_module.CUSTOM_PROMPTS_DIR
    version_path = custom_root / "zh-CN" / ".versions" / "replyer" / f"{version_id}.prompt"
    version_path.write_text("Hi {other}", encoding="utf-8")

    response = client.post(f"/api/webui/config/prompts/zh-CN/replyer.prompt/versions/{version_id}/activate")

    assert response.status_code == 400
    assert "缺少参数: name" in response.json()["detail"]
    assert "多余参数: other" in response.json()["detail"]


def test_delete_prompt_version_removes_active_override_and_restores_default(client: TestClient) -> None:
    save_response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "Hi {name}", "create_version": True, "label": "待删除版本"},
    )
    version_id = save_response.json()["active_version_id"]

    response = client.delete(
        f"/api/webui/config/prompts/zh-CN/replyer.prompt/versions/{version_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "Hello {name}"
    assert payload["customized"] is False
    assert payload["active_version_id"] is None
    assert payload["versions"] == []

    catalog_response = client.get("/api/webui/config/prompts")
    [file_info] = catalog_response.json()["files"]["zh-CN"]
    assert file_info["customized"] is False
    assert file_info["custom_version_count"] == 0


def test_delete_inactive_prompt_version_keeps_active_override(client: TestClient) -> None:
    first_response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "First {name}", "create_version": True, "label": "第一个版本"},
    )
    first_version_id = first_response.json()["active_version_id"]
    second_response = client.put(
        "/api/webui/config/prompts/zh-CN/replyer.prompt",
        json={"content": "Second {name}", "create_version": True, "label": "第二个版本"},
    )
    second_version_id = second_response.json()["active_version_id"]

    response = client.delete(
        f"/api/webui/config/prompts/zh-CN/replyer.prompt/versions/{first_version_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == "Second {name}"
    assert payload["customized"] is True
    assert payload["active_version_id"] == second_version_id
    assert [version["id"] for version in payload["versions"]] == [second_version_id]

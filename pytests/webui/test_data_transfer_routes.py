from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import zipfile

from src.webui.routers import data_transfer


def _build_client(monkeypatch, tmp_path: Path) -> TestClient:
    project_root = tmp_path / "project"
    transfer_dir = tmp_path / "transfer"
    for dirname in ("config", "data", "plugins", "logs"):
        (project_root / dirname).mkdir(parents=True)

    monkeypatch.setattr(data_transfer, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr(
        data_transfer,
        "_EXPORT_DIRS",
        {
            "config": project_root / "config",
            "data": project_root / "data",
            "plugins": project_root / "plugins",
            "logs": project_root / "logs",
        },
    )
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", transfer_dir)
    data_transfer._jobs.clear()

    app = FastAPI()
    app.include_router(data_transfer.router)
    app.dependency_overrides[data_transfer.require_auth] = lambda: "test-token"
    return TestClient(app)


def test_export_creates_manifest_and_selected_directories(monkeypatch, tmp_path) -> None:
    client = _build_client(monkeypatch, tmp_path)
    project_root = data_transfer._PROJECT_ROOT
    (project_root / "config" / "bot_config_template.toml").write_text("bot = true", encoding="utf-8")
    (project_root / "data" / "MaiBot.db").write_text("db", encoding="utf-8")
    (project_root / "plugins" / "demo" / "plugin.py").parent.mkdir(parents=True)
    (project_root / "plugins" / "demo" / "plugin.py").write_text("print('ok')", encoding="utf-8")
    (project_root / "logs" / "runtime.log").write_text("log", encoding="utf-8")

    response = client.post(
        "/data-transfer/export",
        json={"include_plugins": True, "include_logs": False},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    status_response = client.get(f"/data-transfer/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"

    download_response = client.get(f"/data-transfer/export/{job_id}/download")
    assert download_response.status_code == 200
    archive_path = tmp_path / "export.zip"
    archive_path.write_bytes(download_response.content)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "config/bot_config_template.toml" in names
        assert "data/MaiBot.db" in names
        assert "plugins/demo/plugin.py" in names
        assert "logs/runtime.log" not in names


def test_cancel_export_marks_job_cancelled_immediately(monkeypatch, tmp_path) -> None:
    client = _build_client(monkeypatch, tmp_path)
    job = data_transfer._new_job("export")
    job.status = "running"
    job.message = "正在写入压缩包"

    response = client.post(f"/data-transfer/export/{job.job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["message"] == "导出已取消"
    assert data_transfer._jobs[job.job_id].cancel_requested is True


def test_import_rejects_path_traversal(monkeypatch, tmp_path) -> None:
    client = _build_client(monkeypatch, tmp_path)
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            '{"format":"maibot-data-archive","format_version":1,"included":["config"]}',
        )
        archive.writestr("../escape.txt", "bad")

    with archive_path.open("rb") as archive_file:
        response = client.post(
            "/data-transfer/import",
            files={"file": ("bad.zip", archive_file, "application/zip")},
            data={"import_config": "true", "import_data": "false"},
        )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status_response = client.get(f"/data-transfer/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "failed"
    assert "非法路径" in status_response.json()["error"]


def test_import_writes_only_selected_parts(monkeypatch, tmp_path) -> None:
    client = _build_client(monkeypatch, tmp_path)
    project_root = data_transfer._PROJECT_ROOT
    archive_path = tmp_path / "maibot-data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            '{"format":"maibot-data-archive","format_version":1,"included":["config","logs"]}',
        )
        archive.writestr("config/imported.toml", "value = 1")
        archive.writestr("logs/imported.log", "log")

    with archive_path.open("rb") as archive_file:
        response = client.post(
            "/data-transfer/import",
            files={"file": ("maibot-data.zip", archive_file, "application/zip")},
            data={"import_config": "true", "import_data": "false", "import_logs": "false"},
        )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    status_response = client.get(f"/data-transfer/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert (project_root / "config" / "imported.toml").read_text(encoding="utf-8") == "value = 1"
    assert not (project_root / "logs" / "imported.log").exists()

from io import BytesIO
from pathlib import Path

import asyncio
import errno
import json
import os
import shutil
import zipfile

import pytest

from src.webui.routers import data_transfer


def test_export_keeps_business_data_and_excludes_runtime_locks(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    memory_dir = data_dir / "a-memorix"
    config_dir.mkdir()
    memory_dir.mkdir(parents=True)
    (config_dir / "bot_config.toml").write_bytes(b"config")
    (memory_dir / "metadata.db").write_bytes(b"memory")
    for directory in (data_dir, memory_dir):
        (directory / ".a_memorix_runtime_writer.lock").write_bytes(b"runtime lock")
    monkeypatch.setattr(data_transfer, "_EXPORT_DIRS", {"config": config_dir, "data": data_dir})
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path / "export")
    monkeypatch.setattr(data_transfer, "_jobs", {})
    job = data_transfer._new_job("export")
    data_transfer._run_export_job(job.job_id, data_transfer.DataExportRequest())
    assert job.status == "completed"
    with zipfile.ZipFile(job.file_path) as archive:
        assert archive.read("data/a-memorix/metadata.db") == b"memory"
        assert archive.read("config/bot_config.toml") == b"config"
        assert not any(name.endswith(".a_memorix_runtime_writer.lock") for name in archive.namelist())


def test_read_permission_error_identifies_source_file(monkeypatch):
    source_path = Path("data/unreadable.db")

    class LockedSource(BytesIO):
        def read(self, size=-1):
            raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "open", lambda *_: LockedSource())
    with zipfile.ZipFile(BytesIO(), "w") as archive:
        with pytest.raises(PermissionError) as error:
            data_transfer._write_archive_file(
                archive, source_path, "data/unreadable.db", data_transfer._TransferJob("test", "export"),
            )
    assert error.value.filename == str(source_path)
    assert "data/unreadable.db" in str(error.value)


def test_export_preserves_graph_pointer_and_snapshot_during_rotation(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    graph_dir = data_dir / "a-memorix" / "graph"
    generation = f"graph-{'a' * 32}"
    snapshot_dir = graph_dir / "graph_snapshots" / generation
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "graph_metadata.json").write_text(json.dumps({"has_adjacency": True}), encoding="utf-8")
    (snapshot_dir / "graph_adjacency.npz").write_bytes(b"original graph")
    (graph_dir / "graph_snapshot.json").write_text(json.dumps({"generation": generation}), encoding="utf-8")
    old_snapshot = graph_dir / "graph_snapshots" / f"graph-{'b' * 32}"
    old_snapshot.mkdir()
    (old_snapshot / "graph_metadata.json").write_bytes(b"old")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(data_transfer, "_EXPORT_DIRS", {"config": config_dir, "data": data_dir})
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path / "export")
    monkeypatch.setattr(data_transfer, "_jobs", {})
    original_stage = data_transfer._stage_graph_snapshots

    def stage_then_rotate(destination, job):
        result = original_stage(destination, job)
        shutil.rmtree(snapshot_dir)
        (graph_dir / "graph_snapshot.json").write_text(
            json.dumps({"generation": f"graph-{'c' * 32}"}), encoding="utf-8",
        )
        return result

    monkeypatch.setattr(data_transfer, "_stage_graph_snapshots", stage_then_rotate)
    job = data_transfer._new_job("export")
    data_transfer._run_export_job(job.job_id, data_transfer.DataExportRequest())
    assert job.status == "completed", job.error
    with zipfile.ZipFile(job.file_path) as archive:
        root = "data/a-memorix/graph"
        assert json.loads(archive.read(f"{root}/graph_snapshot.json"))["generation"] == generation
        assert archive.read(f"{root}/graph_snapshots/{generation}/graph_adjacency.npz") == b"original graph"
        assert not any(old_snapshot.name in name for name in archive.namelist())


def test_incomplete_active_graph_snapshot_fails_export(monkeypatch, tmp_path):
    graph_dir = tmp_path / "data" / "a-memorix" / "graph"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_snapshot.json").write_text(
        json.dumps({"generation": f"graph-{'a' * 32}"}), encoding="utf-8",
    )
    monkeypatch.setattr(data_transfer, "_EXPORT_DIRS", {"data": tmp_path / "data"})
    with pytest.raises(FileNotFoundError):
        data_transfer._stage_graph_snapshots(tmp_path / "staging", data_transfer._TransferJob("test", "export"))


@pytest.mark.parametrize("cancelled", [False, True])
def test_unsuccessful_export_removes_partial_archive(monkeypatch, tmp_path, cancelled):
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_bytes(b"config")
    monkeypatch.setattr(data_transfer, "_EXPORT_DIRS", {"config": config, "data": tmp_path / "data"})
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path / "export")
    monkeypatch.setattr(data_transfer, "_jobs", {})

    def fail_write(archive, file_path, archive_name, job):
        archive.writestr(archive_name, b"partial")
        if cancelled:
            job.cancel_requested = True
            data_transfer._raise_if_cancelled(job)
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(data_transfer, "_write_archive_file", fail_write)
    job = data_transfer._new_job("export")
    data_transfer._run_export_job(job.job_id, data_transfer.DataExportRequest())
    assert job.status == ("cancelled" if cancelled else "failed")
    assert job.file_path is None
    assert not list(data_transfer._TRANSFER_TEMP_DIR.iterdir())


def test_temp_cleanup_preserves_downloads_and_removes_expired_packages(monkeypatch, tmp_path):
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path)
    monkeypatch.setattr(data_transfer, "_jobs", {})
    job = data_transfer._new_job("export")
    job.file_path = tmp_path / f"{job.job_id}.zip"
    job.file_path.write_bytes(b"download")
    job.status = "completed"
    job.expires_at = 0
    response = data_transfer.download_data_export(job.job_id)
    assert response.path == job.file_path
    job.expires_at = 0
    assert data_transfer.cleanup_transfer_temp_files() == 0
    assert job.file_path.exists()
    data_transfer._finish_export_download(job)
    assert data_transfer.cleanup_transfer_temp_files() == 0
    job.expires_at = 0
    assert data_transfer.cleanup_transfer_temp_files() == 1
    assert job.file_path is None
    assert job.to_response().download_url is None


def test_temp_cleanup_removes_old_orphans_but_preserves_active_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path)
    monkeypatch.setattr(data_transfer, "_jobs", {})
    orphan = tmp_path / f"{'a' * 32}.zip"
    orphan.write_bytes(b"old partial export")
    os.utime(orphan, (0, 0))
    snapshots = tmp_path / f"{'b' * 32}-snapshots-old"
    snapshots.mkdir()
    (snapshots / "graph.json").write_bytes(b"snapshot")
    os.utime(snapshots, (0, 0))
    unrelated = tmp_path / "other.zip"
    unrelated.write_bytes(b"keep")
    active = data_transfer._new_job("export")
    active.status = "cancelled"
    active.worker_active = True
    active_path = tmp_path / f"{active.job_id}.zip"
    active_path.write_bytes(b"in progress")
    assert data_transfer.cleanup_transfer_temp_files() == 2
    assert unrelated.exists()
    assert active_path.exists()


def test_export_history_survives_restart_and_preserves_download_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path)
    monkeypatch.setattr(data_transfer, "_jobs", {})
    job = data_transfer._new_job("export")
    job.status = "completed"
    job.file_path = tmp_path / f"{job.job_id}.zip"
    job.file_path.write_bytes(b"download")
    job.filename = "backup.zip"
    job.completed_at = 100
    job.archive_bytes = 8
    job.expires_at = data_transfer.time.time() + 3600
    data_transfer._save_export_history(job)
    data_transfer._jobs.clear()
    history = data_transfer.list_data_exports()
    assert len(history) == 1
    assert history[0].filename == "backup.zip"
    assert history[0].archive_bytes == 8
    assert history[0].download_url is not None
    restored = data_transfer._jobs[job.job_id]
    assert data_transfer.cleanup_transfer_temp_files() == 0
    restored.expires_at = 0
    data_transfer._save_export_history(restored)
    data_transfer._jobs.clear()
    assert data_transfer.cleanup_transfer_temp_files() == 1
    assert data_transfer.list_data_exports()[0].download_url is None
    data_transfer.delete_data_transfer_job(job.job_id)
    assert data_transfer.list_data_exports() == []


def test_history_recovers_complete_legacy_package_but_not_partial_package(monkeypatch, tmp_path):
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path)
    monkeypatch.setattr(data_transfer, "_jobs", {})
    manifest = {
        "format": "maibot-data-archive", "format_version": 1,
        "parts": {"data": {"file_count": 1, "total_bytes": 6}},
    }
    for job_id, content in (("a" * 32, b"memory"), ("b" * 32, b"")):
        with zipfile.ZipFile(tmp_path / f"{job_id}.zip", "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("data/metadata.db", content)
    history = data_transfer.list_data_exports()
    assert [job.job_id for job in history] == ["a" * 32]
    assert history[0].download_url is not None
    assert (tmp_path / f"{'a' * 32}.export.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_download_releases_protection_on_completion_or_disconnect(monkeypatch, tmp_path, disconnect):
    monkeypatch.setattr(data_transfer, "_TRANSFER_TEMP_DIR", tmp_path)
    monkeypatch.setattr(data_transfer, "_jobs", {})
    job = data_transfer._new_job("export")
    job.status = "completed"
    job.completed_at = 1
    job.file_path = tmp_path / f"{job.job_id}.zip"
    job.file_path.write_bytes(b"x" * 256 * 1024)
    response = data_transfer.download_data_export(job.job_id)
    first_body = asyncio.Event()
    never = asyncio.Event()

    async def receive():
        if disconnect:
            await first_body.wait()
            return {"type": "http.disconnect"}
        await never.wait()

    async def send(message):
        if message["type"] == "http.response.body":
            first_body.set()
            if disconnect:
                await never.wait()

    assert job.active_downloads == 1
    scope = {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}}
    await asyncio.wait_for(response(scope, receive, send), timeout=3)
    assert job.active_downloads == 0
    assert job.file_path.exists()
    assert data_transfer.delete_data_transfer_job(job.job_id) == {"success": True}

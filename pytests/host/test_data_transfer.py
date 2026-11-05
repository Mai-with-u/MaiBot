from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import json
import zipfile

import tomlkit

from src.config.shared_models import CATALOG_VERSION, merge_catalog, read_catalog
from src.config.shared_settings import RETENTION_DEFAULTS
from src.host.data_transfer import register_data_transfer
from src.host.layout import Workspace
from src.host.config import HostConfig
from src.host.maintenance import HostMaintenance
from src.host.registry import Registry
from src.host.supervisor import BotSupervisor
from src.host.transfer_service import DataExportRequest, TransferService
from src.host.transfer_storage import import_archive, portable_config, recover_import


def setup_workspace(root):
    registry = Registry(root)
    registry.create()
    host = root / "host"
    (host / "config.toml").write_text(tomlkit.dumps({"webui": {"port": 8000}, "retention": RETENTION_DEFAULTS}), encoding="utf-8")
    shared = {"inner": {"version": CATALOG_VERSION},
              "api_providers": [{"name": "provider", "base_url": "https://example.com/v1", "api_key": "other-bot-secret"}],
              "models": [{"name": "model", "api_provider": "provider", "model_identifier": "id", "model_type": "llm"}],
              "web": {"services": {"search": {"adapter": "tavily", "api_key": "other-search-secret"}}}}
    (host / "model_catalog.toml").write_text(tomlkit.dumps(shared), encoding="utf-8")
    bots = []
    for name in ["a", "b"]:
        directory = root / name
        (directory / "config").mkdir(parents=True)
        (directory / "data").mkdir()
        (directory / "config" / "model_config.toml").write_text(tomlkit.dumps({"inner": {"version": "1.17.17"}, "model_task_config": {"planner": {"model_list": ["model"]}}}), encoding="utf-8")
        bots.append(registry.add_bot(name, directory, owned=True, initialization="initialized"))
    return registry, host, bots


def service_for(bot, host):
    service = TransferService(bot.directory, host / "transfers" / bot.bot_id, "1.3.20", prepare_export=partial(portable_config, bot, host))
    service.apply_import = partial(import_archive, bot, host, service)
    return service


def archive_1319(path, files):
    # v1.3.19 的数据包协议为 format_version=1，旧模型定义直接位于实例配置中。
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "maibot-data-archive", "format_version": 1, "maibot_version": "1.3.19"}))
        for name, content in files.items():
            archive.writestr(name, content)


def test_1319_import_preserves_other_bot_and_public_settings_then_exports_portable_config(tmp_path):
    _, host, (a, b) = setup_workspace(tmp_path)
    service = service_for(a, host)
    old = {"inner": {"version": "1.17.25"},
           "api_providers": [{"name": "provider", "base_url": "https://example.com/v1", "api_key": "imported-key", "chatgpt_account": "default"}],
           "models": [{"name": "model", "api_provider": "provider", "model_identifier": "id", "model_type": "llm"}],
           "web": {"services": {"search": {"adapter": "tavily", "api_key": "imported-search"}}},
           "model_task_config": {"planner": {"model_list": ["model"]}, "web_search": {"model_list": ["search"]}}}
    config = {"inner": {"version": "8.15.68"}, "bot": {"nickname": "imported"}, "log": {"log_cleanup_days": 1},
              "visual": {"image_cache_cleanup": {"enabled": False}}, "files": {"transfer_base_url": "https://old.example"}}
    archive = tmp_path / "legacy.zip"
    archive_1319(archive, {"config/model_config.toml": tomlkit.dumps(old), "config/bot_config.toml": tomlkit.dumps(config), "data/messages.txt": "legacy-data"})
    original_b = (b.directory / "config" / "model_config.toml").read_bytes()
    original_public = (host / "config.toml").read_bytes()
    job = service._new_job("import")
    service._run_import_job(job.job_id, archive, {"config", "data"})
    assert job.status == "completed", job.error
    assert (host / "config.toml").read_bytes() == original_public
    assert (b.directory / "config" / "model_config.toml").read_bytes() == original_b
    shared = read_catalog(host / "model_catalog.toml")
    assert shared["api_providers"][0]["api_key"] == "other-bot-secret"
    assert shared["web"]["services"]["search"]["api_key"] == "other-search-secret"
    local = tomlkit.parse((a.directory / "config" / "model_config.toml").read_text(encoding="utf-8")).unwrap()
    effective = merge_catalog(local, shared)
    chosen = local["model_task_config"]["planner"]["model_list"][0]
    model = next(item for item in effective["models"] if item["name"] == chosen)
    assert next(item for item in effective["api_providers"] if item["name"] == model["api_provider"])["api_key"] == "imported-key"
    assert "api_providers" not in local
    export = service._new_job("export")
    service._run_export_job(export.job_id, DataExportRequest())
    assert export.status == "completed", export.error
    with zipfile.ZipFile(export.file_path) as result:
        portable = result.read("config/model_config.toml").decode()
        assert "imported-key" in portable and "imported-search" in portable
        assert "other-bot-secret" not in portable and "other-search-secret" not in portable
        assert result.read("data/messages.txt") == b"legacy-data"


def test_interrupted_import_rolls_back_files_and_catalog(tmp_path, monkeypatch):
    from src.host import transfer_storage
    _, host, (a, _) = setup_workspace(tmp_path)
    target = a.directory / "data" / "old.txt"
    target.write_bytes(b"original")
    archive = tmp_path / "legacy.zip"
    archive_1319(archive, {"data/old.txt": "replacement", "data/new.txt": "new"})
    service = service_for(a, host)
    original_replace = transfer_storage.os.replace
    def fail(source, destination):
        if Path(source).parent.name == "prepared" and Path(destination).name == "new.txt":
            raise OSError("simulated write failure")
        return original_replace(source, destination)
    monkeypatch.setattr(transfer_storage.os, "replace", fail)
    job = service._new_job("import")
    service._run_import_job(job.job_id, archive, {"data"})
    assert job.status == "failed"
    assert target.read_bytes() == b"original"
    assert not (a.directory / "data" / "new.txt").exists()
    assert not (a.directory / "temp" / "data-import" / "transaction.json").exists()
    recover_import(a, host)


def test_host_transfer_routes_work_when_stopped_and_cannot_access_other_bot_jobs(tmp_path):
    registry, host, (a, b) = setup_workspace(tmp_path)
    (a.directory / "data" / "only-a.txt").write_text("a", encoding="utf-8")
    supervisor = BotSupervisor(Workspace(root=tmp_path, registry=registry, config=HostConfig()), public_base_url="http://localhost")
    app = FastAPI()
    register_data_transfer(app, HostMaintenance(supervisor), lambda: None)
    def base(bot):
        return f"/api/host/bots/{bot.bot_id}/proxy/api/webui/data-transfer"
    with TestClient(app) as client:
        created = client.post(base(a) + "/export", json={})
        assert created.status_code == 200, created.text
        job = client.get(base(a) + "/jobs/" + created.json()["job_id"]).json()
        assert job["status"] == "completed", job
        assert client.get(base(b) + "/exports").json() == []
        assert client.get(base(b) + "/jobs/" + job["job_id"]).status_code == 404
        assert client.get(base(b) + "/export/" + job["job_id"] + "/download").status_code == 404
        assert client.get(base(a) + "/export/" + job["job_id"] + "/download").status_code == 200

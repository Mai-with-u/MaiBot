from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import asyncio
import json
import subprocess

import pytest

from src.config.file_watcher import FileWatcher
from src.plugin_runtime import integration
from src.plugin_runtime.runner.manifest_validator import ManifestValidator
from src.webui.routers.plugin import management, release_install, releases, support


def manifest(version: str = "1.0.0") -> Dict[str, Any]:
    return {
        "manifest_version": 2, "id": "example.demo", "version": version, "name": "版本测试插件",
        "description": "测试版本选择与安全替换", "author": {"name": "作者", "url": "https://github.com/example"},
        "license": "MIT", "urls": {"repository": "https://github.com/example/demo"},
        "host_application": {"min_version": "1.0.0", "max_version": "9.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"}, "dependencies": [], "capabilities": [],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
    }


def release(version: str = "1.0.0", **overrides: Any) -> releases.PluginRelease:
    return releases.PluginRelease(
        **{"version": version, "tag": f"v{version}", "commit": "a" * 40, "prerelease": False,
           "manifest": manifest(version), **overrides}
    )


def entry(*versions: releases.PluginRelease, **overrides: Any) -> releases.PluginReleaseEntry:
    return releases.PluginReleaseEntry(
        **{"id": "legacy.demo", "manifest_id": "example.demo", "repositoryUrl": "https://github.com/example/demo",
           "mode": "releases" if versions else "branch", "versions": list(versions), **overrides}
    )


@pytest.fixture(autouse=True)
def validator_versions(monkeypatch):
    monkeypatch.setattr(releases, "use_github_market_data", lambda: True)
    def validator(**kwargs):
        return ManifestValidator(host_version="1.3.0", sdk_version="2.8.1", **kwargs)
    monkeypatch.setattr(releases, "ManifestValidator", validator)
    monkeypatch.setattr(release_install, "ManifestValidator", validator)


def test_recommendation_filters_host_sdk_protocol_prerelease_and_yanked():
    host = manifest("5.0.0")
    host["host_application"]["min_version"] = "2.0.0"
    sdk = manifest("4.0.0")
    sdk["sdk"]["min_version"] = "2.9.0"
    old = manifest("3.0.0")
    old["manifest_version"] = 1
    result = releases.describe_entry(entry(
        release("1.9.0"), release("1.10.0"), release("5.0.0", manifest=host),
        release("4.0.0", manifest=sdk), release("3.0.0", manifest=old),
        release("7.0.0", prerelease=True), release("6.0.0", yanked=True),
    ))
    assert result["recommended_version"] == "1.10.0"
    assert result["versions"][0]["prerelease"] is True
    assert all(item["reasons"] for item in result["versions"] if item["version"] in {"3.0.0", "4.0.0", "5.0.0", "6.0.0"})


def test_no_compatible_release_and_sync_errors_are_explicit():
    result = releases.describe_entry(entry(release(), sync_error="Tag 被修改"))
    assert result["recommended_version"] is None
    assert "Tag 被修改" in result["versions"][0]["reasons"][0]


def test_preview_requires_explicit_selection(monkeypatch):
    async def load():
        return releases.PluginReleaseIndex(schema_version=1, plugins=[entry(release(prerelease=True))])
    monkeypatch.setattr(releases, "load_release_index", load)
    with pytest.raises(HTTPException, match="没有可安装"):
        asyncio.run(releases.resolve_release("example.demo", "latest"))
    _, chosen = asyncio.run(releases.resolve_release("legacy.demo", "1.0.0"))
    assert chosen is not None and chosen.prerelease


@pytest.fixture
def local_install(tmp_path, monkeypatch):
    manager = integration.PluginRuntimeManager()
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: manager)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repository / "_manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
    (repository / "plugin.py").write_text("# 发布版本代码\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "初始版本")
    git("tag", "v1.0.0")
    chosen = release(commit=git("rev-parse", "HEAD"))
    catalog_entry = entry(chosen)
    async def load():
        return releases.PluginReleaseIndex(schema_version=1, plugins=[catalog_entry])
    class Mirror:
        async def clone_repository(self, **kwargs):
            subprocess.run(["git", "clone", "-q", "--branch", kwargs["branch"], str(repository), str(kwargs["target_path"])], check=True, capture_output=True)
            return {"success": True}
    async def no_stop(_):
        return []
    async def progress(**kwargs):
        return None
    monkeypatch.setattr(support, "get_plugins_dir", lambda: plugins)
    monkeypatch.setattr(releases, "load_release_index", load)
    monkeypatch.setattr(release_install, "get_git_mirror_service", Mirror)
    monkeypatch.setattr(release_install, "_stop_runtime", no_stop)
    monkeypatch.setattr(release_install, "update_progress", progress)
    monkeypatch.setattr(management, "require_plugin_token", lambda _: None)
    app = FastAPI()
    app.include_router(management.router, prefix="/plugins")
    return TestClient(app), plugins, repository, chosen, catalog_entry


def install(client, version="latest"):
    return client.post("/plugins/install", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/untrusted/ignored",
        "version": version,
    })


def test_install_verifies_commit_and_records_source(local_install):
    client, plugins, _, chosen, _ = local_install
    response = install(client)
    assert response.status_code == 200, response.text
    target = support.resolve_installed_plugin_path("example.demo")
    receipt = release_install.read_release_receipt(target)
    assert receipt["commit"] == chosen.commit
    assert receipt["repository_url"] == "https://github.com/example/demo"
    assert list((plugins / ".update_tmp").iterdir()) == []


@pytest.mark.parametrize("load_failure", [False, True])
def test_install_activates_runtime_before_reporting_success(local_install, monkeypatch, load_failure):
    client, _, _, _, _ = local_install
    manager = integration.get_plugin_runtime_manager()
    events = []

    async def activate(plugin_id):
        assert plugin_id == "example.demo"
        assert manager._plugin_file_update_lock.locked()
        target = support.resolve_installed_plugin_path(plugin_id)
        assert (target / "plugin.py").is_file()
        assert release_install.read_release_receipt(target)["version"] == "1.0.0"
        events.append("activate")
        if load_failure:
            raise RuntimeError("测试插件启动异常")

    async def progress(**kwargs):
        if kwargs["stage"] == "success":
            events.append("success")

    monkeypatch.setattr(manager, "activate_installed_plugin", activate)
    monkeypatch.setattr(release_install, "update_progress", progress)
    response = install(client)
    assert not manager._plugin_file_update_lock.locked()
    if load_failure:
        assert response.status_code == 409
        assert "插件文件已安装" in response.text
        assert "测试插件启动异常" in response.text
        assert events == ["activate"]
    else:
        assert response.status_code == 200, response.text
        assert events == ["activate", "success"]


@pytest.mark.parametrize("status", ["success", "inactive", "failed"])
@pytest.mark.parametrize("environment_changed, blocked_changed", [(False, False), (True, False), (False, True)])
def test_new_plugin_syncs_dependencies_and_config_subscription(
    tmp_path, monkeypatch, status, environment_changed, blocked_changed
):
    manager = integration.PluginRuntimeManager()
    manager._started = True
    plugin_path = tmp_path / "example.demo"
    plugin_path.mkdir()
    manager._plugin_file_watcher = FileWatcher(paths=[tmp_path])
    events = []

    async def sync(plugin_dirs):
        assert plugin_dirs == [tmp_path]
        events.append("dependencies")
        return integration.DependencySyncState(
            blocked_changed_plugin_ids={"example.demo"} if blocked_changed else set(),
            environment_changed=environment_changed,
        )

    async def load(plugin_id, reason):
        assert plugin_id == "example.demo" and reason == "release_install"
        events.append("load")
        return status == "success"

    async def restart(reason):
        assert reason == "release_install"
        events.append("restart")
        return True

    monkeypatch.setattr(manager, "_iter_plugin_dirs", lambda: iter([tmp_path]))
    monkeypatch.setattr(manager, "_iter_watchable_plugin_paths", lambda: iter([("example.demo", plugin_path)]))
    monkeypatch.setattr(manager, "_sync_plugin_dependencies", sync)
    monkeypatch.setattr(manager, "load_plugin_globally", load)
    monkeypatch.setattr(manager, "_restart_supervisors", restart)
    monkeypatch.setattr(manager, "get_plugin_load_statuses", lambda: {"example.demo": status})
    monkeypatch.setattr(manager, "get_plugin_load_failure_reasons", lambda: {"example.demo": "配置无效"})
    if status == "failed":
        with pytest.raises(RuntimeError, match="配置无效"):
            asyncio.run(manager.activate_installed_plugin("example.demo"))
    else:
        asyncio.run(manager.activate_installed_plugin("example.demo"))
    assert events == ["dependencies", "restart" if environment_changed or blocked_changed else "load"]
    assert manager._plugin_config_watcher_subscriptions["example.demo"][0] == plugin_path / "config.toml"


def test_new_plugin_restart_failure_still_registers_config_subscription(tmp_path, monkeypatch):
    manager = integration.PluginRuntimeManager()
    manager._started = True
    manager._plugin_file_watcher = FileWatcher(paths=[tmp_path])

    async def sync(plugin_dirs):
        return integration.DependencySyncState(blocked_changed_plugin_ids=set(), environment_changed=True)

    async def restart(reason):
        return False

    monkeypatch.setattr(manager, "_sync_plugin_dependencies", sync)
    monkeypatch.setattr(manager, "_restart_supervisors", restart)
    monkeypatch.setattr(manager, "_iter_watchable_plugin_paths", lambda: iter([("example.demo", tmp_path)]))
    with pytest.raises(RuntimeError, match="重启插件运行时失败"):
        asyncio.run(manager.activate_installed_plugin("example.demo"))
    assert manager._plugin_config_watcher_subscriptions["example.demo"][0] == tmp_path / "config.toml"


def test_moved_tag_or_manifest_tampering_does_not_install(local_install):
    client, _, _, chosen, _ = local_install
    chosen.commit = "b" * 40
    response = install(client)
    assert response.status_code == 409
    assert support.resolve_installed_plugin_path("example.demo") is None


def test_manifest_mismatch_does_not_install(local_install):
    client, _, _, chosen, _ = local_install
    chosen.manifest["description"] = "篡改索引"
    response = install(client)
    assert response.status_code == 409
    assert support.resolve_installed_plugin_path("example.demo") is None


def test_branch_updates_cannot_override_release(local_install):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    body = {"plugin_id": "example.demo", "repository_url": "https://github.com/example/demo"}
    response = client.post("/plugins/update", json=body)
    assert response.status_code == 409 and "不能使用分支" in response.text


def test_explicit_version_switch_preserves_data_and_backup(local_install):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    target = support.resolve_installed_plugin_path("example.demo")
    (target / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
    (target / "history.db").write_bytes(b"user data")
    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo",
        "version": "1.0.0",
    })
    assert response.status_code == 200, response.text
    assert (target / "history.db").read_bytes() == b"user data"
    assert "false" in (target / "config.toml").read_text(encoding="utf-8")
    assert (Path(response.json()["backup_path"]) / "history.db").read_bytes() == b"user data"
    assert release_install.read_release_receipt(target)["version"] == "1.0.0"


@pytest.mark.parametrize("version_spec, has_warning", [("<1.0.0", True), (">=1.0.0,<2.0.0", False)])
def test_reverse_dependency_only_warns_without_blocking_update(local_install, version_spec, has_warning):
    client, plugins, _, _, _ = local_install
    assert install(client).status_code == 200
    dependent = manifest()
    dependent["id"] = "example.dependent"
    dependent["name"] = "依赖方插件"
    dependent["dependencies"] = [{"type": "plugin", "id": "example.demo", "version_spec": version_spec}]
    directory = plugins / "example.dependent"
    directory.mkdir()
    (directory / "_manifest.json").write_text(json.dumps(dependent), encoding="utf-8")

    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo", "version": "1.0.0",
    })

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    warnings = response.json()["warnings"]
    assert bool(warnings) is has_warning
    if has_warning:
        assert "example.dependent" in warnings[0]
        assert "依赖方插件" in warnings[0]
        assert version_spec in warnings[0]
    target = support.resolve_installed_plugin_path("example.demo")
    assert release_install.read_release_receipt(target)["version"] == "1.0.0"


def test_local_code_changes_block_switch_without_losing_edits(local_install):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    target = support.resolve_installed_plugin_path("example.demo")
    (target / "plugin.py").write_text("# 用户修改\n", encoding="utf-8")
    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo", "version": "1.0.0",
    })
    assert response.status_code == 409
    assert (target / "plugin.py").read_text(encoding="utf-8") == "# 用户修改\n"


def test_automatic_update_never_downgrades(local_install):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo", "version": "latest",
    })
    assert response.status_code == 409 and "不会自动降级" in response.text


def test_failed_directory_swap_restores_previous_install(local_install, monkeypatch):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    target = support.resolve_installed_plugin_path("example.demo")
    (target / "history.db").write_bytes(b"keep me")
    manager = integration.get_plugin_runtime_manager()
    events = []
    async def stop(plugin_id):
        assert manager._plugin_file_update_lock.locked()
        events.append("stop")
        return [plugin_id]
    async def resume(plugin_ids):
        assert manager._plugin_file_update_lock.locked()
        assert plugin_ids == ["example.demo"]
        assert (target / "history.db").read_bytes() == b"keep me"
        events.append("resume")
    monkeypatch.setattr(release_install, "_stop_runtime", stop)
    monkeypatch.setattr(release_install, "_resume_runtime", resume)
    original_rename = Path.rename
    def fail_candidate_swap(path, destination):
        if path.parent.name == ".update_tmp" and destination == target:
            raise OSError("模拟替换失败")
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", fail_candidate_swap)
    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo", "version": "1.0.0",
    })
    assert response.status_code == 500
    assert (target / "history.db").read_bytes() == b"keep me"
    assert release_install.read_release_receipt(target)["version"] == "1.0.0"
    assert events == ["stop", "resume"]
    assert not manager._plugin_file_update_lock.locked()


def test_update_resumes_runtime_before_releasing_transaction(local_install, monkeypatch):
    client, _, _, _, _ = local_install
    assert install(client).status_code == 200
    target = support.resolve_installed_plugin_path("example.demo")
    manager = integration.get_plugin_runtime_manager()
    events = []

    async def stop(plugin_id):
        assert manager._plugin_file_update_lock.locked()
        events.append("stop")
        return [plugin_id]

    async def resume(plugin_ids):
        assert manager._plugin_file_update_lock.locked()
        assert plugin_ids == ["example.demo"]
        assert release_install.read_release_receipt(target)["version"] == "1.0.0"
        events.append("resume")

    monkeypatch.setattr(release_install, "_stop_runtime", stop)
    monkeypatch.setattr(release_install, "_resume_runtime", resume)
    response = client.post("/plugins/update", json={
        "plugin_id": "example.demo", "repository_url": "https://github.com/example/demo",
        "version": "1.0.0",
    })
    assert response.status_code == 200, response.text
    assert events == ["stop", "resume"]
    assert not manager._plugin_file_update_lock.locked()


def test_user_data_conflict_does_not_overwrite_new_code(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "data").mkdir()
    (source / "data" / "user.db").write_bytes(b"user")
    (target / "data").mkdir()
    (target / "data" / "code.py").write_text("# code", encoding="utf-8")
    with pytest.raises(HTTPException, match="路径冲突"):
        release_install._preserve_user_files(source, target)
    assert (target / "data" / "code.py").read_text(encoding="utf-8") == "# code"


def test_registry_alias_shares_install_operation_lock(local_install):
    client, _, _, _, _ = local_install
    with management._reserve_plugin_operation("example.demo", "install"):
        response = client.post("/plugins/install", json={
            "plugin_id": "legacy.demo", "repository_url": "https://github.com/example/demo", "version": "latest",
        })
    assert response.status_code == 409 and "正在执行" in response.text


def test_install_warns_about_reverse_dependency_versions(local_install):
    client, plugins, _, _, _ = local_install
    dependent = plugins / "dependent"
    dependent.mkdir()
    data = manifest()
    data["id"] = "example.dependent"
    data["dependencies"] = [{"type": "plugin", "id": "example.demo", "version_spec": ">=2.0.0"}]
    (dependent / "_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    response = install(client)
    assert response.status_code == 200, response.text
    assert "example.dependent" in response.json()["warnings"][0]
    assert ">=2.0.0" in response.json()["warnings"][0]
    assert support.resolve_installed_plugin_path("example.demo") is not None


def test_missing_plugin_dependency_blocks_install(local_install):
    client, _, repository, chosen, _ = local_install
    chosen.manifest["dependencies"] = [{"type": "plugin", "id": "example.missing", "version_spec": ">=1.0.0"}]
    (repository / "_manifest.json").write_text(json.dumps(chosen.manifest), encoding="utf-8")
    for args in [["add", "."], ["commit", "-qm", "测试依赖"], ["tag", "-f", "v1.0.0"]]:
        subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
    chosen.commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    response = install(client)
    assert response.status_code == 400 and "插件依赖不满足" in response.text
    assert support.resolve_installed_plugin_path("example.demo") is None

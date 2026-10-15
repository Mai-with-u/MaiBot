"""包更新的版本选择、实例互斥、备份和独立 uv 执行器。"""

from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import zipfile

import pytest

from src.common import package_update, self_update
from src.common.package_update import FileLock, SelfUpdateError, ToolEnvironment


def environment(tmp_path: Path) -> ToolEnvironment:
    prefix = tmp_path / "tools/maibot"
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "maibot" }]\n'
        f'entrypoints = [{{ name = "maibot", install-path = "{tmp_path.as_posix()}/bin/maibot" }}]\n',
        encoding="utf-8",
    )
    return ToolEnvironment(prefix, Path(sys.executable), Path(sys.executable), "uv")


def test_latest_ignores_prereleases_yanked_and_empty_files(monkeypatch):
    fetch = Mock(return_value={"releases": {
        "1.3.8": [{}], "1.3.9": [{}], "2.0.0rc1": [{}], "2.0.0": [{"yanked": True}], "3.0.0": [],
    }})
    monkeypatch.setattr(package_update, "fetch_releases", fetch)
    assert package_update.latest_release("1.3.8") == "1.3.9"
    assert package_update.latest_release("4.0.0") == "4.0.0"
    assert fetch.call_count == 2


@pytest.mark.parametrize("version", ["dev", "1.3.9rc1", "1.3.9;echo x", "01.2.3", "-1.2.3"])
def test_invalid_package_targets_fail_closed(version):
    with pytest.raises(SelfUpdateError, match="正式版本"):
        package_update.version_tuple(version)


def test_lease_blocks_other_instances_but_ignores_stale_files(tmp_path):
    env = environment(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    with FileLock(package_update.instance_lock_path(env, first)):
        package_update.ensure_other_instances_stopped(env, first)
        with pytest.raises(SelfUpdateError, match="其他实例"):
            package_update.ensure_other_instances_stopped(env, second)
    package_update.ensure_other_instances_stopped(env, second)


def test_start_is_blocked_while_updating(tmp_path, monkeypatch):
    env = environment(tmp_path)
    monkeypatch.setattr(package_update.sys, "prefix", str(env.prefix))
    with FileLock(env.locks / "update.guard"):
        with pytest.raises(SelfUpdateError, match="正在更新"):
            package_update.register_instance(tmp_path / "instance")


def test_backup_copies_config_and_sqlite_without_recursing_into_backups(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/model_config.toml").write_text("original", encoding="utf-8")
    (tmp_path / "data").mkdir()
    database = tmp_path / "data/MaiBot.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE messages (content TEXT)")
        db.execute("INSERT INTO messages VALUES ('keep me')")
    first = package_update.backup_instance(tmp_path)
    second = package_update.backup_instance(tmp_path)
    assert first != second
    assert (second / "config/model_config.toml").read_text() == "original"
    with sqlite3.connect(second / "data/MaiBot.db") as db:
        assert db.execute("SELECT content FROM messages").fetchone() == ("keep me",)
    assert not (second / "data/self_update").exists()


@pytest.fixture
def package_host(tmp_path, monkeypatch):
    env = environment(tmp_path)
    monkeypatch.setattr(self_update, "PACKAGED_INSTALL", True)
    monkeypatch.setattr(self_update, "tool_environment", lambda: env)
    monkeypatch.setattr(self_update, "read_project_version", lambda: "1.3.8")
    monkeypatch.setattr(self_update, "latest_release", lambda current: "1.3.9")
    monkeypatch.setattr(self_update, "fetch_releases", lambda: {"releases": {"1.3.9": [{}]}})
    monkeypatch.setenv(package_update.PACKAGE_RUNNER_ENV, "1")
    return tmp_path, env


def test_package_check_and_pending_pin_confirmed_version(package_host):
    root, _ = package_host
    check = self_update.check_for_update(root)
    assert check.can_apply and check.local.deployment_type == "package"
    assert (check.latest_version, check.latest_commit, check.source.id) == ("1.3.9", "1.3.9", "pypi")
    pending = self_update.request_update("1.3.9", root)
    assert (pending.branch, pending.base_commit, pending.target_commit) == ("pypi", "1.3.8", "1.3.9")
    with pytest.raises(SelfUpdateError, match="已有更新请求"):
        self_update.request_update("1.3.9", root)


def test_package_request_rejects_downgrade_and_missing_release(package_host):
    root, _ = package_host
    with pytest.raises(SelfUpdateError, match="高于"):
        self_update.request_update("1.3.8", root)
    with pytest.raises(SelfUpdateError, match="没有这个"):
        self_update.request_update("1.4.0", root)
    assert not self_update._pending_path(root).exists()


def test_package_apply_consumes_request_and_records_result(package_host, monkeypatch):
    root, _ = package_host
    self_update.request_update("1.3.9", root)

    def update(instance, current, target, result):
        assert (instance, current, target) == (root, "1.3.8", "1.3.9")
        return {**result, "success": True, "from_version": current, "to_version": target, "stage": "done"}

    monkeypatch.setattr(self_update, "run_update", update)
    result = self_update.apply_pending_update(Mock(), root)
    assert result.success and result.to_version == "1.3.9"
    assert result.from_commit == result.to_commit == ""
    assert self_update.read_last_result(root).success
    assert self_update.apply_pending_update(Mock(), root) is None


def test_expired_package_request_never_executes(package_host, monkeypatch):
    root, _ = package_host
    pending = self_update.request_update("1.3.9", root)
    pending.requested_at = time.time() - 601
    self_update._write_json(self_update._pending_path(root), asdict(pending))
    update = Mock()
    monkeypatch.setattr(self_update, "run_update", update)
    result = self_update.apply_pending_update(Mock(), root)
    assert not result.success and result.error_code == "stale_request"
    update.assert_not_called()


def test_lost_updater_result_marks_environment_uncertain(package_host, monkeypatch):
    root, _ = package_host
    self_update.request_update("1.3.9", root)
    update = Mock(side_effect=SelfUpdateError("package_updater_failed", "updater crashed"))
    monkeypatch.setattr(self_update, "run_update", update)
    result = self_update.apply_pending_update(Mock(), root)
    assert not result.success and result.rollback_error
    assert (result.from_version, result.to_version, result.to_commit) == ("1.3.8", "1.3.9", "")


def test_tool_detection_requires_receipt_and_managed_runner(tmp_path, monkeypatch):
    env = environment(tmp_path)
    monkeypatch.setattr(package_update.sys, "prefix", str(env.prefix))
    monkeypatch.setattr(package_update.shutil, "which", lambda name: "uv")
    monkeypatch.delenv(package_update.PACKAGE_RUNNER_ENV, raising=False)
    with pytest.raises(SelfUpdateError) as error:
        package_update.tool_environment()
    assert error.value.code == "package_runner_missing"
    monkeypatch.setenv(package_update.PACKAGE_RUNNER_ENV, "1")
    assert package_update.tool_environment().prefix == env.prefix
    (env.prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "maibot", path = "/local/wheel" }]\n', encoding="utf-8",
    )
    with pytest.raises(SelfUpdateError) as error:
        package_update.tool_environment()
    assert error.value.code == "not_uv_tool"


def build_fixture_wheel(index: Path, version: str, broken: bool = False) -> None:
    """两个真实小 wheel 模拟升级，不下载 MaiBot 依赖，也不访问 PyPI。"""
    directory = index / "maibot"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"maibot-{version}-py3-none-any.whl"
    info = f"maibot-{version}.dist-info"
    with zipfile.ZipFile(directory / name, "w") as wheel:
        reply = "broken-entrypoint" if broken else version
        wheel.writestr("fixture_app.py", "import sys, time\ndef main():\n"
                       f"    print('{reply}')\n    if '--hold' in sys.argv: time.sleep(90)\n")
        wheel.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: maibot\nVersion: {version}\n")
        wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(f"{info}/entry_points.txt", "[console_scripts]\nmaibot = fixture_app:main\n")
        wheel.writestr(f"{info}/RECORD", "".join(f"{path},,\n" for path in [*wheel.namelist(), f"{info}/RECORD"]))
    names = sorted(path.name for path in directory.glob("*.whl"))
    (directory / "index.html").write_text("\n".join(f'<a href="{item}">{item}</a>' for item in names))


@pytest.mark.skipif(shutil.which("uv") is None, reason="需要 uv 来验证真实工具环境更新")
@pytest.mark.parametrize("pinned", [True, False])
def test_real_uv_upgrade_and_rollback_with_live_interpreter(tmp_path, pinned):
    index = tmp_path / "index"
    build_fixture_wheel(index, "9000.0.1")
    tool_root = tmp_path / "tools"
    bin_root = tmp_path / "bin"
    root = tmp_path / "instance"
    root.mkdir()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("UV_") or key in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}:
            env.pop(key)
    env.update(UV_TOOL_DIR=str(tool_root), UV_TOOL_BIN_DIR=str(bin_root))
    uv = shutil.which("uv")
    base_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    requirement = "maibot==9000.0.1" if pinned else "maibot"
    subprocess.run([uv, "--no-config", "tool", "install", requirement, "--python", str(base_python),
                    "--default-index", index.as_uri()], env=env, cwd=root, capture_output=True, check=True, timeout=60)
    build_fixture_wheel(index, "9000.0.2")
    build_fixture_wheel(index, "9000.0.3", broken=True)
    prefix = tool_root / "maibot"
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    helper = tmp_path / "package_updater.py"
    shutil.copyfile(package_update.__file__, helper)
    assert hashlib.sha256(helper.read_bytes()).digest() == hashlib.sha256(Path(package_update.__file__).read_bytes()).digest()
    # 整个升级过程中旧工具解释器仍在运行，覆盖 Windows 下不能重建解释器的约束。
    executable = bin_root / ("maibot.exe" if os.name == "nt" else "maibot")
    live = subprocess.Popen([str(executable), "--hold"], env=env, cwd=root, stdout=subprocess.DEVNULL)
    try:
        for target, succeeds in (("9000.0.2", True), ("9000.0.3", False), ("9000.0.4", False)):
            current = "9000.0.1" if succeeds else "9000.0.2"
            request = {"root": str(root), "prefix": str(prefix), "python": str(python), "uv": uv,
                       "current": current, "target": target,
                       "result": asdict(self_update.UpdateResult(False, time.time()))}
            request_path = tmp_path / "package_request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            # 执行器文件保持原样；仅在测试进程中把固定 PyPI 地址替换成隔离的本地索引。
            driver = (
                "import runpy,sys; ns=runpy.run_path(sys.argv[1]); "
                "ns['execute_update'].__globals__['PYPI_INDEX']=sys.argv[3]; "
                "sys.argv=[sys.argv[1],sys.argv[2]]; ns['main']()"
            )
            subprocess.run([str(base_python), "-I", "-c", driver, str(helper), str(request_path), index.as_uri()],
                           env=env, cwd=root, capture_output=True, check=True, timeout=90)
            result = json.loads((tmp_path / "package_result.json").read_text(encoding="utf-8"))
            assert result["success"] is succeeds, result
            assert not result["rollback_error"], result
            if not succeeds:
                assert result["rolled_back"], result
            version = subprocess.check_output([str(executable)], env=env, cwd=root, text=True).strip()
            assert version == "9000.0.2"
            receipt = tomllib.loads((prefix / "uv-receipt.toml").read_text(encoding="utf-8"))
            assert receipt["tool"]["requirements"][0].get("specifier") == ("==9000.0.2" if pinned else None)
            assert live.poll() is None
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(live.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            live.terminate()
        live.wait(timeout=10)

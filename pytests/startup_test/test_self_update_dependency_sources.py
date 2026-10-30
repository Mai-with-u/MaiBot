from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from zipfile import ZipFile
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from src.common import self_update


def wheel(directory: Path, name: str, version: str) -> Path:
    path = directory / f"{name}-{version}-py3-none-any.whl"
    info = f"{name}-{version}.dist-info"
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")
    return path


def test_source_update_dependency_install_finds_required_version_on_second_index(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required")
    files = tmp_path / "files"
    files.mkdir()
    name = "maibot_source_probe"
    old = wheel(files, name, "1.0.0")
    new = wheel(files, name, "2.0.0")
    plugin = wheel(files, "maibot_extra_plugin_probe", "1.0.0")
    for source, item in (("aliyun", old), ("official", new)):
        index = tmp_path / source / name.replace("_", "-")
        index.mkdir(parents=True)
        (index / "index.html").write_text(f'<a href="../../files/{item.name}">{item.name}</a>', encoding="utf-8")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    (tmp_path / "pyproject.toml").write_text(
        f'''[project]
name = "source-update-test"
version = "1.0.0"
dependencies = ["maibot-source-probe"]
[tool.uv]
index-strategy = "unsafe-first-match"
constraint-dependencies = ["maibot-source-probe>=2.0.0"]
[[tool.uv.index]]
name = "aliyun"
url = "{base}/aliyun"
[[tool.uv.index]]
name = "official"
url = "{base}/official"
default = true
''', encoding="utf-8")
    # 普通项目同步会按 pyproject 解析缺少的版本并生成锁文件。
    lock = tmp_path / "uv.lock"
    env = {key: value for key, value in os.environ.items() if not key.startswith(("UV_", "PIP_"))}
    env["UV_CACHE_DIR"] = str(tmp_path / "cache")
    environment = tmp_path / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def run(command):
        result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    try:
        run([uv, "venv", str(environment)])
        run([uv, "pip", "install", "--python", str(python), str(plugin)])
        command = self_update._dependency_command("uv")
        command[0] = uv
        command.extend(["--python", str(python)])
        run(command)
        versions = json.loads(run([str(python), "-c",
            "import json; from importlib.metadata import version; "
            "print(json.dumps([version('maibot-source-probe'), version('maibot-extra-plugin-probe')]))"]))
        assert versions == ["2.0.0", "1.0.0"]
        assert "2.0.0" in lock.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "failed_source,available_version,required_version,pinned,success",
    [
        ("official", "2.0.0", "2.0.0", False, True),
        ("aliyun", "2.0.0", "2.0.0", False, True),
        ("official", "1.0.0", "2.0.0", False, False),
        ("official", "2.0.0", "2.0.0", True, False),
        ("both", "2.0.0", "2.0.0", False, False),
    ],
)
def test_sync_excludes_only_timed_out_index(
    tmp_path, monkeypatch, caplog, failed_source, available_version, required_version, pinned, success
):
    if shutil.which("uv") is None:
        pytest.skip("uv is required")
    files = tmp_path / "files"
    files.mkdir()
    package = wheel(files, "maibot_source_probe", available_version)
    for source in ("aliyun", "official"):
        index = tmp_path / source / "maibot-source-probe"
        index.mkdir(parents=True)
        (index / "index.html").write_text(f'<a href="../../files/{package.name}">{package.name}</a>')
    requests = []
    failed_sources = {"aliyun", "official"} if failed_source == "both" else {failed_source}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if any(self.path.startswith(f"/{source}/") for source in failed_sources):
                time.sleep(2)
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    project = tmp_path / "pyproject.toml"
    original = f'''[project]
name = "source-update-test"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["maibot-source-probe=={required_version}"]
[tool.uv]
index-strategy = "unsafe-first-match"
[[tool.uv.index]]
name = "aliyun"
url = "{base}/aliyun"
[[tool.uv.index]]
name = "official"
url = "{base}/official"
default = true
'''
    if pinned:
        original += f'[tool.uv.sources]\nmaibot-source-probe = {{ index = "{failed_source}" }}\n'
    project.write_text(original, encoding="utf-8")
    for key in list(os.environ):
        if key.startswith(("UV_", "PIP_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("UV_HTTP_TIMEOUT", "1")
    monkeypatch.setenv("UV_HTTP_RETRIES", "0")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    command = self_update._dependency_command("uv") + ["--python", sys.executable]
    monkeypatch.setattr(self_update, "_dependency_command", lambda _tool: list(command))
    try:
        if success:
            self_update._sync_dependencies(tmp_path, "uv")
            python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            version = subprocess.check_output(
                [str(python), "-c", "from importlib.metadata import version; print(version('maibot-source-probe'))"],
                text=True,
            ).strip()
            assert version == required_version
            assert "maibot-update-index-" not in (tmp_path / "uv.lock").read_text(encoding="utf-8")
        else:
            with pytest.raises(self_update.SelfUpdateError, match="依赖同步失败"):
                self_update._sync_dependencies(tmp_path, "uv")
        assert project.read_text(encoding="utf-8") == original
        for source in failed_sources:
            # 全部源失效时，第一次失败可能已并发访问第二个源，第二轮才定位到它。
            count = sum(path.startswith(f"/{source}/") for path in requests)
            assert 1 <= count <= len(failed_sources)
            assert f"依赖源 {source} 网络故障" in caplog.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("output", [
    "No solution found when resolving dependencies",
    "Failed to fetch: `https://pypi.org/simple/example/`\nCaused by: invalid peer certificate",
    "Failed to fetch: `https://pypi.org/simple/example/`\nCaused by: 403 Forbidden",
    "Failed to fetch: `https://pypi.org.evil/simple/example/`\nCaused by: operation timed out",
    "Failed to fetch: `https://files.pythonhosted.org/example.whl`\nCaused by: operation timed out",
])
def test_sync_does_not_retry_unattributed_or_non_network_errors(tmp_path, monkeypatch, output):
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.uv.index]]\nname="pypi"\nurl="https://pypi.org/simple"\ndefault=true\n'
    )
    calls = []

    def run(command, _root, _timeout):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2, "", output)

    monkeypatch.setattr(self_update, "_run_command", run)
    with pytest.raises(self_update.SelfUpdateError):
        self_update._sync_dependencies(tmp_path, "uv")
    assert len(calls) == 1


def test_sync_stops_if_override_does_not_exclude_source(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.uv.index]]\nname="pypi"\nurl="https://pypi.org/simple"\ndefault=true\n'
    )
    calls = []

    def run(command, _root, _timeout):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 2, "", "Failed to fetch: `https://pypi.org/simple/probe/`\noperation timed out")

    monkeypatch.setattr(self_update, "_run_command", run)
    with pytest.raises(self_update.SelfUpdateError, match="本次已临时排除故障源：pypi"):
        self_update._sync_dependencies(tmp_path, "uv")
    assert len(calls) == 2


def test_dependency_failure_keeps_rollback_path(tmp_path, monkeypatch):
    import logging

    pending = self_update.PendingUpdate("new", "old", "main", time.time())
    result = self_update.UpdateResult(False, time.time(), stage="dependencies")
    actions = []
    monkeypatch.setattr(self_update, "_git", lambda root, *args: actions.append(args))
    monkeypatch.setattr(self_update, "_sync_dependencies", lambda root, tool, logger: actions.append((tool,)))
    self_update._rollback(pending, "uv", result, logging.getLogger(__name__), tmp_path)
    assert actions == [("reset", "--hard", "old"), ("uv",)]
    assert result.rolled_back

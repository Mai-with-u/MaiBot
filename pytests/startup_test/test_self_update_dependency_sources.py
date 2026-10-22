from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from zipfile import ZipFile
import json
import os
import shutil
import subprocess

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

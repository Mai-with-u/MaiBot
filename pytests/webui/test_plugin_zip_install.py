from io import BytesIO
from zipfile import ZipFile, ZipInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import json
import stat

import pytest

from src.plugin_runtime.runner.manifest_validator import ManifestValidator
from src.webui.routers.plugin import support, zip_install


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(support, "get_plugins_dir", lambda: root)
    monkeypatch.setattr(zip_install, "get_plugins_dir", lambda: root)
    monkeypatch.setattr(zip_install, "require_plugin_token", lambda _: "ok")
    monkeypatch.setattr(
        zip_install,
        "ManifestValidator",
        lambda **kwargs: ManifestValidator(
            host_version="1.3.0",
            sdk_version="2.8.1",
            **kwargs,
        ),
    )
    app = FastAPI()
    app.include_router(zip_install.router)
    return TestClient(app), root


def archive(prefix="", manifest_changes=None, extra=None):
    manifest = {
        "manifest_version": 2,
        "id": "example.demo",
        "version": "1.0.0",
        "name": "ZIP 插件",
        "description": "测试 ZIP 安装",
        "author": {"name": "作者", "url": "https://github.com/example"},
        "license": "MIT",
        "urls": {"repository": "https://github.com/example/demo"},
        "host_application": {"min_version": "1.0.0", "max_version": "9.99.99"},
        "sdk": {"min_version": "2.0.0", "max_version": "2.99.99"},
        "dependencies": [],
        "capabilities": [],
        "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
    }
    manifest.update(manifest_changes or {})
    buffer = BytesIO()
    with ZipFile(buffer, "w") as output:
        output.writestr(prefix + "_manifest.json", json.dumps(manifest))
        output.writestr(prefix + "plugin.py", "raise RuntimeError('安装时不应执行插件')")
        if extra is not None:
            if isinstance(extra, str) and "\\" in extra:
                # Windows 的 ZipInfo 构造器会自动替换反斜杠，手动保留以模拟外部 ZIP。
                raw_entry = ZipInfo(extra)
                raw_entry.filename = extra
                extra = raw_entry
            output.writestr(extra, "unsafe")
    return buffer.getvalue()


@pytest.mark.parametrize("prefix", ["", "demo-main/"])
def test_install_valid_archive_and_refuse_overwrite(client, prefix):
    api, root = client
    data = archive(prefix)
    response = api.post("/install-zip", files={"file": ("plugin.zip", data)})
    assert response.status_code == 200, response.text
    assert (root / "example_demo" / "plugin.py").is_file()
    original = (root / "example_demo" / "_manifest.json").read_bytes()
    assert api.post("/install-zip", files={"file": ("plugin.zip", data)}).status_code == 409
    assert (root / "example_demo" / "_manifest.json").read_bytes() == original
    assert sorted(path.name for path in root.iterdir()) == ["example_demo"]


@pytest.mark.parametrize("changes", [{"manifest_version": 1}, {"id": "../escape"}, {"sdk": {"min_version": "99.0.0"}}])
def test_invalid_manifest_leaves_plugins_untouched(client, changes):
    api, root = client
    response = api.post("/install-zip", files={"file": ("plugin.zip", archive(manifest_changes=changes))})
    assert response.status_code == 400
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "extra",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "demo\\escape",
        "file:stream",
        "CON.txt",
        "_manifest.json",
        ".maibot-release.json",
    ],
)
def test_reject_unsafe_archive_entries(client, extra):
    api, root = client
    response = api.post("/install-zip", files={"file": ("plugin.zip", archive(extra=extra))})
    assert response.status_code == 400
    assert list(root.iterdir()) == []


def test_reject_links_corruption_and_size_limit(client, monkeypatch):
    api, root = client
    link = ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for data in (archive(extra=link), b"not a zip"):
        assert api.post("/install-zip", files={"file": ("plugin.zip", data)}).status_code == 400
    monkeypatch.setattr(zip_install, "MAX_EXTRACTED_BYTES", 1)
    assert api.post("/install-zip", files={"file": ("plugin.zip", archive())}).status_code == 400
    monkeypatch.setattr(zip_install, "MAX_ZIP_BYTES", 1)
    assert api.post("/install-zip", files={"file": ("plugin.zip", archive())}).status_code == 413
    assert list(root.iterdir()) == []


def test_authentication_and_file_extension(client, monkeypatch):
    api, root = client
    assert api.post("/install-zip", files={"file": ("plugin.txt", archive())}).status_code == 400
    monkeypatch.setattr(zip_install, "require_plugin_token", support.require_plugin_token)
    assert api.post("/install-zip", files={"file": ("plugin.zip", archive())}).status_code == 401
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "files",
    [
        {"plugin.py": ""},
        {"_manifest.json": "{}"},
        {"_manifest.json": "invalid JSON", "plugin.py": ""},
        {"a/_manifest.json": "{}", "b/_manifest.json": "{}"},
    ],
)
def test_reject_missing_entrypoint_manifest_or_multiple_plugins(client, files):
    api, root = client
    buffer = BytesIO()
    with ZipFile(buffer, "w") as output:
        for name, content in files.items():
            output.writestr(name, content)
    assert api.post("/install-zip", files={"file": ("plugin.zip", buffer.getvalue())}).status_code == 400
    assert list(root.iterdir()) == []

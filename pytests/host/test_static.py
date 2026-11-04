"""宿主提供的 WebUI 静态资源：资源来源选择与目录越界防护。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.host import static as static_module


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _dist(directory: Path) -> Path:
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text("<html></html>", encoding="utf-8")
    (directory / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return directory


def test_installed_package_is_preferred_over_local_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package = _dist(tmp_path / "package")
    _dist(tmp_path / "source" / "dashboard" / "dist")
    monkeypatch.setattr(static_module, "SOURCE_ROOT", tmp_path / "source")
    monkeypatch.setattr(static_module, "import_module", lambda _name: SimpleNamespace(get_dist_path=lambda: package))
    monkeypatch.delenv("MAIBOT_WEBUI_USE_LOCAL_DASHBOARD", raising=False)
    assert static_module.resolve_static_path() == package
    monkeypatch.setenv("MAIBOT_WEBUI_USE_LOCAL_DASHBOARD", "1")
    assert static_module.resolve_static_path() == tmp_path / "source" / "dashboard" / "dist"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../secret.txt", "assets/../../secret.txt", "missing.js"])
async def test_paths_outside_assets_or_missing_files_are_rejected(tmp_path: Path, path: str) -> None:
    dist = _dist(tmp_path / "dist")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        await static_module.static_response(_request(), dist, path)
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    dist = _dist(tmp_path / "dist")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (dist / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建符号链接")
    with pytest.raises(HTTPException):
        await static_module.static_response(_request(), dist, "escape/secret.txt")

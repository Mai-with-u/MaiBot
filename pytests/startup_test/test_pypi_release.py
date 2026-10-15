"""发布重试必须拒绝覆盖不同内容，发布说明必须来自同版本 changelog。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Dict

import hashlib
import json

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/maibot_release.py"
SPEC = spec_from_file_location("maibot_release_checks", SCRIPT)
release = module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_partial_upload_accepts_identical_files() -> None:
    release.compare_pypi({"a.whl": "aaa", "a.tar.gz": "bbb"}, {"a.whl": "aaa"})


@pytest.mark.parametrize("remote", [{"a.whl": "different"}, {"unknown.whl": "aaa"}])
def test_upload_rejects_existing_different_files(remote: Dict[str, str]) -> None:
    with pytest.raises(ValueError, match="不同内容"):
        release.compare_pypi({"a.whl": "aaa"}, remote)


def test_complete_upload_requires_all_files() -> None:
    with pytest.raises(ValueError, match="全部"):
        release.compare_pypi({"a.whl": "aaa", "a.tar.gz": "bbb"}, {"a.whl": "aaa"}, complete=True)


def test_release_notes_only_include_selected_version() -> None:
    text = "# 更新日志\n# [1.3.9] - 2026-10-14\n\n## 主程序\n- 新功能\n# [1.3.8]\n- 旧功能\n"
    assert release.release_notes(text, "1.3.9") == "## 主程序\n- 新功能\n"
    with pytest.raises(ValueError, match="缺少"):
        release.release_notes(text, "1.4.0")


def test_retry_verifies_original_distribution_bytes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    files = {"a.whl": b"wheel", "a.tar.gz": b"sdist"}
    for name, content in files.items():
        (dist / name).write_bytes(content)
    (dist / ".gitignore").write_text("*", encoding="utf-8")
    manifest = {"version": "1.3.9", "tag": "v1.3.9", "commit": "a" * 40,
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    (tmp_path / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert release.verify(tmp_path) == manifest
    for name in files:
        (dist / f"{name}.publish.attestation").write_text('{"signature":"generated-by-pypa"}', encoding="utf-8")
    # 上传生成的签名证明不改变 wheel/sdist 的校验结果。
    assert release.verify(tmp_path) == manifest
    unexpected = dist / "unknown.whl.publish.attestation"
    unexpected.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="校验和"):
        release.verify(tmp_path)
    unexpected.unlink()
    (dist / "a.whl").write_bytes(b"rebuilt")
    with pytest.raises(ValueError, match="校验和"):
        release.verify(tmp_path)


@pytest.mark.parametrize("path", ["src/data/private.json", "data/MaiBot.db", "logs/test.log", "src/__pycache__/a.pyc"])
def test_archive_rejects_user_data(path: str) -> None:
    with pytest.raises(ValueError, match="非发布文件"):
        release.audit_names(["root/" + path], prefix="root/")


def test_prepare_rejects_dev_dashboard_before_network(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="MaiBot"\nversion="1.3.9"\ndependencies=["maibot-dashboard==1.9.1.dev1"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="正式版本"):
        release.prepare(tmp_path, tmp_path / "release", "1.3.9")


def test_prepare_freezes_commit_and_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="MaiBot"\nversion="1.3.9"\ndependencies=["maibot-dashboard==1.9.1"]\n',
        encoding="utf-8",
    )
    changelogs = tmp_path / "changelogs"
    changelogs.mkdir()
    (changelogs / "changelog.md").write_text("# [1.3.9]\n- 本次变更\n# [1.3.8]\n- 上次变更\n", encoding="utf-8")
    monkeypatch.setattr(release, "pypi_files", lambda name, version: {"dashboard.whl": "sha"})
    monkeypatch.setattr(release.subprocess, "check_output", lambda *args, **kwargs: "a" * 40 + "\n")
    destination = tmp_path / "release"
    release.prepare(tmp_path, destination, "1.3.9")
    assert release.read_manifest(destination)["commit"] == "a" * 40
    assert (destination / "notes.md").read_text(encoding="utf-8") == "- 本次变更\n"

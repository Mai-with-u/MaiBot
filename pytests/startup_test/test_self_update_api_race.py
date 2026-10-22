from threading import Event
import time

import pytest

from src.common import self_update


@pytest.mark.parametrize("winner", ["github", "cnb"])
def test_returns_first_complete_source_without_waiting(monkeypatch, tmp_path, winner):
    release = Event()
    started = Event()
    local = self_update.LocalState(version="1.0.0", branch="dev", commit="1" * 40)
    monkeypatch.setattr(self_update, "inspect_local", lambda root: local)

    def check(local, root, source, name, deadline):
        assert 0 < deadline - time.monotonic() <= 60
        if name != winner:
            started.set()
            release.wait(2)
        else:
            assert started.wait(1)
        return self_update.UpdateCheck(local=local, latest_version=name)

    monkeypatch.setattr(self_update, "_check_source_api", check)
    try:
        assert self_update.check_for_update(tmp_path).latest_version == winner
        assert not release.is_set()
    finally:
        release.set()


def test_failed_source_does_not_override_valid_result(monkeypatch, tmp_path):
    local = self_update.LocalState(version="1.0.0", branch="dev", commit="1" * 40)
    monkeypatch.setattr(self_update, "inspect_local", lambda root: local)

    def check(local, root, source, name, deadline):
        if name == "github":
            raise self_update.SelfUpdateError("fetch_failed", "offline")
        return self_update.UpdateCheck(local=local, latest_version="2.0.0")

    monkeypatch.setattr(self_update, "_check_source_api", check)
    assert self_update.check_for_update(tmp_path).latest_version == "2.0.0"


def test_cnb_comparison_fields(monkeypatch, tmp_path):
    local = self_update.LocalState(version="1.0.0", branch="dev", commit="1" * 40, dependency_tool="uv")
    project = '[project]\nversion="2.0.0"\nrequires-python=">=3.12"\n'
    (tmp_path / "pyproject.toml").write_text(project, encoding="utf-8")
    responses = iter([
        {"commit": {"sha": "2" * 40}},
        {"total_commits": 3, "merge_base_commit": {"sha": local.commit},
         "files": [{"path": "pyproject.toml"}], "commits": []},
    ])
    monkeypatch.setattr(self_update, "_get_update_json", lambda *args: next(responses))
    monkeypatch.setattr(self_update.GitSource, "read_text", lambda *args, **kwargs: project)
    result = self_update._check_source_api(
        local, tmp_path, self_update.select_update_source("cnb"), "cnb", time.monotonic() + 60,
    )
    assert result.behind == 3
    assert result.ahead == 0
    assert result.latest_version == "2.0.0"
    assert result.dependencies_changed


def test_timeout_is_reported_without_waiting_for_workers(monkeypatch, tmp_path):
    release = Event()
    local = self_update.LocalState(version="1.0.0", branch="dev", commit="1" * 40)
    monkeypatch.setattr(self_update, "inspect_local", lambda root: local)
    monkeypatch.setattr(self_update, "_check_source_api", lambda *args: release.wait(2))

    def timeout(pending, *, timeout, return_when):
        assert 59 < timeout <= 60
        return set(), pending

    monkeypatch.setattr(self_update, "wait", timeout)
    try:
        with pytest.raises(self_update.SelfUpdateError, match="60"):
            self_update.check_for_update(tmp_path)
    finally:
        release.set()

"""已发布 Runner 的入口交接与实例锁继承。"""

from pathlib import Path

import os
import runpy
import sys

import pytest

from src.common.package_update import SelfUpdateError, register_instance
from src.host import legacy_runner, update_locks
from src.host.layout import bot_environment


def test_old_runner_entry_hands_off_before_loading_bot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MAIBOT_RUNTIME_CONTEXT", raising=False)
    monkeypatch.delenv("MAIBOT_WORKER_PROCESS", raising=False)
    monkeypatch.setenv("MAIBOT_RUNNER_SUPERVISED", "1")
    monkeypatch.setenv("MAIBOT_PACKAGE_RUNNER", "1")
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    monkeypatch.setattr(legacy_runner, "PACKAGED_INSTALL", True)

    def launch(root: Path) -> int:
        assert root == tmp_path.resolve()
        assert os.environ[legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV] == str(root)
        assert "MAIBOT_RUNNER_SUPERVISED" not in os.environ
        assert legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV not in bot_environment(root)
        return 23

    monkeypatch.setattr(legacy_runner, "run_host", launch)
    monkeypatch.setenv(legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV, "")
    with pytest.raises(SystemExit) as result:
        runpy.run_module("src.runtime", run_name="__main__")
    assert result.value.code == 23


def test_package_handoff_reuses_only_original_instance_lease(monkeypatch, tmp_path: Path) -> None:
    prefix = tmp_path / "maibot"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").touch()
    root = tmp_path / "instance"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(update_locks, "PACKAGED_INSTALL", True)
    monkeypatch.delenv(legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV, raising=False)
    with register_instance(root):
        with pytest.raises(SelfUpdateError):
            update_locks.register_host_instance(root)
        monkeypatch.setenv(legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV, str(root))
        for _ in range(2):  # 宿主与独立更新器先后进入，外层租约持续有效。
            with update_locks.register_host_instance(root):
                with pytest.raises(SelfUpdateError):
                    register_instance(root)
        with pytest.raises(RuntimeError, match="工作区不一致"):
            update_locks.register_host_instance(tmp_path / "other")
    monkeypatch.delenv(legacy_runner.LEGACY_PACKAGE_INSTANCE_ENV)
    with update_locks.register_host_instance(root):
        pass

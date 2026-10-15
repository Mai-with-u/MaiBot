"""用真实的临时 Git 仓库覆盖自更新：检查、登记、Runner 执行、回滚与来源校验。"""

from dataclasses import asdict
from pathlib import Path
from typing import Dict, List
from unittest.mock import Mock

import json
import subprocess
import sys
import time

import pytest

from src.common import process_runner, self_update
from src.common.self_update import PendingUpdate, SelfUpdateError

PYPROJECT = '[project]\nname = "MaiBot"\nversion = "{version}"\nrequires-python = "{python}"\n'
# 依赖同步的替身：requirements.txt 含 broken 时失败，用来触发回滚。
FAKE_SYNC = "import pathlib, sys; sys.exit('broken' in pathlib.Path('requirements.txt').read_text())"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for name, content in files.items():
        (repo / name.replace("__", ".")).write_text(content, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """official 模拟官方仓库，local 是从它克隆出来的本地部署。"""

    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "tester")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "tester@example.invalid")
    official = tmp_path / "official"
    official.mkdir()
    git(official, "init", "-b", "main")
    git(official, "config", "core.autocrlf", "false")
    commit(
        official,
        "init",
        pyproject__toml=PYPROJECT.format(version="1.0.0", python=">=3.12"),
        requirements__txt="fastapi\n",
        app__py="VERSION = 1\n",
    )
    local = tmp_path / "local"
    git(tmp_path, "clone", "--quiet", "-c", "core.autocrlf=false", str(official), str(local))

    monkeypatch.setattr(self_update, "OFFICIAL_REPOSITORY_URL", str(official))
    monkeypatch.setattr(self_update, "detect_dependency_tool", lambda root=local: "pip")
    monkeypatch.setattr(self_update, "_dependency_command", lambda tool: [sys.executable, "-c", FAKE_SYNC])
    return official, local


def use_mirrors(local: Path, *mirrors: dict) -> None:
    """写入与插件市场共用的镜像源配置。"""

    config_path = local / "data" / "webui.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(json.dumps({"git_mirrors": list(mirrors)}, ensure_ascii=False), encoding="utf-8")


def mirror(mirror_id: str, name: str, priority: int, enabled: bool = True, scheme: str = "https") -> dict:
    prefix = f"{scheme}://{mirror_id}.test/gh"
    return {"id": mirror_id, "name": name, "clone_prefix": prefix, "enabled": enabled, "priority": priority}


def release(official: Path, version: str = "1.1.0", **files: str) -> str:
    return commit(
        official,
        f"release {version}",
        pyproject__toml=PYPROJECT.format(version=version, python=">=3.12"),
        app__py="VERSION = 2\n",
        **files,
    )


def test_check_reports_new_version_and_commits(repos):
    official, local = repos
    assert self_update.check_for_update(local).update_available is False

    target = release(official)
    check = self_update.check_for_update(local)
    assert (check.behind, check.ahead, check.latest_commit, check.latest_version) == (1, 0, target, "1.1.0")
    assert check.can_apply is True and check.dependencies_changed is True
    assert [item.summary for item in check.commits] == ["release 1.1.0"]
    # 检查只取回对象，不改动工作区。
    assert (local / "app.py").read_text(encoding="utf-8") == "VERSION = 1\n"


def test_runner_applies_requested_update_once(repos):
    official, local = repos
    base = git(local, "rev-parse", "HEAD")
    target = release(official)
    self_update.check_for_update(local)
    self_update.request_update(target, local)

    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.success is True
    assert (result.from_commit, result.to_commit, result.from_version, result.to_version) == (
        base,
        target,
        "1.0.0",
        "1.1.0",
    )
    assert result.dependencies_synced is True and result.rolled_back is False
    assert git(local, "rev-parse", "HEAD") == target
    assert (local / "app.py").read_text(encoding="utf-8") == "VERSION = 2\n"
    assert self_update.read_last_result(local) == result
    # 请求只执行一次，之后的普通重启不会再触发更新。
    assert self_update.apply_pending_update(Mock(), local) is None


def test_update_stops_at_confirmed_commit_when_official_moves_on(repos):
    official, local = repos
    confirmed = release(official, "1.1.0")
    self_update.check_for_update(local)
    self_update.request_update(confirmed, local)
    release(official, "1.2.0")

    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.success is True
    assert git(local, "rev-parse", "HEAD") == confirmed


def test_dependency_failure_rolls_back_code(repos):
    official, local = repos
    base = git(local, "rev-parse", "HEAD")
    target = release(official, requirements__txt="fastapi\nbroken\n")
    self_update.check_for_update(local)
    self_update.request_update(target, local)

    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.success is False
    assert (result.stage, result.error_code, result.rolled_back) == ("dependencies", "dependency_failed", True)
    assert git(local, "rev-parse", "HEAD") == base
    assert (local / "app.py").read_text(encoding="utf-8") == "VERSION = 1\n"
    assert git(local, "status", "--porcelain", "--untracked-files=no") == ""


def test_commit_outside_official_history_is_refused(repos):
    official, local = repos
    base = git(local, "rev-parse", "HEAD")
    git(local, "checkout", "--quiet", "-b", "evil")
    evil = commit(local, "evil", app__py="VERSION = 666\n")
    git(local, "checkout", "--quiet", "main")
    release(official)
    # 直接伪造状态文件，绕过 WebUI 的登记流程。
    pending = PendingUpdate(target_commit=evil, base_commit=base, branch="main", requested_at=time.time())
    self_update._write_json(self_update._pending_path(local), asdict(pending))

    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.success is False
    assert (result.stage, result.error_code) == ("verify", "untrusted_commit")
    assert git(local, "rev-parse", "HEAD") == base


def test_update_follows_plugin_mirror_config(repos, tmp_path, monkeypatch):
    official, local = repos
    # 用 Git 的 insteadOf 把假域名指到本地仓库：live 可用，dead 指向不存在的路径，官方地址不可达。
    repo_path = "gh/Mai-with-u/MaiBot.git"
    git(local, "config", f"url.{official.as_posix()}.insteadOf", f"https://live.test/{repo_path}")
    git(local, "config", f"url.{(tmp_path / 'nowhere').as_posix()}.insteadOf", f"https://dead.test/{repo_path}")
    monkeypatch.setattr(self_update, "OFFICIAL_REPOSITORY_URL", str(tmp_path / "unreachable-official"))
    use_mirrors(
        local,
        mirror("live", "可用镜像", priority=3),
        mirror("off", "已停用镜像", priority=1, enabled=False),
        mirror("dead", "失效镜像", priority=2),
    )
    assert [source.id for source in self_update.load_update_sources(local)] == ["dead", "live"]

    target = release(official)
    check = self_update.check_for_update(local)
    assert check.source is not None and (check.source.id, check.latest_commit) == ("live", target)

    self_update.request_update(target, local)
    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and (result.success, result.source_name) == (True, "可用镜像")
    assert git(local, "rev-parse", "HEAD") == target


def test_mirror_config_failures_are_reported(repos, tmp_path):
    official, local = repos
    release(official)
    git(local, "config", f"url.{(tmp_path / 'nowhere').as_posix()}.insteadOf", "https://dead.test/")

    use_mirrors(local, mirror("dead", "失效镜像", priority=1))
    with pytest.raises(SelfUpdateError) as error:
        self_update.check_for_update(local)
    assert error.value.code == "fetch_failed" and "失效镜像" in error.value.message

    use_mirrors(local, mirror("plain", "明文镜像", priority=1, scheme="http"))
    with pytest.raises(SelfUpdateError) as error:
        self_update.check_for_update(local)
    assert error.value.code == "invalid_source"

    use_mirrors(local, mirror("off", "已停用镜像", priority=1, enabled=False))
    with pytest.raises(SelfUpdateError) as error:
        self_update.check_for_update(local)
    assert error.value.code == "no_update_source"

    # 镜像源配置里的 github 条目就是官方地址本身。
    use_mirrors(local, {**mirror("github", "GitHub 官方源", priority=1), "clone_prefix": "https://github.com"})
    assert self_update.check_for_update(local).source == self_update.UpdateSource(
        id="github", name="GitHub 官方源", url=str(official)
    )


@pytest.mark.parametrize(
    "prepare,blocker",
    [
        (lambda official, local: (local / "app.py").write_text("LOCAL = 1\n", encoding="utf-8"), "dirty_worktree"),
        (lambda official, local: commit(local, "local work", local__py="x = 1\n"), "diverged"),
        (
            lambda official, local: commit(
                official, "bump python", pyproject__toml=PYPROJECT.format(version="1.2.0", python=">=3.14")
            ),
            "python_requirement_changed",
        ),
    ],
)
def test_blockers_prevent_request(repos, prepare, blocker):
    official, local = repos
    target = release(official)
    prepare(official, local)

    check = self_update.check_for_update(local)
    assert blocker in check.blockers and check.can_apply is False
    with pytest.raises(SelfUpdateError) as error:
        self_update.request_update(check.latest_commit or target, local)
    assert error.value.code == blocker
    assert not self_update._pending_path(local).exists()


def test_stale_and_changed_requests_are_dropped(repos):
    official, local = repos
    base = git(local, "rev-parse", "HEAD")
    target = release(official)
    self_update.check_for_update(local)

    expired = PendingUpdate(target_commit=target, base_commit=base, branch="main", requested_at=time.time() - 3600)
    self_update._write_json(self_update._pending_path(local), asdict(expired))
    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.error_code == "request_expired"

    self_update.request_update(target, local)
    (local / "app.py").write_text("LOCAL = 1\n", encoding="utf-8")
    result = self_update.apply_pending_update(Mock(), local)
    assert result is not None and result.error_code == "dirty_worktree"
    assert git(local, "rev-parse", "HEAD") == base
    assert (local / "app.py").read_text(encoding="utf-8") == "LOCAL = 1\n"


def test_unsupported_deployments_report_blocker(repos, tmp_path):
    _, local = repos
    git(local, "checkout", "--quiet", "-b", "feature")
    assert self_update.check_for_update(local).blockers == ["unsupported_branch"]

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text(PYPROJECT.format(version="1.0.0", python=">=3.12"), encoding="utf-8")
    assert self_update.check_for_update(plain).blockers == ["not_git_repo"]
    with pytest.raises(SelfUpdateError):
        self_update.request_update("0" * 40, plain)


def test_runner_runs_hook_between_workers(monkeypatch):
    events: List[str] = []
    workers = [Mock(returncode=42), Mock(returncode=0)]
    for worker in workers:
        worker.poll.side_effect = lambda w=worker: w.returncode

    def popen(*_args, **_kwargs):
        events.append("worker")
        return workers.pop(0)

    monkeypatch.setattr(process_runner.subprocess, "Popen", popen)
    monkeypatch.setattr(process_runner.time, "sleep", lambda _delay: None)
    assert process_runner.supervise_worker(["fixture"], {}, Mock(), before_restart=lambda: events.append("hook")) == 0
    assert events == ["worker", "hook", "worker"]


@pytest.fixture
def children(monkeypatch):
    """用预设退出码的假子进程替换 Popen，并记录每次启动时传入的环境。"""

    exit_codes: List[int] = []
    launches: List[Dict[str, str]] = []

    def popen(_command, env, **_kwargs):
        launches.append(dict(env))
        child = Mock(returncode=exit_codes.pop(0))
        child.poll.side_effect = lambda: child.returncode
        return child

    monkeypatch.setattr(process_runner.subprocess, "Popen", popen)
    monkeypatch.setattr(process_runner.time, "sleep", lambda _delay: None)
    return exit_codes, launches


def roles(launches: List[Dict[str, str]]) -> List[str]:
    return ["worker" if process_runner.WORKER_PROCESS_ENV in env else "runner" for env in launches]


def test_runner_without_update_only_restarts_worker(children):
    exit_codes, launches = children
    exit_codes += [42, 0]
    assert process_runner.run_runner(["fixture"], {}, Mock(), lambda: False) == 0
    assert roles(launches) == ["worker", "worker"]


def test_runner_hands_over_to_new_runner_after_update(children):
    exit_codes, launches = children
    # Worker 请求重启并完成更新 -> 拉起新 Runner -> 新 Runner 再次更新后请求重新拉起 -> 最终正常退出。
    exit_codes += [42, process_runner.RUNNER_RELAUNCH_EXIT_CODE, 0]
    assert process_runner.run_runner(["fixture"], {}, Mock(), lambda: True) == 0
    assert roles(launches) == ["worker", "runner", "runner"]
    assert all(env[process_runner.RUNNER_SUPERVISED_ENV] == "1" for env in launches[1:])


def test_supervised_runner_asks_parent_to_relaunch(children):
    exit_codes, launches = children
    exit_codes += [42]
    env = {process_runner.RUNNER_SUPERVISED_ENV: "1"}
    assert process_runner.run_runner(["fixture"], env, Mock(), lambda: True) == process_runner.RUNNER_RELAUNCH_EXIT_CODE
    # 已有上级进程时不再往下套一层，进程层级保持不变。
    assert roles(launches) == ["worker"]


def test_worker_exit_code_is_not_mistaken_for_handover(children):
    exit_codes, launches = children
    exit_codes += [process_runner.RUNNER_RELAUNCH_EXIT_CODE]
    assert process_runner.run_runner(["fixture"], {}, Mock(), lambda: False) == 1
    assert roles(launches) == ["worker"]


def test_stop_during_update_wins_over_handover(children):
    exit_codes, launches = children
    exit_codes += [42]

    def apply_update() -> bool:
        process_runner.signal.raise_signal(process_runner.signal.SIGINT)
        return True

    assert process_runner.run_runner(["fixture"], {}, Mock(), apply_update) == 0
    assert roles(launches) == ["worker"]


def test_result_file_is_plain_json(repos):
    official, local = repos
    target = release(official)
    self_update.check_for_update(local)
    self_update.request_update(target, local)
    self_update.apply_pending_update(Mock(), local)

    payload = json.loads(self_update._result_path(local).read_text(encoding="utf-8"))
    assert payload["success"] is True and payload["to_commit"] == target

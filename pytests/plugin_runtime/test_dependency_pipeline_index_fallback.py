from pathlib import Path
from typing import Any, List

import subprocess

import pytest

from src.plugin_runtime.dependency_pipeline import CombinedPackageRequirement, PluginDependencyPipeline

ALIYUN = "https://mirrors.aliyun.com/pypi/simple"
PYPI = "https://pypi.org/simple"


def _write_pyproject(project_root: Path) -> None:
    # 默认索引故意写在前面，验证顺序按 uv 规则而不是按声明位置。
    (project_root / "pyproject.toml").write_text(
        f"""
[project]
name = "fake-host"
version = "1.0.0"
dependencies = []

[tool.uv]
index-strategy = "unsafe-first-match"
constraint-dependencies = ["idna>=3.15", "urllib3>=2.7.0"]

[[tool.uv.index]]
name = "pypi"
url = "{PYPI}"
default = true

[[tool.uv.index]]
name = "aliyun"
url = "{ALIYUN}"
""",
        encoding="utf-8",
    )


def _requirement() -> CombinedPackageRequirement:
    return CombinedPackageRequirement(
        package_name="silk-python",
        plugin_ids=("maibot-team.snowluma-adapter",),
        requirement_text="silk-python>=0.2.8",
        version_spec=">=0.2.8",
    )


def _index_of(command: List[str]) -> str:
    return command[command.index("--index-url") + 1]


def test_index_settings_follow_uv_priority(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)

    settings = PluginDependencyPipeline(project_root=tmp_path)._load_package_index_settings()

    assert settings.index_urls == (ALIYUN, PYPI)
    assert settings.constraints == ("idna>=3.15", "urllib3>=2.7.0")


def test_uv_command_only_queries_one_index_and_keeps_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "uv")
    constraint_path = tmp_path / "constraints.txt"

    command = PluginDependencyPipeline._build_install_command(["silk-python>=0.2.8"], ALIYUN, constraint_path)

    assert command[:3] == ["uv", "pip", "install"]
    assert "--no-config" in command
    assert _index_of(command) == ALIYUN
    assert command[command.index("--constraint") + 1] == str(constraint_path)
    assert command[-1] == "silk-python>=0.2.8"


@pytest.mark.asyncio
async def test_install_falls_back_to_pypi_when_aliyun_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_pyproject(tmp_path)
    attempted_indexes: List[str] = []
    constraint_texts: List[str] = []

    def fake_run(command: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        attempted_indexes.append(_index_of(command))
        constraint_texts.append(Path(command[command.index("--constraint") + 1]).read_text(encoding="utf-8"))
        returncode = 1 if _index_of(command) == ALIYUN else 0
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="No solution found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    succeeded, error_message = await PluginDependencyPipeline(project_root=tmp_path)._install_requirements(
        [_requirement()]
    )

    assert succeeded is True
    assert error_message == ""
    assert attempted_indexes == [ALIYUN, PYPI]
    assert constraint_texts == ["idna>=3.15\nurllib3>=2.7.0\n"] * 2


@pytest.mark.asyncio
async def test_install_stops_at_aliyun_when_it_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_pyproject(tmp_path)
    attempted_indexes: List[str] = []

    def fake_run(command: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        attempted_indexes.append(_index_of(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    succeeded, _ = await PluginDependencyPipeline(project_root=tmp_path)._install_requirements([_requirement()])

    assert succeeded is True
    assert attempted_indexes == [ALIYUN]


@pytest.mark.asyncio
async def test_install_reports_every_index_error_when_all_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_pyproject(tmp_path)

    def fake_run(command: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=f"failed on {_index_of(command)}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    succeeded, error_message = await PluginDependencyPipeline(project_root=tmp_path)._install_requirements(
        [_requirement()]
    )

    assert succeeded is False
    assert error_message == f"[{ALIYUN}] failed on {ALIYUN}；[{PYPI}] failed on {PYPI}"

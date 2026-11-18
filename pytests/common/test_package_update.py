from pathlib import Path
from typing import Any, Dict, List
from zipfile import ZipFile

import json
import os
import shutil
import subprocess
import tomllib

import pytest

from src.common import package_update


def _tool_env() -> Dict[str, str]:
    return {key: os.environ[key] for key in (
        "PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME",
    ) if key in os.environ}


def _wheel(directory: Path, name: str, version: str, metadata: str = "") -> Path:
    info = f"{name}-{version}.dist-info"
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}/__init__.py",
            f"def main():\n    from importlib.metadata import version\n    print(version('{name}'))\n",
        )
        archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{metadata}")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/entry_points.txt", f"[console_scripts]\n{name} = {name}:main\n")
        archive.writestr(f"{info}/RECORD", "\n".join(f"{item},," for item in [*archive.namelist(), f"{info}/RECORD"]) + "\n")
    return path


@pytest.fixture
def installed_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("Requires uv for isolated package installation")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in ("audit_dep", "audit_extra", "audit_feature"):
        for version in ("1.0.0", "2.0.0"):
            _wheel(wheels, name, version)
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
        dependency = "audit-dep>=2" if version == "1.2.0" else "audit-dep>=1"
        feature_version = "1.0.0" if version == "1.0.0" else "2.0.0"
        extra_dependency = "Requires-Dist: audit-extra>=2\n" if version == "1.3.0" else ""
        _wheel(
            wheels, "maibot", version,
            f"Requires-Dist: {dependency}\nProvides-Extra: sample\n"
            f'Requires-Dist: audit-feature=={feature_version}; extra == "sample"\n{extra_dependency}',
        )
    index = tmp_path / "index"
    for name in ("maibot", "audit-dep", "audit-extra", "audit-feature"):
        package = index / name
        package.mkdir(parents=True)
        package.joinpath("index.html").write_text(
            "\n".join(f'<a href="{wheel.as_uri()}">{wheel.name}</a>' for wheel in wheels.glob(f"{name.replace('-', '_')}-*")),
            encoding="utf-8",
        )
    constraint = tmp_path / "constraints.txt"
    constraint.write_text("audit-dep<2\n", encoding="utf-8")
    build_constraint = tmp_path / "build-constraints.txt"
    build_constraint.write_text("setuptools<80\n", encoding="utf-8")
    env = _tool_env()
    env.update(UV_TOOL_DIR=str(tmp_path / "tools"), UV_TOOL_BIN_DIR=str(tmp_path / "bin"))
    subprocess.run(
        [uv, "--no-config", "tool", "install", "--no-index", "--find-links", str(wheels),
         "maibot[sample]==1.0.0", "--with", "audit-extra<2", "--constraint", str(constraint),
         "--build-constraint", str(build_constraint)],
        env=env, capture_output=True, text=True, check=True,
    )
    monkeypatch.setattr(package_update, "package_index", lambda source: index.as_uri())
    prefix = tmp_path / "tools/maibot"
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    root = tmp_path / "instance"
    root.mkdir()
    return {"root": str(root), "prefix": str(prefix), "python": str(python), "uv": uv,
            "current": "1.0.0", "target": "1.1.0"}


def _versions(request: Dict[str, Any]) -> Dict[str, str]:
    result = subprocess.run(
        [request["python"], "-I", "-c",
         "import json; from importlib.metadata import distributions; print(json.dumps({d.name.replace('_','-'):d.version for d in distributions()}))"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_environment_accepts_with_extras_and_constraints(
    installed_tool: Dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_update.sys, "prefix", installed_tool["prefix"])
    monkeypatch.setattr(package_update.sys, "executable", installed_tool["python"])
    monkeypatch.setenv(package_update.PACKAGE_RUNNER_ENV, "1")
    environment = package_update.tool_environment()
    assert environment.prefix == Path(installed_tool["prefix"]).resolve()
    assert environment.python == Path(installed_tool["python"])


def test_update_preserves_with_extras_and_constraints(installed_tool: Dict[str, Any]) -> None:
    receipt = Path(installed_tool["prefix"]) / "uv-receipt.toml"
    original = tomllib.loads(receipt.read_text(encoding="utf-8"))
    result: Dict[str, Any] = {}
    package_update.execute_update(installed_tool, result)
    assert result.get("success") is True, result
    versions = _versions(installed_tool)
    assert versions["maibot"] == "1.1.0"
    assert versions["audit-extra"] == "1.0.0"
    assert versions["audit-feature"] == "2.0.0"
    assert versions["audit-dep"] == "1.0.0"
    updated = tomllib.loads(receipt.read_text(encoding="utf-8"))
    original["tool"]["requirements"][0]["specifier"] = "==1.1.0"
    assert updated == original
    upgrade = subprocess.run(
        [installed_tool["uv"], "--no-config", "--offline", "tool", "upgrade", "maibot", "--no-index",
         "--find-links", str(receipt.parent.parent.parent / "wheels")],
        env={**_tool_env(), "UV_TOOL_DIR": str(receipt.parent.parent)}, capture_output=True, text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr


@pytest.mark.parametrize("target", ["1.2.0", "1.3.0"])
def test_conflict_fails_before_environment_changes(installed_tool: Dict[str, Any], target: str) -> None:
    receipt = Path(installed_tool["prefix"]) / "uv-receipt.toml"
    original = receipt.read_bytes()
    before = _versions(installed_tool)
    installed_tool["target"] = target
    with pytest.raises(package_update.SelfUpdateError, match="No solution found"):
        package_update.execute_update(installed_tool, {})
    assert _versions(installed_tool) == before
    assert receipt.read_bytes() == original


def test_failed_verification_restores_environment_and_receipt(
    installed_tool: Dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = Path(installed_tool["prefix"]) / "uv-receipt.toml"
    original = receipt.read_bytes()
    before = _versions(installed_tool)
    run = package_update._run
    failed = False

    def fail_entrypoint_once(command: List[str], root: Path, env: Dict[str, str]) -> str:
        nonlocal failed
        if command[-1] == "--version" and not failed:
            failed = True
            raise package_update.SelfUpdateError("package_entrypoint_failed", "entrypoint verification failed")
        return run(command, root, env)

    monkeypatch.setattr(package_update, "_run", fail_entrypoint_once)
    result: Dict[str, Any] = {}
    package_update.execute_update(installed_tool, result)
    assert result.get("rolled_back") is True, result
    assert _versions(installed_tool) == before
    assert receipt.read_bytes() == original


def test_override_is_used_during_resolution(installed_tool: Dict[str, Any]) -> None:
    receipt = Path(installed_tool["prefix"]) / "uv-receipt.toml"
    document = tomllib.loads(receipt.read_text(encoding="utf-8"))
    document["tool"]["overrides"] = [{"name": "audit-dep", "specifier": "==1.0.0"}]
    receipt.write_text('"tool" = ' + package_update._toml_value(document["tool"]) + "\n", encoding="utf-8")
    installed_tool["target"] = "1.2.0"
    result: Dict[str, Any] = {}
    package_update.execute_update(installed_tool, result)
    assert result.get("success") is True, result
    assert _versions(installed_tool)["audit-dep"] == "1.0.0"
    assert tomllib.loads(receipt.read_text(encoding="utf-8"))["tool"]["overrides"] == document["tool"]["overrides"]

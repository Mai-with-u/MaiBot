from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import shutil
import subprocess
import sys
import venv

import pytest

from src.plugin_runtime.dependency_pipeline import CombinedPackageRequirement, PluginDependencyPipeline
from src.plugin_runtime.runner.plugin_loader import PluginLoader
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, PluginManifest


@pytest.fixture(autouse=True)
def host_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fake-host"\nversion = "1.0.0"\ndependencies = []\n', encoding="utf-8"
    )


def _manifest(plugin_id: str, requirements: Dict[str, str]) -> PluginManifest:
    return PluginManifest.model_validate(
        {
            "manifest_version": 2,
            "id": plugin_id,
            "version": "1.0.0",
            "name": plugin_id,
            "description": "依赖安全测试插件",
            "author": {"name": "MaiBot", "url": "https://example.com"},
            "license": "GPL-v3.0-or-later",
            "urls": {"repository": "https://example.com/repository"},
            "host_application": {"min_version": "1.0.0", "max_version": "9.9.9"},
            "sdk": {"min_version": "2.0.0", "max_version": "9.9.9"},
            "dependencies": [
                {"type": "python_package", "name": name, "version_spec": spec}
                for name, spec in requirements.items()
            ],
            "capabilities": [],
            "i18n": {"default_locale": "zh-CN", "supported_locales": ["zh-CN"]},
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled_value", ["false", '"off"', "0"])
async def test_disabled_plugin_is_skipped_before_manifest_and_dependency_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled_value: str
) -> None:
    plugin_root = tmp_path / "plugins"
    disabled_dir = plugin_root / "disabled"
    enabled_dir = plugin_root / "enabled"
    disabled_dir.mkdir(parents=True)
    enabled_dir.mkdir()
    (disabled_dir / "config.toml").write_text(f"[plugin]\nenabled = {enabled_value}\n", encoding="utf-8")
    # 无法解析的清单也不应被读取，更不应参与冲突检测或依赖安装。
    (disabled_dir / "_manifest.json").write_text("invalid manifest", encoding="utf-8")
    (disabled_dir / "plugin.py").write_text('raise RuntimeError("must not import")\n', encoding="utf-8")
    (enabled_dir / "_manifest.json").write_text(
        _manifest("test.enabled", {"missing-package": ">=1"}).model_dump_json(), encoding="utf-8"
    )
    (enabled_dir / "plugin.py").write_text("", encoding="utf-8")
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    original_load = pipeline._manifest_validator.load_from_plugin_path

    def load_manifest(path: Path, require_entrypoint: bool = True) -> Optional[PluginManifest]:
        assert path != disabled_dir
        return original_load(path, require_entrypoint=require_entrypoint)

    monkeypatch.setattr(pipeline._manifest_validator, "load_from_plugin_path", load_manifest)
    monkeypatch.setattr(ManifestValidator, "get_installed_package_version", lambda self, name: None)
    installer = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(pipeline, "_install_requirements", installer)

    result = await pipeline.execute([plugin_root])

    assert result.blocked_plugin_reasons == {}
    installer.assert_awaited_once()
    assert [item.requirement_text for item in result.install_requirements] == ["missing-package>=1"]
    assert result.install_requirements[0].plugin_ids == ("test.enabled",)


@pytest.mark.asyncio
@pytest.mark.parametrize("installed_version, requirement", [("13.0.0", ">=11,<13"), ("10.0.0", ">=11")])
@pytest.mark.parametrize("loaded", [False, True])
async def test_installed_version_conflict_skips_install_without_blocking_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed_version: str, requirement: str, loaded: bool
) -> None:
    # Pillow 的发行包名与导入名不同；无论 PIL 是否已导入，都禁止热替换。
    if loaded:
        monkeypatch.setitem(sys.modules, "PIL", Mock())
    else:
        monkeypatch.delitem(sys.modules, "PIL", raising=False)
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    manifests = {"test.incompatible": _manifest("test.incompatible", {"Pillow": requirement})}
    monkeypatch.setattr(pipeline, "_collect_manifests", lambda dirs: manifests)
    monkeypatch.setattr(ManifestValidator, "get_installed_package_version", lambda self, name: installed_version)
    installer = AsyncMock()
    monkeypatch.setattr(pipeline, "_install_requirements", installer)

    result = await pipeline.execute([])

    installer.assert_not_called()
    assert not result.environment_changed
    assert result.install_requirements == ()
    assert result.blocked_plugin_reasons == {}


def test_installed_package_conflicts_do_not_block_plugins_or_their_missing_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    manifests = {
        "test.incompatible": _manifest("test.incompatible", {"Pillow": "<13", "another-package": ">=1"}),
        "test.compatible": _manifest("test.compatible", {"pillow": ">=13", "new-package": ">=1"}),
    }
    monkeypatch.setattr(pipeline, "_collect_manifests", lambda dirs: manifests)
    monkeypatch.setattr(
        ManifestValidator, "get_installed_package_version", lambda self, name: "13.0.0" if name.lower() == "pillow" else None
    )

    plan = pipeline.build_plan([])

    assert plan.blocked_plugin_reasons == {}
    assert [requirement.requirement_text for requirement in plan.install_requirements] == [
        "another-package>=1", "new-package>=1"
    ]
    assert plan.install_requirements[0].plugin_ids == ("test.incompatible",)
    assert plan.install_requirements[1].plugin_ids == ("test.compatible",)


@pytest.mark.asyncio
async def test_install_failure_reports_error_without_blocking_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    manifests = {"test.plugin": _manifest("test.plugin", {"new-package": ">=1"})}
    monkeypatch.setattr(pipeline, "_collect_manifests", lambda dirs: manifests)
    monkeypatch.setattr(ManifestValidator, "get_installed_package_version", lambda self, name: None)
    monkeypatch.setattr(pipeline, "_install_requirements", AsyncMock(return_value=(False, "resolver conflict")))
    error_log = Mock()
    monkeypatch.setattr("src.plugin_runtime.dependency_pipeline.logger.error", error_log)

    result = await pipeline.execute([], initial_blocked_plugin_reasons={"test.duplicate": "插件 ID 重复"})

    assert result.blocked_plugin_reasons == {"test.duplicate": "插件 ID 重复"}
    assert not result.environment_changed
    assert "resolver conflict" in error_log.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_error", [False, True])
async def test_version_mismatch_reaches_plugin_code_and_reports_actual_errors(
    tmp_path: Path, runtime_error: bool
) -> None:
    # 主程序和插件约束没有交集，但已有 packaging 可用，仍应执行插件入口。
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fake-host"\nversion = "1.0.0"\ndependencies = ["packaging>=24"]\n',
        encoding="utf-8",
    )
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "version_mismatch"
    plugin_dir.mkdir(parents=True)
    manifest = _manifest("test.version-mismatch", {"packaging": "<1"})
    (plugin_dir / "_manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    # 成功路径实际调用已安装包；失败路径模拟插件使用不兼容 API 时暴露原始异常。
    code = (
        'raise RuntimeError("actual dependency API incompatible")\n'
        if runtime_error
        else 'from packaging.version import Version\ndef create_plugin():\n    return Version("1.0")\n'
    )
    (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    result = await pipeline.execute([plugin_root])
    assert result.install_requirements == ()
    assert result.blocked_plugin_reasons == {}

    loader = PluginLoader(host_version="1.0.0")
    loader.set_blocked_plugin_reasons(result.blocked_plugin_reasons)
    try:
        loaded = loader.discover_and_load([str(plugin_root)])
        if runtime_error:
            assert loaded == []
            assert "actual dependency API incompatible" in loader.failed_plugins[manifest.id]
        else:
            assert [plugin.plugin_id for plugin in loaded] == [manifest.id]
            assert loader.failed_plugins == {}
    finally:
        loader.purge_plugin_modules(manifest.id, str(plugin_dir))


def test_installed_constraints_use_distribution_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 用真实 dist-info 读取元数据，避免把 PIL 这样的导入名误当作约束中的包名。
    for name, version in [("Pillow", "13.0.0"), ("host_lib", "1.0+local")]:
        dist_info = tmp_path / f"{name}-{version}.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    distributions = list(importlib_metadata.distributions(path=[str(tmp_path)]))
    monkeypatch.setattr(importlib_metadata, "distributions", lambda: distributions)

    assert PluginDependencyPipeline._load_installed_package_constraints() == ("host-lib==1.0+local", "pillow==13.0.0")


@pytest.mark.asyncio
async def test_constraint_snapshot_failure_cancels_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    snapshot = Mock(side_effect=ValueError("broken metadata"))
    installer = Mock()
    monkeypatch.setattr(pipeline, "_load_installed_package_constraints", snapshot)
    monkeypatch.setattr(subprocess, "run", installer)

    succeeded, error = await pipeline._install_requirements(
        [CombinedPackageRequirement("new-package", ("test.plugin",), "new-package", "")]
    )

    assert not succeeded
    assert "broken metadata" in error
    installer.assert_not_called()


def _wheel(directory: Path, name: str, version: str, dependency: Optional[str] = None) -> Path:
    """构造离线测试 wheel；不从网络安装任何包。"""
    wheel_path = directory / f"{name}-{version}-py3-none-any.whl"
    dist_info = f"{name}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if dependency:
        metadata += f"Requires-Dist: {dependency}\n"
    files = {
        f"{name}/__init__.py": f'__version__ = "{version}"\n',
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{dist_info}/RECORD"] = "".join(f"{path},,\n" for path in [*files, f"{dist_info}/RECORD"])
    with ZipFile(wheel_path, "w") as wheel:
        for path, content in files.items():
            wheel.writestr(path, content)
    return wheel_path


@pytest.mark.asyncio
@pytest.mark.parametrize("specifier, expected_success", [("<13", False), (">=13", True)])
async def test_transitive_dependency_preserves_installed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, specifier: str, expected_success: bool
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("离线解析集成测试需要 uv")

    environment = tmp_path / "env"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    host_wheel = _wheel(tmp_path, "safety_host_lib", "13.0.0")
    _wheel(tmp_path, "safety_host_lib", "12.0.0")
    _wheel(tmp_path, "safety_plugin_dep", "1.0.0", f"safety-host-lib{specifier}")
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-config", "--offline", "--no-index", str(host_wheel)],
        check=True,
        capture_output=True,
    )
    pipeline = PluginDependencyPipeline(project_root=tmp_path)
    monkeypatch.setattr(pipeline, "_load_installed_package_constraints", lambda: ("safety-host-lib==13.0.0",))
    build_command = pipeline._build_install_command

    def offline_command(requirements: List[str], index: Optional[str], constraints: Optional[Path]) -> List[str]:
        command = build_command(requirements, index, constraints)
        command[command.index("--python") + 1] = str(python)
        return [*command, "--no-config", "--offline", "--no-index", "--find-links", str(tmp_path)]

    monkeypatch.setattr(pipeline, "_build_install_command", offline_command)
    succeeded, error = await pipeline._install_requirements(
        [CombinedPackageRequirement("safety-plugin-dep", ("test.plugin",), "safety-plugin-dep", "")]
    )

    assert succeeded is expected_success
    if not expected_success:
        assert "safety-host-lib" in error
    # 实际执行解析器后检查版本和源码，证明间接降级没有进入卸载阶段。
    check = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.util, safety_host_lib; print(safety_host_lib.__version__); "
            "print(importlib.util.find_spec('safety_plugin_dep') is not None)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert check.stdout.splitlines() == ["13.0.0", str(expected_success)]

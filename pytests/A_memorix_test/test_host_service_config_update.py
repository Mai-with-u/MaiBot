from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock

import asyncio
import json

import pytest
import tomlkit

from src.A_memorix.host_service import AMemorixHostService, _backup_config_file
from src.config.official_configs import AMemorixConfig


class _FakeConfigManager:
    def __init__(self, reload_results: List[bool]) -> None:
        self.reload_results = list(reload_results)
        self.reload_calls: List[Optional[Sequence[str]]] = []

    async def reload_config(self, changed_scopes: Optional[Sequence[str]] = None) -> bool:
        self.reload_calls.append(changed_scopes)
        if not self.reload_results:
            raise AssertionError("reload_config 调用次数超出预期")
        return self.reload_results.pop(0)


def _write_initial_config(path: Path) -> bytes:
    content = (
        '# 保留主配置注释\n'
        '[inner]\n'
        'version = "8.14.33"\n'
        '\n'
        '[a_memorix.memory]\n'
        'half_life_hours = 24.0\n'
        'prune_threshold = 0.1\n'
        'revive_threshold = 0.15\n'
        '\n'
        '[a_memorix.web.import]\n'
        'enabled = true\n'
    ).encode("utf-8")
    path.write_bytes(content)
    return content


def _prepare_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reload_results: List[bool],
) -> Tuple[AMemorixHostService, Path, _FakeConfigManager, bytes]:
    config_path = tmp_path / "bot_config.toml"
    original_content = _write_initial_config(config_path)
    config_manager = _FakeConfigManager(reload_results)
    service = AMemorixHostService()
    monkeypatch.setattr(service, "reload", AsyncMock())
    monkeypatch.setattr("src.A_memorix.host_service._get_bot_config_path", lambda: config_path)
    monkeypatch.setattr("src.A_memorix.host_service._get_config_manager", lambda: config_manager)
    return service, config_path, config_manager, original_content


@pytest.mark.asyncio
async def test_structured_config_validation_happens_before_backup_and_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [True])

    with pytest.raises(ValueError, match="half_life_hours"):
        await service.update_config({"memory": {"half_life_hours": 0}})

    assert config_path.read_bytes() == original_content
    assert config_manager.reload_calls == []
    assert list(tmp_path.glob("bot_config.toml.backup.*")) == []


@pytest.mark.asyncio
async def test_raw_config_validation_happens_before_backup_and_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [True])

    with pytest.raises(ValueError, match="revive_threshold"):
        await service.update_raw_config(
            "[a_memorix.memory]\nprune_threshold = 0.2\nrevive_threshold = 0.15\n"
        )

    assert config_path.read_bytes() == original_content
    assert config_manager.reload_calls == []
    assert list(tmp_path.glob("bot_config.toml.backup.*")) == []


@pytest.mark.asyncio
async def test_config_reload_failure_restores_original_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [False, True])

    with pytest.raises(RuntimeError, match="已恢复写入前的配置"):
        await service.update_config({"plugin": {"enabled": False}})

    assert config_path.read_bytes() == original_content
    assert config_manager.reload_calls == [("bot",), ("bot",)]
    service.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_valid_partial_config_update_preserves_other_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, _ = _prepare_service(monkeypatch, tmp_path, [True])

    result = await service.update_config({"plugin": {"enabled": False}})

    saved = tomlkit.loads(config_path.read_text(encoding="utf-8"))
    assert result["success"] is True
    assert saved["inner"]["version"] == "8.14.33"
    assert saved["a_memorix"]["plugin"]["enabled"] is False
    assert saved["a_memorix"]["memory"]["half_life_hours"] == 24.0
    assert saved["a_memorix"]["web"]["import"]["enabled"] is True
    assert config_manager.reload_calls == [("bot",)]
    service.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_raw_config_update_replaces_a_memorix_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, _ = _prepare_service(monkeypatch, tmp_path, [True])

    result = await service.update_raw_config("[a_memorix.plugin]\nenabled = false\n")

    saved = tomlkit.loads(config_path.read_text(encoding="utf-8"))
    assert result["success"] is True
    assert saved["inner"]["version"] == "8.14.33"
    assert saved["a_memorix"]["plugin"]["enabled"] is False
    assert "memory" not in saved["a_memorix"]
    assert "web" not in saved["a_memorix"]
    assert config_manager.reload_calls == [("bot",)]
    service.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_host_reload_failure_restores_original_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [True, True])
    monkeypatch.setattr(service, "reload", AsyncMock(side_effect=[RuntimeError("reload failed"), None]))

    with pytest.raises(RuntimeError, match="已恢复写入前的配置"):
        await service.update_config({"plugin": {"enabled": False}})

    assert config_path.read_bytes() == original_content
    assert config_manager.reload_calls == [("bot",), ("bot",)]
    assert service.reload.await_count == 2


@pytest.mark.asyncio
async def test_external_config_overwrite_is_reported_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [])

    async def overwrite_during_reload(changed_scopes: Optional[Sequence[str]] = None) -> bool:
        config_manager.reload_calls.append(changed_scopes)
        config_path.write_bytes(original_content)
        return True

    monkeypatch.setattr(config_manager, "reload_config", overwrite_during_reload)

    with pytest.raises(RuntimeError, match="被其他写入覆盖"):
        await service.update_config({"plugin": {"enabled": False}})

    assert config_path.read_bytes() == original_content
    assert config_manager.reload_calls == [("bot",), ("bot",)]
    service.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_failure_does_not_overwrite_external_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, config_path, config_manager, original_content = _prepare_service(monkeypatch, tmp_path, [])
    external_content = original_content.replace(b"half_life_hours = 24.0", b"half_life_hours = 48.0")

    async def overwrite_then_reload(changed_scopes: Optional[Sequence[str]] = None) -> bool:
        config_manager.reload_calls.append(changed_scopes)
        if len(config_manager.reload_calls) == 1:
            config_path.write_bytes(external_content)
            return False
        return True

    monkeypatch.setattr(config_manager, "reload_config", overwrite_then_reload)

    with pytest.raises(RuntimeError, match="检测到其他写入"):
        await service.update_config({"plugin": {"enabled": False}})

    assert config_path.read_bytes() == external_content
    assert config_manager.reload_calls == [("bot",), ("bot",)]
    service.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_callback_suppression_is_scoped_to_current_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, _, config_manager, _ = _prepare_service(monkeypatch, tmp_path, [])
    reload_started = asyncio.Event()
    allow_reload_to_finish = asyncio.Event()

    async def blocking_reload(changed_scopes: Optional[Sequence[str]] = None) -> bool:
        config_manager.reload_calls.append(changed_scopes)
        await service.on_config_reload(changed_scopes)
        reload_started.set()
        await allow_reload_to_finish.wait()
        return True

    monkeypatch.setattr(config_manager, "reload_config", blocking_reload)
    reload_task = asyncio.create_task(service._reload_config_manager(config_manager))
    await reload_started.wait()

    await service.on_config_reload(("bot",))
    allow_reload_to_finish.set()

    assert await reload_task is True
    service.reload.assert_awaited_once()


def test_config_backups_are_unique_and_keep_their_original_content(tmp_path: Path) -> None:
    config_path = tmp_path / "bot_config.toml"
    config_path.write_bytes(b"first")
    first_backup = _backup_config_file(config_path)
    config_path.write_bytes(b"second")
    second_backup = _backup_config_file(config_path)

    assert first_backup is not None
    assert second_backup is not None
    assert first_backup != second_backup
    assert first_backup.read_bytes() == b"first"
    assert second_backup.read_bytes() == b"second"


def test_atomic_replace_does_not_commit_when_permission_update_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bot_config.toml"
    config_path.write_bytes(b"original")

    def fail_chmod(path: Path, mode: int) -> None:
        raise PermissionError("chmod failed")

    monkeypatch.setattr("src.A_memorix.host_service.os.chmod", fail_chmod)

    with pytest.raises(PermissionError, match="chmod failed"):
        AMemorixHostService._replace_config_file(config_path, b"candidate")

    assert config_path.read_bytes() == b"original"
    assert list(tmp_path.glob(".bot_config.toml.*.tmp")) == []


def test_static_config_schema_uses_source_attempt_budget_contract() -> None:
    project_root = Path(__file__).resolve().parents[2]
    schema = json.loads((project_root / "src" / "A_memorix" / "config_schema.json").read_text(encoding="utf-8"))
    field = schema["sections"]["episode"]["fields"]["source_max_retry"]

    assert field["min"] == 1
    assert field["label"] == "来源任务最大尝试次数"
    assert "包含首次尝试" in field["description"]


def test_config_contract_rejects_crossed_threshold_ranges() -> None:
    with pytest.raises(ValueError, match="min_threshold 必须小于 max_threshold"):
        AMemorixConfig(threshold={"min_threshold": 0.9, "max_threshold": 0.5})

    with pytest.raises(ValueError, match="revive_threshold 必须大于 prune_threshold"):
        AMemorixConfig(memory={"prune_threshold": 0.4, "revive_threshold": 0.2})


def test_static_memory_schema_matches_exclusive_config_bounds() -> None:
    project_root = Path(__file__).resolve().parents[2]
    schema = json.loads((project_root / "src" / "A_memorix" / "config_schema.json").read_text(encoding="utf-8"))
    fields = schema["sections"]["memory"]["fields"]

    assert fields["prune_threshold"]["min"] == 0.01
    assert fields["prune_threshold"]["max"] == 0.99
    assert fields["revive_threshold"]["min"] == 0.01
    assert fields["revive_threshold"]["max"] == 1

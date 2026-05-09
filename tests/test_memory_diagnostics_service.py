from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import json
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.official_configs import DebugConfig
from src.services import memory_diagnostics_service as diagnostics
from src.services.memory_diagnostics_service import MemoryDiagnosticsTask


class VoiceComponent:
    def __init__(self, binary_data: bytes = b"", forward_components: list[Any] | None = None) -> None:
        self.binary_data = binary_data
        self.forward_components = forward_components or []


class ImageComponent:
    def __init__(self, binary_data: bytes = b"") -> None:
        self.binary_data = binary_data


class EmojiComponent:
    def __init__(self, binary_data: bytes = b"") -> None:
        self.binary_data = binary_data


def _message(*components: Any) -> SimpleNamespace:
    return SimpleNamespace(raw_message=SimpleNamespace(components=list(components)))


def _set_debug_config(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    for name, value in values.items():
        monkeypatch.setattr(diagnostics.global_config.debug, name, value)


def test_debug_config_exposes_safe_memory_diagnostics_defaults() -> None:
    config = DebugConfig()

    assert config.enable_memory_diagnostics is False
    assert config.memory_diagnostics_interval_seconds == 300
    assert config.memory_diagnostics_binary_scan_message_limit == 5000
    assert config.memory_diagnostics_enable_tracemalloc is False
    assert config.memory_diagnostics_jsonl_max_total_size_mb == 50


def test_estimate_messages_binary_counts_direct_nested_and_cyclic_components() -> None:
    voice = VoiceComponent(b"a" * 10)
    image = ImageComponent(b"b" * 20)
    emoji = EmojiComponent(b"c" * 5)
    voice.forward_components.append(SimpleNamespace(content=[image, emoji, voice]))

    summary = diagnostics._estimate_messages_binary([_message(voice)])

    assert summary["binary_bytes"] == 35
    assert summary["voice_binary_bytes"] == 10
    assert summary["image_binary_bytes"] == 20
    assert summary["emoji_binary_bytes"] == 5
    assert summary["component_counts"]["VoiceComponent"] == 1
    assert summary["component_counts"]["ImageComponent"] == 1
    assert summary["component_counts"]["EmojiComponent"] == 1
    assert summary["component_counts"]["ForwardCycleSkipped"] == 1


def test_plan_binary_scan_counts_keeps_fairness_and_budget_limit() -> None:
    sessions = [
        {"session_id": "large-a", "message_cache": 100},
        {"session_id": "large-b", "message_cache": 100},
        {"session_id": "small", "message_cache": 1},
        {"session_id": "empty", "message_cache": 0},
    ]

    counts = diagnostics._plan_binary_scan_counts(sessions, scan_limit=5)

    assert sum(counts.values()) == 5
    assert counts["large-a"] <= 100
    assert counts["large-b"] <= 100
    assert counts["small"] == 1
    assert "empty" not in counts
    assert counts["large-a"] > 0
    assert counts["large-b"] > 0


def test_iter_spread_covers_old_and_new_messages_under_budget() -> None:
    messages = [
        _message(VoiceComponent(b"old" * 1024)),
        _message(),
        _message(),
        _message(),
        _message(ImageComponent(b"new" * 1024)),
    ]

    summary = diagnostics._estimate_messages_binary(diagnostics._iter_spread(messages, 2))

    assert summary["voice_binary_bytes"] == 3 * 1024
    assert summary["image_binary_bytes"] == 3 * 1024


def test_collect_heartflow_metrics_reports_runtime_totals_and_truncated_binary_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_debug_config(
        monkeypatch,
        memory_diagnostics_binary_scan_message_limit=2,
        memory_diagnostics_enable_tracemalloc=False,
        memory_diagnostics_interval_seconds=10,
        memory_diagnostics_top_sessions=2,
    )
    runtime = SimpleNamespace(
        session_name="群聊",
        message_cache=[
            _message(VoiceComponent(b"old" * 1024)),
            _message(VoiceComponent(b"new" * 1024)),
            _message(ImageComponent(b"img" * 1024)),
        ],
        history_loop=[],
        _source_messages_by_id={"1": object()},
        _message_received_at_by_id={"1": 1.0},
        _chat_history=[object()],
        _running=True,
        _agent_state="running",
        _last_processed_index=3,
    )
    heartflow_manager = SimpleNamespace(heartflow_chat_list={"session-1": runtime}, _chat_create_locks={"session-1": object()})
    module = SimpleNamespace(heartflow_manager=heartflow_manager)
    monkeypatch.setitem(sys.modules, "src.chat.heart_flow.heartflow_manager", module)

    errors: list[str] = []
    payload = MemoryDiagnosticsTask()._collect_heartflow_metrics(errors)

    assert errors == []
    assert payload["loaded"] is True
    assert payload["runtime_count"] == 1
    assert payload["lock_count"] == 1
    assert payload["totals"]["message_cache"] == 3
    assert payload["totals"]["source_messages"] == 1
    assert payload["totals"]["binary_scanned_messages"] == 2
    assert payload["totals"]["binary_skipped_messages"] == 1
    assert payload["totals"]["binary_lower_bound"] is True
    assert payload["top_binary_sessions"][0]["binary_scan_truncated"] is True
    assert payload["top_binary_sessions"][0]["binary_scan_strategy"] == "spread"


def test_collect_heartflow_metrics_uses_runtime_snapshot_for_binary_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    class SnapshotOnlyRuntimeMap(dict[str, Any]):
        def get(self, _key: str, _default: Any = None) -> Any:
            return None

    _set_debug_config(
        monkeypatch,
        memory_diagnostics_binary_scan_message_limit=1,
        memory_diagnostics_enable_tracemalloc=False,
        memory_diagnostics_interval_seconds=10,
        memory_diagnostics_top_sessions=2,
    )
    runtime = SimpleNamespace(
        session_name="群聊",
        message_cache=[_message(VoiceComponent(b"voice"))],
        history_loop=[],
        _source_messages_by_id={},
        _message_received_at_by_id={},
        _chat_history=[],
        _running=True,
        _agent_state="running",
        _last_processed_index=1,
    )
    heartflow_manager = SimpleNamespace(
        heartflow_chat_list=SnapshotOnlyRuntimeMap({"session-1": runtime}),
        _chat_create_locks={},
    )
    module = SimpleNamespace(heartflow_manager=heartflow_manager)
    monkeypatch.setitem(sys.modules, "src.chat.heart_flow.heartflow_manager", module)

    errors: list[str] = []
    payload = MemoryDiagnosticsTask()._collect_heartflow_metrics(errors)

    assert errors == []
    assert payload["top_binary_sessions"][0]["voice_binary_bytes"] == len(b"voice")
    assert payload["top_binary_sessions"][0]["binary_scan_skipped"] is False


@pytest.mark.asyncio
async def test_collect_snapshot_returns_core_sections_without_loading_runtime_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_debug_config(
        monkeypatch,
        memory_diagnostics_enable_tracemalloc=False,
        memory_diagnostics_interval_seconds=10,
        memory_diagnostics_binary_scan_message_limit=0,
    )
    for module_name in (
        "src.A_memorix.host_service",
        "src.chat.heart_flow.heartflow_manager",
        "src.chat.message_receive.chat_manager",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    payload = MemoryDiagnosticsTask()._collect_snapshot()

    assert payload["process"]["available"] in {True, False}
    assert "python" in payload
    assert payload["asyncio"]["task_count"] >= 1
    assert payload["heartflow"] == {"loaded": False}
    assert payload["chat_manager"] == {"loaded": False}
    assert payload["a_memorix"]["kernel_loaded"] is False
    assert "tracemalloc" not in payload


@pytest.mark.asyncio
async def test_run_writes_jsonl_snapshot_and_keeps_collector_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "memory_diagnostics.jsonl"
    _set_debug_config(
        monkeypatch,
        memory_diagnostics_enable_tracemalloc=False,
        memory_diagnostics_interval_seconds=10,
        memory_diagnostics_jsonl_path=str(output_path),
        memory_diagnostics_jsonl_max_total_size_mb=1,
    )

    task = MemoryDiagnosticsTask()
    monkeypatch.setattr(task, "_collect_snapshot", lambda: {"process": {"rss_mb": 1}, "heartflow": {"totals": {}}})
    monkeypatch.setattr(task, "_log_summary", lambda _payload: None)

    await task.run()

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["process"]["rss_mb"] == 1
    assert payload["collector"]["tracemalloc_enabled"] is False
    assert payload["collector"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_run_isolates_collect_and_write_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_debug_config(
        monkeypatch,
        memory_diagnostics_enable_tracemalloc=False,
        memory_diagnostics_interval_seconds=10,
        memory_diagnostics_jsonl_path=str(tmp_path / "memory_diagnostics.jsonl"),
    )
    task = MemoryDiagnosticsTask()
    monkeypatch.setattr(task, "_collect_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("collect failed")))

    await task.run()

    task = MemoryDiagnosticsTask()
    monkeypatch.setattr(task, "_collect_snapshot", lambda: {"process": {}, "heartflow": {"totals": {}}})
    monkeypatch.setattr(task, "_write_snapshot", lambda _payload: (_ for _ in ()).throw(RuntimeError("write failed")))
    monkeypatch.setattr(task, "_log_summary", lambda _payload: None)

    await task.run()


def test_rotate_snapshot_file_keeps_latest_rotated_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_debug_config(monkeypatch, memory_diagnostics_jsonl_max_total_size_mb=1)
    output_path = tmp_path / "memory_diagnostics.jsonl"
    output_path.write_bytes(b"x" * (diagnostics.BYTES_PER_MB + 1))
    for index in range(7):
        rotated_path = tmp_path / f"memory_diagnostics.20260509-15000{index}.jsonl"
        rotated_path.write_text(str(index), encoding="utf-8")
        os.utime(rotated_path, (index, index))

    diagnostics.MemoryDiagnosticsTask._rotate_snapshot_file_if_needed(output_path)

    rotated_paths = sorted(tmp_path.glob("memory_diagnostics.*.jsonl"))
    assert not output_path.exists()
    assert len(rotated_paths) == diagnostics.DEFAULT_JSONL_ROTATED_FILE_KEEP
    assert all(path.is_file() for path in rotated_paths)

import asyncio
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from src.A_memorix.host_service import AMemorixHostService


class _FakeKernel:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.admin_calls: list[tuple[str, dict[str, Any]]] = []

    async def search_memory(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {"summary": "", "hits": []}

    async def memory_correction_admin(self, *, action: str, **kwargs) -> dict[str, Any]:
        self.admin_calls.append((f"correction:{action}", kwargs))
        return {"success": True, "component": "memory_correction_admin", "action": action}

    async def memory_fuzzy_modify_admin(self, *, action: str, **kwargs) -> dict[str, Any]:
        self.admin_calls.append((f"legacy:{action}", kwargs))
        return {"success": True, "component": "memory_fuzzy_modify_admin", "action": action}

    async def memory_runtime_admin(self, *, action: str, **kwargs) -> dict[str, Any]:
        self.admin_calls.append((f"runtime:{action}", kwargs))
        return {"success": True, "component": "memory_runtime_admin", "action": action}


class _ReplayKernel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def ingest_summary(self, **kwargs) -> dict[str, Any]:
        self.calls.append(("ingest_summary", str(kwargs.get("external_id", ""))))
        return {"success": True, "stored_ids": [kwargs.get("external_id")]}

    async def ingest_text(self, **kwargs) -> dict[str, Any]:
        self.calls.append(("ingest_text", str(kwargs.get("external_id", ""))))
        return {"success": True, "stored_ids": [kwargs.get("external_id")]}


class _FileBackedReplayKernel:
    def __init__(self, business_path: Path) -> None:
        self.business_path = business_path
        self.calls: list[str] = []

    async def ingest_text(self, **kwargs) -> dict[str, Any]:
        external_id = str(kwargs.get("external_id", "") or "")
        self.calls.append(external_id)
        self.business_path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if self.business_path.exists():
            existing = {line.strip() for line in self.business_path.read_text(encoding="utf-8").splitlines()}
        if external_id not in existing:
            with self.business_path.open("a", encoding="utf-8") as handle:
                handle.write(external_id + "\n")
        return {"success": True, "stored_ids": [external_id]}


def _ready_service(fake_kernel: _FakeKernel) -> AMemorixHostService:
    service = AMemorixHostService()
    service._kernel = fake_kernel  # type: ignore[assignment]
    service._runtime_state = "ready"  # type: ignore[attr-defined]
    return service


def _wait_for_marker_or_fail(proc: subprocess.Popen[str], marker_path: Path) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=5)
            raise AssertionError(f"启动队列子进程提前退出: stdout={stdout} stderr={stderr}")
        time.sleep(0.05)
    proc.kill()
    stdout, stderr = proc.communicate(timeout=10)
    raise AssertionError(f"启动队列子进程未进入待 kill 断点: stdout={stdout} stderr={stderr}")


@pytest.mark.asyncio
async def test_host_service_passes_shared_memory_session_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_kernel = _FakeKernel()
    service = _ready_service(fake_kernel)

    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"global_memory_sharing_enabled": False})
    monkeypatch.setattr(
        "src.A_memorix.host_service.AMemorixConfigUtils.get_shared_memory_session_ids",
        lambda chat_id: {"session-a", "session-b"} if chat_id == "session-a" else {chat_id},
    )

    await service.invoke(
        "search_memory",
        {
            "query": "围巾",
            "limit": 3,
            "mode": "search",
            "chat_id": "session-a",
            "respect_filter": True,
        },
    )

    assert len(fake_kernel.requests) == 1
    request = fake_kernel.requests[0]
    assert request.chat_id == "session-a"
    assert set(request.shared_chat_ids) == {"session-a", "session-b"}


@pytest.mark.asyncio
async def test_host_service_global_memory_sharing_uses_global_search_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeKernel()
    service = _ready_service(fake_kernel)

    def fail_shared_group_lookup(chat_id: str) -> set[str]:
        raise AssertionError(f"全局共享开启时不应解析共享记忆组: {chat_id}")

    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"global_memory_sharing_enabled": True})
    monkeypatch.setattr(
        "src.A_memorix.host_service.AMemorixConfigUtils.get_shared_memory_session_ids",
        fail_shared_group_lookup,
    )

    await service.invoke(
        "search_memory",
        {
            "query": "围巾",
            "limit": 3,
            "mode": "search",
            "chat_id": "session-a",
            "group_id": "group-a",
            "user_id": "user-a",
            "respect_filter": True,
        },
    )

    assert len(fake_kernel.requests) == 1
    request = fake_kernel.requests[0]
    assert request.chat_id == ""
    assert tuple(request.shared_chat_ids) == ()
    assert request.group_id == "group-a"
    assert request.user_id == "user-a"


@pytest.mark.asyncio
async def test_host_service_dispatches_memory_correction_and_legacy_fuzzy_modify(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_kernel = _FakeKernel()
    service = _ready_service(fake_kernel)

    monkeypatch.setattr(service, "is_enabled", lambda: True)

    correction = await service.invoke("memory_correction_admin", {"action": "get", "plan_id": "corr-1"})
    legacy = await service.invoke("memory_fuzzy_modify_admin", {"action": "get", "plan_id": "corr-2"})

    assert correction == {"success": True, "component": "memory_correction_admin", "action": "get"}
    assert legacy == {"success": True, "component": "memory_fuzzy_modify_admin", "action": "get"}
    assert fake_kernel.admin_calls == [
        ("correction:get", {"plan_id": "corr-1"}),
        ("legacy:get", {"plan_id": "corr-2"}),
    ]


@pytest.mark.asyncio
async def test_host_service_start_returns_before_background_startup_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SlowStartupService(AMemorixHostService):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def _startup_kernel_task(self) -> None:
            self.started.set()
            await self.release.wait()

    service = _SlowStartupService()
    monkeypatch.setattr(service, "is_enabled", lambda: True)

    await service.start()

    assert service._startup_task is not None  # type: ignore[attr-defined]
    assert not service._startup_task.done()  # type: ignore[attr-defined]
    assert service._runtime_state == "starting"  # type: ignore[attr-defined]

    service.release.set()
    await service._startup_task  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_host_service_initializing_search_empty_and_ingest_queued(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = AMemorixHostService()
    service._runtime_state = "migrating"  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})

    search = await service.invoke("search_memory", {"query": "围巾"})
    queued = await service.invoke(
        "ingest_text",
        {
            "external_id": "queued-1",
            "source_type": "test",
            "text": "初始化期间的写入",
            "chat_id": "chat-1",
        },
    )

    assert search["success"] is True
    assert search["initializing"] is True
    assert search["hits"] == []
    assert queued["success"] is True
    assert queued["queued"] is True

    queue_path = tmp_path / "startup_write_queue.jsonl"
    records = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["component_name"] == "ingest_text"
    assert records[0]["payload"]["external_id"] == "queued-1"


@pytest.mark.asyncio
async def test_host_service_failed_rejects_new_writes_and_admin_stays_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = AMemorixHostService()
    service._runtime_state = "failed"  # type: ignore[attr-defined]
    service._startup_error = "broken pickle"  # type: ignore[attr-defined]
    service._startup_error_stage = "startup_migration"  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})

    write = await service.invoke("ingest_text", {"external_id": "blocked-1", "text": "不会入队"})
    admin = await service.invoke("memory_runtime_admin", {"action": "get_config"})

    assert write["success"] is False
    assert write["queued"] is False
    assert write["reason"] == "a_memorix_initialization_failed"
    assert not (tmp_path / "startup_write_queue.jsonl").exists()
    assert admin["success"] is True
    assert admin["startup_state"] == "failed"
    assert admin["error_stage"] == "startup_migration"


@pytest.mark.asyncio
async def test_host_service_replays_startup_queue_in_created_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = AMemorixHostService()
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})
    queue_path = tmp_path / "startup_write_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False)
            for record in (
                {
                    "record_id": "late",
                    "component_name": "ingest_text",
                    "payload": {"external_id": "late"},
                    "created_at": 2.0,
                },
                {
                    "record_id": "early",
                    "component_name": "ingest_summary",
                    "payload": {"external_id": "early"},
                    "created_at": 1.0,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    kernel = _ReplayKernel()
    await service._replay_startup_write_queue(kernel)  # type: ignore[arg-type]

    assert kernel.calls == [("ingest_summary", "early"), ("ingest_text", "late")]
    done_rows = [
        json.loads(line)
        for line in (tmp_path / "startup_write_queue.done.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["record_id"] for row in done_rows] == ["early", "late"]


@pytest.mark.asyncio
async def test_host_service_queue_append_survives_previous_killed_partial_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "queue_data"
    marker_path = tmp_path / "partial_queue_write.marker"
    child_code = textwrap.dedent(
        """
        import asyncio
        import sys
        import time
        from pathlib import Path

        from src.A_memorix.host_service import AMemorixHostService

        async def main():
            service = AMemorixHostService()
            service._runtime_state = "migrating"
            service.is_enabled = lambda: True
            service._read_config = lambda: {"storage": {"data_dir": sys.argv[1]}}

            async def partial_append(path, payload):
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write('{"record_id": "partial"')
                    handle.flush()
                Path(sys.argv[2]).write_text("partial", encoding="utf-8")
                time.sleep(60)

            service._append_jsonl = partial_append
            await service.invoke(
                "ingest_text",
                {
                    "external_id": "lost-during-partial-write",
                    "source_type": "test",
                    "text": "半行写入被 kill",
                    "chat_id": "chat-1",
                },
            )

        asyncio.run(main())
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir), str(marker_path)],
        cwd=Path.cwd(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker_or_fail(proc, marker_path)
        proc.kill()
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    service = AMemorixHostService()
    service._runtime_state = "migrating"  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(data_dir)}})

    queued = await service.invoke(
        "ingest_text",
        {
            "external_id": "survivor-after-partial-write",
            "source_type": "test",
            "text": "半行之后的新写入",
            "chat_id": "chat-1",
        },
    )

    assert queued["queued"] is True
    pending = service._startup_queue_pending_records()  # type: ignore[attr-defined]
    assert len(pending) == 1
    assert pending[0]["payload"]["external_id"] == "survivor-after-partial-write"


@pytest.mark.asyncio
async def test_host_service_replay_recovers_after_process_kill_before_done_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "queue_data"
    business_path = tmp_path / "business_writes.txt"
    marker_path = tmp_path / "business_written_before_done.marker"
    queue_path = data_dir / "startup_write_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps(
            {
                "record_id": "record-replay",
                "component_name": "ingest_text",
                "payload": {
                    "external_id": "queued-replay",
                    "source_type": "test",
                    "text": "业务已写入但 done 未落盘",
                    "chat_id": "chat-1",
                },
                "created_at": 1.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    child_code = textwrap.dedent(
        """
        import asyncio
        import sys
        from pathlib import Path

        from src.A_memorix.host_service import AMemorixHostService

        class FileKernel:
            async def ingest_text(self, **kwargs):
                external_id = str(kwargs.get("external_id", "") or "")
                business_path = Path(sys.argv[2])
                business_path.parent.mkdir(parents=True, exist_ok=True)
                existing = set()
                if business_path.exists():
                    existing = {line.strip() for line in business_path.read_text(encoding="utf-8").splitlines()}
                if external_id not in existing:
                    with business_path.open("a", encoding="utf-8") as handle:
                        handle.write(external_id + "\\n")
                Path(sys.argv[3]).write_text("business-written", encoding="utf-8")
                await asyncio.sleep(60)
                return {"success": True, "stored_ids": [external_id]}

        async def main():
            service = AMemorixHostService()
            service._read_config = lambda: {"storage": {"data_dir": sys.argv[1]}}
            await service._replay_startup_write_queue(FileKernel())

        asyncio.run(main())
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir), str(business_path), str(marker_path)],
        cwd=Path.cwd(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker_or_fail(proc, marker_path)
        proc.kill()
        proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)

    assert not (data_dir / "startup_write_queue.done.jsonl").exists()
    assert business_path.read_text(encoding="utf-8").splitlines() == ["queued-replay"]

    service = AMemorixHostService()
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(data_dir)}})
    kernel = _FileBackedReplayKernel(business_path)
    await service._replay_startup_write_queue(kernel)  # type: ignore[arg-type]

    assert kernel.calls == ["queued-replay"]
    assert business_path.read_text(encoding="utf-8").splitlines() == ["queued-replay"]
    done_rows = [
        json.loads(line)
        for line in (data_dir / "startup_write_queue.done.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{")
    ]
    assert [row["record_id"] for row in done_rows] == ["record-replay"]


@pytest.mark.asyncio
async def test_host_service_replay_ignores_partial_done_marker_and_marks_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = AMemorixHostService()
    business_path = tmp_path / "business_writes.txt"
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})
    (tmp_path / "startup_write_queue.jsonl").write_text(
        json.dumps(
            {
                "record_id": "partial-done-record",
                "component_name": "ingest_text",
                "payload": {"external_id": "partial-done-record"},
                "created_at": 1.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "startup_write_queue.done.jsonl").write_text('{"record_id": "partial-done-record"', encoding="utf-8")

    await service._replay_startup_write_queue(_FileBackedReplayKernel(business_path))  # type: ignore[arg-type]

    assert business_path.read_text(encoding="utf-8").splitlines() == ["partial-done-record"]
    done_rows = [
        json.loads(line)
        for line in (tmp_path / "startup_write_queue.done.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{") and line.strip().endswith("}")
    ]
    assert done_rows[-1]["record_id"] == "partial-done-record"


@pytest.mark.asyncio
async def test_host_service_startup_failure_marks_failed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.A_memorix.core.runtime import sdk_memory_kernel as kernel_module

    class FailingSDKKernel:
        closed = False

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def initialize(self) -> None:
            raise RuntimeError("migration broken")

        def close(self) -> None:
            type(self).closed = True

    service = AMemorixHostService()
    monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})
    monkeypatch.setattr(kernel_module, "SDKMemoryKernel", FailingSDKKernel)

    await service.start()
    assert service._startup_task is not None  # type: ignore[attr-defined]
    await service._startup_task  # type: ignore[attr-defined]

    assert service._runtime_state == "failed"  # type: ignore[attr-defined]
    assert service._startup_error_stage == "startup_migration"  # type: ignore[attr-defined]
    assert "migration broken" in service._startup_error  # type: ignore[attr-defined]
    assert FailingSDKKernel.closed is True

    search = await service.invoke("search_memory", {"query": "围巾"})
    write = await service.invoke("ingest_text", {"external_id": "blocked", "text": "不会入队"})
    assert search["success"] is False
    assert search["reason"] == "a_memorix_initialization_failed"
    assert write["success"] is False
    assert write["queued"] is False
    assert not (tmp_path / "startup_write_queue.jsonl").exists()


@pytest.mark.asyncio
async def test_host_service_stop_cancels_background_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverReadyService(AMemorixHostService):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def _startup_kernel_task(self) -> None:
            self.started.set()
            await self.release.wait()

    service = NeverReadyService()
    monkeypatch.setattr(service, "is_enabled", lambda: True)

    await service.start()
    await service.started.wait()
    await service.stop()

    assert service._startup_task is None  # type: ignore[attr-defined]
    assert service._runtime_state == "stopped"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_host_service_replay_failure_keeps_record_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingReplayKernel:
        async def ingest_text(self, **kwargs) -> dict[str, Any]:
            return {"success": False, "detail": "write blocked"}

    service = AMemorixHostService()
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})
    queue_path = tmp_path / "startup_write_queue.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "record_id": "failed-record",
                "component_name": "ingest_text",
                "payload": {"external_id": "failed-record", "text": "失败保留"},
                "created_at": 1.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    await service._replay_startup_write_queue(FailingReplayKernel())  # type: ignore[arg-type]

    assert not (tmp_path / "startup_write_queue.done.jsonl").exists()
    failed_rows = [
        json.loads(line)
        for line in (tmp_path / "startup_write_queue.failed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert failed_rows[0]["record_id"] == "failed-record"
    assert service._startup_queue_pending_records()[0]["record_id"] == "failed-record"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_host_service_replay_skips_done_invalid_and_unknown_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = AMemorixHostService()
    monkeypatch.setattr(service, "_read_config", lambda: {"storage": {"data_dir": str(tmp_path)}})
    (tmp_path / "startup_write_queue.done.jsonl").write_text(
        json.dumps({"record_id": "already-done"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "startup_write_queue.jsonl").write_text(
        "not-json\n"
        + json.dumps({"record_id": "already-done", "component_name": "ingest_text", "payload": {}, "created_at": 1.0})
        + "\n"
        + json.dumps({"record_id": "unknown", "component_name": "unknown", "payload": {}, "created_at": 2.0})
        + "\n"
        + json.dumps({"record_id": "valid", "component_name": "ingest_text", "payload": {"external_id": "valid"}, "created_at": 3.0})
        + "\n",
        encoding="utf-8",
    )

    kernel = _ReplayKernel()
    await service._replay_startup_write_queue(kernel)  # type: ignore[arg-type]

    assert kernel.calls == [("ingest_text", "valid")]

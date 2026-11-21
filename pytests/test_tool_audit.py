from __future__ import annotations

from pathlib import Path

import json
import threading

from src.common.tool_audit import ToolAuditWriter, sha256_canonical


def _write(writer: ToolAuditWriter, tool: str = "read_file", **overrides: object) -> None:
    payload = {
        "action": "allow",
        "tool": tool,
        "provider_type": "plugin",
        "platform": "qq",
        "session_id": "s1",
        "user_id": "u1",
        "call_id": "call-1",
        "args": {"path": "a.txt"},
        "reason": "",
        "decided_by": "",
    }
    payload.update(overrides)
    writer.record(**payload)  # type: ignore[arg-type]


def _read_entries(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_sha256_canonical_is_key_order_independent() -> None:
    assert sha256_canonical({"b": 2, "a": 1}) == sha256_canonical({"a": 1, "b": 2})
    assert sha256_canonical({"a": 1}) != sha256_canonical({"a": 2})


def test_record_writes_seq_and_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "tool.jsonl"
    writer = ToolAuditWriter(path)
    _write(writer, "read_file")
    _write(writer, "write_file")

    entries = _read_entries(path)
    assert [entry["seq"] for entry in entries] == [1, 2]
    assert entries[0]["prev_hash"] == ""
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    for entry in entries:
        payload = {key: value for key, value in entry.items() if key != "entry_hash"}
        assert sha256_canonical(payload) == entry["entry_hash"]


def test_args_are_hashed_not_stored(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    writer = ToolAuditWriter(path)
    _write(writer, args={"path": "secret.txt", "content": "top-secret"})

    raw = path.read_text(encoding="utf-8")
    assert "secret.txt" not in raw
    assert "top-secret" not in raw
    entry = _read_entries(path)[0]
    assert entry["args_hash"] == sha256_canonical({"path": "secret.txt", "content": "top-secret"})


def test_resume_reads_last_hash_for_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = ToolAuditWriter(path)
    _write(first)
    first_hash = _read_entries(path)[0]["entry_hash"]

    second = ToolAuditWriter(path)
    _write(second)
    entries = _read_entries(path)
    assert entries[1]["prev_hash"] == first_hash


def test_concurrent_records_keep_unique_seq(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    writer = ToolAuditWriter(path)

    def worker() -> None:
        for _ in range(5):
            _write(writer)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    entries = _read_entries(path)
    assert len(entries) == 100
    assert sorted(int(entry["seq"]) for entry in entries) == list(range(1, 101))
    previous = ""
    for entry in entries:
        assert entry["prev_hash"] == previous
        previous = str(entry["entry_hash"])


def test_configure_uses_instance_root(monkeypatch, tmp_path: Path) -> None:
    import src.common.tool_audit as module

    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "_audit_writer", None)
    writer = module.configure_tool_audit("logs/tool-audit.jsonl")
    assert writer.path == tmp_path / "logs" / "tool-audit.jsonl"
    assert module.get_tool_audit_writer() is writer

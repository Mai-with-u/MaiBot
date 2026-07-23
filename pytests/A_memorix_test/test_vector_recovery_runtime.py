from __future__ import annotations

from pathlib import Path
from typing import Any

import json

import numpy as np
import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage import MetadataStore, VectorStore, VectorStoreIntegrityError
from src.A_memorix.core.storage.vector_store import HAS_FAISS


pytestmark = pytest.mark.skipif(not HAS_FAISS, reason="Faiss 未安装")


class _FingerprintEmbedding:
    def __init__(self, fingerprint: dict[str, Any]) -> None:
        self.fingerprint = dict(fingerprint)

    def get_embedding_fingerprint(self, *, dimension: int) -> dict[str, Any]:
        del dimension
        return dict(self.fingerprint)


def _make_corrupt_v1_store(
    data_dir: Path,
    *,
    paragraph_hash: str,
    fingerprint: dict[str, Any],
) -> VectorStoreIntegrityError:
    store = VectorStore(dimension=2, data_dir=data_dir, buffer_size=1)
    store.add(np.asarray([[1.0, 0.0]], dtype=np.float32), [paragraph_hash])
    store.save(embedding_fingerprint=fingerprint)
    metadata_path = data_dir / "vectors_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = 1
    metadata.pop("binary_commit", None)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    with (data_dir / "vectors.bin").open("ab") as vector_file:
        vector_file.write(np.asarray([0.0, 1.0], dtype=np.float16).tobytes())
    with (data_dir / "vectors_ids.bin").open("ab") as id_file:
        id_file.write(np.asarray([VectorStore._generate_id("orphan")], dtype=">i8").tobytes())

    broken = VectorStore(dimension=2, data_dir=data_dir)
    with pytest.raises(VectorStoreIntegrityError) as exc_info:
        broken.load(expected_embedding_fingerprint=fingerprint)
    return exc_info.value


def test_known_v1_corruption_is_quarantined_and_copied_without_embedding(tmp_path: Path) -> None:
    data_dir = tmp_path / "memory"
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "storage": {"data_dir": str(data_dir)},
            "embedding": {"dimension": 2},
            "retrieval": {"vector_pools": {"mode": "dual"}},
        },
    )
    fingerprint = {"hash": "trusted-model"}
    kernel.embedding_manager = _FingerprintEmbedding(fingerprint)
    metadata_store = MetadataStore(data_dir=data_dir / "metadata")
    metadata_store.connect()
    kernel.metadata_store = metadata_store
    paragraph_hash = metadata_store.add_paragraph(content="可信旧段落", source="test")
    error = _make_corrupt_v1_store(
        data_dir / "vectors",
        paragraph_hash=paragraph_hash,
        fingerprint=fingerprint,
    )

    assert kernel._recover_known_vector_failure(error) is True

    journal = json.loads((data_dir / "vector_recovery.json").read_text(encoding="utf-8"))
    quarantine = Path(journal["quarantine_path"])
    assert journal["stage"] == "completed"
    assert quarantine.exists()
    assert not (data_dir / "vectors" / "vectors.bin").exists()
    assert (data_dir / "vectors" / "dual_ready.json").exists()
    assert kernel._legacy_vector_view is not None
    assert kernel._vector_health["trusted_coverage"] == 1.0

    result = kernel._copy_legacy_vectors_once(batch_size=8)

    assert result == {"success": True, "processed": 1, "copied": 1, "done": True}
    assert kernel._legacy_vector_view is None
    assert paragraph_hash in kernel.paragraph_vector_store
    assert "orphan" not in kernel.paragraph_vector_store
    assert kernel._vector_health["state"] == "recovered"
    metadata_store.close()


def test_unknown_vector_failure_keeps_original_files_and_core_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "memory"
    marker = data_dir / "vectors" / "opaque.bin"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"do-not-touch")
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"storage": {"data_dir": str(data_dir)}},
    )
    kernel._set_runtime_capability("metadata", True)
    kernel.vector_store = object()  # type: ignore[assignment]

    kernel._disable_vector_channel(OSError("unknown vector backend failure"))

    assert marker.read_bytes() == b"do-not-touch"
    assert not (data_dir / "vector_quarantine").exists()
    assert kernel._runtime_capabilities["metadata"] is True
    assert kernel._runtime_capabilities["vector_read"] is False
    assert kernel._runtime_capabilities["vector_write"] is False
    assert kernel._vector_health["state"] == "unavailable"
    assert kernel._vector_health["error_code"] == "vector_unclassified_error"

    status = kernel._runtime_capability_status()
    assert status["memory_enabled"] is True
    assert status["degraded"] is True
    assert status["retrieval_ready"] is False
    assert status["retrieval_mode"] == "metadata_only"
    assert status["vector_health"]["reason"] == "unknown vector backend failure"

    def fail_if_vector_metadata_is_read() -> dict[str, Any]:
        raise AssertionError("向量通道隔离后不应再次读取损坏元数据")

    monkeypatch.setattr(kernel, "_vector_rebuild_status", fail_if_vector_metadata_is_read)
    kernel._persist()


@pytest.mark.parametrize(
    "interrupted_stage",
    ["prepared", "quarantined", "metadata_reset", "new_generation_ready", "completed"],
)
def test_vector_recovery_journal_resumes_idempotently_after_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_stage: str,
) -> None:
    data_dir = tmp_path / interrupted_stage
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "storage": {"data_dir": str(data_dir)},
            "embedding": {"dimension": 2},
            "retrieval": {"vector_pools": {"mode": "dual"}},
        },
    )
    fingerprint = {"hash": "trusted-model"}
    kernel.embedding_manager = _FingerprintEmbedding(fingerprint)
    metadata_store = MetadataStore(data_dir=data_dir / "metadata")
    metadata_store.connect()
    kernel.metadata_store = metadata_store
    paragraph_hash = metadata_store.add_paragraph(content="可恢复段落", source="test")
    error = _make_corrupt_v1_store(
        data_dir / "vectors",
        paragraph_hash=paragraph_hash,
        fingerprint=fingerprint,
    )

    recovery_service = kernel._vector_recovery_service
    original_write = recovery_service._write_vector_recovery_journal
    interrupted = False

    def interrupt_after_journal_write(payload: dict[str, Any]) -> None:
        nonlocal interrupted
        original_write(payload)
        if not interrupted and payload.get("stage") == interrupted_stage:
            interrupted = True
            raise OSError(f"injected interruption after {interrupted_stage}")

    monkeypatch.setattr(recovery_service, "_write_vector_recovery_journal", interrupt_after_journal_write)
    with pytest.raises(OSError, match="injected interruption"):
        kernel._recover_known_vector_failure(error)

    monkeypatch.setattr(recovery_service, "_write_vector_recovery_journal", original_write)
    kernel._resume_vector_recovery_if_needed()

    journal = json.loads((data_dir / "vector_recovery.json").read_text(encoding="utf-8"))
    quarantine_dirs = list((data_dir / "vector_quarantine").iterdir())
    assert journal["stage"] == "completed"
    assert len(quarantine_dirs) == 1
    assert quarantine_dirs[0].name == journal["operation_id"]
    assert (data_dir / "vectors" / "dual_ready.json").exists()
    assert kernel._dual_vector_pools_ready is True
    metadata_store.close()

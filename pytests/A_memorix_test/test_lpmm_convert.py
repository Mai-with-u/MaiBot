from pathlib import Path
from types import SimpleNamespace

import json
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage import MetadataStore, VectorStore
from src.A_memorix.core.utils.web_import_manager import ImportTaskManager


REPO_ROOT = Path(__file__).resolve().parents[2]
CONVERT_SCRIPT = REPO_ROOT / "src" / "A_memorix" / "scripts" / "convert_lpmm.py"


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _run_convert(input_dir: Path, output_dir: Path, *, dimension: int = 2) -> subprocess.CompletedProcess[str]:
    data_dir = input_dir.parents[3]
    return subprocess.run(
        [
            sys.executable,
            str(CONVERT_SCRIPT),
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--data-dir",
            str(data_dir),
            "--dim",
            str(dimension),
            "--skip-relation-vector-rebuild",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _conversion_paths(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "a-memorix"
    return (
        data_dir / "imports" / "source" / "lpmm" / "dataset",
        data_dir / "imports" / "converted" / "dataset",
    )


def test_lpmm_converter_rejects_paths_outside_import_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "a-memorix"
    outside_input = tmp_path / "outside"
    outside_input.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(CONVERT_SCRIPT),
            "--input",
            str(outside_input),
            "--output",
            str(data_dir / "imports" / "converted" / "dataset"),
            "--data-dir",
            str(data_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode != 0
    assert "LPMM 输入必须位于导入目录" in f"{result.stdout}\n{result.stderr}"


def _build_verify_manager(tmp_path: Path) -> ImportTaskManager:
    config = {"storage.data_dir": str(tmp_path / "runtime")}
    plugin = SimpleNamespace(get_config=lambda key, default=None: config.get(key, default))
    return ImportTaskManager(plugin)


def test_lpmm_converter_writes_loadable_dual_pools_and_refuses_overwrite(tmp_path: Path) -> None:
    input_dir, output_dir = _conversion_paths(tmp_path)
    _write_parquet(
        input_dir / "paragraph.parquet",
        [{"hash": "lpmm-paragraph", "str": "旧版段落", "embedding": [1.0, 0.0]}],
    )
    _write_parquet(
        input_dir / "entity.parquet",
        [{"hash": "lpmm-entity", "str": "旧版实体", "embedding": [0.0, 1.0]}],
    )
    _write_parquet(
        input_dir / "relation.parquet",
        [
            {
                "hash": "lpmm-relation",
                "subject": "旧版实体",
                "predicate": "关联",
                "object": "旧版目标",
                "embedding": [0.5, 0.5],
            }
        ],
    )

    result = _run_convert(input_dir, output_dir)

    assert result.returncode == 0, result.stderr or result.stdout
    assert not (output_dir / "vectors" / "vectors.bin").exists()
    manifest_path = output_dir / "vectors" / "dual_ready.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ready"
    assert manifest["paragraph_vectors"] == 1
    assert manifest["graph_vectors"] == 2
    assert manifest["stats"] == {
        "paragraphs": {"done": 1, "failed": 0},
        "entities": {"done": 1, "failed": 0},
        "relations": {"done": 1, "failed": 0},
    }

    paragraph_store = VectorStore(dimension=2, data_dir=output_dir / "vectors" / "paragraph")
    graph_store = VectorStore(dimension=2, data_dir=output_dir / "vectors" / "graph")
    paragraph_store.load(expected_embedding_fingerprint=manifest["embedding_fingerprint"])
    graph_store.load(expected_embedding_fingerprint=manifest["embedding_fingerprint"])
    metadata_store = MetadataStore(data_dir=output_dir / "metadata")
    metadata_store.connect()
    paragraph_hash = str(metadata_store.query("SELECT hash FROM paragraphs")[0]["hash"])
    entity_hash = str(metadata_store.query("SELECT hash FROM entities")[0]["hash"])
    relation_hash = str(metadata_store.query("SELECT hash FROM relations")[0]["hash"])
    metadata_store.close()

    assert paragraph_hash in paragraph_store
    assert f"entity:{entity_hash}" in graph_store
    assert f"relation:{relation_hash}" in graph_store
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={
            "storage": {"data_dir": str(output_dir)},
            "embedding": {"dimension": 2},
            "retrieval": {"vector_pools": {"mode": "dual"}},
        },
    )
    kernel.embedding_manager = SimpleNamespace(
        get_embedding_fingerprint=lambda *, dimension: {
            **manifest["embedding_fingerprint"],
            "dimension": dimension,
        }
    )
    assert kernel._dual_vector_ready(expected_dimension=2) is True
    manager = _build_verify_manager(tmp_path)
    assert manager._verify_convert_output(output_dir)["ok"] is True

    committed_manifest = manifest_path.read_bytes()
    second = _run_convert(input_dir, output_dir)
    assert second.returncode != 0
    assert "输出目录必须为空" in second.stderr
    assert manifest_path.read_bytes() == committed_manifest


def test_lpmm_converter_deduplicates_semantic_ids_within_batch(tmp_path: Path) -> None:
    input_dir, output_dir = _conversion_paths(tmp_path)
    _write_parquet(
        input_dir / "paragraph.parquet",
        [
            {"hash": "paragraph-a", "str": "重复段落", "embedding": [1.0, 0.0]},
            {"hash": "paragraph-b", "str": "重复段落", "embedding": [1.0, 0.0]},
        ],
    )
    _write_parquet(
        input_dir / "entity.parquet",
        [
            {"hash": "entity-a", "str": "重复实体", "embedding": [0.0, 1.0]},
            {"hash": "entity-b", "str": "重复实体", "embedding": [0.0, 1.0]},
        ],
    )
    relation = {
        "subject": "重复实体",
        "predicate": "关联",
        "object": "重复目标",
        "embedding": [0.5, 0.5],
    }
    _write_parquet(
        input_dir / "relation.parquet",
        [{"hash": "relation-a", **relation}, {"hash": "relation-b", **relation}],
    )

    result = _run_convert(input_dir, output_dir)

    assert result.returncode == 0, result.stderr or result.stdout
    manifest = json.loads((output_dir / "vectors" / "dual_ready.json").read_text(encoding="utf-8"))
    assert manifest["stats"] == {
        "paragraphs": {"done": 1, "failed": 0},
        "entities": {"done": 1, "failed": 0},
        "relations": {"done": 1, "failed": 0},
    }
    metadata_store = MetadataStore(data_dir=output_dir / "metadata")
    metadata_store.connect()
    assert len(metadata_store.query("SELECT hash FROM paragraphs")) == 1
    assert len(metadata_store.query("SELECT hash FROM entities")) == 1
    relation_rows = metadata_store.query("SELECT hash, vector_state FROM relations")
    metadata_store.close()
    assert len(relation_rows) == 1
    assert relation_rows[0]["vector_state"] == "ready"


def test_lpmm_converter_dimension_failure_does_not_publish_ready_manifest(tmp_path: Path) -> None:
    input_dir, output_dir = _conversion_paths(tmp_path)
    _write_parquet(
        input_dir / "paragraph.parquet",
        [{"hash": "bad-dimension", "str": "错误维度", "embedding": [1.0, 0.0, 0.5]}],
    )

    result = _run_convert(input_dir, output_dir, dimension=2)

    assert result.returncode != 0
    assert "向量维度不匹配" in result.stderr
    assert not (output_dir / "vectors" / "dual_ready.json").exists()


def test_lpmm_converter_empty_parquet_does_not_publish_ready_manifest(tmp_path: Path) -> None:
    input_dir, output_dir = _conversion_paths(tmp_path)
    _write_parquet(
        input_dir / "paragraph.parquet",
        [],
    )

    result = _run_convert(input_dir, output_dir)

    assert result.returncode != 0
    assert "没有产生任何可用向量" in result.stderr
    assert not (output_dir / "vectors" / "dual_ready.json").exists()


def test_lpmm_output_verification_rejects_metadata_without_graph_vector(tmp_path: Path) -> None:
    input_dir, output_dir = _conversion_paths(tmp_path)
    _write_parquet(
        input_dir / "paragraph.parquet",
        [{"hash": "lpmm-paragraph", "str": "可用段落", "embedding": [1.0, 0.0]}],
    )
    result = _run_convert(input_dir, output_dir)
    assert result.returncode == 0, result.stderr or result.stdout

    metadata_store = MetadataStore(data_dir=output_dir / "metadata")
    metadata_store.connect()
    metadata_store.add_entity("缺少向量的实体")
    metadata_store.close()

    verify = _build_verify_manager(tmp_path)._verify_convert_output(output_dir)
    assert verify["stores_opened"] is True
    assert verify["references_valid"] is False
    assert verify["ok"] is False


@pytest.mark.asyncio
async def test_web_lpmm_convert_rejects_nonempty_target_before_queueing(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime"
    source_root = data_dir / "imports" / "source" / "lpmm"
    source_dir = source_root / "dataset"
    source_dir.mkdir(parents=True)
    target_root = data_dir / "imports" / "converted"
    target_dir = target_root / "converted"
    target_dir.mkdir(parents=True)
    (target_dir / "existing.txt").write_text("keep", encoding="utf-8")
    config = {
        "storage.data_dir": str(data_dir),
    }
    plugin = SimpleNamespace(get_config=lambda key, default=None: config.get(key, default))
    manager = ImportTaskManager(plugin)

    with pytest.raises(ValueError, match="目标目录必须为空"):
        await manager.create_lpmm_convert_task(
            {
                "alias": "lpmm",
                "relative_path": "dataset",
                "target_alias": "converted",
                "target_relative_path": "converted",
                "dimension": 2,
            }
        )

    assert manager._tasks == {}
    assert (target_dir / "existing.txt").read_text(encoding="utf-8") == "keep"

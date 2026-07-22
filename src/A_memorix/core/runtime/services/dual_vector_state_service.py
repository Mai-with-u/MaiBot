from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import json
import shutil
import time

from src.common.logger import get_logger

from ...storage import QuantizationType, VectorStore
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryDualVectorStateService(KernelServiceBase):
    def _vector_pool_mode(self) -> str:
        mode = str(self._cfg("retrieval.vector_pools.mode", "dual") or "dual").strip().lower()
        return mode if mode in {"single", "dual"} else "single"

    def _dual_vector_pools_config_enabled(self) -> bool:
        return self._vector_pool_mode() == "dual"

    def _dual_vector_pools_enabled(self) -> bool:
        return self._dual_vector_pools_config_enabled() and self._dual_vector_pools_ready

    def _vectors_root(self) -> Path:
        return self.data_dir / "vectors"

    def _paragraph_vector_dir(self) -> Path:
        return self._vectors_root() / "paragraph"

    def _graph_vector_dir(self) -> Path:
        return self._vectors_root() / "graph"

    def _dual_vector_ready_manifest_path(self) -> Path:
        return self._vectors_root() / "dual_ready.json"

    def _read_dual_vector_ready_manifest(self) -> Optional[Dict[str, Any]]:
        path = self._dual_vector_ready_manifest_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"读取双池 ready manifest 失败: {exc}")
            return None
        return payload if isinstance(payload, dict) else None

    def _dual_vector_ready(self, *, expected_dimension: Optional[int] = None) -> bool:
        manifest = self._read_dual_vector_ready_manifest()
        if not manifest or manifest.get("status") != "ready":
            return False
        dimension = int(expected_dimension or self._current_embedding_status_dimension() or 0)
        manifest_dimension = int(manifest.get("dimension", 0) or 0)
        if dimension > 0 and manifest_dimension not in {0, dimension}:
            logger.warning(f"双池 ready manifest 维度不匹配: manifest={manifest_dimension}, expected={dimension}")
            return False
        paragraph_count = int(manifest.get("paragraph_vectors", 0) or 0)
        graph_count = int(manifest.get("graph_vectors", 0) or 0)
        if paragraph_count < 0 or graph_count < 0:
            return False
        current_fingerprint = self._current_embedding_fingerprint()
        manifest_fingerprint = self._normalize_embedding_fingerprint(manifest.get("embedding_fingerprint"))
        if current_fingerprint is None or manifest_fingerprint is None:
            logger.warning("双池 ready manifest 缺少可校验 embedding 指纹，保持单池降级")
            return False
        if str(current_fingerprint.get("hash", "") or "") != str(manifest_fingerprint.get("hash", "") or ""):
            logger.warning(
                "双池 ready manifest embedding 指纹不匹配，保持单池降级: "
                f"manifest={manifest_fingerprint.get('hash', '')}, "
                f"current={current_fingerprint.get('hash', '')}"
            )
            return False
        return self._paragraph_vector_dir().exists() and self._graph_vector_dir().exists()

    def _write_dual_vector_ready_manifest(
        self,
        *,
        stats: Dict[str, Dict[str, int]],
        migration_stats: Dict[str, Dict[str, int]],
    ) -> None:
        current_dimension = self._current_embedding_status_dimension()
        embedding_fingerprint = self._current_embedding_fingerprint(dimension=current_dimension)
        payload = {
            "status": "ready",
            "version": 1,
            "mode": "dual",
            "dimension": int(current_dimension),
            "created_at": time.time(),
            "paragraph_vectors": int(stats.get("paragraphs", {}).get("done", 0) or 0),
            "graph_vectors": int(stats.get("entities", {}).get("done", 0) or 0)
            + int(stats.get("relations", {}).get("done", 0) or 0),
            "stats": stats,
            "migration": migration_stats,
        }
        if embedding_fingerprint is not None:
            payload["embedding_fingerprint"] = embedding_fingerprint
        path = self._dual_vector_ready_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _remove_dual_vector_ready_manifest(self) -> None:
        try:
            self._dual_vector_ready_manifest_path().unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"删除双池 ready manifest 失败: {exc}")

    def _refresh_dual_vector_ready_manifest_from_stores(self) -> None:
        paragraph_count = int(getattr(self.paragraph_vector_store, "num_vectors", 0) or 0)
        graph_count = int(getattr(self.graph_vector_store, "num_vectors", 0) or 0)
        entity_count = graph_count
        relation_count = 0
        if self.metadata_store is not None:
            try:
                target_counts = self._count_vector_rebuild_targets()
                entity_count = min(graph_count, int(target_counts.get("entities", 0) or 0))
                relation_count = max(0, graph_count - entity_count)
            except Exception as exc:
                logger.warning(f"刷新双池 ready manifest 统计失败，使用向量池计数: {exc}")
        stats = {
            "paragraphs": {"done": paragraph_count, "failed": 0},
            "entities": {"done": entity_count, "failed": 0},
            "relations": {"done": relation_count, "failed": 0},
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)

    def _clear_legacy_single_vector_files_after_dual_ready(self) -> None:
        root = self._vectors_root()
        for filename in (
            "vectors.bin",
            "vectors_ids.bin",
            "vectors.index",
            "vectors_metadata.json",
            "vectors_metadata.pkl",
        ):
            try:
                (root / filename).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(f"清理旧单池向量文件失败: file={filename}, error={exc}")
        if self.vector_store is not None:
            self.vector_store = self._make_vector_store(root)

    def _prepare_dual_vector_build_dirs(self) -> tuple[Path, Path, Path]:
        build_root = self._vectors_root() / f"dual_build_{int(time.time() * 1000)}"
        if build_root.exists():
            shutil.rmtree(build_root, ignore_errors=True)
        paragraph_dir = build_root / "paragraph"
        graph_dir = build_root / "graph"
        paragraph_dir.mkdir(parents=True, exist_ok=True)
        graph_dir.mkdir(parents=True, exist_ok=True)
        return build_root, paragraph_dir, graph_dir

    def _activate_dual_vector_build_dirs(self, build_root: Path) -> None:
        paragraph_src = build_root / "paragraph"
        graph_src = build_root / "graph"
        if not paragraph_src.exists() or not graph_src.exists():
            raise RuntimeError("dual vector build dirs missing")

        backup_root = self._vectors_root() / f"dual_backup_{int(time.time() * 1000)}"
        backup_paragraph = backup_root / "paragraph"
        backup_graph = backup_root / "graph"
        backup_root.mkdir(parents=True, exist_ok=True)
        activation_journal = backup_root / "activation.json"
        paragraph_dst = self._paragraph_vector_dir()
        graph_dst = self._graph_vector_dir()
        activation_journal.write_text(
            json.dumps(
                {
                    "status": "prepared",
                    "build_root": str(build_root),
                    "had_paragraph": paragraph_dst.exists(),
                    "had_graph": graph_dst.exists(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            if paragraph_dst.exists():
                shutil.move(str(paragraph_dst), str(backup_paragraph))
            if graph_dst.exists():
                shutil.move(str(graph_dst), str(backup_graph))
            shutil.move(str(paragraph_src), str(paragraph_dst))
            shutil.move(str(graph_src), str(graph_dst))
            shutil.rmtree(build_root, ignore_errors=True)
            activation_journal.write_text(
                json.dumps(
                    {
                        "status": "activated",
                        "had_paragraph": backup_paragraph.exists(),
                        "had_graph": backup_graph.exists(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            if paragraph_dst.exists():
                shutil.rmtree(paragraph_dst, ignore_errors=True)
            if graph_dst.exists():
                shutil.rmtree(graph_dst, ignore_errors=True)
            if backup_paragraph.exists():
                shutil.move(str(backup_paragraph), str(paragraph_dst))
            if backup_graph.exists():
                shutil.move(str(backup_graph), str(graph_dst))
            shutil.rmtree(backup_root, ignore_errors=True)
            raise

    def _cleanup_stale_dual_vector_build_dirs(self) -> None:
        vectors_root = self._vectors_root()
        if not vectors_root.exists():
            return
        backup_dirs = sorted(
            (child for child in vectors_root.iterdir() if child.is_dir() and child.name.startswith("dual_backup_")),
            key=lambda path: path.name,
            reverse=True,
        )
        unresolved_backup = False
        for backup_root in backup_dirs:
            journal_path = backup_root / "activation.json"
            if not journal_path.exists():
                unresolved_backup = True
                logger.error(f"发现缺少激活日志的双池备份，已保留供人工恢复: {backup_root}")
                continue
            if self._dual_vector_ready():
                shutil.rmtree(backup_root, ignore_errors=True)
                continue
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                status = str(journal.get("status", "") or "").strip().lower()
                if status == "activated":
                    shutil.rmtree(backup_root, ignore_errors=True)
                    logger.warning("检测到已完成但尚未清理的双池激活，已保留当前目录并清理旧备份")
                    continue
                if status != "prepared":
                    raise RuntimeError(f"双池激活日志状态无效: {status or 'missing'}")
                had_paragraph = bool(journal.get("had_paragraph", True))
                had_graph = bool(journal.get("had_graph", True))
                interrupted_root = backup_root / "interrupted_new"
                interrupted_root.mkdir(parents=True, exist_ok=True)
                for name, destination, existed_before in (
                    ("paragraph", self._paragraph_vector_dir(), had_paragraph),
                    ("graph", self._graph_vector_dir(), had_graph),
                ):
                    if destination.exists():
                        shutil.move(str(destination), str(interrupted_root / name))
                    backup_path = backup_root / name
                    if existed_before:
                        if not backup_path.exists():
                            raise RuntimeError(f"双池备份缺少目录: {backup_path}")
                        shutil.move(str(backup_path), str(destination))
                shutil.rmtree(backup_root, ignore_errors=True)
                logger.warning("检测到未完成的双池激活，已恢复切换前目录")
            except Exception as exc:
                unresolved_backup = True
                logger.error(f"恢复双池激活备份失败，已保留现场: backup={backup_root}, error={exc}")

        if unresolved_backup:
            return
        for child in vectors_root.iterdir():
            if child.is_dir() and child.name.startswith("dual_build_"):
                shutil.rmtree(child, ignore_errors=True)

    def _make_vector_store(self, data_dir: Path, *, dimension: Optional[int] = None) -> VectorStore:
        return VectorStore(
            dimension=max(1, int(dimension or self.embedding_dimension)),
            quantization_type=QuantizationType.INT8,
            data_dir=data_dir,
        )

    def _save_vector_store(self, store: Optional[VectorStore]) -> None:
        if store is None:
            return
        store.save(embedding_fingerprint=self._current_embedding_fingerprint())

    def _reload_dual_vector_stores_from_disk(self) -> bool:
        current_dimension = self._current_embedding_status_dimension()
        if not self._dual_vector_ready(expected_dimension=current_dimension):
            self._try_recover_dual_ready_manifest()
        if not self._dual_vector_ready(expected_dimension=current_dimension):
            self.paragraph_vector_store = self._make_vector_store(self._paragraph_vector_dir())
            self.graph_vector_store = self._make_vector_store(self._graph_vector_dir())
            self._dual_vector_pools_ready = False
            return False
        try:
            paragraph_store = self._make_vector_store(self._paragraph_vector_dir())
            graph_store = self._make_vector_store(self._graph_vector_dir())
            if paragraph_store.has_data():
                paragraph_store.load()
                paragraph_store.warmup_index(force_train=True)
            if graph_store.has_data():
                graph_store.load()
                graph_store.warmup_index(force_train=True)
        except Exception as exc:
            logger.warning(f"加载双池向量失败，将暂时回退单池: {exc}")
            self._dual_vector_pools_ready = False
            return False
        self.paragraph_vector_store = paragraph_store
        self.graph_vector_store = graph_store
        self._dual_vector_pools_ready = True
        return True

    def _try_recover_dual_ready_manifest(self) -> bool:
        if not self._dual_vector_pools_config_enabled() or self.metadata_store is None:
            return False
        if self._dual_vector_ready_manifest_path().exists():
            return False
        paragraph_dir = self._paragraph_vector_dir()
        graph_dir = self._graph_vector_dir()
        if not paragraph_dir.exists() or not graph_dir.exists():
            return False
        paragraph_store = self._make_vector_store(paragraph_dir)
        graph_store = self._make_vector_store(graph_dir)
        if not paragraph_store.has_data() or not graph_store.has_data():
            return False
        try:
            if paragraph_store.has_data():
                paragraph_store.load()
            if graph_store.has_data():
                graph_store.load()
        except Exception as exc:
            logger.warning(f"双池 ready manifest 自愈失败，加载向量池异常: {exc}")
            return False

        if not self._stored_vectors_compatible_with_current_embedding(
            paragraph_store
        ) or not self._stored_vectors_compatible_with_current_embedding(graph_store):
            logger.warning("双池 ready manifest 缺失且向量池指纹无法确认或不匹配，保持单池降级")
            return False

        counts = self._count_vector_rebuild_targets()
        expected_paragraphs = int(counts.get("paragraphs", 0) or 0)
        expected_graph = int(counts.get("entities", 0) or 0)
        if bool(self.relation_vectors_enabled):
            expected_graph += int(counts.get("relations", 0) or 0)
        if paragraph_store.num_vectors != expected_paragraphs or graph_store.num_vectors != expected_graph:
            logger.warning(
                "双池 ready manifest 缺失且向量数量不匹配，保持单池降级: "
                f"paragraph={paragraph_store.num_vectors}/{expected_paragraphs}, "
                f"graph={graph_store.num_vectors}/{expected_graph}"
            )
            return False

        stats = {
            "paragraphs": {"done": expected_paragraphs, "failed": 0},
            "entities": {"done": int(counts.get("entities", 0) or 0), "failed": 0},
            "relations": {
                "done": int(counts.get("relations", 0) or 0) if bool(self.relation_vectors_enabled) else 0,
                "failed": 0,
            },
        }
        migration_stats = {
            "paragraphs": {"copied": 0, "encoded": 0, "missing": 0},
            "entities": {"copied": 0, "encoded": 0, "missing": 0},
            "relations": {"copied": 0, "encoded": 0, "missing": 0},
        }
        self._write_dual_vector_ready_manifest(stats=stats, migration_stats=migration_stats)
        logger.warning("检测到双池目录完整但 ready manifest 缺失，已自动重建 manifest")
        return True

    def _drop_dual_build_root(self, build_root: Optional[Path]) -> None:
        if build_root is None:
            return
        try:
            shutil.rmtree(build_root, ignore_errors=True)
        except Exception as exc:
            logger.warning(f"清理双池临时构建目录失败: {exc}")

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{str(item_type or '').strip()}:{str(hash_value or '').strip()}"

    def _paragraph_store(self) -> Optional[VectorStore]:
        if self._dual_vector_pools_enabled():
            return self.paragraph_vector_store or self.vector_store
        return self.vector_store

    def _graph_vector_store(self) -> Optional[VectorStore]:
        if self._dual_vector_pools_enabled():
            return self.graph_vector_store or self.vector_store
        return self.vector_store

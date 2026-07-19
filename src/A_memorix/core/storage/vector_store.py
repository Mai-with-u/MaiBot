"""
向量存储模块

基于Faiss的高效向量存储与检索，支持SQ8量化、Append-Only磁盘存储和内存映射。
"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

import hashlib
import json
import os
import random
import shutil
import threading  # 线程同步
import time
import uuid
import numpy as np

try:
    import faiss

    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

from src.common.logger import get_logger
from ..utils.quantization import QuantizationType
from ..utils.io import atomic_write, atomic_save_path

logger = get_logger("A_Memorix.VectorStore")


def _read_json_object(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"JSON 元数据必须是对象: {path}")
    return payload


def _write_json_object(path: Path, payload: Dict[str, Any]) -> None:
    with atomic_write(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


class VectorStore:
    """
    向量存储类 (SQ8 + Append-Only Disk)

    特性：
    - 索引: IndexIDMap2(IndexScalarQuantizer(QT_8bit))
    - 存储: float16 on-disk binary (vectors.bin)
    - 内存: 仅索引常驻 RAM (<512MB for 100k vectors)
    - ID: SHA1-based stable int64 IDs
    - 一致性: 强制 L2 Normalization (IP == Cosine)
    """

    # 默认训练触发阈值 (40 样本，过大可能导致小数据集不生效，过小可能量化退化)
    DEFAULT_MIN_TRAIN = 40
    # 强制训练样本量
    TRAIN_SIZE = 10000
    # 储水池采样上限 (流式处理前 50k 数据)
    RESERVOIR_CAPACITY = 10000
    RESERVOIR_SAMPLE_SCOPE = 50000

    def __init__(
        self,
        dimension: int,
        quantization_type: QuantizationType = QuantizationType.INT8,
        index_type: str = "sq8",
        data_dir: Optional[Union[str, Path]] = None,
        use_mmap: bool = True,
        buffer_size: int = 1024,
    ):
        if not HAS_FAISS:
            raise ImportError("Faiss 未安装，请安装: pip install faiss-cpu")

        self.dimension = dimension
        self.data_dir = Path(data_dir) if data_dir else None
        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        if quantization_type != QuantizationType.INT8:
            raise ValueError(
                "vNext 仅支持 quantization_type=int8(SQ8)。 请更新配置并执行 scripts/release_vnext_migrate.py migrate。"
            )
        normalized_index_type = str(index_type or "sq8").strip().lower()
        if normalized_index_type not in {"sq8", "int8"}:
            raise ValueError(
                "vNext 仅支持 index_type=sq8。 请更新配置并执行 scripts/release_vnext_migrate.py migrate。"
            )
        self.quantization_type = QuantizationType.INT8
        self.index_type = "sq8"
        self.buffer_size = buffer_size
        self.min_train_threshold = self.DEFAULT_MIN_TRAIN

        self._index: Optional[faiss.IndexIDMap2] = None
        self._init_index()

        self._is_trained = False
        self._vector_norm = "l2"

        # Fallback Index (Flat) - 用于在 SQ8 训练完成前提供检索能力
        # 必须使用 IndexIDMap2 以保证 ID 与主索引一致
        self._fallback_index: Optional[faiss.IndexIDMap2] = None
        self._init_fallback_index()

        self._known_hashes: Set[str] = set()
        self._deleted_ids: Set[int] = set()
        self._known_hashes_revision = 0
        self._cached_map_revision = -1
        self._cached_map: Dict[int, str] = {}
        self._id_offsets: Dict[int, int] = {}
        self._id_offsets_count = -1

        self._reservoir_buffer: List[np.ndarray] = []
        self._seen_count_for_reservoir = 0

        self._write_buffer_vecs: List[np.ndarray] = []
        self._write_buffer_ids: List[int] = []

        self._total_added = 0
        self._total_deleted = 0
        self._bin_count = 0

        # 外部投影任务用短事务保护同步向量变更。checkpoint 期间禁止自动压缩，
        # 失败时才能仅依赖上一份 metadata 提交和 append 尾部回滚恢复原状态。
        self._cleanup_checkpoint: Optional[Dict[str, Any]] = None
        self._cleanup_checkpoint_error: Optional[str] = None
        self._cleanup_compaction_deferred = False

        # 线程安全锁
        self._lock = threading.RLock()

        logger.debug(f"向量存储实例已创建: dim={dimension}, mode=SQ8, data_dir={self.data_dir}")

    def _init_index(self):
        """初始化空的 Faiss 索引"""
        quantizer = faiss.IndexScalarQuantizer(
            self.dimension, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT
        )
        self._index = faiss.IndexIDMap2(quantizer)
        self._is_trained = False

    def _init_fallback_index(self):
        """初始化 Flat 回退索引"""
        flat_index = faiss.IndexFlatIP(self.dimension)
        self._fallback_index = faiss.IndexIDMap2(flat_index)
        logger.debug("Fallback index (Flat) initialized.")

    @staticmethod
    def _generate_id(key: str) -> int:
        """生成稳定的 int64 ID (SHA1 截断)"""
        h = hashlib.sha1(key.encode("utf-8")).digest()
        val = int.from_bytes(h[:8], byteorder="big", signed=False)
        return val & 0x7FFFFFFFFFFFFFFF

    @property
    def _bin_path(self) -> Path:
        return self.data_dir / "vectors.bin"

    @property
    def _ids_bin_path(self) -> Path:
        return self.data_dir / "vectors_ids.bin"

    @property
    def _append_journal_path(self) -> Path:
        return self.data_dir / "vectors_append.json"

    @property
    def _compaction_journal_path(self) -> Path:
        return self.data_dir / "vectors_compaction.json"

    @property
    def _bin_backup_path(self) -> Path:
        return self.data_dir / "vectors.bin.compaction.bak"

    @property
    def _ids_backup_path(self) -> Path:
        return self.data_dir / "vectors_ids.bin.compaction.bak"

    @property
    def _bin_recovery_tmp_path(self) -> Path:
        return self.data_dir / "vectors.bin.compaction.restore.tmp"

    @property
    def _ids_recovery_tmp_path(self) -> Path:
        return self.data_dir / "vectors_ids.bin.compaction.restore.tmp"

    def _invalidate_id_map(self) -> None:
        self._known_hashes_revision += 1

    def _invalidate_id_offsets(self) -> None:
        self._id_offsets.clear()
        self._id_offsets_count = -1

    def _raise_if_cleanup_checkpoint_broken_unlocked(self) -> None:
        if self._cleanup_checkpoint_error is not None:
            raise RuntimeError(
                "向量清理 checkpoint 已损坏，当前实例禁止继续读写，"
                f"必须重建 VectorStore 实例: {self._cleanup_checkpoint_error}"
            )

    def _vector_pair_sizes_unlocked(self) -> Tuple[int, int]:
        """读取当前成对文件长度，并拒绝无日志的结构损坏。"""
        bin_exists = self._bin_path.exists()
        ids_exists = self._ids_bin_path.exists()
        if bin_exists != ids_exists:
            raise RuntimeError("向量数据文件与 ID 文件必须成对存在")
        if not bin_exists:
            return 0, 0

        vector_bytes = self._bin_path.stat().st_size
        id_bytes = self._ids_bin_path.stat().st_size
        vector_item_size = self.dimension * 2
        if vector_bytes % vector_item_size != 0 or id_bytes % 8 != 0:
            raise RuntimeError("向量数据文件或 ID 文件存在不完整记录")
        if vector_bytes // vector_item_size != id_bytes // 8:
            raise RuntimeError("向量数据文件与 ID 文件记录数不一致")
        return vector_bytes, id_bytes

    def _clear_pair_state_unlocked(self) -> Tuple[int, int, int]:
        """按已提交元数据维度校验待清空 pair，支持跨维度重建。"""
        bin_exists = self._bin_path.exists()
        ids_exists = self._ids_bin_path.exists()
        if bin_exists != ids_exists:
            raise RuntimeError("向量数据文件与 ID 文件必须成对存在")
        if not bin_exists:
            return 0, 0, self.dimension

        vector_bytes = self._bin_path.stat().st_size
        id_bytes = self._ids_bin_path.stat().st_size
        meta_path = self.data_dir / "vectors_metadata.json"
        if not meta_path.exists():
            current_vector_bytes, current_id_bytes = self._vector_pair_sizes_unlocked()
            return current_vector_bytes, current_id_bytes, self.dimension

        meta = _read_json_object(meta_path)
        stored_dimension = meta.get("dimension")
        if (
            not isinstance(stored_dimension, int)
            or isinstance(stored_dimension, bool)
            or stored_dimension <= 0
        ):
            raise RuntimeError("向量元数据的 dimension 无效，拒绝清空")
        vector_item_size = stored_dimension * 2
        if (
            vector_bytes % vector_item_size != 0
            or id_bytes % 8 != 0
            or vector_bytes // vector_item_size != id_bytes // 8
        ):
            raise RuntimeError("待清空向量 pair 与已提交维度不一致")

        if meta.get("schema_version", 1) == 2:
            binary_commit = meta.get("binary_commit")
            if not isinstance(binary_commit, dict):
                raise RuntimeError("V2 向量元数据缺少 binary_commit，拒绝清空")
            if (
                binary_commit.get("vector_bytes") != vector_bytes
                or binary_commit.get("id_bytes") != id_bytes
            ):
                raise RuntimeError("待清空向量 pair 与已提交长度不一致")
        return vector_bytes, id_bytes, stored_dimension

    def _read_append_journal_unlocked(self) -> Optional[Dict[str, Any]]:
        if not self._append_journal_path.exists():
            return None
        journal = _read_json_object(self._append_journal_path)
        required_fields = {
            "transaction_id",
            "base_vector_bytes",
            "base_id_bytes",
            "target_vector_bytes",
            "target_id_bytes",
        }
        missing_fields = required_fields.difference(journal)
        if missing_fields:
            raise RuntimeError(f"向量追加日志缺少字段: {sorted(missing_fields)}")

        transaction_id = journal["transaction_id"]
        if not isinstance(transaction_id, str) or not transaction_id:
            raise RuntimeError("向量追加日志的 transaction_id 无效")
        numeric_fields = required_fields - {"transaction_id"}
        for field in numeric_fields:
            value = journal[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RuntimeError(f"向量追加日志的 {field} 无效")

        base_vector_bytes = int(journal["base_vector_bytes"])
        base_id_bytes = int(journal["base_id_bytes"])
        target_vector_bytes = int(journal["target_vector_bytes"])
        target_id_bytes = int(journal["target_id_bytes"])
        vector_item_size = self.dimension * 2
        if (
            base_vector_bytes % vector_item_size != 0
            or target_vector_bytes % vector_item_size != 0
            or base_id_bytes % 8 != 0
            or target_id_bytes % 8 != 0
            or base_vector_bytes // vector_item_size != base_id_bytes // 8
            or target_vector_bytes // vector_item_size != target_id_bytes // 8
            or target_vector_bytes < base_vector_bytes
            or target_id_bytes < base_id_bytes
        ):
            raise RuntimeError("向量追加日志的成对文件长度无效")
        return journal

    @staticmethod
    def _metadata_commits_append(meta: Dict[str, Any], journal: Dict[str, Any]) -> bool:
        commit = meta.get("binary_commit")
        if not isinstance(commit, dict):
            return False
        return (
            commit.get("transaction_id") == journal["transaction_id"]
            and commit.get("vector_bytes") == journal["target_vector_bytes"]
            and commit.get("id_bytes") == journal["target_id_bytes"]
        )

    @staticmethod
    def _sync_append(path: Path, payload: bytes) -> None:
        with path.open("ab") as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError(f"文件追加不完整: {path}, {written}/{len(payload)}")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _truncate_and_sync(path: Path, size: int) -> None:
        if not path.exists():
            if size != 0:
                raise RuntimeError(f"待回滚文件不存在: {path}")
            path.touch()
        current_size = path.stat().st_size
        if current_size < size:
            raise RuntimeError(f"待回滚文件小于已提交长度: {path}, {current_size} < {size}")
        with path.open("r+b") as handle:
            handle.truncate(size)
            handle.flush()
            os.fsync(handle.fileno())

    def _rollback_append_tail_unlocked(
        self,
        journal: Dict[str, Any],
        *,
        vector_bytes: int,
        id_bytes: int,
    ) -> None:
        target_vector_bytes = int(journal["target_vector_bytes"])
        target_id_bytes = int(journal["target_id_bytes"])
        actual_vector_bytes = self._bin_path.stat().st_size if self._bin_path.exists() else 0
        actual_id_bytes = self._ids_bin_path.stat().st_size if self._ids_bin_path.exists() else 0
        if actual_vector_bytes > target_vector_bytes or actual_id_bytes > target_id_bytes:
            raise RuntimeError("向量追加文件超出日志目标长度，拒绝截断")
        if actual_vector_bytes < vector_bytes or actual_id_bytes < id_bytes:
            raise RuntimeError("向量追加文件小于已提交基线，拒绝截断")
        self._truncate_and_sync(self._bin_path, vector_bytes)
        self._truncate_and_sync(self._ids_bin_path, id_bytes)
        self._invalidate_id_offsets()

        if (
            vector_bytes == int(journal["base_vector_bytes"])
            and id_bytes == int(journal["base_id_bytes"])
        ):
            self._append_journal_path.unlink()
            return
        journal["target_vector_bytes"] = vector_bytes
        journal["target_id_bytes"] = id_bytes
        _write_json_object(self._append_journal_path, journal)

    def _recover_interrupted_append_unlocked(self) -> None:
        """以元数据原子替换为提交点，恢复被中断的成对追加。"""
        journal = self._read_append_journal_unlocked()
        if journal is None:
            return

        meta_path = self.data_dir / "vectors_metadata.json"
        meta = _read_json_object(meta_path) if meta_path.exists() else {}
        actual_vector_bytes = self._bin_path.stat().st_size if self._bin_path.exists() else 0
        actual_id_bytes = self._ids_bin_path.stat().st_size if self._ids_bin_path.exists() else 0
        target_vector_bytes = int(journal["target_vector_bytes"])
        target_id_bytes = int(journal["target_id_bytes"])
        if self._metadata_commits_append(meta, journal):
            if actual_vector_bytes != target_vector_bytes or actual_id_bytes != target_id_bytes:
                raise RuntimeError("向量追加已提交，但成对文件未达到提交长度")
            self._append_journal_path.unlink()
            return

        self._rollback_append_tail_unlocked(
            journal,
            vector_bytes=int(journal["base_vector_bytes"]),
            id_bytes=int(journal["base_id_bytes"]),
        )
        # save() 可能已把新索引替换到位，而元数据尚未提交。
        # 删除它可以强制 load() 按已回滚的二进制文件重建。
        (self.data_dir / "vectors.index").unlink(missing_ok=True)

    def _finalize_append_commit_unlocked(self) -> None:
        self._append_journal_path.unlink()

    def _read_compaction_journal_unlocked(self) -> Optional[Dict[str, Any]]:
        if not self._compaction_journal_path.exists():
            return None
        journal = _read_json_object(self._compaction_journal_path)
        required_fields = {
            "transaction_id",
            "base_vector_bytes",
            "base_id_bytes",
            "target_vector_bytes",
            "target_id_bytes",
        }
        missing_fields = required_fields.difference(journal)
        if missing_fields:
            raise RuntimeError(f"向量压缩日志缺少字段: {sorted(missing_fields)}")

        transaction_id = journal["transaction_id"]
        if not isinstance(transaction_id, str) or not transaction_id:
            raise RuntimeError("向量压缩日志的 transaction_id 无效")
        numeric_fields = required_fields - {"transaction_id"}
        for field in numeric_fields:
            value = journal[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RuntimeError(f"向量压缩日志的 {field} 无效")

        base_vector_bytes = int(journal["base_vector_bytes"])
        base_id_bytes = int(journal["base_id_bytes"])
        target_vector_bytes = int(journal["target_vector_bytes"])
        target_id_bytes = int(journal["target_id_bytes"])
        journal_dimension = journal.get("dimension", self.dimension)
        if (
            not isinstance(journal_dimension, int)
            or isinstance(journal_dimension, bool)
            or journal_dimension <= 0
        ):
            raise RuntimeError("向量压缩日志的 dimension 无效")
        vector_item_size = journal_dimension * 2
        if (
            base_vector_bytes % vector_item_size != 0
            or target_vector_bytes % vector_item_size != 0
            or base_id_bytes % 8 != 0
            or target_id_bytes % 8 != 0
            or base_vector_bytes // vector_item_size != base_id_bytes // 8
            or target_vector_bytes // vector_item_size != target_id_bytes // 8
        ):
            raise RuntimeError("向量压缩日志的成对文件长度无效")
        return journal

    @staticmethod
    def _copy_and_sync(source: Path, target: Path) -> None:
        with source.open("rb") as source_handle, target.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())

    def _replace_compaction_recovery_pair_unlocked(self) -> None:
        os.replace(self._bin_recovery_tmp_path, self._bin_path)
        os.replace(self._ids_recovery_tmp_path, self._ids_bin_path)

    def _restore_compaction_backup_unlocked(self, journal: Dict[str, Any]) -> None:
        """从不可变备份重建旧 pair，恢复中途再中断也可重入。"""
        base_vector_bytes = int(journal["base_vector_bytes"])
        base_id_bytes = int(journal["base_id_bytes"])
        journal_dimension = int(journal.get("dimension", self.dimension))
        if not self._bin_backup_path.exists() or not self._ids_backup_path.exists():
            raise RuntimeError("向量压缩被中断，且找不到成对备份")
        if (
            self._bin_backup_path.stat().st_size != base_vector_bytes
            or self._ids_backup_path.stat().st_size != base_id_bytes
            or not self._vector_pair_matches(
                self._bin_backup_path,
                self._ids_backup_path,
                expected_count=base_id_bytes // 8,
                dimension=journal_dimension,
            )
        ):
            raise RuntimeError("向量压缩备份与日志基线不一致")

        # 备份只读：每次恢复都重新生成临时 pair，不会因为第一次
        # os.replace 成功就消耗掉唯一备份。
        self._copy_and_sync(self._bin_backup_path, self._bin_recovery_tmp_path)
        self._copy_and_sync(self._ids_backup_path, self._ids_recovery_tmp_path)
        if not self._vector_pair_matches(
            self._bin_recovery_tmp_path,
            self._ids_recovery_tmp_path,
            expected_count=base_id_bytes // 8,
            dimension=journal_dimension,
        ):
            raise RuntimeError("向量压缩恢复临时文件不一致")

        self._replace_compaction_recovery_pair_unlocked()
        if (
            self._bin_path.stat().st_size != base_vector_bytes
            or self._ids_bin_path.stat().st_size != base_id_bytes
            or not self._vector_pair_matches(
                self._bin_path,
                self._ids_bin_path,
                expected_count=base_id_bytes // 8,
                dimension=journal_dimension,
            )
        ):
            raise RuntimeError("向量压缩备份恢复后校验失败")

        self._invalidate_id_offsets()

    def _finalize_compaction_transaction_unlocked(self) -> None:
        self._compaction_journal_path.unlink()
        self._bin_backup_path.unlink(missing_ok=True)
        self._ids_backup_path.unlink(missing_ok=True)

    def _finalize_compaction_commit_unlocked(self) -> None:
        self._finalize_compaction_transaction_unlocked()

    def _vector_pair_matches(
        self,
        bin_path: Path,
        ids_path: Path,
        *,
        expected_count: Optional[int] = None,
        dimension: Optional[int] = None,
    ) -> bool:
        if not bin_path.exists() or not ids_path.exists():
            return False
        vector_item_size = int(dimension if dimension is not None else self.dimension) * 2
        bin_size = bin_path.stat().st_size
        ids_size = ids_path.stat().st_size
        if bin_size % vector_item_size != 0 or ids_size % 8 != 0:
            return False
        vector_count = bin_size // vector_item_size
        id_count = ids_size // 8
        return vector_count == id_count and (expected_count is None or vector_count == expected_count)

    def _recover_interrupted_compaction_unlocked(self) -> None:
        """根据 canonical metadata 提交代际完成或回滚压缩。"""
        journal = self._read_compaction_journal_unlocked()
        if journal is None:
            return
        self._invalidate_id_offsets()

        meta_path = self.data_dir / "vectors_metadata.json"
        meta = _read_json_object(meta_path) if meta_path.exists() else {}
        target_vector_bytes = int(journal["target_vector_bytes"])
        target_id_bytes = int(journal["target_id_bytes"])
        if self._metadata_commits_append(meta, journal):
            actual_vector_bytes = self._bin_path.stat().st_size if self._bin_path.exists() else 0
            actual_id_bytes = self._ids_bin_path.stat().st_size if self._ids_bin_path.exists() else 0
            if actual_vector_bytes != target_vector_bytes or actual_id_bytes != target_id_bytes:
                raise RuntimeError("向量压缩已提交，但成对文件未达到提交长度")
            self._finalize_compaction_commit_unlocked()
            return

        self._restore_compaction_backup_unlocked(journal)
        # canonical index 可能已替换，但 canonical metadata 尚未提交。
        # 回滚 pair 后删除该索引，由 load() 按旧 pair 和 tombstone 重建。
        (self.data_dir / "vectors.index").unlink(missing_ok=True)
        journal_dimension = int(journal.get("dimension", self.dimension))
        if journal_dimension == self.dimension:
            self._reload_runtime_after_compaction_rollback_unlocked()
        else:
            logger.warning(
                "已恢复跨维度清空前的向量 pair；当前实例维度不同，跳过运行时回放: "
                f"stored={journal_dimension}, current={self.dimension}"
            )
        # journal 和只读备份必须保留到 pair、索引、metadata 和运行时状态
        # 全部恢复成功。任意一步失败时，下一次均可从同一基线重入。
        self._finalize_compaction_transaction_unlocked()

    def _reload_runtime_after_compaction_rollback_unlocked(self) -> None:
        """根据压缩前已提交的 metadata 和已恢复 pair 重建同一实例。"""
        if self._write_buffer_ids or self._write_buffer_vecs:
            raise RuntimeError("向量压缩回滚时存在未提交写缓冲，拒绝覆盖运行时状态")

        vector_bytes, id_bytes = self._vector_pair_sizes_unlocked()
        meta_path = self.data_dir / "vectors_metadata.json"
        if not meta_path.exists():
            raise RuntimeError("向量压缩回滚后缺少基线元数据")
        meta = self._validate_or_migrate_vector_metadata_unlocked(
            meta_path,
            _read_json_object(meta_path),
            vector_bytes=vector_bytes,
            id_bytes=id_bytes,
        )

        raw_known_hashes = meta.get("known_hashes")
        raw_deleted_ids = meta.get("deleted_ids", [])
        if not isinstance(raw_known_hashes, list) or any(
            not isinstance(hash_value, str) or not hash_value for hash_value in raw_known_hashes
        ):
            raise RuntimeError("向量压缩基线元数据的 known_hashes 无效")
        if not isinstance(raw_deleted_ids, list) or any(
            not isinstance(int_id, int) or isinstance(int_id, bool) for int_id in raw_deleted_ids
        ):
            raise RuntimeError("向量压缩基线元数据的 deleted_ids 无效")

        expected_disk_ids = Counter(self._generate_id(hash_value) for hash_value in raw_known_hashes)
        actual_disk_ids = self._disk_id_multiset_unlocked()
        if expected_disk_ids != actual_disk_ids:
            missing_count = sum((expected_disk_ids - actual_disk_ids).values())
            unexpected_count = sum((actual_disk_ids - expected_disk_ids).values())
            raise RuntimeError(
                "向量压缩基线元数据与恢复 pair 不一致: "
                f"missing={missing_count}, unexpected={unexpected_count}"
            )

        self._known_hashes = set(raw_known_hashes)
        self._deleted_ids = set(raw_deleted_ids)
        self._bin_count = vector_bytes // (self.dimension * 2)
        self._vector_norm = "l2"
        self._invalidate_id_map()
        self._invalidate_id_offsets()

        metadata_is_trained = bool(meta.get("is_trained", False))
        self._init_index()
        self._init_fallback_index()
        if metadata_is_trained:
            self._rebuild_loaded_index_unlocked()
        elif self._bin_count > 0:
            self._bootstrap_fallback_from_disk_unlocked()
            if self._faiss_id_multiset(self._fallback_index) != self._expected_active_id_multiset_unlocked():
                raise RuntimeError("向量压缩回滚后 fallback 索引与基线元数据不一致")

    @property
    def _int_to_str_map(self) -> Dict[int, str]:
        """按需从已知哈希构建易失的 ID 反查表。"""
        # 反查表读取频繁，且依赖可变的 _known_hashes；版本号可以识别等量替换。
        if self._cached_map_revision != self._known_hashes_revision:
            with self._lock:  # 在锁内重建缓存
                if self._cached_map_revision != self._known_hashes_revision:
                    self._cached_map = {self._generate_id(k): k for k in self._known_hashes}
                    self._cached_map_revision = self._known_hashes_revision
        return self._cached_map

    def _expected_active_id_multiset_unlocked(self) -> Counter[int]:
        owners: Dict[int, str] = {}
        active_ids: List[int] = []
        for hash_value in self._known_hashes:
            int_id = self._generate_id(hash_value)
            if int_id in self._deleted_ids:
                continue
            owner = owners.get(int_id)
            if owner is not None and owner != hash_value:
                raise RuntimeError(f"向量 int64 ID 冲突: {owner} / {hash_value}")
            owners[int_id] = hash_value
            active_ids.append(int_id)
        return Counter(active_ids)

    @staticmethod
    def _faiss_id_multiset(index: Any) -> Counter[int]:
        raw_ids = faiss.vector_to_array(index.id_map)
        if len(raw_ids) != int(index.ntotal):
            raise RuntimeError("Faiss IndexIDMap2 的 id_map 长度与 ntotal 不一致")
        return Counter(int(raw_id) for raw_id in raw_ids)

    def _loaded_index_matches_metadata_unlocked(self) -> bool:
        if not isinstance(self._index, faiss.IndexIDMap2):
            return False
        return self._faiss_id_multiset(self._index) == self._expected_active_id_multiset_unlocked()

    def _rebuild_loaded_index_unlocked(self) -> None:
        """从已校验的 pair 按 tombstone 重建索引，并反向校验 ID 多重集。"""
        self._init_index()
        self._init_fallback_index()
        if self._bin_count > 0:
            self._force_train_small_data()
        actual_ids = self._faiss_id_multiset(self._index)
        expected_ids = self._expected_active_id_multiset_unlocked()
        if actual_ids != expected_ids:
            missing_count = sum((expected_ids - actual_ids).values())
            unexpected_count = sum((actual_ids - expected_ids).values())
            raise RuntimeError(
                "向量索引按成对文件重建后仍与元数据不一致: "
                f"missing={missing_count}, unexpected={unexpected_count}"
            )

    def _disk_id_multiset_unlocked(self) -> Counter[int]:
        if not self._ids_bin_path.exists():
            return Counter()
        return Counter(int(raw_id) for raw_id in np.fromfile(self._ids_bin_path, dtype=">i8"))

    def _migrate_vector_metadata_v1_unlocked(
        self,
        meta_path: Path,
        meta: Dict[str, Any],
        *,
        vector_bytes: int,
        id_bytes: int,
    ) -> Dict[str, Any]:
        """V1 无提交代际；只有全量 ID 与 pair 严格一致时才单向升级。"""
        raw_known_hashes = meta.get("known_hashes", meta.get("ids", []))
        if not isinstance(raw_known_hashes, list) or any(
            not isinstance(hash_value, str) or not hash_value for hash_value in raw_known_hashes
        ):
            raise RuntimeError("V1 向量元数据的 known_hashes 无效，拒绝升级")
        expected_ids = Counter(self._generate_id(hash_value) for hash_value in raw_known_hashes)
        if len(expected_ids) != len(raw_known_hashes):
            raise RuntimeError("V1 向量元数据存在重复哈希或 int64 ID 冲突，拒绝升级")
        actual_ids = self._disk_id_multiset_unlocked()
        if actual_ids != expected_ids:
            missing_count = sum((expected_ids - actual_ids).values())
            unexpected_count = sum((actual_ids - expected_ids).values())
            raise RuntimeError(
                "V1 向量元数据与成对文件不一致，拒绝升级: "
                f"missing={missing_count}, unexpected={unexpected_count}"
            )

        raw_deleted_ids = meta.get("deleted_ids", [])
        if not isinstance(raw_deleted_ids, list) or any(
            not isinstance(int_id, int) or isinstance(int_id, bool) for int_id in raw_deleted_ids
        ):
            raise RuntimeError("V1 向量元数据的 deleted_ids 无效，拒绝升级")
        deleted_ids = set(raw_deleted_ids)
        if not deleted_ids.issubset(expected_ids):
            raise RuntimeError("V1 向量元数据存在无对应向量的 tombstone，拒绝升级")

        metadata_dimension = meta.get("dimension", self.dimension)
        if metadata_dimension != self.dimension or isinstance(metadata_dimension, bool):
            raise RuntimeError(
                f"V1 向量维度与当前实例不一致: {metadata_dimension!r} != {self.dimension}"
            )

        self._known_hashes = set(raw_known_hashes)
        self._deleted_ids = deleted_ids
        self._invalidate_id_map()
        self._is_trained = bool(meta.get("is_trained", False))
        self._vector_norm = "l2"
        if self._is_trained:
            index_path = self.data_dir / "vectors.index"
            index_is_valid = False
            if index_path.exists() and meta.get("vector_norm") == "l2":
                try:
                    self._index = faiss.read_index(str(index_path))
                    index_is_valid = self._loaded_index_matches_metadata_unlocked()
                except Exception:
                    index_is_valid = False
            if not index_is_valid:
                self._rebuild_loaded_index_unlocked()
                with atomic_save_path(index_path) as tmp:
                    faiss.write_index(self._index, tmp)

        migrated_meta = dict(meta)
        migrated_meta["known_hashes"] = list(raw_known_hashes)
        migrated_meta["deleted_ids"] = list(deleted_ids)
        migrated_meta["dimension"] = self.dimension
        migrated_meta["is_trained"] = self._is_trained
        migrated_meta["vector_norm"] = "l2"
        migrated_meta["schema_version"] = 2
        migrated_meta["binary_commit"] = {
            "transaction_id": f"metadata-v1-migration-{uuid.uuid4().hex}",
            "vector_bytes": vector_bytes,
            "id_bytes": id_bytes,
        }
        _write_json_object(meta_path, migrated_meta)
        return migrated_meta

    def _validate_or_migrate_vector_metadata_unlocked(
        self,
        meta_path: Path,
        meta: Dict[str, Any],
        *,
        vector_bytes: int,
        id_bytes: int,
    ) -> Dict[str, Any]:
        schema_version = meta.get("schema_version", 1)
        if schema_version == 1 and not isinstance(schema_version, bool):
            return self._migrate_vector_metadata_v1_unlocked(
                meta_path,
                meta,
                vector_bytes=vector_bytes,
                id_bytes=id_bytes,
            )
        if schema_version != 2 or isinstance(schema_version, bool):
            raise RuntimeError(f"不支持的向量元数据版本: {schema_version!r}")

        binary_commit = meta.get("binary_commit")
        if not isinstance(binary_commit, dict):
            raise RuntimeError("V2 向量元数据缺少 binary_commit")
        transaction_id = binary_commit.get("transaction_id")
        if transaction_id is not None and (not isinstance(transaction_id, str) or not transaction_id):
            raise RuntimeError("V2 向量元数据的 transaction_id 无效")
        committed_vector_bytes = binary_commit.get("vector_bytes")
        committed_id_bytes = binary_commit.get("id_bytes")
        if (
            not isinstance(committed_vector_bytes, int)
            or isinstance(committed_vector_bytes, bool)
            or not isinstance(committed_id_bytes, int)
            or isinstance(committed_id_bytes, bool)
            or committed_vector_bytes != vector_bytes
            or committed_id_bytes != id_bytes
        ):
            raise RuntimeError(
                "V2 向量元数据提交长度与成对文件不一致: "
                f"metadata=({committed_vector_bytes}, {committed_id_bytes}), "
                f"pair=({vector_bytes}, {id_bytes})"
            )
        return meta

    def add(self, vectors: np.ndarray, ids: List[str]) -> int:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            vector_array = np.asarray(vectors)
            if vector_array.ndim != 2:
                raise ValueError(f"vectors must have shape (N, D), got {tuple(vector_array.shape)}")
            if vector_array.shape[0] != len(ids):
                raise ValueError(f"Vector/ID count mismatch: {vector_array.shape[0]} vs {len(ids)}")
            if vector_array.shape[1] != self.dimension:
                raise ValueError(f"Dimension mismatch: {vector_array.shape[1]} vs {self.dimension}")
            if any(not isinstance(str_id, str) or not str_id for str_id in ids):
                raise ValueError("Vector IDs must be non-empty strings")
            if len(set(ids)) != len(ids):
                raise ValueError("Vector IDs must be unique within one add batch")

            vector_array = np.array(vector_array, dtype=np.float32, order="C", copy=True)
            if not np.all(np.isfinite(vector_array)):
                raise ValueError("vectors contain non-finite values")
            vector_norms = np.linalg.norm(vector_array, axis=1)
            if np.any(vector_norms <= 1e-12):
                raise ValueError("vectors contain zero-norm rows")
            faiss.normalize_L2(vector_array)

            tombstoned_ids = [
                str_id
                for str_id in ids
                if str_id in self._known_hashes and self._generate_id(str_id) in self._deleted_ids
            ]
            if tombstoned_ids:
                raise ValueError(
                    f"{len(tombstoned_ids)} 个向量 ID 已被删除，请先调用 restore() 恢复"
                )

            pending = [
                (str_id, vector_array[index], self._generate_id(str_id))
                for index, str_id in enumerate(ids)
                if str_id not in self._known_hashes
            ]
            if not pending:
                return 0

            processed_str_ids = [str_id for str_id, _, _ in pending]
            processed_vecs = [vector for _, vector, _ in pending]
            processed_int_ids = [int_id for _, _, int_id in pending]

            batch_vecs = np.array(processed_vecs, dtype=np.float32)
            batch_ids = np.array(processed_int_ids, dtype=np.int64)

            self._write_buffer_vecs.append(batch_vecs)
            self._write_buffer_ids.extend(processed_int_ids)
            self._known_hashes.update(processed_str_ids)
            self._invalidate_id_map()

            if len(self._write_buffer_ids) >= self.buffer_size:
                self._flush_write_buffer_unlocked()

            if not self._is_trained:
                # 未训练阶段由 flush 统一写入回退索引。这里提前写入会导致
                # 同一批向量在后续 search/save 刷新缓冲区时重复入索引。
                self._update_reservoir(batch_vecs)
                # 这里的 TRAIN_SIZE 取默认 10k，或者根据当前数据量动态判断
                if len(self._reservoir_buffer) >= 10000:
                    logger.info("训练样本达到上限，开始训练...")
                    self._train_and_replay_unlocked()

            self._total_added += len(batch_ids)
            return len(batch_ids)

    def _flush_write_buffer(self):
        with self._lock:
            self._flush_write_buffer_unlocked()

    def _flush_write_buffer_unlocked(self):
        if not self._write_buffer_vecs:
            return

        batch_vecs = np.concatenate(self._write_buffer_vecs, axis=0)
        batch_ids = np.array(self._write_buffer_ids, dtype=np.int64)
        vecs_fp16 = batch_vecs.astype(np.float16)
        vector_bytes = vecs_fp16.tobytes()
        ids_bytes = batch_ids.astype(">i8").tobytes()

        journal = self._read_append_journal_unlocked()
        if journal is None:
            current_vector_bytes, current_id_bytes = self._vector_pair_sizes_unlocked()
            journal = {
                "version": 1,
                "transaction_id": uuid.uuid4().hex,
                "base_vector_bytes": current_vector_bytes,
                "base_id_bytes": current_id_bytes,
                "target_vector_bytes": current_vector_bytes,
                "target_id_bytes": current_id_bytes,
            }
        else:
            current_vector_bytes = self._bin_path.stat().st_size if self._bin_path.exists() else 0
            current_id_bytes = self._ids_bin_path.stat().st_size if self._ids_bin_path.exists() else 0
            if (
                current_vector_bytes != int(journal["target_vector_bytes"])
                or current_id_bytes != int(journal["target_id_bytes"])
            ):
                raise RuntimeError("上一次向量追加未完成，请重启并执行 load() 恢复")

            meta_path = self.data_dir / "vectors_metadata.json"
            meta = _read_json_object(meta_path) if meta_path.exists() else {}
            if self._metadata_commits_append(meta, journal):
                # 元数据已成为上一笔追加的持久化提交记录。
                # 即使上一次日志删除被中断，也不能继续沿用其基线。
                self._append_journal_path.unlink()
                journal = {
                    "version": 1,
                    "transaction_id": uuid.uuid4().hex,
                    "base_vector_bytes": current_vector_bytes,
                    "base_id_bytes": current_id_bytes,
                    "target_vector_bytes": current_vector_bytes,
                    "target_id_bytes": current_id_bytes,
                }

        journal["target_vector_bytes"] = current_vector_bytes + len(vector_bytes)
        journal["target_id_bytes"] = current_id_bytes + len(ids_bytes)
        _write_json_object(self._append_journal_path, journal)

        try:
            self._sync_append(self._bin_path, vector_bytes)
            self._sync_append(self._ids_bin_path, ids_bytes)

            if self._is_trained and self._index.is_trained:
                self._index.add_with_ids(batch_vecs, batch_ids)
            else:
                # 即使在 flush 时，如果未训练，也要同步到 fallback
                self._fallback_index.add_with_ids(batch_vecs, batch_ids)
        except Exception:
            # 普通进程内异常会精确回滚当前批次，缓冲区保留供调用方重试。
            # BaseException 代表进程级中断，留给 load() 按持久化日志恢复。
            self._rollback_append_tail_unlocked(
                journal,
                vector_bytes=current_vector_bytes,
                id_bytes=current_id_bytes,
            )
            if self._index.ntotal > 0:
                self._index.remove_ids(batch_ids)
            if self._fallback_index.ntotal > 0:
                self._fallback_index.remove_ids(batch_ids)
            raise

        self._bin_count += len(batch_ids)
        self._invalidate_id_offsets()

        self._write_buffer_vecs.clear()
        self._write_buffer_ids.clear()

    def _update_reservoir(self, vectors: np.ndarray):
        for vec in vectors:
            self._seen_count_for_reservoir += 1
            if len(self._reservoir_buffer) < self.RESERVOIR_CAPACITY:
                self._reservoir_buffer.append(vec)
            else:
                if self._seen_count_for_reservoir <= self.RESERVOIR_SAMPLE_SCOPE:
                    r = random.randint(0, self._seen_count_for_reservoir - 1)
                    if r < self.RESERVOIR_CAPACITY:
                        self._reservoir_buffer[r] = vec

    def _train_and_replay(self):
        with self._lock:
            self._train_and_replay_unlocked()

    def _train_and_replay_unlocked(self):
        if not self._reservoir_buffer:
            logger.warning("No training data available.")
            return

        train_data = np.array(self._reservoir_buffer, dtype=np.float32)
        logger.info(f"Training Index with {len(train_data)} samples...")

        try:
            self._index.train(train_data)
        except Exception as e:
            logger.error(f"SQ8 Training failed: {e}. Staying in fallback mode.")
            return

        self._is_trained = True
        self._reservoir_buffer = []

        logger.info("Replaying data from disk to populate index...")
        try:
            replay_count = self._replay_vectors_to_index()
            # 只有当 replay 成功且数据量一致时，才释放回退索引
            if self._index.ntotal >= self._bin_count:
                logger.info(f"Replay successful ({replay_count}/{self._bin_count}). Releasing fallback index.")
                self._fallback_index.reset()
            else:
                logger.warning(
                    f"Replay count mismatch: {self._index.ntotal} vs {self._bin_count}. Keeping fallback index."
                )
        except Exception as e:
            logger.error(f"Replay failed: {e}. Keeping fallback index as backup.")

    def _replay_vectors_to_index(self) -> int:
        """从 vectors.bin 读取并添加到 index"""
        if not self._bin_path.exists() or not self._ids_bin_path.exists():
            return 0

        vec_item_size = self.dimension * 2
        id_item_size = 8
        chunk_size = 10000

        replay_count = 0
        with open(self._bin_path, "rb") as f_vec, open(self._ids_bin_path, "rb") as f_id:
            while True:
                vec_data = f_vec.read(chunk_size * vec_item_size)
                id_data = f_id.read(chunk_size * id_item_size)

                if not vec_data:
                    break

                batch_fp16 = np.frombuffer(vec_data, dtype=np.float16).reshape(-1, self.dimension)
                batch_fp32 = batch_fp16.astype(np.float32)
                faiss.normalize_L2(batch_fp32)

                batch_ids = np.frombuffer(id_data, dtype=">i8").astype(np.int64)

                valid_mask = [id_ not in self._deleted_ids for id_ in batch_ids]
                if not all(valid_mask):
                    batch_fp32 = batch_fp32[valid_mask]
                    batch_ids = batch_ids[valid_mask]

                if len(batch_ids) > 0:
                    self._index.add_with_ids(batch_fp32, batch_ids)
                    replay_count += len(batch_ids)

        return replay_count

    def search(
        self,
        query: np.ndarray,
        k: int = 10,
        filter_deleted: bool = True,
    ) -> Tuple[List[str], List[float]]:
        query_local = np.array(query, dtype=np.float32, order="C", copy=True)
        if query_local.ndim == 1:
            got_dim = int(query_local.shape[0])
            query_local = query_local.reshape(1, -1)
        elif query_local.ndim == 2:
            if query_local.shape[0] != 1:
                raise ValueError(f"query embedding must have shape (D,) or (1, D), got {tuple(query_local.shape)}")
            got_dim = int(query_local.shape[1])
        else:
            raise ValueError(f"query embedding must have shape (D,) or (1, D), got {tuple(query_local.shape)}")

        if got_dim != self.dimension:
            raise ValueError(f"query embedding dimension mismatch: expected={self.dimension} got={got_dim}")
        if not np.all(np.isfinite(query_local)):
            raise ValueError("query embedding contains non-finite values")

        faiss.normalize_L2(query_local)

        # 查询路径仅负责检索，不在此触发训练/回放。
        # 训练/回放前置到 warmup_index()，并由插件启动阶段触发。
        # Faiss 索引在并发 search 下可能出现阻塞，这里串行化检索调用保证稳定性。
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            self._flush_write_buffer_unlocked()
            search_index = self._index if (self._is_trained and self._index.ntotal > 0) else self._fallback_index
            if search_index.ntotal == 0:
                logger.warning("Indices are empty. No data to search.")
                return [], []
            # 执行检索
            dists, ids = search_index.search(query_local, k * 2)

        # Faiss search 返回的是 (1, K) 的数组，取第一行
        dists = dists[0]
        ids = ids[0]

        results = []
        for id_val, score in zip(ids, dists, strict=True):
            if id_val == -1:
                continue
            if filter_deleted and id_val in self._deleted_ids:
                continue

            str_id = self._int_to_str_map.get(id_val)
            if str_id:
                results.append((str_id, float(score)))

        # 过滤可能减少结果数量，因此重新排序并截断。
        results.sort(key=lambda x: x[1], reverse=True)
        results = results[:k]

        if not results:
            return [], []

        return [r[0] for r in results], [r[1] for r in results]

    def get_vectors(self, ids: Sequence[str]) -> Dict[str, np.ndarray]:
        """按字符串 ID 读取已持久化向量，用于无 embedding 的池间迁移。"""
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
        return {key: vector for batch in self.iter_vectors_by_ids(ids) for key, vector in batch.items()}

    def iter_vectors_by_ids(
        self,
        ids: Sequence[str],
        *,
        batch_size: int = 1024,
    ) -> Iterator[Dict[str, np.ndarray]]:
        """按字符串 ID 分批读取持久化向量，避免迁移时把全部向量集中留在内存。"""
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
        requested_ids = [str(item or "").strip() for item in ids if str(item or "").strip()]
        if not requested_ids:
            return

        safe_batch_size = max(1, int(batch_size or 1024))
        unique_ids = list(dict.fromkeys(requested_ids))
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            self._flush_write_buffer_unlocked()
            known_hashes = set(self._known_hashes)
            deleted_ids = set(self._deleted_ids)
            bin_path = self._bin_path
            ids_bin_path = self._ids_bin_path
            dimension = int(self.dimension)

        int_to_str: Dict[int, str] = {}
        for str_id in unique_ids:
            if str_id not in known_hashes:
                continue
            int_id = self._generate_id(str_id)
            if int_id in deleted_ids:
                continue
            int_to_str[int_id] = str_id

        if not int_to_str or not bin_path.exists() or not ids_bin_path.exists():
            return

        result: Dict[str, np.ndarray] = {}
        vec_item_size = dimension * 2
        id_item_size = 8
        chunk_size = 10000

        with open(bin_path, "rb") as f_vec, open(ids_bin_path, "rb") as f_id:
            while True:
                vec_data = f_vec.read(chunk_size * vec_item_size)
                id_data = f_id.read(chunk_size * id_item_size)
                if not vec_data:
                    break

                batch_fp16 = np.frombuffer(vec_data, dtype=np.float16).reshape(-1, dimension)
                batch_fp32 = batch_fp16.astype(np.float32)
                faiss.normalize_L2(batch_fp32)
                batch_ids = np.frombuffer(id_data, dtype=">i8").astype(np.int64)

                for index, int_id in enumerate(batch_ids):
                    int_key = int(int_id)
                    key = int_to_str.pop(int_key, None)
                    if key is None or int_key in deleted_ids:
                        continue
                    result[key] = np.array(batch_fp32[index], dtype=np.float32, copy=True)
                    if len(result) >= safe_batch_size:
                        yield result
                        result = {}
                    if not int_to_str:
                        break
                if not int_to_str:
                    break

        if result:
            yield result

    def _get_vectors_chunk(self, requested_ids: Sequence[str]) -> Dict[str, np.ndarray]:
        """读取一批向量，调用方负责控制 batch 大小。"""
        if not requested_ids:
            return {}

        with self._lock:
            self._flush_write_buffer_unlocked()
            int_to_str: Dict[int, str] = {}
            for str_id in requested_ids:
                if str_id not in self._known_hashes:
                    continue
                int_id = self._generate_id(str_id)
                if int_id in self._deleted_ids:
                    continue
                int_to_str[int_id] = str_id

            if not int_to_str or not self._bin_path.exists() or not self._ids_bin_path.exists():
                return {}

            result: Dict[str, np.ndarray] = {}
            vec_item_size = self.dimension * 2
            id_item_size = 8
            chunk_size = 10000

            with open(self._bin_path, "rb") as f_vec, open(self._ids_bin_path, "rb") as f_id:
                while True:
                    vec_data = f_vec.read(chunk_size * vec_item_size)
                    id_data = f_id.read(chunk_size * id_item_size)
                    if not vec_data:
                        break

                    batch_fp16 = np.frombuffer(vec_data, dtype=np.float16).reshape(-1, self.dimension)
                    batch_fp32 = batch_fp16.astype(np.float32)
                    faiss.normalize_L2(batch_fp32)
                    batch_ids = np.frombuffer(id_data, dtype=">i8").astype(np.int64)

                    for index, int_id in enumerate(batch_ids):
                        key = int_to_str.get(int(int_id))
                        if key is None or key in result or int_id in self._deleted_ids:
                            continue
                        result[key] = np.array(batch_fp32[index], dtype=np.float32, copy=True)
                        if len(result) >= len(int_to_str):
                            return result

            return result

    def warmup_index(self, force_train: bool = True) -> Dict[str, Any]:
        """
        预热向量索引（训练/回放前置），避免首个线上查询触发重初始化。

        Args:
            force_train: 是否在满足阈值时强制训练 SQ8 索引

        Returns:
            预热状态摘要
        """
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
        started = time.perf_counter()
        logger.debug(f"metric.vector_index_prewarm_started=1 force_train={bool(force_train)}")

        try:
            with self._lock:
                self._raise_if_cleanup_checkpoint_broken_unlocked()
                self._flush_write_buffer()

                if self._bin_path.exists():
                    self._bin_count = self._bin_path.stat().st_size // (self.dimension * 2)
                else:
                    self._bin_count = 0

                needs_fallback_bootstrap = (
                    self._bin_count > 0
                    and self._fallback_index.ntotal == 0
                    and (not self._is_trained or self._index.ntotal == 0)
                )
                if needs_fallback_bootstrap:
                    self._bootstrap_fallback_from_disk()

                min_train = max(1, int(getattr(self, "min_train_threshold", self.DEFAULT_MIN_TRAIN)))
                needs_train = bool(force_train) and self._bin_count >= min_train and not self._is_trained
                if needs_train:
                    self._force_train_small_data()

                duration_ms = (time.perf_counter() - started) * 1000.0
                summary = {
                    "ok": True,
                    "trained": bool(self._is_trained),
                    "index_ntotal": int(self._index.ntotal),
                    "fallback_ntotal": int(self._fallback_index.ntotal),
                    "bin_count": int(self._bin_count),
                    "duration_ms": duration_ms,
                    "error": None,
                }
        except Exception as e:
            duration_ms = (time.perf_counter() - started) * 1000.0
            summary = {
                "ok": False,
                "trained": bool(self._is_trained),
                "index_ntotal": int(self._index.ntotal) if self._index is not None else 0,
                "fallback_ntotal": int(self._fallback_index.ntotal) if self._fallback_index is not None else 0,
                "bin_count": int(getattr(self, "_bin_count", 0)),
                "duration_ms": duration_ms,
                "error": str(e),
            }
            logger.error(
                "metric.vector_index_prewarm_fail=1 "
                f"metric.vector_index_prewarm_duration_ms={duration_ms:.2f} "
                f"error={e}"
            )
            return summary

        logger.debug(
            "metric.vector_index_prewarm_success=1 "
            f"metric.vector_index_prewarm_duration_ms={summary['duration_ms']:.2f} "
            f"trained={summary['trained']} "
            f"index_ntotal={summary['index_ntotal']} "
            f"fallback_ntotal={summary['fallback_ntotal']} "
            f"bin_count={summary['bin_count']}"
        )
        return summary

    def _bootstrap_fallback_from_disk(self):
        with self._lock:
            self._bootstrap_fallback_from_disk_unlocked()

    def _bootstrap_fallback_from_disk_unlocked(self):
        """重启后自举：从磁盘 vectors.bin 加载数据到 fallback 索引"""
        if not self._bin_path.exists() or not self._ids_bin_path.exists():
            return

        logger.info("Replaying all disk vectors to fallback index...")
        vec_item_size = self.dimension * 2
        id_item_size = 8
        chunk_size = 10000

        with open(self._bin_path, "rb") as f_vec, open(self._ids_bin_path, "rb") as f_id:
            while True:
                vec_data = f_vec.read(chunk_size * vec_item_size)
                id_data = f_id.read(chunk_size * id_item_size)
                if not vec_data:
                    break

                batch_fp16 = np.frombuffer(vec_data, dtype=np.float16).reshape(-1, self.dimension)
                batch_fp32 = batch_fp16.astype(np.float32)
                faiss.normalize_L2(batch_fp32)
                batch_ids = np.frombuffer(id_data, dtype=">i8").astype(np.int64)

                valid_mask = [id_ not in self._deleted_ids for id_ in batch_ids]
                if any(valid_mask):
                    self._fallback_index.add_with_ids(batch_fp32[valid_mask], batch_ids[valid_mask])

        logger.info(f"Fallback index self-bootstrapped with {self._fallback_index.ntotal} items.")

    def _force_train_small_data(self):
        with self._lock:
            self._force_train_small_data_unlocked()

    def _force_train_small_data_unlocked(self):
        logger.info("Forcing training on small dataset...")
        self._reservoir_buffer = []

        chunk_size = 10000
        vec_item_size = self.dimension * 2

        with open(self._bin_path, "rb") as f:
            while len(self._reservoir_buffer) < self.TRAIN_SIZE:
                data = f.read(chunk_size * vec_item_size)
                if not data:
                    break
                fp16 = np.frombuffer(data, dtype=np.float16).reshape(-1, self.dimension)
                fp32 = fp16.astype(np.float32)
                faiss.normalize_L2(fp32)

                for vec in fp32:
                    self._reservoir_buffer.append(vec)
                    if len(self._reservoir_buffer) >= self.TRAIN_SIZE:
                        break

        self._train_and_replay_unlocked()

    def _ensure_id_offsets_unlocked(self) -> None:
        """建立磁盘 ID 到行号的唯一映射，供恢复任务随机读取。"""
        bin_exists = self._bin_path.exists()
        ids_exists = self._ids_bin_path.exists()
        if not bin_exists and not ids_exists:
            self._id_offsets = {}
            self._id_offsets_count = 0
            return
        if not self._vector_pair_matches(self._bin_path, self._ids_bin_path):
            raise RuntimeError("向量数据文件与 ID 文件不一致，拒绝恢复")

        count = self._ids_bin_path.stat().st_size // 8
        if self._id_offsets_count == count:
            return
        raw_ids = np.fromfile(self._ids_bin_path, dtype=">i8")
        id_offsets: Dict[int, int] = {}
        for offset, raw_int_id in enumerate(raw_ids):
            int_id = int(raw_int_id)
            if int_id in id_offsets:
                raise RuntimeError(f"向量磁盘文件存在重复 int64 ID: {int_id}")
            id_offsets[int_id] = offset
        self._id_offsets = id_offsets
        self._id_offsets_count = len(raw_ids)

    def _read_persisted_vectors_unlocked(self, int_ids: Set[int]) -> Dict[int, np.ndarray]:
        """从成对二进制文件中读取指定 ID，包括已被 tombstone 的向量。"""
        if not int_ids:
            return {}
        self._ensure_id_offsets_unlocked()
        if not self._id_offsets:
            return {}

        result: Dict[int, np.ndarray] = {}
        vector_item_size = self.dimension * 2
        with open(self._bin_path, "rb") as vector_file:
            for int_id in int_ids:
                offset = self._id_offsets.get(int_id)
                if offset is None:
                    continue
                vector_file.seek(offset * vector_item_size)
                vector_data = vector_file.read(vector_item_size)
                if len(vector_data) != vector_item_size:
                    raise RuntimeError(f"向量数据读取不完整: int_id={int_id}")
                result[int_id] = np.frombuffer(vector_data, dtype=np.float16).astype(
                    np.float32,
                )
        return result

    def restore(self, ids: Sequence[str]) -> int:
        """恢复尚未被磁盘压缩清除的向量，不向 append-only 文件写入重复 ID。"""
        if any(not isinstance(str_id, str) or not str_id for str_id in ids):
            raise ValueError("Vector IDs must be non-empty strings")
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return 0

        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            self._flush_write_buffer_unlocked()
            int_to_str: Dict[int, str] = {}
            for str_id in unique_ids:
                int_id = self._generate_id(str_id)
                if str_id not in self._known_hashes or int_id not in self._deleted_ids:
                    continue
                colliding_id = int_to_str.get(int_id)
                if colliding_id is not None and colliding_id != str_id:
                    raise RuntimeError(f"向量 ID 冲突: {colliding_id} / {str_id}")
                int_to_str[int_id] = str_id
            if not int_to_str:
                return 0

            persisted = self._read_persisted_vectors_unlocked(set(int_to_str))
            if not persisted:
                return 0

            restored_int_ids = list(persisted)
            restored_vectors = np.asarray(
                [persisted[int_id] for int_id in restored_int_ids],
                dtype=np.float32,
            )
            faiss.normalize_L2(restored_vectors)
            restored_ids_array = np.asarray(restored_int_ids, dtype=np.int64)

            # 删除可能残留的索引项后再写回，保证每个 int64 ID 只有一个活动条目。
            if self._index.ntotal > 0:
                self._index.remove_ids(restored_ids_array)
            if self._fallback_index.ntotal > 0:
                self._fallback_index.remove_ids(restored_ids_array)
            try:
                if self._is_trained and self._index.is_trained:
                    self._index.add_with_ids(restored_vectors, restored_ids_array)
                else:
                    self._fallback_index.add_with_ids(restored_vectors, restored_ids_array)
            except Exception:
                # 写回失败时保持 tombstone，避免成员状态显示为已恢复。
                if self._index.ntotal > 0:
                    self._index.remove_ids(restored_ids_array)
                if self._fallback_index.ntotal > 0:
                    self._fallback_index.remove_ids(restored_ids_array)
                raise

            self._deleted_ids.difference_update(restored_int_ids)
            return len(restored_int_ids)

    def is_tombstoned(self, hash_value: str) -> bool:
        """判断向量是否存在于持久化成员集中但当前已被删除。"""
        if not isinstance(hash_value, str) or not hash_value:
            raise ValueError("Vector ID must be a non-empty string")
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            return (
                hash_value in self._known_hashes
                and self._generate_id(hash_value) in self._deleted_ids
            )

    def begin_cleanup_checkpoint(self) -> str:
        """锁定一个同步清理批次，并记录最后一次正式提交的恢复基线。"""
        self._lock.acquire()
        try:
            if self._cleanup_checkpoint_error is not None:
                raise RuntimeError(
                    f"向量清理 checkpoint 已损坏，必须重建 VectorStore 实例: {self._cleanup_checkpoint_error}"
                )
            if self._cleanup_checkpoint is not None:
                raise RuntimeError("同一向量池不允许嵌套 cleanup checkpoint")
            if self._write_buffer_vecs or self._write_buffer_ids:
                raise RuntimeError("cleanup checkpoint 前必须先提交向量写缓冲区")
            if self._append_journal_path.exists() or self._compaction_journal_path.exists():
                raise RuntimeError("cleanup checkpoint 前存在未完成的向量文件事务")

            vector_bytes, id_bytes = self._vector_pair_sizes_unlocked()
            meta_path = self.data_dir / "vectors_metadata.json"
            if not meta_path.exists():
                raise RuntimeError("cleanup checkpoint 缺少已提交的向量元数据")
            meta = self._validate_or_migrate_vector_metadata_unlocked(
                meta_path,
                _read_json_object(meta_path),
                vector_bytes=vector_bytes,
                id_bytes=id_bytes,
            )
            baseline_known_hashes = set(meta.get("known_hashes", []))
            baseline_deleted_ids = set(meta.get("deleted_ids", []))
            if baseline_known_hashes != self._known_hashes or baseline_deleted_ids != self._deleted_ids:
                raise RuntimeError("cleanup checkpoint 的内存成员集与已提交元数据不一致")

            checkpoint_token = uuid.uuid4().hex
            self._cleanup_checkpoint = {
                "token": checkpoint_token,
                "vector_bytes": vector_bytes,
                "id_bytes": id_bytes,
                "pair_existed": self._bin_path.exists(),
                "metadata": meta,
                "known_hashes": baseline_known_hashes,
                "deleted_ids": baseline_deleted_ids,
                "reservoir_buffer": [
                    np.array(vector, dtype=np.float32, copy=True)
                    for vector in self._reservoir_buffer
                ],
                "seen_count_for_reservoir": int(self._seen_count_for_reservoir),
                "total_added": int(self._total_added),
                "total_deleted": int(self._total_deleted),
            }
            self._cleanup_compaction_deferred = False
            return checkpoint_token
        except Exception:
            self._lock.release()
            raise

    def _require_cleanup_checkpoint_unlocked(self, checkpoint_token: str) -> Dict[str, Any]:
        checkpoint = self._cleanup_checkpoint
        if checkpoint is None:
            raise RuntimeError("当前没有活动的 cleanup checkpoint")
        if str(checkpoint.get("token", "")) != str(checkpoint_token or ""):
            raise RuntimeError("cleanup checkpoint token 不匹配")
        return checkpoint

    def commit_cleanup_checkpoint(self, checkpoint_token: str) -> None:
        """确认 checkpoint 内的变更已经由 save() 正式提交。"""
        self._require_cleanup_checkpoint_unlocked(checkpoint_token)
        if self._append_journal_path.exists() or self._compaction_journal_path.exists():
            raise RuntimeError("cleanup checkpoint 提交时仍有未完成的向量文件事务")
        vector_bytes, id_bytes = self._vector_pair_sizes_unlocked()
        meta_path = self.data_dir / "vectors_metadata.json"
        meta = self._validate_or_migrate_vector_metadata_unlocked(
            meta_path,
            _read_json_object(meta_path),
            vector_bytes=vector_bytes,
            id_bytes=id_bytes,
        )
        if set(meta.get("known_hashes", [])) != self._known_hashes:
            raise RuntimeError("cleanup checkpoint 提交后的 known_hashes 不一致")
        if set(meta.get("deleted_ids", [])) != self._deleted_ids:
            raise RuntimeError("cleanup checkpoint 提交后的 tombstone 不一致")
        self._cleanup_checkpoint = None
        # checkpoint 只延后本批次内的自动压缩；逻辑提交完成后清除一次性标志。
        # 后续普通删除仍会按当前 tombstone 比例重新触发压缩判定。
        self._cleanup_compaction_deferred = False
        self._lock.release()

    def rollback_cleanup_checkpoint(self, checkpoint_token: str) -> None:
        """回滚 append 尾部和内存索引，恢复 checkpoint 前的正式提交态。"""
        rollback_error: Optional[Exception] = None
        try:
            checkpoint = self._require_cleanup_checkpoint_unlocked(checkpoint_token)
            if self._compaction_journal_path.exists():
                raise RuntimeError("cleanup checkpoint 期间出现了不允许的向量压缩事务")

            vector_bytes = int(checkpoint["vector_bytes"])
            id_bytes = int(checkpoint["id_bytes"])
            self._truncate_and_sync(self._bin_path, vector_bytes)
            self._truncate_and_sync(self._ids_bin_path, id_bytes)
            if not bool(checkpoint["pair_existed"]):
                self._bin_path.unlink(missing_ok=True)
                self._ids_bin_path.unlink(missing_ok=True)
            self._append_journal_path.unlink(missing_ok=True)
            (self.data_dir / "vectors.index").unlink(missing_ok=True)
            _write_json_object(
                self.data_dir / "vectors_metadata.json",
                dict(checkpoint["metadata"]),
            )

            self._init_index()
            self._init_fallback_index()
            self._known_hashes.clear()
            self._deleted_ids.clear()
            self._invalidate_id_map()
            self._invalidate_id_offsets()
            self._write_buffer_vecs.clear()
            self._write_buffer_ids.clear()
            self._reservoir_buffer.clear()
            self._seen_count_for_reservoir = 0
            self._bin_count = 0
            self.load()
            warmup = self.warmup_index(force_train=False)
            if not bool(warmup.get("ok")):
                raise RuntimeError(f"cleanup checkpoint 回滚后索引预热失败: {warmup.get('error')}")

            self._reservoir_buffer = [
                np.array(vector, dtype=np.float32, copy=True)
                for vector in checkpoint["reservoir_buffer"]
            ]
            self._seen_count_for_reservoir = int(checkpoint["seen_count_for_reservoir"])
            self._total_added = int(checkpoint["total_added"])
            self._total_deleted = int(checkpoint["total_deleted"])
            if self._known_hashes != checkpoint["known_hashes"]:
                raise RuntimeError("cleanup checkpoint 回滚后的 known_hashes 不一致")
            if self._deleted_ids != checkpoint["deleted_ids"]:
                raise RuntimeError("cleanup checkpoint 回滚后的 tombstone 不一致")
            if self._write_buffer_vecs or self._write_buffer_ids:
                raise RuntimeError("cleanup checkpoint 回滚后残留向量写缓冲区")
            restored_vector_bytes, restored_id_bytes = self._vector_pair_sizes_unlocked()
            if restored_vector_bytes != vector_bytes or restored_id_bytes != id_bytes:
                raise RuntimeError("cleanup checkpoint 回滚后的成对文件长度不一致")
            self._cleanup_checkpoint = None
            self._cleanup_compaction_deferred = False
        except Exception as exc:
            rollback_error = exc
            self._cleanup_checkpoint_error = str(exc)
            self._cleanup_checkpoint = None
        finally:
            self._lock.release()
        if rollback_error is not None:
            raise RuntimeError("cleanup checkpoint 回滚失败") from rollback_error

    def delete(self, ids: List[str]) -> int:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            count = 0
            for str_id in ids:
                if str_id not in self._known_hashes:
                    continue
                int_id = self._generate_id(str_id)
                if int_id not in self._deleted_ids:
                    self._deleted_ids.add(int_id)
                    if self._index.is_trained:
                        self._index.remove_ids(np.array([int_id], dtype=np.int64))
                    # 同步从 fallback 移除
                    if self._fallback_index.ntotal > 0:
                        self._fallback_index.remove_ids(np.array([int_id], dtype=np.int64))
                    count += 1
            self._total_deleted += count

            # 检查是否需要执行垃圾回收
            self._check_rebuild_needed()
            return count

    def _check_rebuild_needed(self):
        """检查是否需要执行垃圾回收重建。"""
        if self._bin_count == 0:
            return
        ratio = len(self._deleted_ids) / self._bin_count
        if ratio > 0.3 and len(self._deleted_ids) > 1000:
            if self._cleanup_checkpoint is not None:
                self._cleanup_compaction_deferred = True
                return
            logger.info(f"Triggering GC/Rebuild (deleted ratio: {ratio:.2f})")
            self.rebuild_index()

    def rebuild_index(self):
        """GC: 重建索引，压缩 bin 文件"""
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            if self._cleanup_checkpoint is not None:
                raise RuntimeError("cleanup checkpoint 活动期间禁止手工压缩向量文件")
            self._rebuild_index_locked()

    def _rebuild_index_locked(self):
        """实际 GC 重建逻辑。"""
        logger.info("Starting Compaction (GC)...")

        self._recover_interrupted_compaction_unlocked()
        self._flush_write_buffer_unlocked()
        # 压缩会改写整个 pair，切换前无条件提交当前 append 和 tombstone。
        self.save()
        base_vector_bytes, base_id_bytes = self._vector_pair_sizes_unlocked()

        tmp_bin = self.data_dir / "vectors.bin.tmp"
        tmp_ids = self.data_dir / "vectors_ids.bin.tmp"

        vec_item_size = self.dimension * 2
        id_item_size = 8
        chunk_size = 10000

        new_count = 0

        # 1. 压缩数据文件（Compact Files）
        with (
            open(self._bin_path, "rb") as f_vec,
            open(self._ids_bin_path, "rb") as f_id,
            open(tmp_bin, "wb") as w_vec,
            open(tmp_ids, "wb") as w_id,
        ):
            while True:
                vec_data = f_vec.read(chunk_size * vec_item_size)
                id_data = f_id.read(chunk_size * id_item_size)
                if not vec_data:
                    break

                batch_fp16 = np.frombuffer(vec_data, dtype=np.float16).reshape(-1, self.dimension)
                batch_ids = np.frombuffer(id_data, dtype=">i8").astype(np.int64)

                keep_mask = [id_ not in self._deleted_ids for id_ in batch_ids]

                if any(keep_mask):
                    keep_vecs = batch_fp16[keep_mask]
                    keep_ids = batch_ids[keep_mask]

                    w_vec.write(keep_vecs.tobytes())
                    w_id.write(keep_ids.astype(">i8").tobytes())
                    new_count += len(keep_ids)
            w_vec.flush()
            os.fsync(w_vec.fileno())
            w_id.flush()
            os.fsync(w_id.fileno())

        if not self._vector_pair_matches(tmp_bin, tmp_ids, expected_count=new_count):
            raise RuntimeError("向量压缩临时文件不一致，已拒绝切换")

        # 2. 通过日志和只读成对备份切换文件。备份保留至
        # canonical metadata 原子落盘，任何中断都能整体回滚。
        self._bin_backup_path.unlink(missing_ok=True)
        self._ids_backup_path.unlink(missing_ok=True)
        self._copy_and_sync(self._bin_path, self._bin_backup_path)
        self._copy_and_sync(self._ids_bin_path, self._ids_backup_path)
        _write_json_object(
            self._compaction_journal_path,
            {
                "version": 2,
                "transaction_id": uuid.uuid4().hex,
                "dimension": self.dimension,
                "base_vector_bytes": base_vector_bytes,
                "base_id_bytes": base_id_bytes,
                "target_vector_bytes": tmp_bin.stat().st_size,
                "target_id_bytes": tmp_ids.stat().st_size,
            },
        )
        os.replace(tmp_bin, self._bin_path)
        os.replace(tmp_ids, self._ids_bin_path)
        if not self._vector_pair_matches(self._bin_path, self._ids_bin_path, expected_count=new_count):
            self._recover_interrupted_compaction_unlocked()
            raise RuntimeError("向量压缩文件切换失败，已恢复原始文件")

        self._bin_count = new_count
        self._invalidate_id_offsets()

        # 关闭当前索引
        self._index.reset()
        if self._fallback_index:
            self._fallback_index.reset()  # 同时清空回退索引
        self._is_trained = False

        # 已删除哈希必须同时移出成员集合，之后才能重新写入同名向量。
        deleted_hashes = {
            hash_value for hash_value in self._known_hashes if self._generate_id(hash_value) in self._deleted_ids
        }
        self._known_hashes.difference_update(deleted_hashes)
        self._deleted_ids.clear()
        if deleted_hashes:
            self._invalidate_id_map()

        # 3. 重新加载并训练索引
        # 删除后的数据分布可能明显变化，因此必须重新训练。
        self._init_index()
        self._init_fallback_index()  # 同时重新初始化回退索引
        self._force_train_small_data()  # 基于新的压缩文件训练并回放数据
        # 压缩会同时改变成对文件、成员集和 tombstone，结束前提交同一版元数据。
        self.save()

        logger.info("Compaction Complete.")

    def save(
        self,
        data_dir: Optional[Union[str, Path]] = None,
        *,
        embedding_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            if not data_dir:
                data_dir = self.data_dir
            if not data_dir:
                raise ValueError("No data_dir")

            data_dir = Path(data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            if self.data_dir is None or data_dir.resolve() != self.data_dir.resolve():
                raise ValueError("VectorStore 只能向初始化时绑定的 data_dir 提交")

            self._flush_write_buffer_unlocked()
            vector_bytes, id_bytes = self._vector_pair_sizes_unlocked()
            append_journal = self._read_append_journal_unlocked()
            compaction_journal = self._read_compaction_journal_unlocked()
            if append_journal is not None and compaction_journal is not None:
                raise RuntimeError("向量追加与压缩事务不能同时存在")
            if append_journal is not None and (
                vector_bytes != int(append_journal["target_vector_bytes"])
                or id_bytes != int(append_journal["target_id_bytes"])
            ):
                raise RuntimeError("向量追加文件未达到日志目标长度，拒绝提交")
            if compaction_journal is not None and (
                vector_bytes != int(compaction_journal["target_vector_bytes"])
                or id_bytes != int(compaction_journal["target_id_bytes"])
            ):
                raise RuntimeError("向量压缩文件未达到日志目标长度，拒绝提交")
            binary_transaction_id: Optional[str] = None
            if append_journal is not None:
                binary_transaction_id = str(append_journal["transaction_id"])
            elif compaction_journal is not None:
                binary_transaction_id = str(compaction_journal["transaction_id"])

            previous_embedding_fingerprint: Optional[Dict[str, Any]] = None
            meta_path = data_dir / "vectors_metadata.json"
            if embedding_fingerprint is None and meta_path.exists():
                try:
                    previous_meta = _read_json_object(meta_path)
                except Exception as exc:
                    logger.warning(f"读取旧向量元数据失败，跳过 embedding 指纹继承: {exc}")
                else:
                    if isinstance(previous_meta, dict):
                        previous_raw = previous_meta.get("embedding_fingerprint")
                        if isinstance(previous_raw, dict) and previous_raw:
                            previous_embedding_fingerprint = dict(previous_raw)

            if self._is_trained:
                index_path = data_dir / "vectors.index"
                with atomic_save_path(index_path) as tmp:
                    faiss.write_index(self._index, tmp)

            meta = {
                "dimension": self.dimension,
                "quantization_type": self.quantization_type.value,
                "is_trained": self._is_trained,
                "vector_norm": self._vector_norm,
                "deleted_ids": list(self._deleted_ids),
                "known_hashes": list(self._known_hashes),
                "schema_version": 2,
                "binary_commit": {
                    "transaction_id": binary_transaction_id,
                    "vector_bytes": vector_bytes,
                    "id_bytes": id_bytes,
                },
            }
            if isinstance(embedding_fingerprint, dict) and embedding_fingerprint:
                meta["embedding_fingerprint"] = dict(embedding_fingerprint)
            elif previous_embedding_fingerprint is not None:
                meta["embedding_fingerprint"] = previous_embedding_fingerprint

            _write_json_object(meta_path, meta)
            if append_journal is not None:
                self._finalize_append_commit_unlocked()
            if compaction_journal is not None:
                self._finalize_compaction_commit_unlocked()

            logger.debug("VectorStore saved.")

    def migrate_legacy_npy(self, data_dir: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """
        离线迁移入口：将 legacy vectors.npy 转为 vNext 二进制格式。
        """
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            target_dir = Path(data_dir) if data_dir else self.data_dir
            if target_dir is None:
                raise ValueError("No data_dir")
            target_dir = Path(target_dir)
            npy_path = target_dir / "vectors.npy"
            idx_path = target_dir / "vectors.index"
            bin_path = target_dir / "vectors.bin"
            ids_bin_path = target_dir / "vectors_ids.bin"
            meta_path = target_dir / "vectors_metadata.json"

            if not npy_path.exists():
                return {"migrated": False, "reason": "npy_missing"}
            if not meta_path.exists():
                raise RuntimeError("legacy vectors.npy migration requires vectors_metadata.json")
            if bin_path.exists() and ids_bin_path.exists():
                return {"migrated": False, "reason": "bin_exists"}

            # 重置内存状态，避免继续向旧运行时缓冲区追加数据。
            self._known_hashes.clear()
            self._deleted_ids.clear()
            self._invalidate_id_map()
            self._invalidate_id_offsets()
            self._write_buffer_vecs.clear()
            self._write_buffer_ids.clear()
            self._init_index()
            self._init_fallback_index()
            self._is_trained = False
            self._bin_count = 0

            self._migrate_from_npy_unlocked(npy_path, idx_path, target_dir)
            self.save(target_dir)
            return {"migrated": True, "reason": "ok"}

    def load(self, data_dir: Optional[Union[str, Path]] = None) -> None:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            if not data_dir:
                data_dir = self.data_dir
            data_dir = Path(data_dir)
            if self.data_dir is None or data_dir.resolve() != self.data_dir.resolve():
                raise ValueError("VectorStore 只能从初始化时绑定的 data_dir 加载")
            self._recover_interrupted_append_unlocked()
            self._recover_interrupted_compaction_unlocked()
            vector_bytes, _id_bytes = self._vector_pair_sizes_unlocked()
            self._bin_count = vector_bytes // (self.dimension * 2)

            npy_path = data_dir / "vectors.npy"
            idx_path = data_dir / "vectors.index"
            bin_path = data_dir / "vectors.bin"

            if npy_path.exists() and not bin_path.exists():
                raise RuntimeError(
                    "检测到 legacy vectors.npy，vNext 不再支持运行时自动迁移。"
                    " 请先执行 scripts/release_vnext_migrate.py migrate。"
                )

            meta_path = data_dir / "vectors_metadata.json"
            if not meta_path.exists():
                if vector_bytes:
                    raise RuntimeError("检测到未受元数据管理的向量二进制文件")
                logger.warning("No metadata found, initialized empty.")
                return

            meta = self._validate_or_migrate_vector_metadata_unlocked(
                meta_path,
                _read_json_object(meta_path),
                vector_bytes=vector_bytes,
                id_bytes=_id_bytes,
            )

            if meta.get("vector_norm") != "l2":
                logger.warning("Index IDMap2 version mismatch (L2 Norm), forcing rebuild...")
                self._known_hashes = set(meta.get("ids", [])) | set(meta.get("known_hashes", []))
                self._deleted_ids = set(meta.get("deleted_ids", []))
                self._invalidate_id_map()
                self._rebuild_loaded_index_unlocked()
                return

            self._is_trained = meta.get("is_trained", False)
            self._vector_norm = meta.get("vector_norm", "l2")
            self._deleted_ids = set(meta.get("deleted_ids", []))
            self._known_hashes = set(meta.get("known_hashes", []))
            self._invalidate_id_map()
            self._invalidate_id_offsets()

            if self._is_trained:
                if idx_path.exists():
                    try:
                        self._index = faiss.read_index(str(idx_path))
                    except Exception as e:
                        logger.error(f"Failed to load index: {e}. Rebuilding...")
                        self._rebuild_loaded_index_unlocked()
                    else:
                        if not self._loaded_index_matches_metadata_unlocked():
                            logger.warning(
                                "Loaded index ID multiset does not match active metadata. Rebuilding from pair..."
                            )
                            self._rebuild_loaded_index_unlocked()
                else:
                    logger.warning("Index file missing despite metadata indicating trained. Rebuilding from bin...")
                    self._rebuild_loaded_index_unlocked()

    def _migrate_from_npy(self, npy_path, idx_path, data_dir):
        with self._lock:
            self._migrate_from_npy_unlocked(npy_path, idx_path, data_dir)

    def _migrate_from_npy_unlocked(self, npy_path, idx_path, data_dir):
        try:
            arr = np.load(npy_path, mmap_mode="r")
        except Exception:
            arr = np.load(npy_path)

        meta_path = data_dir / "vectors_metadata.json"
        old_ids = []
        if meta_path.exists():
            m = _read_json_object(meta_path)
            old_ids = m.get("ids", [])

        if len(arr) != len(old_ids):
            logger.error(f"Migration mismatch: arr {len(arr)} != ids {len(old_ids)}")
            return

        logger.info(f"Migrating {len(arr)} vectors...")

        chunk = 1000
        for i in range(0, len(arr), chunk):
            sub_arr = arr[i : i + chunk]
            sub_ids = old_ids[i : i + chunk]
            self.add(sub_arr, sub_ids)

        if not self._is_trained:
            self._force_train_small_data()

        shutil.move(str(npy_path), str(npy_path) + ".bak")
        if idx_path.exists():
            shutil.move(str(idx_path), str(idx_path) + ".bak")

        logger.info("Migration complete.")

    def _reset_empty_runtime_unlocked(self) -> None:
        self._init_index()
        self._init_fallback_index()
        self._known_hashes.clear()
        self._deleted_ids.clear()
        self._invalidate_id_map()
        self._invalidate_id_offsets()
        self._write_buffer_vecs.clear()
        self._write_buffer_ids.clear()
        self._reservoir_buffer.clear()
        self._seen_count_for_reservoir = 0
        self._total_added = 0
        self._total_deleted = 0
        self._bin_count = 0

    def clear(self) -> None:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            if self._cleanup_checkpoint is not None:
                raise RuntimeError("cleanup checkpoint 活动期间禁止清空向量存储")

            # clear 也必须遵守成对文件提交协议。先恢复已有事务，再把空 pair
            # 作为一次压缩结果提交；只有提交完成后才能移除空 metadata。
            self._write_buffer_vecs.clear()
            self._write_buffer_ids.clear()
            self._recover_interrupted_append_unlocked()
            self._recover_interrupted_compaction_unlocked()
            vector_bytes, id_bytes, stored_dimension = self._clear_pair_state_unlocked()

            if vector_bytes or id_bytes:
                tmp_bin = self.data_dir / "vectors.bin.tmp"
                tmp_ids = self.data_dir / "vectors_ids.bin.tmp"
                tmp_bin.unlink(missing_ok=True)
                tmp_ids.unlink(missing_ok=True)
                for path in (tmp_bin, tmp_ids):
                    with path.open("wb") as handle:
                        handle.flush()
                        os.fsync(handle.fileno())

                self._bin_backup_path.unlink(missing_ok=True)
                self._ids_backup_path.unlink(missing_ok=True)
                self._copy_and_sync(self._bin_path, self._bin_backup_path)
                self._copy_and_sync(self._ids_bin_path, self._ids_backup_path)
                _write_json_object(
                    self._compaction_journal_path,
                    {
                        "version": 2,
                        "transaction_id": uuid.uuid4().hex,
                        "dimension": stored_dimension,
                        "base_vector_bytes": vector_bytes,
                        "base_id_bytes": id_bytes,
                        "target_vector_bytes": 0,
                        "target_id_bytes": 0,
                    },
                )
                try:
                    os.replace(tmp_bin, self._bin_path)
                    os.replace(tmp_ids, self._ids_bin_path)
                    if not self._vector_pair_matches(self._bin_path, self._ids_bin_path, expected_count=0):
                        raise RuntimeError("向量清空后的成对文件不一致")

                    self._reset_empty_runtime_unlocked()
                    # metadata 是压缩事务的提交点。save 完成后再删除空提交，
                    # 中途退出时 load() 仍可根据 journal 完成或回滚。
                    self.save()
                except Exception:
                    self._write_buffer_vecs.clear()
                    self._write_buffer_ids.clear()
                    self._recover_interrupted_compaction_unlocked()
                    raise
            else:
                self._reset_empty_runtime_unlocked()

            (self.data_dir / "vectors_metadata.json").unlink(missing_ok=True)
            (self.data_dir / "vectors.index").unlink(missing_ok=True)
            logger.info("VectorStore cleared.")

    def has_data(self) -> bool:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            return (self.data_dir / "vectors_metadata.json").exists()

    @property
    def num_vectors(self) -> int:
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            return len(self._known_hashes) - len(self._deleted_ids)

    def needs_training(self, runtime_threshold: int) -> bool:
        """判断未训练索引是否达到运行期后台训练阈值。"""
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            threshold = max(
                1,
                int(self.min_train_threshold),
                int(runtime_threshold),
            )
            return not self._is_trained and self.num_vectors >= threshold

    def __contains__(self, hash_value: str) -> bool:
        """检查指定哈希是否存在于向量库中。"""
        with self._lock:
            self._raise_if_cleanup_checkpoint_broken_unlocked()
            return hash_value in self._known_hashes and self._generate_id(hash_value) not in self._deleted_ids

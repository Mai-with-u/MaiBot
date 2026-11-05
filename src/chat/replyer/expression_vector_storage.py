"""表达索引的有界数组操作和不可变 NPY 代存储。"""

from pathlib import Path
from queue import Empty, SimpleQueue
from typing import AbstractSet, Any, Dict, Iterable, List, Optional, Set, Tuple, Union
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
import weakref

import numpy as np

logger = logging.getLogger(__name__)
BLOCK_BYTES = 8 * 1024 * 1024
DELTA_MERGE_RATIO = 0.05
DELTA_MAX_BYTES = 32 * 1024 * 1024
DELTA_MAX_AGE_SECONDS = 3600


def blocks(rows: int, dimension: int) -> Iterable[slice]:
    step = max(1, BLOCK_BYTES // max(4, dimension * 4))
    for start in range(0, rows, step):
        yield slice(start, min(rows, start + step))


def scratch(shape: Tuple[int, int]) -> np.ndarray:
    """临时矩阵由磁盘承载；最终一个视图释放后才关闭映射。"""
    if np.prod(shape) * 4 <= BLOCK_BYTES:
        return np.empty(shape, dtype=np.float32)
    fd, filename = tempfile.mkstemp(prefix="maibot-expression-", suffix=".npy")
    os.close(fd)
    try:
        result = np.lib.format.open_memmap(filename, mode="w+", dtype=np.float32, shape=shape)
    except Exception:
        Path(filename).unlink(missing_ok=True)
        raise
    return _mapping_lifecycle.track(result, Path(filename), temporary=True)


def normalize(matrix: np.ndarray, *, repair: bool = False) -> np.ndarray:
    """不修改调用方数组，分块归一化；新格式只读矩阵已在发布前校验。"""
    if matrix.ndim != 2:
        raise ValueError("表达向量矩阵必须是二维")
    output = scratch(matrix.shape)
    invalid_count = 0
    for part in blocks(*matrix.shape):
        batch = np.asarray(matrix[part], dtype=np.float32)
        norms = np.sqrt(np.einsum("ij,ij->i", batch, batch))
        invalid = ~np.isfinite(norms) | (norms <= 0)
        invalid_count += int(invalid.sum())
        if invalid_count and (not repair or invalid_count > max(1, matrix.shape[0] // 100)
                              or invalid_count >= matrix.shape[0]):
            raise ValueError(f"表达向量损坏超出可恢复范围: invalid={invalid_count} total={matrix.shape[0]}")
        with np.errstate(divide="ignore", invalid="ignore"):
            np.divide(batch, norms[:, None], out=output[part])
        output[part][invalid] = np.nan
    if invalid_count:
        logger.warning("表达旧索引发现 %s/%s 条坏向量，将跳过对应表达并交给历史回填恢复",
                       invalid_count, matrix.shape[0])
    return output


class RowMatrix:
    """只读行视图：增量维护复用旧映射，不拼接整份矩阵。"""

    def __init__(self, rows: List[np.ndarray]) -> None:
        if not rows:
            raise ValueError("表达索引没有可堆叠向量")
        self.rows = rows
        self.shape = (len(rows), len(rows[0]))
        self.dtype = np.dtype(np.float32)
        self.ndim = 2
        self.size = self.shape[0] * self.shape[1]
        self.nbytes = self.size * 4

    def __len__(self) -> int:
        return self.shape[0]

    def __getitem__(self, key: Any) -> np.ndarray:
        if isinstance(key, (int, np.integer)):
            return self.rows[key]
        indices = np.arange(self.shape[0])[key]
        if len(indices) == 0:
            return np.empty((0, self.shape[1]), dtype=self.dtype)
        return np.stack([self.rows[int(index)] for index in indices])

    def __matmul__(self, other: np.ndarray) -> np.ndarray:
        shape = (self.shape[0],) if other.ndim == 1 else (self.shape[0], other.shape[1])
        result = np.empty(shape, dtype=np.float32)
        for part in blocks(*self.shape):
            result[part] = self[part] @ other
        return result

    def __array__(self, dtype: Any = None, copy: Optional[bool] = None) -> np.ndarray:
        result = scratch(self.shape)
        for part in blocks(*self.shape):
            result[part] = self[part]
        return np.asarray(result, dtype=dtype)


VectorMatrix = Union[np.ndarray, RowMatrix]


def stack_rows(rows: List[np.ndarray]) -> RowMatrix:
    return RowMatrix(rows)


class MappingLifecycle:
    """按实际根映射引用回收；finalizer 只通知，关闭和删除由单个后台线程完成。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.references: Dict[Path, int] = {}
        self.released: SimpleQueue[Tuple[Path, Any]] = SimpleQueue()
        self.wakeup = threading.Event()
        self.pending: Set[Path] = set()
        self.temporary: Set[Path] = set()
        self.worker: Optional[threading.Thread] = None

    def _wake(self) -> None:
        if self.worker is None or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run, name="expression-index-reclaimer", daemon=True)
            self.worker.start()
        self.wakeup.set()

    def track(self, array: np.ndarray, path: Path, *, temporary: bool = False) -> np.ndarray:
        path = path.resolve()
        with self.lock:
            self.references[path] = self.references.get(path, 0) + 1
            if temporary:
                self.temporary.add(path)
            # NumPy 切片/行视图通过 base 链持有根数组。不能以快照消失代替根数组消失。
            weakref.finalize(array, self.release, path, array._mmap)
            if self.worker is None or not self.worker.is_alive():
                self._wake()
            return array

    def open(self, path: Path) -> np.ndarray:
        with self.lock:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            return self.track(array, path)

    def release(self, path: Path, mapping: Any) -> None:
        # finalizer 可能在主循环执行；不能等待回收线程正在持有的文件操作锁。
        self.released.put((path, mapping))
        self.wakeup.set()

    def retire(self, path: Path) -> None:
        if not path.name.startswith("expression-arrays-") or path.suffix != ".json":
            return  # 历史 NPZ 仍遵循独立的七天备份保留策略。
        with self.lock:
            self.pending.add(path.resolve())
            self._wake()

    def _collect(self) -> bool:
        """锁内完成一轮；只有真实 IO 失败才定时重试，引用未释放时等待通知。"""
        retry = False
        releasing = []
        while True:
            try:
                releasing.append(self.released.get_nowait())
            except Empty:
                break
        for path, mapping in releasing:
            try:
                mapping.close()
            except (OSError, BufferError):
                self.released.put((path, mapping))
                logger.warning("表达索引映射关闭失败，保留引用并重试: %s", path, exc_info=True)
                retry = True
                continue
            count = self.references[path] - 1
            if count:
                self.references[path] = count
            else:
                del self.references[path]
        for path in list(self.temporary):
            if path in self.references:
                continue
            try:
                path.unlink(missing_ok=True)
                self.temporary.discard(path)
            except OSError as exc:
                _log_cleanup_error("表达临时矩阵释放失败，下次重试", path, exc)
                retry = True
        directories = {path.parent for path in self.pending}
        for directory in directories:
            try:
                active: Set[Path] = set()
                for candidate in _active_index_paths(directory):
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                    if isinstance(payload, dict) and isinstance(payload.get("vectors_file"), str):
                        active.add((directory / payload["vectors_file"]).resolve())
                keep: Set[str] = set()
                for current in active:
                    keep.update(generation_files(current))
                for path in list(self.pending):
                    if path.parent != directory or path in active:
                        continue
                    # 所有独占旧文件都不再被读取，才能删除该代。共享基文件由新代保护。
                    owned = generation_files(path) - keep
                    if any(self.references.get(directory / name, 0) for name in owned):
                        continue
                    if _remove_generation(path, keep=keep):
                        self.pending.discard(path)
                    else:
                        retry = True
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("表达索引退役回收失败，保留数据并重试: %s", directory, exc_info=True)
                retry = True
        self.condition.notify_all()
        return retry

    def _run(self) -> None:
        timeout = None
        while True:
            self.wakeup.wait(timeout=timeout)
            self.wakeup.clear()
            with self.condition:
                try:
                    retry = self._collect()
                except Exception:
                    # 回收线程不能悄悄退出；保留待处理记录，异常恢复后继续重试。
                    logger.exception("表达索引回收线程异常，保留待回收记录")
                    retry = True
            # 一分钟只用于 IO 错误；有使用者时等待真正的引用释放通知。
            timeout = 60 if retry else None


_mapping_lifecycle = MappingLifecycle()


def generation_guard():
    """原子发布与回收共享锁，避免回收线程使用过期的活动代清单。"""
    return _mapping_lifecycle.lock


def retire_generation(path: Path) -> None:
    _mapping_lifecycle.retire(path)


def load_array(path: Path, key: str) -> VectorMatrix:
    # 读取清单到登记全部映射为一个区间，防止 base 打开后、delta 打开前被回收。
    with generation_guard():
        return _load_array(path, key)


def _load_array(path: Path, key: str) -> VectorMatrix:
    if path.suffix != ".json":
        with np.load(path, allow_pickle=False) as archive:
            result = normalize(archive[key], repair=not key.startswith("cluster_centers"))
            result.flags.writeable = False
            return result
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != "expression-npy-v1":
        raise ValueError("未知表达向量存储格式")
    entry = manifest["arrays"][key]
    name = entry["file"]
    if Path(name).name != name or not name.startswith("expression-array-"):
        raise ValueError("表达向量文件路径非法")
    array = _mapping_lifecycle.open(path.parent / name)
    if array.dtype != np.float32 or list(array.shape) != entry.get("base_shape", entry["shape"]) or array.ndim != 2:
        raise ValueError("表达向量文件形状或类型不匹配")
    # 每次加载验证分块，不把未校验的磁盘内容当作可信归一化矩阵。
    for part in blocks(*array.shape):
        batch = array[part]
        norms = np.einsum("ij,ij->i", batch, batch)
        if not np.all(np.isfinite(norms)) or not np.allclose(norms, 1, rtol=1e-4, atol=1e-5):
            raise ValueError("表达向量文件存在无效或未归一化行")
    if "delta_file" in entry:
        delta_name = entry["delta_file"]
        if Path(delta_name).name != delta_name or not delta_name.startswith("expression-array-"):
            raise ValueError("表达增量文件路径非法")
        delta = _mapping_lifecycle.open(path.parent / delta_name)
        indices = entry["delta_indices"]
        if delta.dtype != np.float32 or delta.shape != (len(indices), array.shape[1]):
            raise ValueError("表达增量文件形状或类型非法")
        if len(set(indices)) != len(indices) or any(i < 0 or i >= entry["shape"][0] for i in indices):
            raise ValueError("表达增量行号非法")
        if not np.isfinite(delta).all() or not np.allclose(np.einsum("ij,ij->i", delta, delta), 1, rtol=1e-4, atol=1e-5):
            raise ValueError("表达增量包含非法向量")
        replacements = dict(zip(indices, delta, strict=True))
        rows = []
        for i in range(entry["shape"][0]):
            if i in replacements:
                rows.append(replacements[i])
            elif i < len(array):
                rows.append(array[i])
            else:
                raise ValueError("表达增量缺少新增行")
        return RowMatrix(rows)
    return array[:entry["shape"][0]]


def generation_files(path: Path) -> Set[str]:
    if path.suffix != ".json" or not path.exists():
        return set()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {entry[key] for entry in manifest["arrays"].values() for key in ("file", "delta_file") if key in entry}


def write_arrays(directory: Path, arrays: Dict[str, VectorMatrix], previous: Optional[Path] = None) -> Path:
    """先落盘并 fsync 数组，再发布代清单；上层最后切换主索引清单。"""
    generation = uuid.uuid4().hex
    manifest_path = directory / f"expression-arrays-{generation}.json"
    created = []
    manifest = {"format": "expression-npy-v1", "arrays": {}}
    old = {}
    if previous is not None and previous.suffix == ".json":
        old = json.loads(previous.read_text(encoding="utf-8"))["arrays"]
    try:
        for number, (key, matrix) in enumerate(arrays.items()):
            entry = old.get(key)
            if entry is not None and key.startswith("vectors"):
                # 增量始终相对于基矩阵，不叠加链；删除/重排较多时自然触发合并。
                base_name = entry["file"]
                if Path(base_name).name != base_name or not base_name.startswith("expression-array-"):
                    raise ValueError("表达基矩阵路径非法")
                base = _mapping_lifecycle.open(directory / base_name)
                changed = []
                if base.shape[1] == matrix.shape[1]:
                    for part in blocks(*matrix.shape):
                        batch = matrix[part]
                        end = min(part.stop, len(base))
                        if end > part.start:
                            changed.extend((np.flatnonzero(np.any(batch[:end-part.start] != base[part.start:end], axis=1)) + part.start).tolist())
                        changed.extend(range(max(part.start, len(base)), part.stop))
                        if len(changed) > max(1, int(matrix.shape[0] * DELTA_MERGE_RATIO)):
                            break
                    delta_bytes = len(changed) * matrix.shape[1] * 4
                    if (len(changed) < max(1, matrix.shape[0] * DELTA_MERGE_RATIO) and delta_bytes <= DELTA_MAX_BYTES
                            and time.time() - entry.get("merged_at", 0) < DELTA_MAX_AGE_SECONDS):
                        updated = {"file": base_name, "base_shape": list(base.shape), "shape": list(matrix.shape),
                                   "merged_at": entry["merged_at"]}
                        if changed:
                            delta_name = f"expression-array-{generation}-{number}-delta.npy"
                            delta_path = directory / delta_name
                            created.append(delta_path)
                            delta = matrix[np.asarray(changed)]
                            if not np.isfinite(delta).all() or not np.allclose(np.einsum("ij,ij->i", delta, delta), 1, rtol=1e-4, atol=1e-5):
                                raise ValueError("表达增量发布校验失败")
                            with delta_path.open("xb") as handle:
                                np.save(handle, delta, allow_pickle=False)
                                handle.flush()
                                os.fsync(handle.fileno())
                            updated.update(delta_file=delta_name, delta_indices=changed)
                        manifest["arrays"][key] = updated
                        continue
            name = f"expression-array-{generation}-{number}.npy"
            path = directory / name
            created.append(path)
            output = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=matrix.shape)
            try:
                for part in blocks(*matrix.shape):
                    batch = np.asarray(matrix[part], dtype=np.float32)
                    norms = np.sqrt(np.einsum("ij,ij->i", batch, batch))
                    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
                        raise ValueError(f"表达向量发布校验失败: key={key} rows={part}")
                    if np.allclose(norms, 1, rtol=1e-4, atol=1e-5):
                        output[part] = batch
                    else:
                        np.divide(batch, norms[:, None], out=output[part])
                output.flush()
            finally:
                output._mmap.close()
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())
            manifest["arrays"][key] = {"file": name, "shape": list(matrix.shape), "merged_at": time.time()}
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle)
            handle.flush()
            os.fsync(handle.fileno())
        return manifest_path
    except Exception:
        logger.exception("表达向量新代写入失败，未切换当前索引")
        for path in [manifest_path, *created]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("表达向量未发布文件清理失败: %s", path, exc_info=True)
        raise


_QUARANTINED_INDEX = re.compile(r".+\.corrupt-\d{8}-\d{6}\.json$")
_retired_cleanup_attempts: Dict[Path, float] = {}


def _active_index_paths(directory: Path) -> Iterable[Path]:
    """仅排除本程序已隔离的历史文件；真实活动清单损坏仍阻止不安全的回收。"""
    for candidate in directory.glob("*.json"):
        if not candidate.name.startswith("expression-arrays-") and not _QUARANTINED_INDEX.fullmatch(candidate.name):
            yield candidate


def _log_cleanup_error(message: str, path: Path, exc: OSError) -> None:
    if hasattr(exc, "winerror") and exc.winerror in (32, 33):
        # Windows 上查询持有 mmap 时不能删除，这是延迟回收状态，不是业务异常。
        logger.debug("%s（映射仍被使用，将稍后重试）: %s", message, path)
    else:
        logger.warning("%s: %s", message, path, exc_info=True)


def remove_generation(path: Path, *, keep: AbstractSet[str] = frozenset()) -> None:
    with generation_guard():
        if not _remove_generation(path, keep=keep):
            retire_generation(path)


def _remove_generation(path: Path, *, keep: AbstractSet[str] = frozenset()) -> bool:
    """仅删除指定代；Windows 仍被查询占用的文件保留并报告，不阻断发布。"""
    if path.suffix != ".json" or not path.exists():
        return True  # 首次迁移保留旧 NPZ，便于人工恢复。
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("format") != "expression-npy-v1":
            raise ValueError("表达向量清理清单格式非法")
        if any(_mapping_lifecycle.references.get((path.parent / name).resolve(), 0)
               for name in generation_files(path) - keep):
            return False
        pending = False
        for name in generation_files(path):
            if name in keep:
                continue
            if Path(name).name != name or not name.startswith("expression-array-"):
                raise ValueError("表达向量清理路径非法")
            try:
                (path.parent / name).unlink(missing_ok=True)
            except OSError as exc:
                pending = True
                _log_cleanup_error("表达向量旧代保留待清理", path.parent / name, exc)
        if not pending:
            path.unlink(missing_ok=True)
        return not pending
    except (OSError, ValueError, KeyError):
        logger.warning("表达向量旧代清理失败，不影响当前索引: %s", path, exc_info=True)
        return False


def cleanup_retired(directory: Path) -> None:
    with generation_guard():
        _cleanup_retired(directory)


def _cleanup_retired(directory: Path) -> None:
    """重试清理一小时前的未引用代，兼容崩溃残留及 Windows 映射占用。"""
    directory = directory.resolve()
    now = time.monotonic()
    if now - _retired_cleanup_attempts.get(directory, float("-inf")) < 3600:
        return
    _retired_cleanup_attempts[directory] = now
    try:
        active = set()
        # 多个索引可能共享目录；任何主清单仍引用的代都不能回收。
        for candidate in _active_index_paths(directory):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("vectors_file"), str):
                    active.add((directory / payload["vectors_file"]).resolve())
            except (OSError, ValueError):
                # 无法确认引用关系时只停止本次清理，不影响发布或以后重试。
                logger.warning("无法确认旧代引用，推迟清理: %s", candidate, exc_info=True)
                return
        keep = set()
        for manifest in active:
            keep.update(generation_files(manifest))
        cutoff = time.time() - 3600
        for candidate in directory.glob("expression-arrays-*.json"):
            if candidate.resolve() not in active and candidate.stat().st_mtime < cutoff:
                remove_generation(candidate, keep=keep)
        # 未写成代清单就退出的文件也可恢复清理；仍被任一代引用的数组全部保留。
        referenced = set()
        for candidate in directory.glob("expression-arrays-*.json"):
            referenced.update(generation_files(candidate))
        for candidate in directory.glob("expression-array-*.npy"):
            if (candidate.name not in referenced and candidate.stat().st_mtime < cutoff
                    and not _mapping_lifecycle.references.get(candidate.resolve(), 0)):
                try:
                    candidate.unlink()
                except OSError as exc:
                    _log_cleanup_error("表达未引用数组清理失败，下次重试", candidate, exc)
    except (OSError, ValueError, KeyError):
        logger.warning("表达向量后台清理失败，下次发布重试", exc_info=True)


MIGRATION_BACKUP_RETENTION_SECONDS = 7 * 24 * 3600
_backup_cleanup_attempts: Dict[Path, float] = {}


def cleanup_migration_backup(index_path: Path) -> None:
    """迁移备份满 7 天后回收；失败最多每小时重试，不影响查询或更新。"""
    backup = index_path.with_name(index_path.name + ".pre-npy.bak")
    try:
        if not backup.exists() or time.time() - backup.stat().st_mtime < MIGRATION_BACKUP_RETENTION_SECONDS:
            return
        now = time.monotonic()
        if now - _backup_cleanup_attempts.get(index_path, float("-inf")) < 3600:
            return
        _backup_cleanup_attempts[index_path] = now
        directory = index_path.parent.resolve()
        previous = json.loads(backup.read_text(encoding="utf-8"))
        old_path = (directory / previous["vectors_file"]).resolve()
        # 备份里的路径不是删除授权；只允许本索引目录内、由本索引命名的旧 NPZ。
        if (old_path.parent != directory or old_path.suffix != ".npz"
                or not (old_path.name == index_path.stem + ".npz"
                        or old_path.name.startswith(index_path.stem + ".vectors-"))):
            raise ValueError("迁移备份引用了非本索引管理的向量文件")
        current = json.loads(index_path.read_text(encoding="utf-8"))
        current_path = (directory / current["vectors_file"]).resolve()
        if current_path.parent != directory or current_path.suffix != ".json":
            return  # 仍在使用旧格式，不能删除恢复来源。
        manifest = json.loads(current_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "expression-npy-v1" or not manifest.get("arrays"):
            raise ValueError("当前表达索引存储清单无效")
        for key in manifest["arrays"]:
            checked = load_array(current_path, key)
            del checked
        # 防止同目录另一个仍在运行的索引引用这份旧向量。
        for candidate in _active_index_paths(directory):
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("vectors_file"), str):
                if (directory / payload["vectors_file"]).resolve() == old_path:
                    logger.warning("过期迁移备份仍被索引引用，保留并稍后重试: %s", old_path)
                    return
        old_path.unlink(missing_ok=True)
        # 先删除大文件，成功后再删除用于重试定位的备份清单。
        backup.unlink(missing_ok=True)
        _backup_cleanup_attempts.pop(index_path, None)
        logger.info("表达迁移备份已保留满 7 天，自动删除: %s, %s", backup, old_path)
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("表达迁移备份清理失败，保留备份并稍后重试: %s", backup, exc_info=True)

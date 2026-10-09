from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import asyncio
import hashlib
import json
import re
import shutil
import time

from src.common.logger import get_logger

from .base import KernelServiceBase
from .vector_rebuild_checkpoint import EmbeddingConfigurationChanged

logger = get_logger("A_Memorix.VectorSpaces")


class MemoryVectorSpaceService(KernelServiceBase):
    """按模型指纹保存独立向量库，切换时只重新编码发生变化的输入。"""

    @staticmethod
    def input_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def space_id(fingerprint: Dict[str, Any]) -> str:
        return hashlib.sha256(str(fingerprint["hash"]).encode("utf-8")).hexdigest()

    def _catalog_root(self) -> Path:
        return self.data_dir / "vectors" / "spaces"

    def _read_active_space(self) -> str:
        path = self.data_dir / "vectors" / "active.json"
        if not path.exists():
            return ""
        return str(json.loads(path.read_text(encoding="utf-8"))["space_id"])

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def _read_inputs(self) -> Dict[str, str]:
        path = self._vectors_root() / "embedding_inputs.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _save_inputs(self) -> None:
        self._write_json(self._vectors_root() / "embedding_inputs.json", self._vector_space_inputs)

    def record_input(self, item_type: str, item_id: str, text: str) -> None:
        self._vector_space_inputs[f"{item_type}:{item_id}"] = self.input_hash(text)

    async def run_io(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """取消时先等当前文件操作结束，避免关机后线程继续改动向量目录。"""
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise

    def _register_current_space(self, fingerprint: Dict[str, Any]) -> None:
        space_id = self._active_vector_space_id
        root = self._catalog_root() / space_id
        path = root / "space.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self._write_json(
            path,
            {
                "space_id": space_id,
                "embedding_fingerprint": fingerprint,
                "created_at": previous.get("created_at", time.time()),
                "last_used_at": time.time(),
            },
        )
        self._write_json(self.data_dir / "vectors" / "active.json", {"space_id": space_id})
        self._save_inputs()

    def _migrate_legacy_space(self) -> None:
        """给原来固定路径的向量库登记模型身份，不修改其向量内容。"""
        if self._active_vector_space_id:
            return
        fingerprint = self._stored_embedding_fingerprint()
        if fingerprint is None:
            return
        old_root = self.data_dir / "vectors"
        space_id = self.space_id(fingerprint)
        target = self._catalog_root() / space_id
        target.mkdir(parents=True, exist_ok=True)
        for name in (
            "paragraph",
            "graph",
            "dual_ready.json",
            "embedding_inputs.json",
            "vectors.bin",
            "vectors_ids.bin",
            "vectors.index",
            "vectors_metadata.json",
            "vectors_metadata.pkl",
        ):
            source = old_root / name
            if source.exists():
                shutil.move(str(source), str(target / name))
        self._active_vector_space_id = space_id
        self._vector_space_inputs = self._read_inputs()
        self._register_current_space(fingerprint)

    def _list_spaces(self) -> List[Dict[str, Any]]:
        catalog = self._catalog_root()
        if not catalog.exists():
            return []
        selected = self._read_active_space()
        items: List[Dict[str, Any]] = []
        for root in catalog.iterdir():
            if not root.is_dir() or re.fullmatch(r"[0-9a-f]{64}", root.name) is None:
                continue
            info_path = root / "space.json"
            info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
            fingerprint = info.get("embedding_fingerprint", {})
            state = "saved"
            if root.name == self._target_vector_space_id:
                state = "syncing"
                fingerprint = self._current_embedding_fingerprint_for_validation() or fingerprint
            elif root.name in {self._active_vector_space_id, selected}:
                state = "active"
            counts: Dict[str, int] = {}
            for pool in ("paragraph", "graph", "single"):
                meta_path = root / ("" if pool == "single" else pool) / "vectors_metadata.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    counts[pool] = len(meta.get("known_hashes", [])) - len(meta.get("deleted_ids", []))
            items.append(
                {
                    **info,
                    "space_id": root.name,
                    "embedding_fingerprint": fingerprint,
                    "state": state,
                    "can_delete": state == "saved",
                    "vector_count": sum(counts.values()),
                    "counts": counts,
                    "size_bytes": sum(path.stat().st_size for path in root.rglob("*") if path.is_file()),
                }
            )
        return sorted(items, key=lambda item: float(item.get("last_used_at", 0)), reverse=True)

    async def list_spaces(self) -> Dict[str, Any]:
        # 读取状态不等待耗时的重嵌入，界面可以及时显示同步中的库。
        return {"success": True, "items": await self.run_io(self._list_spaces)}

    async def delete_space(self, space_id: str) -> Dict[str, Any]:
        async with self._vector_rebuild_lock:
            if re.fullmatch(r"[0-9a-f]{64}", space_id) is None:
                return {"success": False, "error": "vector_space_not_found"}
            selected = await self.run_io(self._read_active_space)
            if space_id in {self._active_vector_space_id, self._target_vector_space_id, selected}:
                return {"success": False, "error": "vector_space_in_use"}
            root = self._catalog_root() / space_id
            if not await self.run_io(root.is_dir):
                return {"success": False, "error": "vector_space_not_found"}
            await self.run_io(shutil.rmtree, root)
            return {"success": True, "deleted": space_id}

    async def synchronize(
        self, *, force: bool = False, batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
    ) -> Dict[str, Any]:
        while True:
            try:
                return await self._synchronize_once(
                    force=force, batch_size=batch_size, include_relations=include_relations,
                )
            except EmbeddingConfigurationChanged:
                # 旧任务已退出并释放构建锁；确认新模型身份后立即重开一轮。
                self.embedding_dimension = await self._detect_current_embedding_dimension_for_rebuild()

    async def _synchronize_once(
        self,
        *,
        force: bool = False,
        batch_size: Optional[int] = None,
        include_relations: Optional[bool] = None,
    ) -> Dict[str, Any]:
        fingerprint = self._current_embedding_fingerprint_for_validation()
        if fingerprint is None:
            return {"success": False, "error": "embedding_fingerprint_unavailable"}
        space_id = self.space_id(fingerprint)
        if not force and space_id == self._active_vector_space_id and self._runtime_capabilities["vector_read"]:
            return {"success": True, "changed": False}
        async with self._vector_rebuild_lock:
            if not force and space_id == self._active_vector_space_id and self._runtime_capabilities["vector_read"]:
                return {"success": True, "changed": False}
            self._target_vector_space_id = space_id
            self._set_embedding_degraded(active=True, reason="正在同步当前模型向量库")
            self._set_runtime_capability("vector_read", False)
            self._set_runtime_capability("vector_write", False)
            original_inputs = dict(self._vector_space_inputs)
            try:
                # 先保存仍属于旧模型的内存向量，再改动当前库引用和目录。
                old_fingerprint = self._stored_embedding_fingerprint()
                if old_fingerprint is not None:
                    for store in (self.vector_store, self.paragraph_vector_store, self.graph_vector_store):
                        if store is not None:
                            await self.run_io(store.save, embedding_fingerprint=old_fingerprint)
                    await self.run_io(self._save_inputs)
                await self.run_io(self._migrate_legacy_space)
                self._active_vector_space_id = space_id
                self._vector_space_inputs = await self.run_io(self._read_inputs)
                original_inputs = dict(self._vector_space_inputs)
                self._vector_space_sources = {}
                self._dual_vector_pools_ready = False
                self._legacy_vector_view = None
                self.embedding_dimension = self._current_embedding_status_dimension()
                self.vector_store = self._make_vector_store(self._vectors_root())
                self.paragraph_vector_store = None
                self.graph_vector_store = None
                await self.run_io(self.metadata_store.reset_vector_projection_state)
                # 已保存的目标库先加载为复制来源；全量计划只对缺失或变化的输入调用模型。
                await self.run_io(self._embedding_state_service._load_vector_stores_for_runtime)
                if self._dual_vector_pools_enabled():
                    self._vector_space_sources = {
                        "paragraph": self.paragraph_vector_store,
                        "graph": self.graph_vector_store,
                    }
                elif self.vector_store.has_data():
                    self._vector_space_sources = {"single": self.vector_store}
                result = await self._vector_runtime_service._rebuild_all_vectors_locked(
                    reuse_space=not force,
                    batch_size=batch_size,
                    include_relations=include_relations,
                )
                if not result.get("success"):
                    raise RuntimeError(str(result.get("errors") or result.get("error") or "向量库同步失败"))
                await self.run_io(self._register_current_space, fingerprint)
                logger.info(f"模型向量库已启用: model={fingerprint.get('model')}, space={space_id}")
                return {**result, "changed": True, "space_id": space_id}
            except BaseException as exc:
                self._vector_space_inputs = original_inputs
                reason = "向量库同步已取消" if isinstance(exc, asyncio.CancelledError) else str(exc)
                self._set_runtime_capability("vector_read", False)
                self._set_runtime_capability("vector_write", False)
                self._set_vector_health(state="unavailable", error_code="vector_space_sync_failed", reason=reason)
                self._set_embedding_degraded(active=True, reason=reason)
                raise
            finally:
                self._vector_space_sources = {}
                self._target_vector_space_id = ""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import asyncio
import hashlib
import json
import time
import uuid

import numpy as np

from src.common.logger import get_logger

from ..storage import MetadataStore, QuantizationType, VectorStore
from .asset_store import ImageAssetStore, InspectedImage
from .component_paths import build_package_external_ref
from .embedder import ImageEmbedder
from .fingerprints import fingerprint_hash

logger = get_logger("A_Memorix.ImageMemory")


class ImageMemoryRuntime:
    """图片资产、认知、向量任务和范围内相似召回的统一运行时。"""

    def __init__(
        self,
        *,
        metadata_store: MetadataStore,
        asset_store: ImageAssetStore,
        embedder: ImageEmbedder,
        vector_root: Path,
        config: Dict[str, Any],
        persist_vector_store: Callable[[VectorStore, Dict[str, Any]], None],
    ) -> None:
        self.metadata_store = metadata_store
        self.asset_store = asset_store
        self.embedder = embedder
        self.vector_root = Path(vector_root)
        self.config = dict(config)
        self.persist_vector_store = persist_vector_store
        self.vector_store: Optional[VectorStore] = None
        self.fingerprint: Dict[str, Any] = {}
        self.generation = 0
        self._status = "pending"
        self._status_message = "等待真实图片嵌入探测"
        self._initialization_lock = asyncio.Lock()
        self._publication_lock = asyncio.Lock()
        # 内核创建运行时发生在写入任务启动前；以已提交的出现记录为准核对发布中断。
        self.recovery = self.reconcile_assets()

    def reconcile_assets(self) -> Dict[str, Any]:
        """恢复文件发布与数据库提交之间中断留下的资产，不删除仍有引用的文件。"""
        removed = 0
        issues: List[Dict[str, str]] = []
        assets = self.metadata_store._conn.execute("SELECT * FROM image_assets").fetchall()
        retained: Dict[str, str] = {}
        for row in assets:
            asset = dict(row)
            asset_id = str(asset["asset_id"])
            with self.metadata_store.transaction(immediate=True) as database:
                unreferenced = self.metadata_store.abort_image_jobs_if_unreferenced(asset_id, conn=database)
            if unreferenced:
                removed += int(self.asset_store.delete(str(asset["storage_key"])))
            else:
                retained[str(asset["storage_key"])] = str(asset["content_hash"])
        for path in self.asset_store.assets_root.iterdir():
            # 只处理资产库自身生成的文件；未知目录和文件保留并报告。
            name = path.name
            if name in retained:
                continue
            digest = name.split(".", 1)[0]
            managed = (
                len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
                and path.suffix in {".png", ".jpg", ".bmp", ".webp"}
            )
            temporary_digest = name[1:].split(".", 1)[0]
            temporary = (
                name.startswith(".") and name.endswith(".tmp") and len(temporary_digest) == 64
                and all(char in "0123456789abcdef" for char in temporary_digest)
            )
            if path.is_file() and not path.is_symlink() and (managed or temporary):
                removed += int(self.asset_store.delete(name))
            else:
                issues.append({"storage_key": name, "error": "资产目录中存在未知文件或目录"})
        for key, expected_hash in retained.items():
            try:
                path = self.asset_store.path_for_read(key)
                if self.asset_store._hash_file(path) != expected_hash:
                    raise ValueError("图片文件校验和与登记值不一致")
            except (OSError, ValueError) as exc:
                issues.append({"storage_key": key, "error": str(exc)})
        if issues:
            logger.error(f"图片资产恢复核对发现 {len(issues)} 项异常: {issues}")
        return {"removed_files": removed, "issues": issues}

    def _cfg(self, key: str, default: Any) -> Any:
        return self.config.get(key, default)

    def status(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "message": self._status_message,
            "fingerprint": dict(self.fingerprint),
            "dimension": int(self.vector_store.dimension) if self.vector_store is not None else 0,
            "generation": self.generation,
            "stats": self.metadata_store.image_memory_stats(),
            "recovery": self.recovery,
            "index_progress": self.metadata_store.image_index_progress(fingerprint_hash(self.fingerprint)),
        }

    async def ensure_embedding_space(self) -> Dict[str, Any]:
        if self.vector_store is not None and self._status == "ready":
            return self.status()
        async with self._initialization_lock:
            if self.vector_store is not None and self._status == "ready":
                return self.status()
            report = await self.embedder.probe()
            if not report.get("available"):
                self._status = "unavailable"
                self._status_message = str(report.get("message") or "图片嵌入模型不可用")
                return self.status()
            fingerprint = dict(report.get("fingerprint") or {})
            fp_hash = fingerprint_hash(fingerprint)
            dimension = int(report.get("dimension") or fingerprint.get("dimension") or 0)
            if not fp_hash or dimension <= 0:
                self._status = "failed"
                self._status_message = "图片嵌入探测没有返回可验证的指纹和维度"
                return self.status()
            state = self.metadata_store.get_image_runtime_state()
            previous_hash = str(state.get("fingerprint_hash") or "")
            generation = int(state.get("generation") or 0)
            if previous_hash != fp_hash:
                generation += 1
            generation = max(1, generation)
            directory = self.vector_root / fp_hash.removeprefix("sha256:")[:24]
            store = VectorStore(
                dimension=dimension,
                quantization_type=QuantizationType.INT8,
                data_dir=directory,
            )
            store.min_train_threshold = int(self._cfg("min_train_threshold", 40))
            if store.has_data():
                store.load(expected_embedding_fingerprint=fingerprint)
                warmup = store.warmup_index(force_train=True)
                if not warmup["ok"]:
                    raise RuntimeError(f"图片索引预热失败: {warmup['error']}")
            # 向量磁盘与SQLite不能跨介质原子提交；重启后重新排队缺失的投影。
            embeddings = self.metadata_store._conn.execute(
                "SELECT e.asset_id, a.status AS asset_status FROM image_embeddings e "
                "JOIN image_assets a ON a.asset_id=e.asset_id WHERE e.fingerprint_hash=? AND e.status='ready'",
                (fp_hash,),
            ).fetchall()
            for embedding in embeddings:
                asset_id = str(embedding["asset_id"])
                if embedding["asset_status"] == "active" and asset_id not in store:
                    with self.metadata_store.transaction(immediate=True) as database:
                        database.execute(
                            "UPDATE image_embeddings SET status='pending', updated_at=? WHERE asset_id=? AND fingerprint_hash=?",
                            (time.time(), asset_id, fp_hash),
                        )
                        self.metadata_store.enqueue_image_embedding_job(
                            asset_id=asset_id, fingerprint_hash=fp_hash, generation=generation, conn=database,
                        )
            deleted_ids = [str(row[0]) for row in self.metadata_store._conn.execute(
                "SELECT asset_id FROM image_assets WHERE status='deleted'"
            ).fetchall()]
            if deleted_ids and store.delete(deleted_ids):
                self.persist_vector_store(store, fingerprint)
            with self.metadata_store.transaction(immediate=True) as database:
                self.metadata_store.set_image_runtime_state(
                    generation=generation,
                    fingerprint_hash=fp_hash,
                    dimension=dimension,
                    status="ready",
                    conn=database,
                )
            self.vector_store = store
            self.fingerprint = fingerprint
            self.generation = generation
            self._status = "ready"
            self._status_message = str(report.get("message") or "图片嵌入空间已就绪")
            return self.status()

    def _publish_asset_only(self, image_bytes: bytes) -> tuple[Dict[str, Any], InspectedImage]:
        inspected = self.asset_store.inspect(image_bytes)
        if inspected.frame_count > 1:
            raise ValueError("当前图片记忆只支持静态图片")
        digest, storage_key = self.asset_store.publish(image_bytes, inspected)
        with self.metadata_store.transaction(immediate=True) as database:
            asset = self.metadata_store.upsert_image_asset(
                content_hash=digest,
                storage_key=storage_key,
                mime_type=inspected.mime_type,
                byte_size=inspected.byte_size,
                width=inspected.width,
                height=inspected.height,
                conn=database,
            )
        return asset, inspected

    async def ingest(
        self,
        *,
        image_bytes: bytes,
        source_kind: str,
        external_ref: str,
        scope_type: str,
        chat_id: str = "",
        message_id: str = "",
        component_path: str = "",
        occurred_at: Optional[float] = None,
        user_statement: str = "",
        installation_id: str = "",
    ) -> Dict[str, Any]:
        async with self._publication_lock:
            asset, _ = self._publish_asset_only(bytes(image_bytes))
            asset_id = str(asset["asset_id"])
            previous = self.metadata_store.get_image_occurrence_by_ref(external_ref)
            previous_asset_id = str((previous or {}).get("asset_id") or "")
            released: List[Dict[str, str]] = []
            try:
                with self.metadata_store.transaction(immediate=True) as database:
                    occurrence = self.metadata_store.upsert_image_occurrence(
                        asset_id=asset_id,
                        external_ref=external_ref,
                        source_kind=source_kind,
                        scope_type=scope_type,
                        chat_id=chat_id,
                        message_id=message_id,
                        component_path=component_path,
                        occurred_at=occurred_at,
                        installation_id=installation_id,
                        conn=database,
                    )
                    if str(user_statement or "").strip():
                        self.metadata_store.add_image_observation(
                            occurrence_id=str(occurrence["occurrence_id"]),
                            text=user_statement,
                            source_kind="user_statement",
                            confirm_status="stated",
                            evidence={"message_id": message_id},
                            conn=database,
                        )
                    job = None
                    if self._status == "ready":
                        fp_hash = fingerprint_hash(self.fingerprint)
                        ready = self.metadata_store.get_image_embedding(asset_id, fp_hash)
                        if ready is None or ready.get("status") != "ready":
                            job = self.metadata_store.enqueue_image_embedding_job(
                                asset_id=asset_id,
                                fingerprint_hash=fp_hash,
                                generation=self.generation,
                                conn=database,
                            )
                    if previous_asset_id and previous_asset_id != asset_id:
                        previous_asset = self.metadata_store.get_image_asset(previous_asset_id)
                        if (
                            previous_asset is not None
                            and self.metadata_store.abort_image_jobs_if_unreferenced(previous_asset_id, conn=database)
                        ):
                            released.append(
                                {
                                    "asset_id": previous_asset_id,
                                    "storage_key": str(previous_asset["storage_key"]),
                                }
                            )
            except Exception:
                with self.metadata_store.transaction(immediate=True) as database:
                    if self.metadata_store.abort_image_jobs_if_unreferenced(asset_id, conn=database):
                        released.append({"asset_id": asset_id, "storage_key": str(asset["storage_key"])})
                self.finalize_released_assets(released)
                raise
            self.finalize_released_assets(released)
            pending_descriptions = self.metadata_store.list_pending_image_descriptions(str(asset["content_hash"]))
            for pending in pending_descriptions:
                self._apply_model_description_locked(
                    content_hash=str(asset["content_hash"]),
                    text=str(pending["text"]),
                )
                with self.metadata_store.transaction(immediate=True) as database:
                    self.metadata_store.delete_pending_image_description(
                        content_hash=str(asset["content_hash"]),
                        description_hash=str(pending["description_hash"]),
                        conn=database,
                    )
        return {
            "success": True,
            "asset_id": str(asset["asset_id"]),
            "occurrence_id": str(occurrence["occurrence_id"]),
            "content_hash": str(asset["content_hash"]),
            "embedding_status": str((job or {}).get("status") or ("ready" if self._status == "ready" else self._status)),
        }

    async def process_jobs_once(self) -> Dict[str, Any]:
        status = await self.ensure_embedding_space()
        if status["status"] != "ready" or self.vector_store is None:
            return {"claimed": 0, "processed": 0, **status}
        fp_hash = fingerprint_hash(self.fingerprint)
        self.metadata_store.enqueue_unindexed_image_assets(
            fingerprint_hash=fp_hash,
            generation=self.generation,
            limit=int(self._cfg("job_enqueue_batch_size", 200)),
        )
        lease_token = uuid.uuid4().hex
        jobs = self.metadata_store.claim_image_embedding_jobs(
            lease_token=lease_token,
            lease_seconds=float(self._cfg("job_lease_seconds", 120.0)),
            max_attempts=1 + int(self._cfg("job_max_retries", 5)),
            limit=int(self._cfg("job_batch_size", 4)),
        )
        processed = 0
        for job in jobs:
            asset_id = str(job["asset_id"])
            try:
                asset = self.metadata_store.get_image_asset(asset_id)
                if asset is None:
                    raise FileNotFoundError("图片资产元数据不存在")
                payload = self.asset_store.read_bytes(str(asset["storage_key"]))
                encoded = await self.embedder.embed(payload, mime_type=str(asset["mime_type"]))
                vector = np.asarray(encoded["embedding"], dtype=np.float32).reshape(1, -1)
                if fingerprint_hash(dict(encoded.get("fingerprint") or {})) != fp_hash:
                    raise RuntimeError("图片嵌入指纹与任务不一致")
                if vector.shape[1] != self.vector_store.dimension:
                    raise RuntimeError("图片嵌入维度与图片向量池不一致")
                async with self._publication_lock:
                    publishable = self.metadata_store.image_embedding_job_is_publishable(
                        job_id=str(job["job_id"]),
                        lease_token=lease_token,
                        generation=self.generation,
                        asset_id=asset_id,
                    )
                    if not publishable:
                        continue
                    # 同一资产重新出现时，当前指纹空间内的删除标记可安全恢复。
                    self.vector_store.restore([asset_id])
                    self.vector_store.add(vector, [asset_id])
                    published = self.metadata_store.publish_image_embedding(
                        job_id=str(job["job_id"]),
                        lease_token=lease_token,
                        asset_id=asset_id,
                        fingerprint_hash=fp_hash,
                        vector_id=asset_id,
                        generation=self.generation,
                        dimension=int(vector.shape[1]),
                    )
                    if not published:
                        self.vector_store.delete([asset_id])
                        continue
                    processed += 1
            except Exception as exc:
                self.metadata_store.fail_image_embedding_job(
                    job_id=str(job["job_id"]),
                    lease_token=lease_token,
                    error=str(exc),
                )
        if processed:
            self.persist_vector_store(self.vector_store, self.fingerprint)
        # 达到图片配置的样本门槛后在后台训练，不必等到重启或通用向量库的万条门槛。
        if self.vector_store.needs_training(int(self._cfg("min_train_threshold", 40))):
            warmup = self.vector_store.warmup_index(force_train=True)
            if not warmup["ok"]:
                raise RuntimeError(f"图片索引训练失败: {warmup['error']}")
            self.persist_vector_store(self.vector_store, self.fingerprint)
        return {"claimed": len(jobs), "processed": processed, **self.status()}

    def _target_content(self, target_type: str, target_id: str) -> Optional[Dict[str, Any]]:
        queries = {
            "paragraph": ("SELECT hash AS id, content FROM paragraphs WHERE hash=? AND is_deleted=0", "content"),
            "entity": ("SELECT hash AS id, name AS content FROM entities WHERE hash=? AND is_deleted=0", "content"),
            "relation": (
                "SELECT hash AS id, subject || ' ' || predicate || ' ' || object AS content FROM relations WHERE hash=? AND is_inactive=0",
                "content",
            ),
            "episode": ("SELECT episode_id AS id, summary AS content FROM episodes WHERE episode_id=?", "content"),
        }
        query = queries.get(target_type)
        if query is None:
            return None
        row = self.metadata_store._conn.execute(query[0], (target_id,)).fetchone()
        return {"target_type": target_type, "target_id": target_id, "content": str(row[query[1]])} if row else None

    async def search(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        content_hash: str = "",
        current_occurrence_id: str = "",
        chat_ids: Optional[Sequence[str]],
        candidate_limit: int,
        similarity_threshold: float,
        session_id: str = "",
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        timings: Dict[str, float] = {}
        visible = self.metadata_store.list_visible_image_occurrences(
            chat_ids=chat_ids,
            exclude_occurrence_id=current_occurrence_id,
        )
        occurrences_by_asset: Dict[str, List[Dict[str, Any]]] = {}
        for occurrence in visible:
            occurrences_by_asset.setdefault(str(occurrence["asset_id"]), []).append(occurrence)
        if not occurrences_by_asset:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            return {
                "status": self._status, "query_asset_id": "", "hits": [], "related_memory_count": 0,
                "timings_ms": {"scope": elapsed, "embedding": 0.0, "vector_search": 0.0, "expansion": 0.0, "total": elapsed},
            }

        query_asset = self.metadata_store.get_image_asset_by_hash(content_hash) if content_hash else None
        if image_bytes:
            query_asset = self.metadata_store.get_image_asset_by_hash(self.asset_store.content_hash(bytes(image_bytes)))
        query_asset_id = str((query_asset or {}).get("asset_id") or "")
        hits: Dict[str, Dict[str, Any]] = {}
        if query_asset_id in occurrences_by_asset:
            hits[query_asset_id] = {"asset_id": query_asset_id, "match_kind": "exact_hash", "similarity": 1.0}

        query_vector: Optional[np.ndarray] = None
        timings["scope"] = round((time.perf_counter() - started) * 1000, 2)
        stage_started = time.perf_counter()
        status = await self.ensure_embedding_space()
        if status["status"] == "ready" and self.vector_store is not None:
            if image_bytes:
                encoded = await self.embedder.embed(bytes(image_bytes), mime_type=self.asset_store.inspect(bytes(image_bytes)).mime_type, session_id=session_id)
                query_vector = np.asarray(encoded["embedding"], dtype=np.float32)
            elif query_asset_id:
                query_vector = self.vector_store.get_vectors([query_asset_id]).get(query_asset_id)
                if query_vector is None and query_asset is not None:
                    # 新图片的后台索引可能尚未完成，在线查询仍可直接编码已保存的资产。
                    encoded = await self.embedder.embed(
                        self.asset_store.read_bytes(str(query_asset["storage_key"])),
                        mime_type=str(query_asset["mime_type"]),
                        session_id=session_id,
                    )
                    query_vector = np.asarray(encoded["embedding"], dtype=np.float32)
            if query_vector is not None:
                timings["embedding"] = round((time.perf_counter() - stage_started) * 1000, 2)
                stage_started = time.perf_counter()
                ids, scores = self.vector_store.search(
                    query_vector,
                    k=max(1, int(candidate_limit)),
                    allowed_ids=set(occurrences_by_asset),
                )
                for asset_id, score in zip(ids, scores, strict=True):
                    similarity = float(score)
                    if similarity < float(similarity_threshold):
                        continue
                    asset_id = str(asset_id)
                    if asset_id == query_asset_id and asset_id in hits:
                        continue
                    hits[asset_id] = {
                        "asset_id": asset_id,
                        "match_kind": "visual_similar",
                        "similarity": similarity,
                    }
        timings.setdefault("embedding", round((time.perf_counter() - stage_started) * 1000, 2))
        timings["vector_search"] = round((time.perf_counter() - stage_started) * 1000, 2) if query_vector is not None else 0.0
        stage_started = time.perf_counter()
        ordered = sorted(hits.values(), key=lambda item: float(item["similarity"]), reverse=True)
        expanded: List[Dict[str, Any]] = []
        for hit in ordered[: max(1, int(candidate_limit))]:
            asset = self.metadata_store.get_image_asset(str(hit["asset_id"])) or {}
            occurrences = occurrences_by_asset[hit["asset_id"]]
            occurrence_ids = [str(item["occurrence_id"]) for item in occurrences]
            links = self.metadata_store.list_image_memory_links(occurrence_ids)
            related = []
            for link in links:
                content = self._target_content(str(link["target_type"]), str(link["target_id"]))
                if content is not None:
                    related.append({**content, "link_kind": str(link["link_kind"])})
            expanded.append(
                {
                    **hit,
                    "content_hash": str(asset.get("content_hash") or ""),
                    "occurrences": occurrences,
                    "observations": self.metadata_store.list_image_observations(occurrence_ids),
                    "related_memories": related,
                }
            )
        timings["expansion"] = round((time.perf_counter() - stage_started) * 1000, 2)
        timings["total"] = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": status["status"],
            "query_asset_id": query_asset_id,
            "hits": expanded,
            "timings_ms": timings,
            "related_memory_count": sum(len(hit["related_memories"]) for hit in expanded),
        }

    def get(self, *, asset_id: str, chat_ids: Optional[Sequence[str]]) -> Dict[str, Any]:
        asset = self.metadata_store.get_image_asset(asset_id)
        if asset is None or asset.get("status") != "active":
            return {"success": False, "error": "图片不存在"}
        visible = self.metadata_store.list_visible_image_occurrences(chat_ids=chat_ids)
        occurrences = [item for item in visible if str(item["asset_id"]) == asset_id]
        if chat_ids is not None and not occurrences:
            return {"success": False, "error": "当前范围不可见该图片"}
        ids = [str(item["occurrence_id"]) for item in occurrences]
        links = self.metadata_store.list_image_memory_links(ids)
        return {
            "success": True,
            "asset": asset,
            "occurrences": occurrences,
            "observations": self.metadata_store.list_image_observations(ids),
            "links": [{**item, "memory": self._target_content(str(item["target_type"]), str(item["target_id"]))} for item in links],
        }

    def link(
        self,
        *,
        occurrence_id: str,
        target_type: str,
        target_id: str,
        link_kind: str,
        evidence: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self.metadata_store.get_image_occurrence(occurrence_id) is None:
            raise ValueError("图片出现记录不存在")
        if self._target_content(target_type, target_id) is None:
            raise ValueError("关联目标不存在或已失效")
        with self.metadata_store.transaction(immediate=True) as database:
            link = self.metadata_store.upsert_image_memory_link(
                occurrence_id=occurrence_id,
                target_type=target_type,
                target_id=target_id,
                link_kind=link_kind,
                evidence=evidence,
                conn=database,
            )
        return {"success": True, "link": link}

    def add_observation(self, **kwargs: Any) -> Dict[str, Any]:
        with self.metadata_store.transaction(immediate=True) as database:
            observation = self.metadata_store.add_image_observation(conn=database, **kwargs)
        return {"success": True, "observation": observation}

    def unlink(self, link_id: str) -> Dict[str, Any]:
        with self.metadata_store.transaction(immediate=True) as database:
            invalidated = self.metadata_store.invalidate_image_memory_link(link_id, conn=database)
        return {
            "success": invalidated == 1,
            "invalidated": invalidated,
            "error": "" if invalidated == 1 else "图片记忆关联不存在或已失效",
        }

    def _apply_model_description_locked(self, *, content_hash: str, text: str) -> List[Dict[str, Any]]:
        asset = self.metadata_store.get_image_asset_by_hash(content_hash)
        description = str(text or "").strip()
        if asset is None or not description:
            return []
        description_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()
        updated: List[Dict[str, Any]] = []
        with self.metadata_store.transaction(immediate=True) as database:
            for occurrence in self.metadata_store.list_image_occurrences_for_asset(str(asset["asset_id"])):
                active = self.metadata_store.list_image_observations([str(occurrence["occurrence_id"])])
                already_recorded = any(
                    item["source_kind"] == "model_description" and item["text"] == description
                    for item in active
                )
                if not already_recorded:
                    self.metadata_store.add_image_observation(
                        occurrence_id=str(occurrence["occurrence_id"]),
                        text=description,
                        source_kind="model_description",
                        confirm_status="unconfirmed",
                        evidence={"content_hash": content_hash},
                        conn=database,
                    )
                if (
                    occurrence.get("scope_type") == "chat"
                    and occurrence.get("chat_id")
                    and occurrence.get("message_id")
                    and occurrence.get("occurred_at") is not None
                ):
                    self.metadata_store.enqueue_image_description_compensation(
                        occurrence_id=str(occurrence["occurrence_id"]),
                        description_hash=description_hash,
                        conn=database,
                    )
                    updated.append(
                        {
                            "chat_id": str(occurrence["chat_id"]),
                            "message_id": str(occurrence["message_id"]),
                            "occurred_at": occurrence["occurred_at"],
                        }
                    )
        return updated

    async def apply_model_description(self, *, content_hash: str, text: str) -> List[Dict[str, Any]]:
        description = str(text or "").strip()
        if not content_hash or not description:
            return []
        description_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()
        async with self._publication_lock:
            if self.metadata_store.get_image_asset_by_hash(content_hash) is None:
                with self.metadata_store.transaction(immediate=True) as database:
                    self.metadata_store.upsert_pending_image_description(
                        content_hash=content_hash,
                        description_hash=description_hash,
                        text=description,
                        conn=database,
                    )
                return []
            return self._apply_model_description_locked(content_hash=content_hash, text=description)

    async def delete_occurrence(self, occurrence_id: str) -> Dict[str, Any]:
        occurrence = self.metadata_store.get_image_occurrence(occurrence_id)
        if occurrence is None:
            return {"success": False, "error": "图片出现记录不存在"}
        asset_id = str(occurrence["asset_id"])
        async with self._publication_lock:
            with self.metadata_store.transaction(immediate=True) as database:
                self.metadata_store.deactivate_image_occurrence(occurrence_id, conn=database)
                released = self.metadata_store.abort_image_jobs_if_unreferenced(asset_id, conn=database)
            if released:
                asset = self.metadata_store.get_image_asset(asset_id)
                if self.vector_store is not None:
                    self.vector_store.delete([asset_id])
                    self.persist_vector_store(self.vector_store, self.fingerprint)
                if asset is not None:
                    self.asset_store.delete(str(asset["storage_key"]))
        return {"success": True, "asset_released": released}

    @asynccontextmanager
    async def publication_guard(self):
        """让包卸载和向量发布共享同一临界区。"""

        async with self._publication_lock:
            yield

    def release_installation(self, installation_id: str, *, conn: Any) -> List[Dict[str, str]]:
        """在调用方事务中停用出现记录，返回提交后应清理的独占资产。"""

        released: List[Dict[str, str]] = []
        asset_ids = self.metadata_store.deactivate_installation_image_occurrences(installation_id, conn=conn)
        for asset_id in asset_ids:
            if self.metadata_store.abort_image_jobs_if_unreferenced(asset_id, conn=conn):
                asset = conn.execute("SELECT * FROM image_assets WHERE asset_id=?", (asset_id,)).fetchone()
                if asset is not None:
                    released.append({"asset_id": asset_id, "storage_key": str(asset["storage_key"])})
        return released

    async def release_installation_atomic(self, installation_id: str) -> List[Dict[str, str]]:
        """在线性发布锁内提交包图片卸载，阻止在途任务重新发布向量。"""

        async with self._publication_lock:
            with self.metadata_store.transaction(immediate=True) as database:
                return self.release_installation(installation_id, conn=database)

    def finalize_released_assets(self, assets: Sequence[Dict[str, str]]) -> None:
        for asset in assets:
            asset_id = asset["asset_id"]
            if self.vector_store is not None:
                self.vector_store.delete([asset_id])
            self.asset_store.delete(asset["storage_key"])
        if assets and self.vector_store is not None:
            self.persist_vector_store(self.vector_store, self.fingerprint)

    def list_assets(self, *, limit: int, offset: int) -> Dict[str, Any]:
        return {"success": True, "items": self.metadata_store.list_image_assets(limit=limit, offset=offset), **self.status()}

    def export_records(self, occurrence_ids: Iterable[str]) -> Dict[str, Any]:
        records = self.metadata_store.export_image_records(occurrence_ids)
        return {**records, "fingerprint": dict(self.fingerprint)}

    async def import_records(
        self,
        *,
        records: Dict[str, Any],
        asset_bytes: Optional[Dict[str, bytes]] = None,
        asset_loader: Optional[Callable[[Dict[str, Any]], bytes]] = None,
        installation_id: str,
        scope_type: str,
        chat_id: str,
        target_id_map: Dict[str, Dict[str, str]],
    ) -> Dict[str, Any]:
        asset_map: Dict[str, str] = {}
        occurrence_map: Dict[str, str] = {}
        published_assets: Dict[str, str] = {}
        async with self._publication_lock:
            try:
                for asset in records.get("assets") or []:
                    declared_hash = str(asset.get("content_hash") or "")
                    payload = (
                        asset_loader(asset)
                        if asset_loader is not None
                        else (asset_bytes or {}).get(declared_hash, b"")
                    )
                    if not payload or self.asset_store.content_hash(payload) != declared_hash:
                        raise ValueError(f"记忆包图片资产与声明哈希不一致: {declared_hash}")
                    published, _ = self._publish_asset_only(payload)
                    asset_id = str(published["asset_id"])
                    asset_map[str(asset.get("asset_id") or declared_hash)] = asset_id
                    published_assets[asset_id] = str(published["storage_key"])
                with self.metadata_store.transaction(immediate=True) as database:
                    for occurrence in records.get("occurrences") or []:
                        old_occurrence_id = str(occurrence.get("occurrence_id") or "")
                        asset_id = asset_map.get(str(occurrence.get("asset_id") or ""))
                        if not old_occurrence_id or not asset_id:
                            raise ValueError("记忆包图片出现记录引用了未知资产")
                        created = self.metadata_store.upsert_image_occurrence(
                            asset_id=asset_id,
                            external_ref=build_package_external_ref(
                                installation_id=installation_id,
                                occurrence_id=old_occurrence_id,
                            ),
                            source_kind="imported_knowledge",
                            scope_type=scope_type,
                            chat_id=chat_id,
                            component_path=str(occurrence.get("component_path") or ""),
                            occurred_at=occurrence.get("occurred_at"),
                            installation_id=installation_id,
                            conn=database,
                        )
                        occurrence_map[old_occurrence_id] = str(created["occurrence_id"])
                    for observation in records.get("observations") or []:
                        occurrence_id = occurrence_map.get(str(observation.get("occurrence_id") or ""))
                        if occurrence_id is None:
                            raise ValueError("记忆包图片认知引用了未知出现记录")
                        try:
                            observation_evidence = json.loads(str(observation.get("evidence_json") or "{}"))
                        except (TypeError, ValueError) as exc:
                            raise ValueError("记忆包图片认知证据不是有效对象") from exc
                        if not isinstance(observation_evidence, dict):
                            raise ValueError("记忆包图片认知证据不是有效对象")
                        self.metadata_store.add_image_observation(
                            occurrence_id=occurrence_id,
                            text=str(observation.get("text") or ""),
                            source_kind=str(observation.get("source_kind") or "imported_knowledge"),
                            confirm_status=str(observation.get("confirm_status") or "imported"),
                            evidence={**observation_evidence, "imported": True},
                            conn=database,
                        )
                    for link in records.get("links") or []:
                        occurrence_id = occurrence_map.get(str(link.get("occurrence_id") or ""))
                        target_type = str(link.get("target_type") or "")
                        original_target = str(link.get("target_id") or "")
                        target_id = target_id_map.get(target_type, {}).get(original_target, original_target)
                        if occurrence_id is None or self._target_content(target_type, target_id) is None:
                            raise ValueError("记忆包图片关联无法映射到有效目标")
                        try:
                            link_evidence = json.loads(str(link.get("evidence_json") or "{}"))
                        except (TypeError, ValueError) as exc:
                            raise ValueError("记忆包图片关联证据不是有效对象") from exc
                        if not isinstance(link_evidence, dict):
                            raise ValueError("记忆包图片关联证据不是有效对象")
                        self.metadata_store.upsert_image_memory_link(
                            occurrence_id=occurrence_id,
                            target_type=target_type,
                            target_id=target_id,
                            link_kind=str(link.get("link_kind") or "imported"),
                            evidence={
                                **link_evidence,
                                "imported": True,
                                "source_link_id": str(link.get("link_id") or ""),
                            },
                            conn=database,
                        )
                    if self._status == "ready":
                        fp_hash = fingerprint_hash(self.fingerprint)
                        for asset_id in asset_map.values():
                            if self.metadata_store.get_image_embedding(asset_id, fp_hash) is None:
                                self.metadata_store.enqueue_image_embedding_job(
                                    asset_id=asset_id,
                                    fingerprint_hash=fp_hash,
                                    generation=self.generation,
                                    conn=database,
                                )
            except Exception:
                released: List[Dict[str, str]] = []
                with self.metadata_store.transaction(immediate=True) as database:
                    for asset_id, storage_key in published_assets.items():
                        if self.metadata_store.abort_image_jobs_if_unreferenced(asset_id, conn=database):
                            released.append({"asset_id": asset_id, "storage_key": storage_key})
                self.finalize_released_assets(released)
                raise
        return {"assets": len(asset_map), "occurrences": len(occurrence_map), "asset_map": asset_map}

    def export_vectors(self, asset_ids: Sequence[str]) -> Optional[bytes]:
        if self.vector_store is None:
            return None
        vectors = self.vector_store.get_vectors(asset_ids)
        ids = [item for item in asset_ids if item in vectors]
        if not ids:
            return None
        output = BytesIO()
        np.savez_compressed(output, ids=np.asarray(ids), vectors=np.asarray([vectors[item] for item in ids]))
        return output.getvalue()

    async def import_vectors(self, payload: bytes, *, asset_map: Dict[str, str], fingerprint: Dict[str, Any]) -> int:
        status = await self.ensure_embedding_space()
        if status["status"] != "ready" or fingerprint != self.fingerprint or self.vector_store is None:
            return 0
        with np.load(BytesIO(payload), allow_pickle=False) as archive:
            ids = [str(item) for item in archive["ids"].tolist()]
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
        if len(ids) != len(set(ids)) or vectors.ndim != 2 or vectors.shape != (len(ids), self.vector_store.dimension):
            raise ValueError("记忆包图片向量的 ID、数量或维度无效")
        if not np.isfinite(vectors).all():
            raise ValueError("记忆包图片向量包含非有限数值")
        imported = 0
        fp_hash = fingerprint_hash(self.fingerprint)
        added_ids: List[str] = []
        async with self._publication_lock:
            try:
                with self.metadata_store.transaction(immediate=True) as database:
                    for source_id, vector in zip(ids, vectors, strict=True):
                        asset_id = asset_map.get(source_id)
                        if not asset_id:
                            raise ValueError("记忆包图片向量引用了未知资产")
                        asset = self.metadata_store.get_image_asset(asset_id)
                        if asset is None or asset["status"] != "active":
                            raise ValueError("记忆包图片向量引用的资产已失效")
                        was_active = asset_id in self.vector_store
                        self.vector_store.restore([asset_id])
                        self.vector_store.add(vector.reshape(1, -1), [asset_id])
                        if not was_active:
                            added_ids.append(asset_id)
                        self.metadata_store.record_imported_image_embedding(
                            asset_id=asset_id,
                            fingerprint_hash=fp_hash,
                            vector_id=asset_id,
                            generation=self.generation,
                            dimension=self.vector_store.dimension,
                            conn=database,
                        )
                        imported += 1
            except Exception:
                if added_ids:
                    self.vector_store.delete(added_ids)
                raise
            if imported:
                self.persist_vector_store(self.vector_store, self.fingerprint)
        return imported

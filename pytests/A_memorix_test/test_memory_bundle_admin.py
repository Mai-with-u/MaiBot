from io import BytesIO
from pathlib import Path
from typing import Any

import asyncio
import json
import shutil
import time
import zipfile

import numpy as np
import pytest

from src.A_memorix.core.runtime.services.bundle_admin_service import MemoryBundleAdminService
from src.A_memorix.core.storage import MetadataStore


class _GraphAdmin:
    def _rebuild_graph_from_metadata(self) -> dict[str, int]:
        return {"node_count": 0, "edge_count": 0}


class _FakeVectorStore:
    def __init__(self) -> None:
        self.ids: set[str] = set()

    def delete(self, ids: list[str]) -> int:
        removed = len(self.ids.intersection(ids))
        self.ids.difference_update(ids)
        return removed

    def __contains__(self, item_id: str) -> bool:
        return item_id in self.ids

    def add(self, vectors: np.ndarray, ids: list[str]) -> None:
        assert vectors.shape[0] == len(ids)
        self.ids.update(ids)

    def get_vectors(self, ids: list[str]) -> dict[str, np.ndarray]:
        return {
            item_id: np.asarray([float(index), 1.0], dtype=np.float32)
            for index, item_id in enumerate(ids)
            if item_id in self.ids
        }


class _BundleKernel:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.metadata_store = MetadataStore(data_dir)
        self.metadata_store.connect()
        self.import_task_manager = None
        self.relation_vectors_enabled = False
        self._storage_cleanup_lock = asyncio.Lock()
        self._graph_admin_service = _GraphAdmin()
        self.paragraph_vectors = _FakeVectorStore()
        self.graph_vectors = _FakeVectorStore()
        self.dual_vector_pools_enabled = True
        self.current_embedding_fingerprint: dict[str, Any] | None = None

    async def initialize(self) -> None:
        return None

    @staticmethod
    def _graph_vector_id(item_type: str, hash_value: str) -> str:
        return f"{item_type}:{hash_value}"

    def _paragraph_store(self) -> _FakeVectorStore:
        return self.paragraph_vectors

    def _graph_vector_store(self) -> _FakeVectorStore:
        return self.graph_vectors

    def _dual_vector_pools_enabled(self) -> bool:
        return self.dual_vector_pools_enabled

    @staticmethod
    def _stored_embedding_fingerprint(store: _FakeVectorStore) -> dict[str, Any]:
        _ = store
        return {"hash": "test-embedding", "dimension": 2}

    def _current_embedding_fingerprint_for_validation(self) -> dict[str, Any] | None:
        return self.current_embedding_fingerprint

    @staticmethod
    def _persist(*, force_vectors: bool = False) -> None:
        _ = force_vectors

    @staticmethod
    async def _ensure_paragraph_vector(paragraph: dict[str, Any]) -> bool:
        _ = paragraph
        return False

    @staticmethod
    async def _ensure_entity_vector(entity: dict[str, Any]) -> bool:
        _ = entity
        return False

    @staticmethod
    async def _ensure_relation_vector(relation: dict[str, Any]) -> bool:
        _ = relation
        return False


class _HistoryOnlyImportManager:
    @staticmethod
    async def get_task(task_id: str, include_chunks: bool = False) -> None:
        _ = task_id, include_chunks
        return None


def _copy_bundle_for_install(source_path: str, target_root: Path) -> Path:
    staging = target_root / "imports" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    target = staging / Path(source_path).name
    shutil.copyfile(source_path, target)
    return target


@pytest.mark.asyncio
async def test_knowledge_bundle_exports_lpmm_semantics_and_installs_without_extraction(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "source")
    target_kernel = _BundleKernel(tmp_path / "target")
    try:
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "小麦是一种谷物。",
            source="manual:test",
            metadata={"scope_type": "global"},
            knowledge_type="factual",
        )
        source_kernel.metadata_store.add_entity("小麦", source_paragraph=paragraph_hash)
        source_kernel.metadata_store.add_entity("谷物", source_paragraph=paragraph_hash)
        source_kernel.metadata_store.add_relation(
            "小麦",
            "属于",
            "谷物",
            source_paragraph=paragraph_hash,
        )

        source_service = MemoryBundleAdminService(source_kernel)
        exported = await source_service.memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:test"},
            package={"id": "example.grain", "version": "1.0.0", "name": "谷物知识"},
        )

        assert exported["success"] is True
        loaded = source_service._load_bundle(Path(exported["path"]))
        assert loaded["manifest"]["content_level"] == "knowledge"
        assert loaded["knowledge"]["docs"][0]["extracted_triples"] == [["小麦", "属于", "谷物"]]

        staged_path = _copy_bundle_for_install(exported["path"], target_kernel.data_dir)
        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(staged_path),
            scope_type="global",
        )

        assert installed["success"] is True
        assert installed["content_level"] == "knowledge"
        paragraph = target_kernel.metadata_store.query(
            "SELECT source, metadata FROM paragraphs WHERE content = ?",
            ("小麦是一种谷物。",),
        )[0]
        assert paragraph["source"] == "knowledge_pack:example.grain"
        assert json.loads(paragraph["metadata"])["knowledge_package_id"] == "example.grain"
        assert target_kernel.metadata_store.query("SELECT * FROM relations")
        assert target_kernel.metadata_store.query("SELECT * FROM episodes") == []
        assert target_kernel.metadata_store.query("SELECT * FROM knowledge_packages")[0]["package_id"] == "example.grain"
        target_kernel.paragraph_vectors.ids.add(paragraph_hash)
        entity_hashes = {
            str(row["hash"]) for row in target_kernel.metadata_store.query("SELECT hash FROM entities")
        }
        relation_hashes = {
            str(row["hash"]) for row in target_kernel.metadata_store.query("SELECT hash FROM relations")
        }
        target_kernel.graph_vectors.ids.update(f"entity:{entity_hash}" for entity_hash in entity_hashes)
        target_kernel.graph_vectors.ids.update(f"relation:{relation_hash}" for relation_hash in relation_hashes)

        uninstalled = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="uninstall",
            installation_id=installed["installation_id"],
        )
        assert uninstalled["success"] is True
        assert uninstalled["removed_memories"] is True
        assert uninstalled["removed"]["paragraphs"] == 1
        assert uninstalled["removed_vectors"] == {"paragraphs": 1, "graph": 3}
        assert target_kernel.paragraph_vectors.ids == set()
        assert target_kernel.graph_vectors.ids == set()
        assert target_kernel.metadata_store.query("SELECT * FROM paragraphs") == []
        assert target_kernel.metadata_store.query("SELECT * FROM entities") == []
        assert target_kernel.metadata_store.query("SELECT * FROM relations") == []
        assert target_kernel.metadata_store.query("SELECT * FROM knowledge_packages") == []
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_single_pool_bundle_exports_graph_vectors_with_typed_archive_ids(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "single-pool-source")
    target_kernel = _BundleKernel(tmp_path / "single-pool-target")
    try:
        source_kernel.dual_vector_pools_enabled = False
        source_kernel.graph_vectors = source_kernel.paragraph_vectors
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "水稻属于禾本科。",
            source="manual:single-pool",
            metadata={"scope_type": "global"},
            knowledge_type="factual",
        )
        entity_hashes = [
            source_kernel.metadata_store.add_entity(name, source_paragraph=paragraph_hash)
            for name in ("水稻", "禾本科")
        ]
        relation_hash = source_kernel.metadata_store.add_relation(
            "水稻",
            "属于",
            "禾本科",
            source_paragraph=paragraph_hash,
        )
        source_kernel.paragraph_vectors.ids.update([paragraph_hash, *entity_hashes, relation_hash])

        exported = await MemoryBundleAdminService(source_kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=True,
            selector={"type": "source", "value": "manual:single-pool"},
        )

        assert exported["success"] is True
        assert "vectors/graph.npz" in exported["manifest"]["components"]
        with zipfile.ZipFile(exported["path"], "r") as archive:
            with np.load(BytesIO(archive.read("vectors/graph.npz")), allow_pickle=False) as payload:
                archived_ids = {str(item_id) for item_id in payload["ids"].tolist()}
        assert archived_ids == {
            *(f"entity:{entity_hash}" for entity_hash in entity_hashes),
            f"relation:{relation_hash}",
        }

        target_kernel.dual_vector_pools_enabled = False
        target_kernel.graph_vectors = target_kernel.paragraph_vectors
        target_kernel.relation_vectors_enabled = True
        target_kernel.current_embedding_fingerprint = {"hash": "test-embedding", "dimension": 2}
        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
            scope_type="global",
        )
        assert installed["vectors"] == {
            "bundle_compatible": True,
            "imported": {"paragraphs": 1, "graph": 3},
            "generated": {"paragraphs": 0, "entities": 0, "relations": 0},
        }
        assert target_kernel.paragraph_vectors.ids == {
            paragraph_hash,
            *entity_hashes,
            relation_hash,
        }

        uninstalled = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="uninstall",
            installation_id=installed["installation_id"],
        )
        assert uninstalled["removed_vectors"] == {"paragraphs": 1, "graph": 3}
        assert target_kernel.paragraph_vectors.ids == set()
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_bundle_does_not_import_relation_vectors_when_target_disables_them(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "relation-vector-source")
    target_kernel = _BundleKernel(tmp_path / "relation-vector-target")
    try:
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "水稻属于禾本科。",
            source="manual:no-relation-vector",
        )
        entity_hashes = [
            source_kernel.metadata_store.add_entity(name, source_paragraph=paragraph_hash)
            for name in ("水稻", "禾本科")
        ]
        relation_hash = source_kernel.metadata_store.add_relation(
            "水稻",
            "属于",
            "禾本科",
            source_paragraph=paragraph_hash,
        )
        source_kernel.paragraph_vectors.ids.add(paragraph_hash)
        source_kernel.graph_vectors.ids.update(
            [
                *(f"entity:{entity_hash}" for entity_hash in entity_hashes),
                f"relation:{relation_hash}",
            ]
        )

        exported = await MemoryBundleAdminService(source_kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=True,
            selector={"type": "source", "value": "manual:no-relation-vector"},
        )
        target_kernel.relation_vectors_enabled = False
        target_kernel.current_embedding_fingerprint = {"hash": "test-embedding", "dimension": 2}
        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
            scope_type="global",
        )

        assert installed["vectors"]["imported"] == {"paragraphs": 1, "graph": 2}
        assert target_kernel.graph_vectors.ids == {
            *(f"entity:{entity_hash}" for entity_hash in entity_hashes),
        }
        assert f"relation:{relation_hash}" not in target_kernel.graph_vectors.ids
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_import_task_selector_reads_persisted_source_set_report(tmp_path: Path) -> None:
    kernel = _BundleKernel(tmp_path / "history-source")
    kernel.import_task_manager = _HistoryOnlyImportManager()
    try:
        kernel.metadata_store.add_paragraph(
            "历史批次知识",
            source="web_import:history.md",
            metadata={"scope_type": "global"},
            knowledge_type="factual",
        )
        reports_root = kernel.data_dir / "imports" / "reports"
        reports_root.mkdir(parents=True, exist_ok=True)
        (reports_root / "task-history_summary.json").write_text(
            json.dumps(
                {
                    "task_id": "task-history",
                    "files": [{"imported_sources": ["web_import:history.md"]}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        exported = await MemoryBundleAdminService(kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "import_task", "task_id": "task-history"},
        )

        assert exported["success"] is True
        assert exported["manifest"]["selector"] == {
            "type": "import_task",
            "values": ["web_import:history.md"],
            "precision": "source_set",
        }
    finally:
        kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_full_bundle_restores_closed_episode_and_profile_with_chat_remap(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "source-full")
    target_kernel = _BundleKernel(tmp_path / "target-full")
    try:
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "小明在周末去了书店。",
            source="chat_summary:chat-old",
            metadata={"scope_type": "chat", "chat_id": "chat-old", "person_ids": ["person-1"]},
            knowledge_type="narrative",
        )
        fact = source_kernel.metadata_store.upsert_fact_claim(
            scope_type="chat",
            scope_id="chat-old",
            fact_key="shared.place",
            value_text="书店",
            authority="imported",
            evidence_type="paragraph",
            evidence_id=paragraph_hash,
        )
        now = time.time()
        with source_kernel.metadata_store.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO episodes (
                    episode_id, source, title, summary, paragraph_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                ("episode-1", "chat_summary:chat-old", "周末书店", "小明去了书店", now, now),
            )
            connection.execute(
                "INSERT INTO episode_paragraphs (episode_id, paragraph_hash, position) VALUES (?, ?, 0)",
                ("episode-1", paragraph_hash),
            )
            connection.execute(
                """
                INSERT INTO person_profile_snapshots (
                    person_id, profile_version, profile_text, evidence_ids_json,
                    fact_claim_ids_json, updated_at, source_note
                ) VALUES (?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    "person-1",
                    "小明喜欢逛书店",
                    json.dumps([paragraph_hash]),
                    json.dumps([fact["claim_id"]]),
                    now,
                    "test",
                ),
            )

        source_service = MemoryBundleAdminService(source_kernel)
        knowledge_exported = await source_service.memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "chat", "chat_id": "chat-old"},
        )
        assert knowledge_exported["manifest"]["counts"] == {
            "paragraphs": 1,
            "entities": 0,
            "relations": 0,
        }

        exported = await source_service.memory_bundle_admin(
            action="export",
            content_level="full",
            include_vectors=False,
            selector={"type": "chat", "chat_id": "chat-old"},
            package={"id": "example.chat", "version": "2.0.0", "name": "群聊记忆"},
        )
        assert exported["manifest"]["counts"]["episodes"] == 1
        assert exported["manifest"]["counts"]["person_profile_snapshots"] == 1

        staged_path = _copy_bundle_for_install(exported["path"], target_kernel.data_dir)
        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(staged_path),
            scope_type="chat",
            chat_id="chat-new",
        )

        assert installed["success"] is True
        paragraph = target_kernel.metadata_store.query("SELECT source, metadata FROM paragraphs")[0]
        assert paragraph["source"] == "chat_summary:chat-new"
        assert json.loads(paragraph["metadata"])["chat_id"] == "chat-new"
        assert target_kernel.metadata_store.query("SELECT source FROM episodes")[0]["source"] == "chat_summary:chat-new"
        target_episode = target_kernel.metadata_store.query("SELECT episode_id FROM episodes")[0]["episode_id"]
        assert target_episode != "episode-1"
        assert target_kernel.metadata_store.query("SELECT episode_id FROM episode_paragraphs")[0][
            "episode_id"
        ] == target_episode
        target_fact = target_kernel.metadata_store.query("SELECT * FROM fact_claims")[0]
        assert target_fact["scope_id"] == "chat-new"
        assert target_fact["claim_id"] != fact["claim_id"]
        assert target_kernel.metadata_store.query("SELECT claim_id FROM fact_evidence")[0]["claim_id"] == target_fact[
            "claim_id"
        ]
        assert target_kernel.metadata_store.query("SELECT profile_text FROM person_profile_snapshots")[0][
            "profile_text"
        ] == "小明喜欢逛书店"
        uninstalled = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="uninstall",
            installation_id=installed["installation_id"],
        )
        assert uninstalled["success"] is True
        for table in (
            "paragraphs",
            "episodes",
            "episode_paragraphs",
            "fact_claims",
            "fact_evidence",
            "person_profile_snapshots",
            "knowledge_packages",
        ):
            assert target_kernel.metadata_store.query(f"SELECT * FROM {table}") == []
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_full_bundle_recovers_legacy_chat_scope_from_summary_source(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "legacy-chat-source")
    target_kernel = _BundleKernel(tmp_path / "legacy-chat-target")
    try:
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "旧版摘要没有 chat_id 元数据。",
            source="chat_summary:chat-old",
            metadata={},
        )
        source_kernel.metadata_store.upsert_fact_claim(
            scope_type="chat",
            scope_id="chat-old",
            fact_key="legacy.with_evidence",
            value_text="保留",
            evidence_type="paragraph",
            evidence_id=paragraph_hash,
        )
        source_kernel.metadata_store.upsert_fact_claim(
            scope_type="chat",
            scope_id="chat-old",
            fact_key="legacy.without_evidence",
            value_text="也保留",
        )

        source_service = MemoryBundleAdminService(source_kernel)
        exported = await source_service.memory_bundle_admin(
            action="export",
            content_level="full",
            include_vectors=False,
            selector={"type": "chat", "chat_id": "chat-old"},
        )
        loaded = source_service._load_bundle(Path(exported["path"]))
        assert {
            str(row["fact_key"])
            for row in loaded["state"]["tables"]["fact_claims"]
        } == {"legacy.with_evidence", "legacy.without_evidence"}

        installed = await MemoryBundleAdminService(target_kernel).memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
            scope_type="chat",
            chat_id="chat-new",
        )

        assert installed["success"] is True
        target_facts = target_kernel.metadata_store.query(
            "SELECT fact_key, scope_id FROM fact_claims ORDER BY fact_key"
        )
        assert target_facts == [
            {"fact_key": "legacy.with_evidence", "scope_id": "chat-new"},
            {"fact_key": "legacy.without_evidence", "scope_id": "chat-new"},
        ]
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_uninstall_transfers_shared_resource_ownership_between_packages(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "shared-source")
    target_kernel = _BundleKernel(tmp_path / "shared-target")
    try:
        paragraph_hash = source_kernel.metadata_store.add_paragraph(
            "共享知识不会被先卸载的知识包误删。",
            source="manual:shared",
            metadata={"scope_type": "global"},
            knowledge_type="factual",
        )
        source_service = MemoryBundleAdminService(source_kernel)
        exported_a = await source_service.memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:shared"},
            package={"id": "example.shared-a", "version": "1.0.0", "name": "共享知识A"},
        )
        exported_b = await source_service.memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:shared"},
            package={"id": "example.shared-b", "version": "1.0.0", "name": "共享知识B"},
        )
        target_service = MemoryBundleAdminService(target_kernel)
        installed_a = await target_service.memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported_a["path"], target_kernel.data_dir)),
            scope_type="global",
        )
        installed_b = await target_service.memory_bundle_admin(
            action="import",
            path=str(_copy_bundle_for_install(exported_b["path"], target_kernel.data_dir)),
            scope_type="global",
        )
        assert installed_b["inserted"]["paragraphs"] == 0

        uninstalled_a = await target_service.memory_bundle_admin(
            action="uninstall",
            installation_id=installed_a["installation_id"],
        )
        assert uninstalled_a["success"] is True
        assert uninstalled_a["retained_shared"]["paragraphs"] == 1
        assert target_kernel.metadata_store.get_paragraph(paragraph_hash) is not None

        uninstalled_b = await target_service.memory_bundle_admin(
            action="uninstall",
            installation_id=installed_b["installation_id"],
        )
        assert uninstalled_b["success"] is True
        assert target_kernel.metadata_store.get_paragraph(paragraph_hash) is None
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


def test_bundle_rejects_incomplete_checksum_coverage(tmp_path: Path) -> None:
    kernel = _BundleKernel(tmp_path / "checksum")
    try:
        kernel.metadata_store.add_paragraph(
            "校验和必须覆盖每个记忆包成员。",
            source="manual:checksum",
        )
        service = MemoryBundleAdminService(kernel)
        exported = asyncio.run(
            service.memory_bundle_admin(
                action="export",
                content_level="knowledge",
                include_vectors=False,
                selector={"type": "source", "value": "manual:checksum"},
            )
        )
        tampered_path = kernel.data_dir / "imports" / "staging" / "incomplete-checksums.amembundle"
        tampered_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(exported["path"], "r") as source_archive:
            members = {name: source_archive.read(name) for name in source_archive.namelist()}
        checksums = json.loads(members["checksums.json"])
        checksums["files"].pop("manifest.json")
        members["checksums.json"] = json.dumps(checksums, ensure_ascii=False).encode("utf-8")
        with zipfile.ZipFile(tampered_path, "w", compression=zipfile.ZIP_DEFLATED) as target_archive:
            for member_name, content in members.items():
                target_archive.writestr(member_name, content)

        with pytest.raises(ValueError, match="未完整覆盖"):
            service._load_bundle(tampered_path)
    finally:
        kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_registration_failure_rolls_back_authoritative_metadata(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "registration-source")
    target_kernel = _BundleKernel(tmp_path / "registration-target")
    try:
        source_kernel.metadata_store.add_paragraph(
            "安装登记和内容必须共享提交边界。",
            source="manual:registration",
        )
        exported = await MemoryBundleAdminService(source_kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:registration"},
        )
        service = MemoryBundleAdminService(target_kernel)

        def fail_registration(*args: Any, **kwargs: Any) -> None:
            _ = args, kwargs
            raise RuntimeError("登记失败")

        service._register_installation = fail_registration  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="登记失败"):
            await service.memory_bundle_admin(
                action="import",
                path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
                scope_type="global",
            )

        assert target_kernel.metadata_store.query("SELECT * FROM paragraphs") == []
        assert target_kernel.metadata_store.query("SELECT * FROM knowledge_packages") == []
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_projection_failure_cleans_registered_installation(tmp_path: Path) -> None:
    source_kernel = _BundleKernel(tmp_path / "projection-source")
    target_kernel = _BundleKernel(tmp_path / "projection-target")
    try:
        source_kernel.metadata_store.add_paragraph(
            "派生投影失败时必须清理已登记的安装。",
            source="manual:projection",
        )
        exported = await MemoryBundleAdminService(source_kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:projection"},
        )
        service = MemoryBundleAdminService(target_kernel)

        async def fail_vector_projection(**kwargs: Any) -> dict[str, int]:
            _ = kwargs
            raise RuntimeError("向量投影失败")

        service._ensure_imported_vectors = fail_vector_projection  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="向量投影失败"):
            await service.memory_bundle_admin(
                action="import",
                path=str(_copy_bundle_for_install(exported["path"], target_kernel.data_dir)),
                scope_type="global",
            )

        assert target_kernel.metadata_store.query("SELECT * FROM paragraphs") == []
        assert target_kernel.metadata_store.query("SELECT * FROM knowledge_packages") == []
        assert target_kernel.metadata_store.query("SELECT * FROM knowledge_package_resources") == []
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()


@pytest.mark.asyncio
async def test_interrupted_installation_is_cleaned_before_retry(tmp_path: Path) -> None:
    class ProjectionInterrupted(BaseException):
        pass

    source_kernel = _BundleKernel(tmp_path / "interrupted-source")
    target_kernel = _BundleKernel(tmp_path / "interrupted-target")
    try:
        source_kernel.metadata_store.add_paragraph(
            "中断后的安装可以通过同包重试恢复。",
            source="manual:interrupted",
        )
        exported = await MemoryBundleAdminService(source_kernel).memory_bundle_admin(
            action="export",
            content_level="knowledge",
            include_vectors=False,
            selector={"type": "source", "value": "manual:interrupted"},
            package={"id": "example.interrupted", "version": "1.0.0", "name": "中断恢复"},
        )
        staged_path = _copy_bundle_for_install(exported["path"], target_kernel.data_dir)
        service = MemoryBundleAdminService(target_kernel)
        original_rebuild = target_kernel._graph_admin_service._rebuild_graph_from_metadata

        def interrupt_projection() -> dict[str, int]:
            raise ProjectionInterrupted

        target_kernel._graph_admin_service._rebuild_graph_from_metadata = interrupt_projection
        with pytest.raises(ProjectionInterrupted):
            await service.memory_bundle_admin(action="import", path=str(staged_path), scope_type="global")

        pending = target_kernel.metadata_store.query("SELECT status FROM knowledge_packages")
        assert pending == [{"status": "installing"}]
        assert len(target_kernel.metadata_store.query("SELECT hash FROM paragraphs")) == 1

        target_kernel._graph_admin_service._rebuild_graph_from_metadata = original_rebuild
        installed = await service.memory_bundle_admin(
            action="import",
            path=str(staged_path),
            scope_type="global",
        )
        assert installed["success"] is True
        assert installed["already_installed"] is False
        assert target_kernel.metadata_store.query("SELECT status FROM knowledge_packages") == [
            {"status": "installed"}
        ]

        uninstalled = await service.memory_bundle_admin(
            action="uninstall",
            installation_id=installed["installation_id"],
        )
        assert uninstalled["removed"]["paragraphs"] == 1
        assert target_kernel.metadata_store.query("SELECT * FROM paragraphs") == []
    finally:
        source_kernel.metadata_store.close()
        target_kernel.metadata_store.close()

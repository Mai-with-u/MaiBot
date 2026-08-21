from pathlib import Path
from types import SimpleNamespace

import pytest

from src.A_memorix.core.runtime.services.fact_admin_service import MemoryFactAdminService
from src.A_memorix.core.storage.metadata_store import MetadataStore


def _fact_service(store: MetadataStore) -> MemoryFactAdminService:
    async def initialize() -> None:
        return None

    kernel = SimpleNamespace(
        metadata_store=store,
        initialize=initialize,
        _cfg=lambda key, default=None: True,
    )
    return MemoryFactAdminService(kernel)


@pytest.mark.asyncio
async def test_fact_admin_crud_revises_claim_and_refreshes_person_profile(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    service = _fact_service(store)
    try:
        created = await service.memory_fact_admin(
            action="create",
            scope_type="person",
            scope_id="person-1",
            fact_key="favorite_drink",
            value_text="咖啡",
            cardinality="single",
            stability="stable",
            profile_section="interaction_preferences",
            authority="manual",
            confidence=0.9,
            reason="人工新增",
        )
        assert created["success"] is True
        assert created["refresh_queued"] is True
        old_claim_id = created["claim"]["claim_id"]

        fetched = await service.memory_fact_admin(action="get", claim_id=old_claim_id)
        listed = await service.memory_fact_admin(
            action="list",
            scope_type="person",
            scope_id="person-1",
            statuses=["active"],
        )
        assert fetched["claim"]["claim_id"] == old_claim_id
        assert [item["claim_id"] for item in listed["items"]] == [old_claim_id]

        classified = await service.memory_fact_admin(
            action="update",
            claim_id=old_claim_id,
            profile_section="identity_settings",
            confidence=0.6,
            reason="调整分类",
        )
        assert classified["claim"]["claim_id"] == old_claim_id
        assert classified["claim"]["profile_section"] == "identity_settings"
        assert classified["claim"]["confidence"] == 0.6
        assert classified["replaced"] is False

        revised = await service.memory_fact_admin(
            action="update",
            claim_id=old_claim_id,
            value_text="绿茶",
            reason="修正事实值",
        )
        new_claim_id = revised["claim"]["claim_id"]
        assert revised["replaced"] is True
        assert new_claim_id != old_claim_id
        assert revised["claim"]["status"] == "active"
        assert store.get_fact_claim(old_claim_id)["status"] == "superseded"

        retracted = await service.memory_fact_admin(
            action="retract",
            claim_id=new_claim_id,
            reason="人工撤回",
        )
        assert retracted["claim"]["status"] == "retracted"
        restored = await service.memory_fact_admin(
            action="restore",
            claim_id=new_claim_id,
            reason="人工恢复",
        )
        assert restored["claim"]["status"] == "active"
        assert store.get_person_profile_refresh_request("person-1")["reason"] == "fact_claim_restored"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fact_admin_update_can_clear_validity_boundary(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    service = _fact_service(store)
    try:
        created = await service.memory_fact_admin(
            action="create",
            scope_type="person",
            scope_id="person-1",
            fact_key="current_city",
            value_text="杭州",
            stability="temporal",
            valid_from=100.0,
            valid_to=200.0,
        )

        updated = await service.memory_fact_admin(
            action="update",
            claim_id=created["claim"]["claim_id"],
            valid_from=None,
            valid_to=None,
            reason="取消有效期限制",
        )

        assert updated["success"] is True
        assert updated["claim"]["valid_from"] is None
        assert updated["claim"]["valid_to"] is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fact_admin_rejects_scope_change_and_does_not_queue_chat_profile(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    service = _fact_service(store)
    try:
        created = await service.memory_fact_admin(
            action="create",
            scope_type="chat",
            scope_id="chat-1",
            fact_key="topic",
            value_text="项目讨论",
        )
        assert created["success"] is True
        assert created["refresh_queued"] is False

        rejected = await service.memory_fact_admin(
            action="update",
            claim_id=created["claim"]["claim_id"],
            scope_type="person",
            scope_id="person-1",
        )
        assert rejected == {"success": False, "error": "事实更新不能改变 scope_type 或 scope_id"}
        assert store.get_person_profile_refresh_request("person-1") is None
    finally:
        store.close()

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.A_memorix.core.runtime.services.profile_admin_service import MemoryProfileAdminService
from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_person_profile_alias_override_and_refresh_are_committed_together(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with store.transaction(immediate=True) as conn:
            override = store.set_person_profile_alias_override(
                person_id="person-1",
                aliases=[" 张三 ", "zhang san", "ZHANG SAN"],
                updated_by="tester",
                source="pytest",
                conn=conn,
            )
            store.enqueue_person_profile_refresh(
                person_id="person-1",
                reason="person_aliases_updated",
                conn=conn,
            )

        assert override["aliases"] == ["张三", "zhang san"]
        assert store.get_person_profile_alias_override("person-1") == override
        refresh = store.get_person_profile_refresh_request("person-1")
        assert refresh is not None
        assert refresh["status"] == "pending"
        assert refresh["reason"] == "person_aliases_updated"

        with store.transaction(immediate=True) as conn:
            assert store.delete_person_profile_alias_override("person-1", conn=conn) is True
            store.enqueue_person_profile_refresh(
                person_id="person-1",
                reason="person_aliases_override_deleted",
                conn=conn,
            )

        assert store.get_person_profile_alias_override("person-1") is None
        assert store.get_person_profile_refresh_request("person-1")["reason"] == "person_aliases_override_deleted"

        store.set_person_profile_alias_override(person_id="person-1", aliases=["再次写入"])
        store.clear_all()
        assert store.get_person_profile_alias_override("person-1") is None
    finally:
        store.close()


def test_person_profile_alias_override_rejects_empty_aliases(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        with pytest.raises(ValueError, match="至少需要保留一个人物别名"):
            store.set_person_profile_alias_override(person_id="person-1", aliases=["  "])
    finally:
        store.close()


@pytest.mark.asyncio
async def test_profile_admin_alias_actions_write_override_and_enqueue_refresh(tmp_path: Path) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()

    async def initialize() -> None:
        return None

    def alias_details(person_id: str):
        override = store.get_person_profile_alias_override(person_id)
        derived_aliases = ["自动别名"]
        return {
            "person_id": person_id,
            "primary_name": "测试人物",
            "derived_aliases": derived_aliases,
            "manual_aliases": list(override["aliases"]) if override else [],
            "effective_aliases": list(override["aliases"]) if override else derived_aliases,
            "has_override": override is not None,
            "memory_traits": [],
            "override": override,
        }

    kernel = SimpleNamespace(
        metadata_store=store,
        person_profile_service=SimpleNamespace(get_person_alias_details=alias_details),
        initialize=initialize,
        _cfg=lambda key, default=None: True,
    )
    service = MemoryProfileAdminService(kernel)
    try:
        saved = await service.memory_profile_admin(
            action="set_aliases",
            person_id="person-1",
            aliases=["新别名"],
            updated_by="tester",
        )
        assert saved["success"] is True
        assert saved["effective_aliases"] == ["新别名"]
        assert saved["refresh_queued"] is True

        restored = await service.memory_profile_admin(action="delete_aliases", person_id="person-1")
        assert restored["success"] is True
        assert restored["effective_aliases"] == ["自动别名"]
        assert restored["refresh_queued"] is True
    finally:
        store.close()

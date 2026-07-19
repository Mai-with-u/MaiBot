from pathlib import Path

from src.A_memorix.core.storage.metadata_store import MetadataStore


def test_person_profile_snapshot_persists_fingerprint_and_refreshes_without_new_version(
    tmp_path: Path,
) -> None:
    store = MetadataStore(data_dir=tmp_path)
    store.connect()
    try:
        snapshot = store.upsert_person_profile_snapshot(
            person_id="person-1",
            profile_text="测试画像",
            aliases=["测试用户"],
            evidence_ids=["evidence-1"],
            evidence_fingerprint="fingerprint-v1",
            expires_at=100.0,
            source_note="initial",
            updated_at=10.0,
        )

        refreshed = store.refresh_person_profile_snapshot_cache(
            snapshot["snapshot_id"],
            expires_at=200.0,
            source_note="unchanged",
            updated_at=20.0,
        )
        count = store.query(
            "SELECT COUNT(*) AS count FROM person_profile_snapshots WHERE person_id = ?",
            ("person-1",),
        )[0]["count"]

        assert count == 1
        assert refreshed["profile_version"] == 1
        assert refreshed["evidence_fingerprint"] == "fingerprint-v1"
        assert refreshed["expires_at"] == 200.0
        assert refreshed["source_note"] == "unchanged"
    finally:
        store.close()

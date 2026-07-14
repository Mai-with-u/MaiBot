from __future__ import annotations

from typing import Any, Dict

from .base import KernelServiceBase


class MemoryStatsService(KernelServiceBase):
    def memory_stats(self) -> Dict[str, Any]:
        assert self.metadata_store
        stats = self.metadata_store.get_statistics()
        episodes = self.metadata_store.query("SELECT COUNT(*) AS c FROM episodes")[0]["c"]
        profiles = self.metadata_store.query("SELECT COUNT(*) AS c FROM person_profile_snapshots")[0]["c"]
        pending = self.metadata_store.query(
            "SELECT COUNT(*) AS c FROM episode_pending_paragraphs WHERE status IN ('pending', 'running', 'failed')"
        )[0]["c"]
        backfill = self._paragraph_vector_backfill_counts()
        episode_rebuild_summary = self.metadata_store.get_episode_source_rebuild_summary()
        episode_rebuild_counts = (
            episode_rebuild_summary.get("counts", {}) if isinstance(episode_rebuild_summary, dict) else {}
        )
        return {
            "paragraphs": int(stats.get("paragraph_count", 0) or 0),
            "relations": int(stats.get("relation_count", 0) or 0),
            "episodes": int(episodes or 0),
            "profiles": int(profiles or 0),
            "episode_pending": int(pending or 0),
            "stale_paragraph_marks": int(stats.get("stale_paragraph_mark_count", 0) or 0),
            "profile_refresh_pending": int(stats.get("person_profile_refresh_pending_count", 0) or 0),
            "profile_refresh_failed": int(stats.get("person_profile_refresh_failed_count", 0) or 0),
            "episode_rebuild_pending": int(
                (episode_rebuild_counts.get("pending", 0) or 0)
                + (episode_rebuild_counts.get("running", 0) or 0)
                + (episode_rebuild_counts.get("failed", 0) or 0)
            ),
            "paragraph_vector_backfill_pending": int(backfill.get("pending", 0) or 0),
            "paragraph_vector_backfill_failed": int(backfill.get("failed", 0) or 0),
            "last_maintenance_at": self._last_maintenance_at,
        }

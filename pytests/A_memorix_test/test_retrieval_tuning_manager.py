import pytest

from src.A_memorix.core.utils.retrieval_tuning_manager import (
    RetrievalTuningManager,
    RetrievalTuningTaskRecord,
)


class _FakePlugin:
    def __init__(self) -> None:
        self.config = {
            "retrieval": {
                "top_k_paragraphs": 20,
                "top_k_relations": 10,
                "top_k_final": 10,
                "alpha": 0.5,
                "enable_ppr": True,
                "ppr_alpha": 0.85,
                "ppr_timeout_seconds": 1.5,
                "vector_pools": {"mode": "single"},
            },
            "threshold": {
                "min_threshold": 0.3,
                "max_threshold": 0.95,
                "percentile": 75,
                "min_results": 3,
                "enable_auto_adjust": True,
            },
        }

    async def apply_retrieval_tuning_profile(self, profile, *, validate=True):
        self.applied_profile = profile
        self.applied_validate = validate
        return {"success": True, "runtime_rebuilt": True, "validation_passed": True}


def test_tuning_profile_normalize_clamps_and_drops_unknown_fields():
    manager = RetrievalTuningManager(_FakePlugin())

    normalized = manager.get_persistable_profile(
        {
            "retrieval": {
                "top_k_final": 9999,
                "ppr_alpha": 5,
                "fusion": {"vector_weight": 10, "bm25_weight": 0},
                "vector_pools": {
                    "semantic_weight": 0,
                    "sparse_weight": 0,
                    "graph_weight": 0,
                    "unknown": "drop-me",
                },
                "unknown": "drop-me",
            },
            "threshold": {
                "min_threshold": 0.9,
                "max_threshold": 0.2,
                "unknown": "drop-me",
            },
            "unknown": "drop-me",
        }
    )

    assert normalized["retrieval"]["top_k_final"] == 512
    assert normalized["retrieval"]["ppr_alpha"] == 0.99
    assert normalized["retrieval"]["fusion"]["vector_weight"] == 1.0
    assert "unknown" not in normalized
    assert "unknown" not in normalized["retrieval"]
    assert "unknown" not in normalized["retrieval"]["vector_pools"]
    assert normalized["threshold"]["min_threshold"] == 0.29
    assert normalized["threshold"]["max_threshold"] == 0.95
    assert "enable_auto_adjust" not in normalized["threshold"]


@pytest.mark.asyncio
async def test_apply_best_rejects_unrecommended_task_by_default():
    manager = RetrievalTuningManager(_FakePlugin())
    manager._tasks["task-1"] = RetrievalTuningTaskRecord(
        task_id="task-1",
        status="completed",
        progress=1.0,
        objective="balanced",
        intensity="quick",
        rounds_total=1,
        best_profile=manager.get_profile_snapshot(),
        validation_summary={"recommended": False},
        recommended=False,
    )

    with pytest.raises(ValueError, match="未通过"):
        await manager.apply_best("task-1")


@pytest.mark.asyncio
async def test_apply_profile_uses_runtime_hot_rebuild_hook():
    plugin = _FakePlugin()
    manager = RetrievalTuningManager(plugin)

    result = await manager.apply_profile({"retrieval": {"top_k_final": 12}}, validate=False)

    assert result["runtime_rebuilt"] is True
    assert result["validation_passed"] is True
    assert plugin.applied_validate is False
    assert plugin.applied_profile["retrieval"]["top_k_final"] == 12

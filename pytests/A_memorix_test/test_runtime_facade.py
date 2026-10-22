from pathlib import Path

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.utils.memory_lifecycle_policy import RelationLifecycleEvent


@pytest.mark.asyncio
async def test_runtime_facade_reinforce_access_filters_empty_hashes(
) -> None:
    reinforced: list[tuple[list[str], RelationLifecycleEvent]] = []
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})
    facade = kernel._runtime_facade

    class FakeMaintenanceService:
        def apply_relation_lifecycle_event(
            self,
            hashes: list[str],
            *,
            event: RelationLifecycleEvent,
        ) -> None:
            reinforced.append((hashes, event))

    kernel.metadata_store = object()  # type: ignore[assignment]
    kernel.graph_store = object()  # type: ignore[assignment]
    kernel._maintenance_service = FakeMaintenanceService()  # type: ignore[assignment]

    await facade.reinforce_access(
        ["", " relation-1 ", None, "relation-2", "relation-1"]  # type: ignore[list-item]
    )

    assert reinforced == [(["relation-1", "relation-2"], RelationLifecycleEvent.ACCESS)]
    assert kernel._last_maintenance_at is None


@pytest.mark.asyncio
async def test_runtime_facade_reinforce_access_skips_when_store_missing() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    await kernel._runtime_facade.reinforce_access(["relation-1"])

    assert kernel._last_maintenance_at is None

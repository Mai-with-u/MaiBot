import asyncio

import pytest

from src.A_memorix.core.utils.aggregate_query_service import AggregateQueryService


@pytest.mark.asyncio
async def test_aggregate_query_propagates_branch_cancellation() -> None:
    async def cancelled_search() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await AggregateQueryService().execute(
            query="测试查询",
            top_k=5,
            mix=False,
            mix_top_k=None,
            time_from=None,
            time_to=None,
            search_runner=cancelled_search,
            time_runner=None,
            episode_runner=None,
        )


@pytest.mark.asyncio
async def test_aggregate_query_keeps_ordinary_branch_failure_in_result() -> None:
    async def failed_search() -> None:
        raise RuntimeError("search failed")

    result = await AggregateQueryService().execute(
        query="测试查询",
        top_k=5,
        mix=False,
        mix_top_k=None,
        time_from=None,
        time_to=None,
        search_runner=failed_search,
        time_runner=None,
        episode_runner=None,
    )

    assert result["branches"]["search"]["success"] is False
    assert result["branches"]["search"]["error"] == "search failed"

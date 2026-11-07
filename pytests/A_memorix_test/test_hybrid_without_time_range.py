"""回归测试：hybrid 模式未提供时间范围时不应整次失败（#2187）。

报告人统计最近 400 次 query_memory 调用中 hybrid+无时间 14 次全部失败，
失败点在 SearchExecutionService._build_temporal 的一刀切校验。本文件锁定：
- hybrid 无时间 -> 开放窗口（time_from/time_to 均为 None）继续检索
- hybrid 无时间时 person/source 过滤保留
- time 无时间 -> 仍报错（语义不变）
- hybrid/time 带时间、时间格式错误、时序检索被禁用 -> 行为与修复前一致
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.A_memorix.core.retrieval import RetrievalResult, TemporalQueryOptions
from src.A_memorix.core.retrieval.dual_path import DualPathRetriever
from src.A_memorix.core.utils.search_execution_service import (
    SearchExecutionRequest,
    SearchExecutionService,
)


def test_hybrid_without_time_range_uses_open_window():
    ok, temporal, error = SearchExecutionService._build_temporal(None, "hybrid", None, None, None, None)

    assert ok is True
    assert error == ""
    assert temporal is not None
    assert temporal.time_from is None
    assert temporal.time_to is None


def test_hybrid_without_time_range_keeps_person_and_source():
    ok, temporal, _ = SearchExecutionService._build_temporal(None, "hybrid", None, None, "Alice", "chat")

    assert ok is True
    assert temporal is not None
    assert temporal.person == "Alice"
    assert temporal.source == "chat"


def test_time_without_time_range_still_fails():
    ok, temporal, error = SearchExecutionService._build_temporal(None, "time", None, None, None, None)

    assert ok is False
    assert temporal is None
    assert error == "time 模式至少需要 time_from 或 time_to"


@pytest.mark.parametrize("blank", ["", " ", "  "])
def test_hybrid_with_blank_time_strings_uses_open_window(blank):
    ok, temporal, error = SearchExecutionService._build_temporal(None, "hybrid", blank, blank, None, None)

    assert ok is True
    assert error == ""
    assert temporal is not None
    assert temporal.time_from is None
    assert temporal.time_to is None


def test_time_with_blank_time_strings_still_fails():
    ok, temporal, error = SearchExecutionService._build_temporal(None, "time", " ", None, None, None)

    assert ok is False
    assert temporal is None
    assert error == "time 模式至少需要 time_from 或 time_to"


def test_hybrid_with_time_range_unchanged():
    ok, temporal, _ = SearchExecutionService._build_temporal(None, "hybrid", "2026/10/01", "2026/10/21", None, None)

    assert ok is True
    assert temporal is not None
    # 日期型上界按当日 23:59 解析（is_end 语义），与修复前一致
    assert temporal.time_from == datetime(2026, 10, 1).timestamp()
    assert temporal.time_to == datetime(2026, 10, 21, 23, 59).timestamp()


def test_hybrid_with_invalid_time_still_fails():
    ok, temporal, error = SearchExecutionService._build_temporal(None, "hybrid", "not-a-date", None, None, None)

    assert ok is False
    assert temporal is None
    assert "时间参数错误" in error


def test_hybrid_without_time_range_still_fails_when_temporal_disabled():
    plugin_config = {"retrieval": {"temporal": {"enabled": False}}}

    ok, temporal, error = SearchExecutionService._build_temporal(plugin_config, "hybrid", None, None, None, None)

    assert ok is False
    assert temporal is None
    assert "已禁用" in error


class _CapturingRetriever:
    def __init__(self):
        self.called = False
        self.temporal = None

    async def retrieve(self, *, query, top_k, temporal, enable_ppr):
        del query, top_k, enable_ppr
        self.called = True
        self.temporal = temporal
        return [
            RetrievalResult(
                hash_value="paragraph-1",
                content="content",
                score=1.0,
                result_type="paragraph",
                source="test",
                metadata={},
            )
        ]


def _request(query_type: str) -> SearchExecutionRequest:
    return SearchExecutionRequest(
        caller="test",
        query_type=query_type,
        query="content",
        top_k=1,
        time_from=None,
        time_to=None,
        person=None,
        source=None,
        use_threshold=True,
        enable_ppr=False,
    )


_PLUGIN_CONFIG = {
    "retrieval": {
        "search": {
            "smart_fallback": {"enabled": False},
            "safe_content_dedup": {"enabled": False},
        }
    }
}


@pytest.mark.asyncio
async def test_execute_hybrid_without_time_range_succeeds():
    retriever = _CapturingRetriever()

    result = await SearchExecutionService.execute(
        retriever=retriever,
        threshold_filter=None,
        plugin_config=_PLUGIN_CONFIG,
        request=_request("hybrid"),
        enforce_chat_filter=False,
    )

    assert result.success is True
    assert retriever.called is True
    assert len(result.results) == 1
    assert retriever.temporal is not None
    assert retriever.temporal.time_from is None
    assert retriever.temporal.time_to is None


def _open_window(**kwargs) -> TemporalQueryOptions:
    return TemporalQueryOptions(time_from=None, time_to=None, **kwargs)


def _bare_retriever(metadata_store) -> DualPathRetriever:
    retriever = object.__new__(DualPathRetriever)
    retriever.metadata_store = metadata_store
    return retriever


def test_open_window_temporal_match_on_real_retriever():
    """开放窗口经真实 DualPathRetriever._is_temporal_match 的行为（锁定义预期）：

    - 有 event_time 的段落通过；
    - 只有 created_at 的段落在 allow_created_fallback=True 时通过、False 时不通过；
    - 任何时间都没有的段落不通过；source 不符不通过。
    这是 hybrid 区别于 search（temporal=None 不过滤）的固有语义。
    """
    retriever = _bare_retriever(SimpleNamespace(get_paragraph_entities=lambda _h: []))

    with_event = {"hash": "a", "event_time": 100.0, "created_at": 90.0}
    only_created = {"hash": "b", "created_at": 90.0}
    no_time = {"hash": "c"}
    other_source = {"hash": "d", "event_time": 100.0, "created_at": 90.0, "source": "other"}

    assert retriever._is_temporal_match(with_event, _open_window()) is True
    assert retriever._is_temporal_match(only_created, _open_window()) is True
    assert retriever._is_temporal_match(only_created, _open_window(allow_created_fallback=False)) is False
    assert retriever._is_temporal_match(no_time, _open_window()) is False
    assert retriever._is_temporal_match(other_source, _open_window(source="chat")) is False


def test_open_window_relation_filter_on_real_retriever():
    """relation 结果：开放窗口下仍要求至少一个有有效时间的支撑段落。"""
    supports = {
        "rel-with-time": [{"hash": "p1", "event_time": 100.0, "created_at": 90.0}],
        "rel-no-time": [{"hash": "p2"}],
    }
    retriever = _bare_retriever(
        SimpleNamespace(get_paragraphs_by_relation_hashes=lambda hashes: {h: supports[h] for h in hashes})
    )
    results = [
        RetrievalResult("rel-with-time", "r1", 1.0, "relation", "test", {}),
        RetrievalResult("rel-no-time", "r2", 1.0, "relation", "test", {}),
    ]

    filtered = retriever._apply_temporal_filter_to_relations(results, _open_window())

    assert [item.hash_value for item in filtered] == ["rel-with-time"]


def test_request_key_does_not_collide_between_modes():
    def key_for(query_type, temporal, time_from=None, time_to=None):
        request = SearchExecutionRequest(
            caller="test",
            query_type=query_type,
            query="content",
            top_k=1,
            time_from=time_from,
            time_to=time_to,
        )
        return SearchExecutionService._build_request_key(request, query_type, 1, temporal)

    hybrid_open = key_for("hybrid", _open_window())
    search_key = key_for("search", None)
    hybrid_timed = key_for("hybrid", TemporalQueryOptions(time_from=1.0, time_to=2.0), "2026/10/01", "2026/10/21")

    assert len({hybrid_open, search_key, hybrid_timed}) == 3


@pytest.mark.asyncio
async def test_execute_time_without_time_range_still_fails():
    retriever = _CapturingRetriever()

    result = await SearchExecutionService.execute(
        retriever=retriever,
        threshold_filter=None,
        plugin_config=_PLUGIN_CONFIG,
        request=_request("time"),
        enforce_chat_filter=False,
    )

    assert result.success is False
    assert retriever.called is False
    assert "time_from" in result.error

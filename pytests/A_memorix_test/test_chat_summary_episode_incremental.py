from typing import Any, Dict, List

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.storage.metadata_store import MetadataStore
from src.A_memorix.core.utils.episode_service import EpisodeService
from src.A_memorix.core.utils.summary_importer import SummaryImportResult


class _FakeSummaryImporter:
    async def import_from_stream(self, **kwargs: Any) -> SummaryImportResult:
        del kwargs
        return SummaryImportResult(
            success=True,
            detail="ok",
            paragraph_hash="new-summary-hash",
            source="chat_summary:session-1",
        )


class _MissingHashSummaryImporter:
    async def import_from_stream(self, **kwargs: Any) -> SummaryImportResult:
        del kwargs
        return SummaryImportResult(success=True, detail="ok")


class _FakeSegmentationService:
    def __init__(self) -> None:
        self.calls: List[List[Dict[str, Any]]] = []

    @staticmethod
    def generation_signature() -> Dict[str, Any]:
        return {"segmentation_version": "test", "mode": "fake"}

    async def segment(self, **kwargs: Any) -> Dict[str, Any]:
        paragraphs = list(kwargs.get("paragraphs") or [])
        self.calls.append(paragraphs)
        hashes = [str(item.get("hash", "") or "") for item in paragraphs]
        return {
            "episodes": [
                {
                    "title": "绿色围巾",
                    "summary": "用户提到自己买了绿色围巾。",
                    "paragraph_hashes": hashes,
                    "participants": [],
                    "keywords": ["绿色围巾"],
                    "time_confidence": 1.0,
                    "llm_confidence": 0.9,
                }
            ],
            "segmentation_model": "fake",
            "segmentation_version": "test",
        }


@pytest.mark.asyncio
async def test_auto_chat_summary_relies_on_source_outbox(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})
    persist_calls = 0

    async def fake_initialize() -> None:
        return None

    def fake_persist() -> None:
        nonlocal persist_calls
        persist_calls += 1

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    monkeypatch.setattr(kernel, "_persist", fake_persist)
    kernel.summary_importer = _FakeSummaryImporter()

    result = await kernel.summarize_chat_stream(chat_id="session-1")

    assert result["success"] is True
    assert result["stored_ids"] == ["new-summary-hash"]
    assert result["episode_source"] == "chat_summary:session-1"
    assert "episode_pending_ids" not in result
    assert persist_calls == 1


@pytest.mark.asyncio
async def test_auto_chat_summary_requires_paragraph_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})

    async def fake_initialize() -> None:
        return None

    monkeypatch.setattr(kernel, "initialize", fake_initialize)
    kernel.summary_importer = _MissingHashSummaryImporter()

    with pytest.raises(RuntimeError, match="paragraph_hash"):
        await kernel.summarize_chat_stream(chat_id="session-1")


def test_interval_sweep_keeps_overlapping_ranges_in_one_group() -> None:
    episode_service = EpisodeService(
        metadata_store=object(),
        plugin_config={
            "episode": {
                "max_paragraphs_per_call": 20,
                "max_chars_per_call": 6000,
                "source_time_window_hours": 24,
            }
        },
        segmentation_service=_FakeSegmentationService(),
    )
    groups = episode_service.group_paragraphs(
        [
            {
                "hash": "wide",
                "source": "chat_summary:session-1",
                "content": "跨越较长时间的事件",
                "event_time_start": 0.0,
                "event_time_end": 100.0 * 3600.0,
            },
            {
                "hash": "inside",
                "source": "chat_summary:session-1",
                "content": "位于区间内部的事件",
                "event_time_start": 3600.0,
                "event_time_end": 3600.0,
            },
        ]
    )

    assert len(groups) == 1
    assert [item["hash"] for item in groups[0]["paragraphs"]] == ["wide", "inside"]


@pytest.mark.asyncio
async def test_source_rebuild_reuses_unchanged_groups_and_only_recomputes_new_tail(tmp_path) -> None:
    metadata_store = MetadataStore(data_dir=tmp_path)
    metadata_store.connect()
    segmentation_service = _FakeSegmentationService()
    episode_service = EpisodeService(
        metadata_store=metadata_store,
        plugin_config={
            "episode": {
                "max_paragraphs_per_call": 2,
                "max_chars_per_call": 6000,
                "source_time_window_hours": 24,
            }
        },
        segmentation_service=segmentation_service,
    )
    source = "chat_summary:cache-test"
    try:
        for index in range(4):
            metadata_store.add_paragraph(
                f"缓存测试段落 {index}",
                source=source,
                time_meta={"event_time": float(index)},
            )

        first = await episode_service.rebuild_source(source)
        second = await episode_service.rebuild_source(source)
        metadata_store.add_paragraph(
            "缓存测试新增尾段",
            source=source,
            time_meta={"event_time": 5.0},
        )
        third = await episode_service.rebuild_source(source)

        assert first["recomputed_group_count"] == 2
        assert first["reused_group_count"] == 0
        assert second["recomputed_group_count"] == 0
        assert second["reused_group_count"] == 2
        assert third["recomputed_group_count"] == 1
        assert third["reused_group_count"] == 2
        assert len(segmentation_service.calls) == 3
        assert len(metadata_store.get_episodes_by_source(source)) == 3
    finally:
        metadata_store.close()


def test_source_rebuild_does_not_cache_fallback_segmentation() -> None:
    group = {"paragraphs": [{"hash": "paragraph-1"}]}
    cached = {
        "fingerprint-1": [
            {
                "title": "回退结果",
                "summary": "临时回退结果不应长期缓存。",
                "evidence_ids": ["paragraph-1"],
                "segmentation_model": "fallback_rule",
            }
        ]
    }

    assert EpisodeService._reusable_group_payloads(group, "fingerprint-1", cached) == []

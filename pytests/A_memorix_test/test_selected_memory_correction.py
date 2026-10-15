from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.runtime.services import correction_admin_service
from src.A_memorix.core.storage import MetadataStore


@pytest.fixture
def correction_kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    kernel = SDKMemoryKernel(plugin_root=tmp_path, config={})
    metadata = MetadataStore(data_dir=tmp_path / "metadata")
    metadata.connect()
    kernel.metadata_store = metadata
    monkeypatch.setattr(kernel, "initialize", AsyncMock())
    monkeypatch.setattr(kernel, "search_memory", AsyncMock(side_effect=AssertionError("多选预览不应重新搜索")))
    monkeypatch.setattr(correction_admin_service, "fuzzy_modify_cfg_enabled", lambda: True)
    monkeypatch.setattr(correction_admin_service, "fuzzy_modify_cfg_allow_global_scope", lambda: False)
    monkeypatch.setattr(correction_admin_service, "fuzzy_modify_cfg_max_targets", lambda: 3)
    monkeypatch.setattr(correction_admin_service, "fuzzy_modify_cfg_candidate_limit", lambda: 6)
    yield kernel
    metadata.close()


@pytest.mark.asyncio
async def test_selected_preview_reads_exact_records_and_still_requires_confirmation(
    correction_kernel: SDKMemoryKernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = correction_kernel
    metadata = kernel.metadata_store
    first = metadata.add_paragraph(content="小明喜欢咖啡", source="test")
    unrelated = metadata.add_paragraph(content="小明喜欢红茶", source="test")
    relation = metadata.add_relation("小明", "喜欢", "咖啡")
    captured: Dict[str, Any] = {}

    async def make_plan(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {
            "confidence": 0.9,
            "operations": [
                {"action": "mark_superseded", "candidate_id": item["candidate_id"]} for item in kwargs["candidates"]
            ]
            + [{"action": "mark_superseded", "hash": unrelated}],
        }

    monkeypatch.setattr(kernel, "_build_fuzzy_modify_llm_plan", make_plan)
    monkeypatch.setattr(kernel, "_build_fuzzy_modify_cascade_preview", lambda **kwargs: {})
    targets = [{"type": "paragraph", "id": first}, {"type": "relation", "id": relation}]
    result = await kernel.memory_correction_admin(
        action="preview", scope="memory", request_text="小明现在喜欢绿茶", targets=targets, limit=1
    )
    assert result["success"] is True
    assert result["requires_confirmation"] is True
    assert [(item["target_type"], item["hash"]) for item in captured["candidates"]] == [
        ("paragraph", first),
        ("relation", relation),
    ]
    assert captured["candidates"][1]["content"] == "小明 喜欢 咖啡"
    assert {item["hash"] for item in result["preview"]["operations"]} == {first, relation}
    assert result["plan"]["status"] == "awaiting_confirmation"
    assert metadata.get_paragraph(first)["content"] == "小明喜欢咖啡"
    assert metadata.get_relation(relation, include_inactive=False) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "missing", "entity", "duplicate", "too_many", "protected", "scope"])
async def test_invalid_selected_targets_reject_entire_preview(
    correction_kernel: SDKMemoryKernel, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    kernel = correction_kernel
    relation = kernel.metadata_store.add_relation("小明", "喜欢", "咖啡")
    target = {"type": "relation", "id": relation}
    targets = [target]
    if case == "empty":
        targets = []
    elif case == "missing":
        targets += [{"type": "paragraph", "id": "missing"}]
    elif case == "entity":
        targets = [{"type": "entity", "id": "entity-1"}]
    elif case == "duplicate":
        targets = [target, target]
    elif case == "too_many":
        targets = [target] * 4
    elif case == "protected":
        kernel.metadata_store.protect_relations([relation], is_pinned=True)
    planner = AsyncMock(side_effect=AssertionError("无效目标不应调用模型"))
    monkeypatch.setattr(kernel, "_build_fuzzy_modify_llm_plan", planner)
    result = await kernel.memory_correction_admin(
        action="preview",
        request_text="修改记忆",
        scope="memory",
        targets=targets,
        chat_id="chat-1" if case == "scope" else "",
    )
    assert result["success"] is False
    assert result["error"]
    planner.assert_not_called()
    assert kernel.metadata_store.list_fuzzy_modify_plans() == []

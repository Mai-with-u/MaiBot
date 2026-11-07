from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json

import pytest

from src.desktop_pet import exploration, exploration_search, exploration_tools


@pytest.fixture
def experience(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(exploration_tools, "is_owner_chat", lambda session: session == "owner")
    task = exploration.exploration_tasks("owner", "create", goal="资料检索")
    entry = {"title": "检索方法", "conditions": "可访问接口", "path": ["查询", "核对"],
             "outcome": "success", "result": "找到资料", "feedback": "匹配准确", "feedback_source": "observation"}
    done = exploration.exploration_tasks("owner", "complete", task["id"], summary="完成", result="找到资料",
        products={"resources": [], "experiences": [entry]})
    return done, exploration.experience_ref(entry), tmp_path / "data/desktop_pet/exploration_tasks.json"


def report(outcome="success"):
    return {"title": "本次检索", "conditions": "接口已认证", "path": ["增加分类", "查询并核对"],
            "outcome": outcome, "result": "接口返回三条结果" if outcome == "success" else "接口拒绝请求",
            "feedback": "工具实际回执", "feedback_source": "observation",
            "improvement": "分类参数可缩小结果范围" if outcome == "success" else "",
            "failure_reason": "认证过期" if outcome != "success" else ""}


def test_reuse_accumulates_without_rewriting_original_and_is_searchable(experience):
    original, reference, _ = experience
    exploration.record_experience_reuse("owner", original["id"], reference, "attempt1", report())
    exploration.record_experience_reuse("owner", original["id"], reference, "attempt2", report("failed"))
    task = exploration.exploration_tasks("owner", "get", original["id"])
    for key in ("status", "updated_at", "experiences", "result", "events"):
        assert task[key] == original[key]
    summary = exploration.reuse_summary(task, reference)
    assert summary["total"] == 2 and summary["success"] == summary["failed"] == 1
    assert summary["latest_failure"]["report"]["failure_reason"] == "认证过期"
    result = exploration_search.search_exploration_products("owner", ["认证过期"], "experience")
    assert result["total"] == 1
    assert result["items"][0]["reuse"]["total"] == 2
    assert result["items"][0]["experience_ref"] == reference


def test_concurrent_retries_are_idempotent_and_conflicts_rejected(experience):
    task, reference, _ = experience
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: exploration.record_experience_reuse(
            "owner", task["id"], reference, "same-attempt", report()), range(8)))
    assert len({item["record"]["id"] for item in results}) == 1
    assert results[-1]["reuse"]["total"] == 1
    with pytest.raises(ValueError, match="其他反馈"):
        exploration.record_experience_reuse("owner", task["id"], reference, "same-attempt", report("failed"))


def test_foreign_and_stale_refs_rejected_and_history_not_mixed(experience):
    task, reference, path = experience
    with pytest.raises(ValueError, match="不属于"):
        exploration.record_experience_reuse("other", task["id"], reference, "attempt", report())
    exploration.record_experience_reuse("owner", task["id"], reference, "attempt", report())
    tasks = json.loads(path.read_text("utf-8"))
    tasks[0]["experiences"][0]["conditions"] = "新版本条件"
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="版本"):
        exploration.record_experience_reuse("owner", task["id"], reference, "new-attempt", report())
    result = exploration_search.search_exploration_products("owner", ["检索方法"])["items"][0]
    assert result["reuse"]["total"] == 0
    assert len(exploration.exploration_tasks("owner", "get", task["id"])["reuse_records"]) == 1


@pytest.mark.parametrize("outcome,field", [("success", "improvement"), ("failed", "failure_reason"), ("partial", "failure_reason")])
def test_feedback_requires_learning_or_failure_reason(experience, outcome, field):
    task, reference, _ = experience
    payload = report(outcome)
    payload[field] = ""
    with pytest.raises(ValueError):
        exploration.record_experience_reuse("owner", task["id"], reference, "attempt", payload)
    assert "reuse_records" not in exploration.exploration_tasks("owner", "get", task["id"])


@pytest.mark.asyncio
async def test_tool_records_owner_feedback_only(experience, monkeypatch):
    from jsonschema import validate

    monkeypatch.setattr(exploration_tools.global_config.experimental, "desktop_pet_exploration_enabled", True)
    task, reference, _ = experience
    provider = exploration_tools.ExplorationToolProvider()
    context = SimpleNamespace(session_id="owner")
    specs = await provider.list_tools(context)
    spec = next(item for item in specs if item.name == "record_experience_reuse")
    args = {"task_id": task["id"], "experience_ref": reference, "reuse_key": "attempt", "report": report()}
    validate(args, spec.parameters_schema)
    invocation = SimpleNamespace(tool_name=spec.name, arguments=args)
    assert not (await provider.invoke(invocation, SimpleNamespace(session_id="group"))).success
    saved = await provider.invoke(invocation, context)
    assert saved.success and saved.structured_content["reuse"]["success"] == 1

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from src.desktop_pet import exploration


def test_records_survive_reload_and_are_session_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    record = {"title": "检索讨论", "workflow": "查询后核对", "evidence": "返回两条记录"}
    saved = exploration.schema_records("owner-session", record)[0]
    assert saved["status"] == "candidate"
    assert exploration.schema_records("other-session") == []
    assert exploration.schema_records("owner-session") == [saved]
    path = tmp_path / "data/desktop_pet/exploration_schemas.json"
    assert json.loads(path.read_text("utf-8")) == [saved]


def test_concurrent_saves_do_not_lose_records(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda i: exploration.schema_records("owner", {"title": str(i)}), range(12)))
    records = exploration.schema_records("owner")
    assert len({item["id"] for item in records}) == 12


def test_corrupt_store_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    path = tmp_path / "data/desktop_pet/exploration_schemas.json"
    path.parent.mkdir(parents=True)
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        exploration.schema_records("owner", {"title": "new"})
    assert path.read_text("utf-8") == "broken"


@pytest.mark.parametrize("goal", ["", "  ", "x" * 2001, None])
def test_invalid_goals_rejected(goal):
    with pytest.raises(ValueError):
        exploration.validate_goal(goal)


def test_task_lifecycle_scope_and_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    task = exploration.exploration_tasks("owner", "create", goal="研究工具组合")
    assert task["status"] == "queued"
    assert exploration.exploration_tasks("other") == []
    with pytest.raises(ValueError):
        exploration.exploration_tasks("other", "progress", task["id"], summary="冒用")
    progress = exploration.exploration_tasks("owner", "progress", task["id"], summary="已查询已有方法")
    assert progress["status"] == "running"
    with pytest.raises(ValueError, match="实际结果"):
        exploration.exploration_tasks("owner", "complete", task["id"], summary="完成")
    done = exploration.exploration_tasks("owner", "complete", task["id"], summary="验证完毕", result="已确认检索接口",
                                        products={"resources": [], "experiences": []})
    assert done["status"] == "completed"
    assert len(done["events"]) == 3
    assert exploration.exploration_tasks("owner", "get", task["id"])["result"] == done["result"]
    with pytest.raises(ValueError, match="已结束"):
        exploration.exploration_tasks("owner", "progress", task["id"], summary="迟到的进度")


def test_restart_marks_only_unfinished_tasks_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    pending = exploration.exploration_tasks("owner", "create", goal="未完成")
    failed = exploration.exploration_tasks("owner", "create", goal="失败任务")
    exploration.exploration_tasks("owner", "fail", failed["id"], summary="工具不可用")
    monkeypatch.setattr(exploration, "_run_id", "another-process")
    assert exploration.exploration_tasks("owner", "get", pending["id"])["status"] == "interrupted"
    assert exploration.exploration_tasks("owner", "get", failed["id"])["status"] == "failed"
    with pytest.raises(ValueError, match="已结束"):
        exploration.exploration_tasks("owner", "complete", pending["id"], summary="过期", result="错误结果")


@pytest.mark.asyncio
async def test_task_tool_reports_only_in_owner_chat(tmp_path, monkeypatch):
    from src.desktop_pet import exploration_tools

    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(exploration_tools.global_config.experimental, "desktop_pet_exploration_enabled", True)
    monkeypatch.setattr(exploration_tools, "is_owner_chat", lambda session: session == "owner")
    task = exploration.exploration_tasks("owner", "create", goal="探索")
    provider = exploration_tools.ExplorationToolProvider()
    call = SimpleNamespace(tool_name="desktop_pet_exploration_task", arguments={"action": "progress", "task_id": task["id"], "summary": "已完成第一步"})
    result = await provider.invoke(call, SimpleNamespace(session_id="group"))
    assert not result.success
    result = await provider.invoke(call, SimpleNamespace(session_id="owner"))
    assert result.success and result.structured_content["status"] == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_submission_failure_is_stored_and_returned(tmp_path, monkeypatch, enabled):
    from src.desktop_pet.service import DesktopPetService, global_config
    from src.prompt import prompt_manager as prompt_module

    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(global_config.experimental, "desktop_pet_exploration_enabled", enabled)
    monkeypatch.setattr(prompt_module.prompt_manager, "get_prompt", lambda name: object())
    monkeypatch.setattr(prompt_module.prompt_manager, "render_prompt", AsyncMock(return_value="探索说明"))
    service = DesktopPetService()
    service._exploration_session_id = AsyncMock(return_value="owner")
    service._enqueue_owner_task = AsyncMock(side_effect=RuntimeError("会话不可用"))
    service._command = AsyncMock()
    await service._explore("研究工具")
    if not enabled:
        assert exploration.exploration_tasks("owner") == []
        service._enqueue_owner_task.assert_not_awaited()
        service._exploration_session_id.assert_not_awaited()
        assert "实验性功能" in service._command.await_args.args[0]["error"]
        return
    record = exploration.exploration_tasks("owner")[0]
    assert record["status"] == "failed"
    assert "会话不可用" in record["result"]
    assert record["id"] in service._enqueue_owner_task.await_args.kwargs["text"]
    assert service._command.await_args.args[0]["task_id"] == record["id"]


def sample_products():
    return {"resources": [{"title": "接口说明", "kind": "text", "summary": "支持按名称检索",
                           "content": "传入名称返回匹配记录", "source": "https://example.com/docs"}],
            "experiences": [{"title": "按名称检索", "conditions": "需要查询权限", "path": ["查询名称", "核对返回记录"],
                             "outcome": "partial", "result": "找到两条匹配，尚未消除重名", "feedback": "下次增加平台条件",
                             "feedback_source": "observation"}]}


def test_structured_products_persist_through_progress_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    task = exploration.exploration_tasks("owner", "create", goal="寻找信息")
    exploration.exploration_tasks("owner", "progress", task["id"], summary="发现部分成果", products=sample_products())
    failed = exploration.exploration_tasks("owner", "fail", task["id"], summary="后续接口不可用")
    assert failed["resources"][0]["content"] == "传入名称返回匹配记录"
    assert failed["experiences"][0]["path"] == ["查询名称", "核对返回记录"]
    assert exploration.exploration_tasks("owner", "get", task["id"]) == failed
    with pytest.raises(ValueError):
        exploration.exploration_tasks("other", "get", task["id"])


@pytest.mark.parametrize("invalid", [
    {"resources": [{"title": "图片", "kind": "image", "summary": "无地址"}], "experiences": []},
    {"resources": [{"title": "文本", "kind": "text", "summary": "无正文"}], "experiences": []},
    {"resources": [], "experiences": [{**sample_products()["experiences"][0], "path": ["x" * 161]}]},
    {"resources": [], "experiences": [{**sample_products()["experiences"][0], "feedback": "x" * 301}]},
    {"resources": []},
])
def test_invalid_products_do_not_finish_or_overwrite_task(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr(exploration, "get_instance_root", lambda: tmp_path)
    task = exploration.exploration_tasks("owner", "create", goal="探索")
    with pytest.raises(ValueError):
        exploration.exploration_tasks("owner", "complete", task["id"], summary="完成", result="概述", products=invalid)
    assert exploration.exploration_tasks("owner", "get", task["id"]) == task

import pytest

from src.desktop_pet.todos import TodoStore


def test_archive_survives_restart_and_late_delivery(tmp_path):
    path = tmp_path / "todos.json"
    store = TodoStore(path)
    item = store.add("完成工作", condition="出现编辑器")
    store.claim_condition(item["id"], item["revision"], "当前画面可见编辑器")
    store.done(item["id"])
    archived = store.list(include_completed=True)[0]
    assert archived["completed_at"] > 0
    assert archived["evidence"] == "当前画面可见编辑器"
    assert archived["revision"] != item["revision"]
    store.finish(item["id"], item["revision"])
    store.done(item["id"])
    restarted = TodoStore(path)
    assert restarted.list() == []
    assert restarted.list(include_completed=True) == [archived]
    assert restarted.claim_due() == []
    with pytest.raises(ValueError):
        restarted.claim_condition(item["id"], archived["revision"], "仍可见")
    with pytest.raises(ValueError, match="不可修改"):
        restarted.update(item["id"], "不能重启任务", delay_seconds=1)

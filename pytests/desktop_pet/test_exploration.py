from concurrent.futures import ThreadPoolExecutor
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

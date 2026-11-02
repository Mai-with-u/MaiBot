from types import SimpleNamespace

import json

from src.desktop_pet import pet_window


def make_api(tmp_path):
    events = []
    api = pet_window.PetApi(
        SimpleNamespace(emit=events.append), None, tmp_path / "window.json", tmp_path / "requests",
    )
    api._anchor = (300, 500)
    api._scale = 1.5
    return api, events


def test_drag_events_only_start_after_actual_movement_and_pair_once(tmp_path, monkeypatch):
    api, events = make_api(tmp_path)
    positions = iter([(100, 200), (120, 230), (140, 250)])
    monkeypatch.setattr(pet_window, "cursor_pos", lambda: next(positions))
    monkeypatch.setattr(api, "_place", lambda: None)
    api.drag_begin()
    assert not events
    api.drag_move()
    api.drag_move()
    assert len(events) == 1
    assert api._anchor == (340, 550)
    started = events[0]
    assert started == {
        "type": "drag_started", "gesture_id": api._drag_id,
        "offset_x": 200, "offset_y": 300, "scale": 1.5,
    }
    api.drag_end()
    api.drag_end()
    api.drag_move()
    assert events[1:] == [{"type": "drag_ended", "gesture_id": started["gesture_id"]}]
    assert json.loads((tmp_path / "window.json").read_bytes()) == {"anchor_x": 340, "anchor_y": 550}


def test_click_does_not_start_drag_or_save_position(tmp_path, monkeypatch):
    api, events = make_api(tmp_path)
    monkeypatch.setattr(pet_window, "cursor_pos", lambda: (100, 200))
    api.drag_begin()
    api.drag_end()
    api.clicked()
    assert events == [{"type": "clicked"}]
    assert not (tmp_path / "window.json").exists()

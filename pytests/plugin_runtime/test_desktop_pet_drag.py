from types import SimpleNamespace

import json

import pytest

from src.common import atomic_file
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


@pytest.mark.parametrize("contents", [bytes(35), b"{broken", b"\xff", b"[]", b"{}", b'{"anchor_x": true, "anchor_y": 500}'])
def test_corrupt_position_is_preserved_without_blocking_startup(tmp_path, contents, capsys):
    path = tmp_path / "window.json"
    path.write_bytes(contents)

    api, events = make_api(tmp_path)

    assert not path.exists()
    backup, = tmp_path.glob("window.json.corrupt-*")
    assert backup.read_bytes() == contents
    assert str(backup) in capsys.readouterr().err
    assert not events


def test_failed_position_commit_preserves_previous_position(tmp_path, monkeypatch):
    path = tmp_path / "window.json"
    original = b'{"anchor_x": 300, "anchor_y": 500}'
    api, events = make_api(tmp_path)
    path.write_bytes(original)
    positions = iter([(100, 200), (120, 230)])
    monkeypatch.setattr(pet_window, "cursor_pos", lambda: next(positions))
    monkeypatch.setattr(api, "_place", lambda: None)

    def fail_commit(source, target):
        raise OSError("commit interrupted")

    monkeypatch.setattr(atomic_file, "replace_file", fail_commit)
    api.drag_begin()
    api.drag_move()
    with pytest.raises(OSError, match="commit interrupted"):
        api.drag_end()

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("on_screen", [True, False])
def test_saved_position_respects_monitor_layout(tmp_path, monkeypatch, on_screen):
    path = tmp_path / "window.json"
    original = b'{"anchor_x": 300, "anchor_y": 500}'
    path.write_bytes(original)
    monkeypatch.setattr(pet_window, "user32", SimpleNamespace(MonitorFromPoint=lambda point, flags: on_screen))

    assert pet_window.saved_anchor(path) == ((300, 500) if on_screen else None)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("window.json.corrupt-*"))

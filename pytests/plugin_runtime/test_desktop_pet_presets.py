from pathlib import Path
from PIL import Image
from typing import Any, Dict
import json
import pytest
import zipfile

from src.desktop_pet import skins


def write_skin(directory: Path, skin_id: str) -> Path:
    directory.mkdir(parents=True)
    Image.new("RGBA", (2, 2), (255, 160, 80, 255)).save(directory / "frame.png")
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "id": skin_id,
        "name": skin_id,
        "version": "1",
        "canvas": {
            "width": 160, "height": 160, "anchor": [80, 160],
            "bubble_anchor": [80, 0], "hitbox": [0, 0, 160, 160],
        },
        "states": {state: "idle" for state in skins.STATES},
        "events": {"click": "idle"},
        "animations": {"idle": {"file": "frame.png", "fps": 8}},
    }
    path = directory / "skin.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_bundled_presets_are_available_to_separate_instances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "installed-package" / "src" / "desktop_pet" / "presets"
    write_skin(bundled / "orange", "orange-sprout")
    monkeypatch.setattr(skins, "BUNDLED_SKIN_ROOT", bundled)

    for instance in (tmp_path / "bot-a", tmp_path / "bot-b"):
        result = skins.list_skins(instance)
        assert [skin["id"] for skin in result["skins"]] == ["builtin", "orange-sprout"]
        assert result["errors"] == []
        skin = skins.find_skin(instance, "orange-sprout")
        assert skin["resources"]["frame.png"].startswith("data:image/png;base64,")
        assert skin["animations"]["idle"]["duration_ms"] == 125
        assert not instance.exists()


@pytest.mark.parametrize("relative_directory", ("data/desktop_pet/skins/local", "plugins/example/skins/local"))
def test_duplicate_id_between_bundled_and_instance_skin_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_directory: str,
) -> None:
    bundled = tmp_path / "program-presets"
    write_skin(bundled / "orange", "orange-sprout")
    monkeypatch.setattr(skins, "BUNDLED_SKIN_ROOT", bundled)
    instance = tmp_path / "instance"
    write_skin(instance / relative_directory, "orange-sprout")

    with pytest.raises(ValueError, match="ID 重复"):
        skins.find_skin(instance, "orange-sprout")
    result = skins.list_skins(instance)
    assert [skin["id"] for skin in result["skins"]] == ["builtin"]
    assert len(result["errors"]) == 2


def test_import_cannot_replace_bundled_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "program-presets"
    path = write_skin(bundled / "orange", "orange-sprout")
    original = path.read_bytes()
    monkeypatch.setattr(skins, "BUNDLED_SKIN_ROOT", bundled)
    archive = tmp_path / "skin.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(path, "skin.json")
        output.write(path.parent / "frame.png", "frame.png")

    instance = tmp_path / "instance"
    with pytest.raises(ValueError, match="皮肤 ID 已存在"):
        skins.install_zip(instance, archive.read_bytes())
    assert path.read_bytes() == original
    assert not (instance / "data/desktop_pet/skins/orange-sprout").exists()

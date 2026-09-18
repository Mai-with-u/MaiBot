from pathlib import Path

import numpy as np
import pytest
import trimesh

from src.maisaka.modeling.service import ModelWorkspace


def test_vertex_colors_survive_import_edit_and_export(tmp_path: Path) -> None:
    workspace = ModelWorkspace("test-colors", tmp_path)
    imports = workspace.directory / "imports"
    imports.mkdir(parents=True)
    (imports / "colored.obj").write_text("v 0 0 0 1 0 0\nv 1 0 0 0 1 0\nv 0 1 0 0 0 1\nf 1 2 3\n", encoding="utf-8")
    model = workspace.inspect_model(source_file="colored.obj")
    edited = workspace.edit_model(model["model_id"], [{"op": "transform", "target": "*", "position": [1, 0, 0]}])
    expected = {(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)}
    for current in (model, edited):
        _, parts = workspace._load(current["model_id"])
        mesh = next(iter(parts.values()))
        assert mesh.visual.kind == "vertex"
        assert {tuple(color) for color in mesh.visual.vertex_colors} == expected
        exported = trimesh.load_scene(current["files"]["glb"]).to_mesh()
        assert {tuple(color) for color in exported.visual.vertex_colors} == expected


def test_face_color_boundaries_survive_glb_export(tmp_path: Path) -> None:
    workspace = ModelWorkspace("test-colors", tmp_path)
    imports = workspace.directory / "imports"
    imports.mkdir(parents=True)
    mesh = trimesh.creation.box()
    mesh.visual.face_colors = [[255, 0, 0, 255], [0, 0, 255, 255]] * 6
    mesh.export(imports / "colored.ply")
    imported = workspace.inspect_model(source_file="colored.ply")
    exported = trimesh.load_scene(imported["files"]["glb"]).to_mesh()
    expected = {(255, 0, 0, 255), (0, 0, 255, 255)}
    assert {tuple(color) for color in exported.visual.vertex_colors} == expected
    for colors in exported.visual.vertex_colors[exported.faces]:
        np.testing.assert_array_equal(colors, np.repeat(colors[:1], 3, axis=0))


def test_float32_export_collapse_is_rejected_before_saving(tmp_path: Path) -> None:
    workspace = ModelWorkspace("test-precision", tmp_path)
    with pytest.raises(ValueError, match="导出精度"):
        workspace.create_model(
            "无法导出", [{"name": "薄片", "primitive": "box", "size": [0.01, 1, 1], "position": [999999, 0, 0]}]
        )
    assert not workspace.directory.exists()


def test_precision_failure_during_edit_preserves_original(tmp_path: Path) -> None:
    workspace = ModelWorkspace("test-precision", tmp_path)
    original = workspace.create_model("薄片", [{"name": "主体", "primitive": "box", "size": [0.01, 1, 1]}])
    original_path = Path(original["files"]["glb"])
    contents = original_path.read_bytes()
    with pytest.raises(ValueError, match="导出精度"):
        workspace.edit_model(original["model_id"], [{"op": "transform", "target": "*", "position": [999999, 0, 0]}])
    assert original_path.read_bytes() == contents
    assert len(list(original_path.parent.parent.iterdir())) == 1

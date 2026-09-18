from pathlib import Path
from typing import Any, Dict

import json
import shutil
import struct

import numpy as np
import pytest
import trimesh

from src.maisaka.modeling.geometry import create_part
from src.maisaka.modeling.service import ModelWorkspace


def box(name: str = "底座", **parameters: Any) -> Dict[str, Any]:
    return {"name": name, "primitive": "box", "size": [2, 4, 1], **parameters}


def test_create_inspect_and_edit_preserve_source(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    original = workspace.create_model("桌子", [box(), {"name": "柱子", "primitive": "cylinder"}])
    original_file = Path(original["files"]["glb"])
    original_bytes = original_file.read_bytes()
    edited = workspace.edit_model(
        original["model_id"],
        [
            {"op": "transform", "target": "底座", "position": [0, 0, 2], "rotation": [0, 0, 90]},
            {"op": "color", "target": "柱子", "color": "#FF0000"},
            {"op": "add", "part": {"name": "球", "primitive": "sphere", "position": [0, 0, 3]}},
        ],
    )
    assert edited["parent_model_id"] == original["model_id"]
    assert edited["model_id"] != original["model_id"]
    assert edited["part_count"] == 3
    assert original_file.read_bytes() == original_bytes
    inspected = workspace.inspect_model(model_id=edited["model_id"])
    base = next(part for part in inspected["parts"] if part["name"] == "底座")
    np.testing.assert_allclose(base["size"], [4, 2, 1])
    np.testing.assert_allclose(base["bounds"], [[-2, -1, 1.5], [2, 1, 2.5]])
    _, meshes = workspace._load(edited["model_id"])
    assert np.all(meshes["柱子"].visual.face_colors == [255, 0, 0, 255])
    reloaded = ModelWorkspace("chat_a", tmp_path).inspect_model(model_id=original["model_id"])
    assert reloaded["part_count"] == 2
    assert Path(edited["files"]["stl"]).is_file()
    assert len(trimesh.load_mesh(edited["files"]["stl"]).faces) == edited["face_count"]


def test_transform_all_rotates_assembly_about_shared_center(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    model = workspace.create_model("组合", [box("左", position=[-3, 0, 0]), box("右", position=[3, 0, 0])])
    changed = workspace.edit_model(
        model["model_id"],
        [
            {"op": "transform", "target": "*", "rotation": [0, 0, 90]},
        ],
    )
    centers = [np.mean(part["bounds"], axis=0) for part in changed["parts"]]
    np.testing.assert_allclose(centers, [[0, -3, 0], [0, 3, 0]], atol=1e-8)


def test_failed_edit_does_not_publish_a_partial_version(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    model = workspace.create_model("测试", [box()])
    before = list((workspace.directory / "models").iterdir())
    with pytest.raises(ValueError, match="找不到部件"):
        workspace.edit_model(
            model["model_id"],
            [
                {"op": "add", "part": box("新增")},
                {"op": "remove", "target": "不存在"},
            ],
        )
    assert list((workspace.directory / "models").iterdir()) == before
    assert workspace.inspect_model(model_id=model["model_id"])["part_count"] == 1


def test_session_isolation_and_import_traversal(tmp_path: Path) -> None:
    first = ModelWorkspace("chat_a", tmp_path)
    second = ModelWorkspace("chat_b", tmp_path)
    model = first.create_model("私有模型", [box()])
    with pytest.raises(ValueError, match="不存在"):
        second.inspect_model(model_id=model["model_id"])
    for path in ("../secret.obj", "../../chat_a/model.glb", str(tmp_path / "outside.obj"), "C:\\outside.obj"):
        with pytest.raises(ValueError):
            first.inspect_model(source_file=path)
    for invalid_id in ("../chat_b", "", "../" + model["model_id"]):
        with pytest.raises(ValueError):
            first.inspect_model(model_id=invalid_id)
    with pytest.raises(ValueError):
        ModelWorkspace("../outside", tmp_path)


@pytest.mark.parametrize("extension", ["obj", "stl", "ply", "glb"])
def test_import_supported_formats(tmp_path: Path, extension: str) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    imports = workspace.directory / "imports"
    imports.mkdir(parents=True)
    mesh = trimesh.creation.box(extents=[2, 3, 4])
    mesh.visual.face_colors = [35, 120, 240, 255]
    path = imports / f"sample.{extension}"
    mesh.export(path)
    imported = workspace.inspect_model(source_file=path.name)
    assert imported["part_count"] == 1
    assert imported["face_count"] == 12
    np.testing.assert_allclose(imported["bounds"], [[-1, -1.5, -2], [1, 1.5, 2]])
    assert imported["source_file"] == path.name
    changed = workspace.edit_model(
        imported["model_id"],
        [
            {"op": "transform", "target": "*", "scale": [2, 1, 1]},
        ],
    )
    np.testing.assert_allclose(changed["parts"][0]["size"], [4, 3, 4])


def test_import_glb_preserves_instances_and_transforms(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    imports = workspace.directory / "imports"
    imports.mkdir(parents=True)
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(), geom_name="cube", node_name="one")
    matrix = np.eye(4)
    matrix[0, 3] = 3
    scene.graph.update(frame_to="two", matrix=matrix, geometry="cube")
    (imports / "instances.glb").write_bytes(scene.export(file_type="glb"))
    imported = workspace.inspect_model(source_file="instances.glb")
    assert imported["part_count"] == 2
    np.testing.assert_allclose(imported["bounds"], [[-0.5, -0.5, -0.5], [3.5, 0.5, 0.5]])


def test_glb_export_can_be_imported_without_losing_parts(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    source = workspace.create_model("组装", [box(), {"name": "圆环", "primitive": "torus"}])
    imports = workspace.directory / "imports"
    imports.mkdir()
    shutil.copyfile(source["files"]["glb"], imports / "roundtrip.glb")
    imported = workspace.inspect_model(source_file="roundtrip.glb")
    assert imported["part_count"] == 2
    assert imported["face_count"] == source["face_count"]


def test_import_rejects_external_obj_materials_and_glb_resources(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    imports = workspace.directory / "imports"
    imports.mkdir(parents=True)
    (imports / "external.obj").write_text("mtllib ../../secret.mtl\nv 0 0 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="材质引用"):
        workspace.inspect_model(source_file="external.obj")
    for extra, match in [
        ({"buffers": [{"uri": "https://example.invalid/model.bin"}]}, "自包含"),
        ({"textures": [{}]}, "贴图"),
        ({"animations": [{}]}, "动画"),
    ]:
        raw = json.dumps({"asset": {"version": "2.0"}, **extra}).encode()
        raw += b" " * (-len(raw) % 4)
        (imports / "unsupported.glb").write_bytes(
            struct.pack("<4sIIII", b"glTF", 2, 20 + len(raw), len(raw), 0x4E4F534A) + raw
        )
        with pytest.raises(ValueError, match=match):
            workspace.inspect_model(source_file="unsupported.glb")


@pytest.mark.parametrize(
    "part",
    [
        box(size=[1, 0, 1]),
        box(size=[1, True, 1]),
        box(position=[float("nan"), 0, 0]),
        box(scale=[-1, 1, 1]),
        box(color="red"),
        box(radius=2),
        {"name": "环", "primitive": "torus", "segments": 100_000},
        {"name": "环", "primitive": "torus", "radius": 0.1, "tube_radius": 0.2},
        {"name": "球", "primitive": "sphere", "unknown": 1},
    ],
)
def test_invalid_geometry_fails_without_files(tmp_path: Path, part: Dict[str, Any]) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    with pytest.raises(ValueError):
        workspace.create_model("无效", [part])
    assert not workspace.directory.exists()


def test_part_limit_and_duplicate_names(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    with pytest.raises(ValueError, match="64"):
        workspace.create_model("太多", [box(str(index)) for index in range(65)])
    with pytest.raises(ValueError, match="重复"):
        workspace.create_model("重名", [box(), box()])


@pytest.mark.parametrize("primitive", ["box", "sphere", "cylinder", "cone", "torus"])
def test_primitives_are_centered_watertight_and_colored(primitive: str) -> None:
    mesh = create_part({"name": "部件", "primitive": primitive, "color": "#00FF00"})
    np.testing.assert_allclose(mesh.bounds.mean(axis=0), [0, 0, 0], atol=1e-8)
    assert mesh.is_watertight
    assert np.all(mesh.visual.face_colors == [0, 255, 0, 255])


def test_render_model_saves_png_and_keeps_original(tmp_path: Path) -> None:
    workspace = ModelWorkspace("chat_a", tmp_path)
    model = workspace.create_model("渲染", [box()])
    first, png = workspace.render_model(model["model_id"], width=256, height=192)
    second, _ = workspace.render_model(model["model_id"], width=256, height=192, azimuth=90)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert Path(first["render_file"]).read_bytes() == png
    assert first["render_file"] != second["render_file"]
    assert workspace.inspect_model(model_id=model["model_id"])["part_count"] == 1

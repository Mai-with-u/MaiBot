from io import BytesIO
from typing import Any, Dict, List

from PIL import Image
from trimesh.visual.texture import TextureVisuals
import numpy as np
import pytest
import trimesh

from src.maisaka.modeling.renderer import render_scene


def _pixels(png: bytes) -> np.ndarray:
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        return np.array(image)


def _triangle(depths: List[float], color: List[int]) -> trimesh.Trimesh:
    # 从 +X 观察时，Y 为屏幕横轴，Z 为屏幕纵轴，X 决定遮挡。
    return trimesh.Trimesh(
        vertices=[[depths[0], -1.0, -1.0], [depths[1], 1.0, -1.0], [depths[2], 0.0, 1.0]],
        faces=[[0, 1, 2]],
        face_colors=[color],
        process=False,
    )


def test_render_scene_returns_framed_png_at_requested_size() -> None:
    mesh = trimesh.creation.box(extents=[2, 1, 3])
    vertices_before = mesh.vertices.copy()
    faces_before = mesh.faces.copy()

    png = render_scene({"长方体": mesh}, width=320, height=240, background="#F1F5F9")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    pixels = _pixels(png)
    assert pixels.shape == (240, 320, 3)
    foreground = np.any(pixels != [241, 245, 249], axis=2)
    assert foreground.sum() > 5_000
    assert not foreground[:5].any()
    assert not foreground[-5:].any()
    assert not foreground[:, :5].any()
    assert not foreground[:, -5:].any()
    np.testing.assert_array_equal(mesh.vertices, vertices_before)
    np.testing.assert_array_equal(mesh.faces, faces_before)


def test_camera_changes_view_and_supports_both_poles() -> None:
    box = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    box.visual.face_colors = [40, 150, 230, 255]
    cap = trimesh.creation.box(extents=[0.5, 0.7, 0.5])
    cap.apply_translation([0.2, 0.4, 1.8])
    cap.visual.face_colors = [240, 50, 20, 255]
    parts = {"主体": box, "顶部": cap}

    views = [
        _pixels(render_scene(parts, width=256, height=192, azimuth=azimuth, elevation=elevation))
        for azimuth, elevation in [(20.0, 30.0), (110.0, 30.0), (20.0, 90.0), (20.0, -90.0)]
    ]
    for pixels in views:
        assert np.any(pixels != [241, 245, 249])
    for pixels in views[1:]:
        assert not np.array_equal(views[0], pixels)
    assert not np.array_equal(views[2], views[3])


def test_z_buffer_resolves_intersections_independent_of_part_order() -> None:
    # 两面投影完全相同，但红面在左侧更近、蓝面在右侧更近；整体面排序无法正确绘制。
    red = _triangle([0.8, -0.8, 0.0], [255, 0, 0, 255])
    blue = _triangle([-0.8, 0.8, 0.0], [0, 0, 255, 255])
    options = {"width": 256, "height": 256, "azimuth": 0.0, "elevation": 0.0}
    first = _pixels(render_scene({"红面": red, "蓝面": blue}, **options))
    second = _pixels(render_scene({"蓝面": blue, "红面": red}, **options))

    np.testing.assert_array_equal(first, second)
    assert first[165, 80, 0] > 60
    assert first[165, 80, 2] == 0
    assert first[165, 175, 2] > 60
    assert first[165, 175, 0] == 0


def test_back_faces_render_with_the_same_lighting_and_depth() -> None:
    front = _triangle([1.0, 1.0, 1.0], [240, 0, 0, 255])
    back = _triangle([-1.0, -1.0, -1.0], [0, 0, 240, 255])
    options = {"width": 128, "height": 128, "azimuth": 0.0, "elevation": 0.0}
    forward = _pixels(render_scene({"后面": back, "前面": front}, **options))
    front.faces = front.faces[:, ::-1]
    reverse = _pixels(render_scene({"前面": front, "后面": back}, **options))

    np.testing.assert_array_equal(forward, reverse)
    assert forward[64, 64, 0] > 60
    assert forward[64, 64, 2] == 0


def test_vertex_colors_are_averaged_per_face() -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [85, 85, 85, 255])
    options = {"width": 128, "height": 128, "azimuth": 0.0, "elevation": 0.0}
    face_colored = _pixels(render_scene({"三角形": mesh}, **options))
    mesh.visual.vertex_colors = [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]]
    vertex_colored = _pixels(render_scene({"三角形": mesh}, **options))

    np.testing.assert_array_equal(face_colored, vertex_colored)


@pytest.mark.parametrize("scale", [1e-200, 1e200])
def test_auto_framing_handles_small_and_large_finite_coordinates(scale: float) -> None:
    mesh = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    expected = _pixels(render_scene({"方块": mesh}, width=128, height=128))
    mesh.vertices = (mesh.vertices + [3.0, -5.0, 9.0]) * scale
    actual = _pixels(render_scene({"方块": mesh}, width=128, height=128))

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"width": 127}, "width"),
        ({"width": 1025}, "width"),
        ({"width": 640.0}, "width"),
        ({"width": True}, "width"),
        ({"height": 0}, "height"),
        ({"height": 1025}, "height"),
        ({"height": 480.5}, "height"),
        ({"height": False}, "height"),
        ({"azimuth": float("nan")}, "azimuth"),
        ({"azimuth": float("inf")}, "azimuth"),
        ({"azimuth": "45"}, "azimuth"),
        ({"elevation": float("nan")}, "elevation"),
        ({"elevation": float("-inf")}, "elevation"),
        ({"elevation": 90.1}, "elevation"),
        ({"elevation": -90.1}, "elevation"),
        ({"background": "invalid-color"}, "background"),
        ({"background": "rgb(256, 0, 0)"}, "background"),
        ({"background": None}, "background"),
        ({"background": "#11223380"}, "透明"),
    ],
)
def test_invalid_render_parameters_raise_clear_errors(options: Dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        render_scene({"方块": trimesh.creation.box()}, **options)


@pytest.mark.parametrize("parts", [{}, None, [], {"错误": object()}, {1: trimesh.creation.box()}])
def test_invalid_parts_raise_clear_errors(parts: Any) -> None:
    with pytest.raises(ValueError, match="parts"):
        render_scene(parts)


def test_empty_mesh_and_degenerate_faces_are_rejected() -> None:
    with pytest.raises(ValueError, match="非空"):
        render_scene({"空网格": trimesh.Trimesh()})

    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    mesh.faces = [[0, 1, 2], [0, 0, 1]]
    with pytest.raises(ValueError, match="退化"):
        render_scene({"含退化面": mesh})

    collinear = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 1, 1], [2, 2, 2]], faces=[[0, 1, 2]], process=False)
    with pytest.raises(ValueError, match="退化"):
        render_scene({"共线": collinear})


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_vertices_are_rejected(coordinate: float) -> None:
    mesh = trimesh.creation.box()
    mesh.vertices[0, 0] = coordinate
    with pytest.raises(ValueError, match="有限实数"):
        render_scene({"无效顶点": mesh})


@pytest.mark.parametrize("index", [-1, 3])
def test_invalid_face_indices_are_rejected(index: int) -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    mesh.faces = [[0, 1, index]]
    with pytest.raises(ValueError, match="索引"):
        render_scene({"越界面": mesh})


def test_face_limit_is_total_across_parts() -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    mesh.faces = np.tile([[0, 1, 2]], (15_001, 1))
    mesh.visual.face_colors = [255, 0, 0, 255]
    with pytest.raises(ValueError, match="30000"):
        render_scene({"一": mesh, "二": mesh})


def test_highly_overlapping_faces_exceed_pixel_work_limit() -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    mesh.faces = np.tile([[0, 1, 2]], (30_000, 1))
    mesh.visual.face_colors = [255, 0, 0, 255]

    with pytest.raises(ValueError, match="像素工作量超过 100000000.*降低分辨率或简化模型"):
        render_scene({"重叠面": mesh}, width=1024, height=1024, azimuth=0.0, elevation=0.0)


@pytest.mark.parametrize("kind", ["face", "vertex"])
def test_transparency_is_rejected_before_averaging(kind: str) -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    if kind == "face":
        mesh.visual.face_colors = [255, 0, 0, 254]
    else:
        mesh.visual.vertex_colors = [[255, 0, 0, 254], [0, 255, 0, 255], [0, 0, 255, 255]]
    with pytest.raises(ValueError, match="透明"):
        render_scene({"透明面": mesh})


def test_texture_visuals_are_rejected() -> None:
    mesh = trimesh.creation.box()
    mesh.visual = TextureVisuals(uv=np.zeros((len(mesh.vertices), 2)))
    with pytest.raises(ValueError, match="纹理"):
        render_scene({"纹理": mesh})


def test_edge_on_scene_raises_instead_of_returning_empty_image() -> None:
    mesh = _triangle([0.0, 0.0, 0.0], [255, 0, 0, 255])
    with pytest.raises(ValueError, match="投影.*退化"):
        render_scene({"侧面": mesh}, azimuth=90.0, elevation=0.0)

"""不依赖图形上下文的同步三角网格渲染器。"""

from io import BytesIO
from math import cos, isfinite, radians, sin
from numbers import Integral, Real
from typing import Dict, List, Tuple

from PIL import Image, ImageColor
from trimesh.visual.color import ColorVisuals
import numpy as np
import trimesh


_MAX_FACES = 30_000
_MAX_PIXEL_WORK = 100_000_000
_FRAME_FILL = 0.88


def _validate_dimension(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or not 128 <= value <= 1024:
        raise ValueError(f"{name} 必须是 [128, 1024] 范围内的整数")
    return int(value)


def _validate_angle(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须是有限实数")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} 必须是有限实数") from exc
    if not isfinite(result):
        raise ValueError(f"{name} 必须是有限实数")
    return result


def _parse_background(background: str) -> Tuple[int, int, int]:
    if not isinstance(background, str):
        raise ValueError("background 必须是有效的颜色字符串")
    try:
        color = ImageColor.getcolor(background, "RGBA")
    except ValueError as exc:
        raise ValueError("background 必须是有效的颜色字符串") from exc
    if any(channel < 0 or channel > 255 for channel in color):
        raise ValueError("background 颜色分量必须在 [0, 255] 范围内")
    if color[3] != 255:
        raise ValueError("不支持透明背景，background 必须完全不透明")
    return color[:3]


def _face_colors(mesh: trimesh.Trimesh, name: str, faces: np.ndarray) -> np.ndarray:
    visual = mesh.visual
    if not isinstance(visual, ColorVisuals):
        raise ValueError(f"部件 {name!r} 仅支持 ColorVisuals 面颜色或顶点颜色，不支持纹理材质")

    kind = visual.kind
    if kind == "vertex":
        colors = np.asarray(visual.vertex_colors)
        count = len(mesh.vertices)
    elif kind in (None, "face"):
        # 未指定颜色时采用 trimesh 的默认不透明面颜色。
        colors = np.asarray(visual.face_colors)
        count = len(faces)
    else:
        raise ValueError(f"部件 {name!r} 的颜色模式无效")

    if colors.shape != (count, 4):
        raise ValueError(f"部件 {name!r} 的 RGBA 颜色数量与几何数据不一致")
    if colors.dtype.kind not in "fiu" or not np.isfinite(colors).all() or np.any((colors < 0) | (colors > 255)):
        raise ValueError(f"部件 {name!r} 的颜色必须是 [0, 255] 范围内的有限数值")
    # 必须先检查所有顶点的透明度，不能用面平均掩盖透明顶点。
    if np.any(colors[:, 3] != 255):
        raise ValueError(f"部件 {name!r} 包含透明颜色，渲染器仅支持完全不透明的颜色")
    if kind == "vertex":
        return colors[faces, :3].mean(axis=1)
    return colors[:, :3].astype(np.float64)


def _prepare_scene(parts: Dict[str, trimesh.Trimesh]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not isinstance(parts, dict) or not parts:
        raise ValueError("parts 必须是非空的部件名称到 Trimesh 的字典")

    triangle_parts: List[np.ndarray] = []
    color_parts: List[np.ndarray] = []
    face_count = 0
    for name, mesh in parts.items():
        if not isinstance(name, str) or not isinstance(mesh, trimesh.Trimesh):
            raise ValueError("parts 的键必须是字符串，值必须是 trimesh.Trimesh")
        vertices = np.asarray(mesh.vertices)
        faces = np.asarray(mesh.faces)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
            raise ValueError(f"部件 {name!r} 的顶点必须是非空的 (N, 3) 数组")
        if vertices.dtype.kind not in "fiu" or not np.isfinite(vertices).all():
            raise ValueError(f"部件 {name!r} 的顶点必须全部是有限实数")
        if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
            raise ValueError(f"部件 {name!r} 必须包含非空的三角形面 (N, 3)")
        if faces.dtype.kind not in "iu" or np.any(faces < 0) or np.any(faces >= len(vertices)):
            raise ValueError(f"部件 {name!r} 包含无效的三角形顶点索引")
        face_count += len(faces)
        if face_count > _MAX_FACES:
            raise ValueError(f"场景三角形面总数不能超过 {_MAX_FACES}")
        triangle_parts.append(vertices[faces])
        color_parts.append(_face_colors(mesh, name, faces))

    triangles = np.concatenate(triangle_parts).astype(np.float64)
    # 先缩放再居中，避免有限但绝对值很大的世界坐标在减法和叉积中溢出。
    magnitude = np.abs(triangles).max()
    if magnitude == 0:
        raise ValueError("场景包含退化三角形，所有顶点均重合")
    triangles /= magnitude
    lower = triangles.min(axis=(0, 1))
    upper = triangles.max(axis=(0, 1))
    triangles -= lower * 0.5 + upper * 0.5

    edge_a = triangles[:, 1] - triangles[:, 0]
    edge_b = triangles[:, 2] - triangles[:, 0]
    edge_scale = np.maximum(np.abs(edge_a).max(axis=1), np.abs(edge_b).max(axis=1))
    if np.any(edge_scale == 0):
        raise ValueError("场景包含顶点重合的退化三角形")
    # 按每个面的边长缩放，避免大小差异明显的部件在面积计算时下溢。
    normals = np.cross(edge_a / edge_scale[:, None], edge_b / edge_scale[:, None])
    normal_lengths = np.linalg.norm(normals, axis=1)
    if np.any(normal_lengths <= np.finfo(np.float64).eps * 16):
        raise ValueError("场景包含零面积或数值退化的三角形")
    normals /= normal_lengths[:, None]
    return triangles, np.concatenate(color_parts), normals


def _camera_basis(azimuth: float, elevation: float) -> np.ndarray:
    azimuth_rad = radians(azimuth % 360.0)
    elevation_rad = radians(elevation)
    ca, sa = cos(azimuth_rad), sin(azimuth_rad)
    ce, se = cos(elevation_rad), sin(elevation_rad)
    if abs(elevation) == 90.0:
        ce = 0.0

    # 直接构造右手正交基，在顶视和底视时也无需与世界上方向做叉积。
    # 列向量分别为屏幕右方、屏幕上方、指向相机的方向；深度越大越近。
    return np.array([[-sa, -se * ca, ce * ca], [ca, -se * sa, ce * sa], [0.0, ce, se]])


def _project_scene(triangles: np.ndarray, basis: np.ndarray, width: int, height: int) -> np.ndarray:
    projected = triangles @ basis
    lower = projected[:, :, :2].min(axis=(0, 1))
    upper = projected[:, :, :2].max(axis=(0, 1))
    extent = upper - lower
    units_per_pixel = max(extent[0] / (width * _FRAME_FILL), extent[1] / (height * _FRAME_FILL))
    if units_per_pixel <= 0:
        raise ValueError("当前相机视角下场景投影退化")

    # 根据屏幕空间包围盒自动取景，保持宽高比并在四周留出边距。
    projected[:, :, :2] = (projected[:, :, :2] - (lower * 0.5 + upper * 0.5)) / units_per_pixel
    projected[:, :, 0] += width * 0.5
    projected[:, :, 1] = height * 0.5 - projected[:, :, 1]
    return projected


def _shade_faces(colors: np.ndarray, normals: np.ndarray, basis: np.ndarray) -> np.ndarray:
    camera_normals = normals @ basis
    # 双面渲染：把法线转向观察者，使反向绕序的面获得相同的光照。
    camera_normals[camera_normals[:, 2] < 0] *= -1
    light = np.array([-0.35, 0.5, 1.0])
    light /= np.linalg.norm(light)
    diffuse = np.clip(camera_normals @ light, 0.0, 1.0)
    intensity = 0.35 + 0.65 * diffuse
    return np.clip(np.rint(colors * intensity[:, None]), 0, 255).astype(np.uint8)


def _rasterize(
    triangles: np.ndarray,
    colors: np.ndarray,
    width: int,
    height: int,
    background: Tuple[int, int, int],
) -> np.ndarray:
    # 在开始光栅化前累计屏幕包围盒工作量，避免大量重叠的大面耗尽 CPU 时间。
    # 边界与下方像素中心采样一致，并裁剪到画布；完全遮挡的面也会消耗计算量。
    bounds_min = np.maximum(np.ceil(triangles[:, :, :2].min(axis=1) - 0.5), 0).astype(np.int64)
    bounds_max = np.minimum(np.floor(triangles[:, :, :2].max(axis=1) - 0.5), [width - 1, height - 1]).astype(np.int64)
    bounds_size = np.maximum(bounds_max - bounds_min + 1, 0)
    pixel_work = np.sum(bounds_size[:, 0] * bounds_size[:, 1], dtype=np.int64)
    if pixel_work > _MAX_PIXEL_WORK:
        raise ValueError(f"三角形屏幕包围盒累计像素工作量超过 {_MAX_PIXEL_WORK}，请降低分辨率或简化模型")

    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = background
    depth_buffer = np.full((height, width), -np.inf, dtype=np.float64)
    pixel_x = np.arange(width, dtype=np.float64) + 0.5
    pixel_y = np.arange(height, dtype=np.float64) + 0.5
    rendered = False

    for triangle, color, lower, upper in zip(triangles, colors, bounds_min, bounds_max, strict=True):
        x0, y0, z0 = triangle[0]
        x1, y1, z1 = triangle[1]
        x2, y2, z2 = triangle[2]
        ax, ay = x1 - x0, y1 - y0
        bx, by = x2 - x0, y2 - y0
        determinant = ax * by - ay * bx
        # 三维合法的面仍可能正好侧对相机，不能把它当作覆盖屏幕的面。
        if abs(determinant) <= 1e-8:
            continue

        left, top = lower
        right, bottom = upper
        if left > right or top > bottom:
            continue

        # 仅对三角形包围盒做 NumPy 广播，避免逐像素 Python 循环。
        dx = pixel_x[None, left : right + 1] - x0
        dy = pixel_y[top : bottom + 1, None] - y0
        weight_a = (by * dx - bx * dy) / determinant
        weight_b = (ax * dy - ay * dx) / determinant
        inside = (weight_a >= -1e-9) & (weight_b >= -1e-9) & (weight_a + weight_b <= 1.0 + 1e-9)
        # 正交投影的深度可直接按重心坐标插值，相交面无需排序或拆分。
        depth = z0 + weight_a * (z1 - z0) + weight_b * (z2 - z0)
        current_depth = depth_buffer[top : bottom + 1, left : right + 1]
        visible = inside & (depth > current_depth)
        if not np.any(visible):
            continue
        current_depth[visible] = depth[visible]
        image_region = image[top : bottom + 1, left : right + 1]
        image_region[visible] = color
        rendered = True

    if not rendered:
        raise ValueError("当前视角下没有可见三角形，投影可能退化或小于一个像素")
    return image


def render_scene(
    parts: Dict[str, trimesh.Trimesh],
    *,
    width: int = 640,
    height: int = 480,
    azimuth: float = 45.0,
    elevation: float = 30.0,
    background: str = "#F1F5F9",
) -> bytes:
    """将场景自动取景为不透明 RGB PNG，返回 PNG 字节。

    坐标系为 Z 轴向上的右手系。方位角从 +X 朝 +Y 增加，仰角从 XY
    平面朝 +Z 增加，单位均为度；仰角支持 [-90, 90]，包括顶视和底视。
    宽高必须是 [128, 1024] 范围的整数，场景面总数最多 30000。
    三角形屏幕包围盒累计像素工作量最多 100000000，超限时在光栅化前拒绝。
    支持 ColorVisuals 面颜色和顶点颜色（按三角形平均），不支持透明或纹理。
    空场景、退化面、无效参数、工作量超限或当前视角完全不可见时抛出 ValueError。

    本函数执行同步 CPU 工作，异步调用方应使用 asyncio.to_thread。
    """
    width = _validate_dimension(width, "width")
    height = _validate_dimension(height, "height")
    azimuth = _validate_angle(azimuth, "azimuth")
    elevation = _validate_angle(elevation, "elevation")
    if not -90.0 <= elevation <= 90.0:
        raise ValueError("elevation 必须在 [-90, 90] 范围内")
    background_rgb = _parse_background(background)
    triangles, colors, normals = _prepare_scene(parts)
    basis = _camera_basis(azimuth, elevation)
    projected = _project_scene(triangles, basis, width, height)
    shaded_colors = _shade_faces(colors, normals, basis)
    pixels = _rasterize(projected, shaded_colors, width, height, background_rgb)
    with BytesIO() as output:
        Image.fromarray(pixels).save(output, format="PNG")
        return output.getvalue()

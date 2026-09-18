"""参数化几何体及部件变换。"""

from typing import Any, Dict, List, Set

import math
import re

import numpy as np
import trimesh

MAX_PARTS = 64
MAX_FACES = 30_000
MAX_VERTICES = 60_000
MAX_COORDINATE = 1_000_000.0
DEFAULT_COLOR = "#7C9BC0"


def check_keys(value: Dict[str, Any], allowed: Set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是对象")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{label}包含未知参数：{', '.join(sorted(unknown))}")


def number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是有限数字")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label}超出数值范围") from exc
    if not math.isfinite(result) or abs(result) > MAX_COORDINATE or (positive and result <= 0):
        raise ValueError(f"{label}必须{'大于 0 且' if positive else ''}有限，绝对值不超过 {MAX_COORDINATE:g}")
    return result


def vector(value: Any, label: str, *, positive: bool = False) -> List[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label}必须是三个数字组成的数组 [x, y, z]")
    return [number(item, label, positive=positive) for item in value]


def color_rgba(value: Any) -> List[int]:
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("color 必须是 #RRGGBB 格式的不透明颜色")
    return [int(value[index : index + 2], 16) for index in (1, 3, 5)] + [255]


def part_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 64 or value.strip() == "*":
        raise ValueError("部件 name 必须是 1 至 64 个字符，且不能为 *")
    if any(ord(char) < 32 for char in value):
        raise ValueError("部件 name 不能包含控制字符")
    return value.strip()


def transform_matrix(arguments: Dict[str, Any], center: np.ndarray) -> np.ndarray:
    position = vector(arguments.get("position", [0, 0, 0]), "position")
    rotation = vector(arguments.get("rotation", [0, 0, 0]), "rotation")
    scale = vector(arguments.get("scale", [1, 1, 1]), "scale", positive=True)
    matrix = trimesh.transformations.euler_matrix(*np.radians(rotation), axes="sxyz")
    matrix[:3, :3] = matrix[:3, :3] @ np.diag(scale)
    matrix[:3, 3] = center + position - matrix[:3, :3] @ center
    return matrix


def create_part(arguments: Dict[str, Any]) -> trimesh.Trimesh:
    check_keys(
        arguments,
        {
            "name",
            "primitive",
            "size",
            "radius",
            "height",
            "tube_radius",
            "segments",
            "position",
            "rotation",
            "scale",
            "color",
        },
        "部件",
    )
    part_name(arguments.get("name"))
    primitive = arguments.get("primitive")
    dimensions = {
        "box": {"size"},
        "sphere": {"radius"},
        "cylinder": {"radius", "height", "segments"},
        "cone": {"radius", "height", "segments"},
        "torus": {"radius", "tube_radius", "segments"},
    }
    if not isinstance(primitive, str) or primitive not in dimensions:
        raise ValueError("primitive 必须是 box、sphere、cylinder、cone 或 torus")
    extra = (arguments.keys() & {"size", "radius", "height", "tube_radius", "segments"}) - dimensions[primitive]
    if extra:
        raise ValueError(f"{primitive} 不支持参数：{', '.join(sorted(extra))}")
    radius = number(arguments.get("radius", 0.5), "radius", positive=True)
    height = number(arguments.get("height", 1), "height", positive=True)
    segments = arguments.get("segments", 32)
    if isinstance(segments, bool) or not isinstance(segments, int) or not 8 <= segments <= 64:
        raise ValueError("segments 必须是 8 至 64 的整数")
    if primitive == "box":
        mesh = trimesh.creation.box(extents=vector(arguments.get("size", [1, 1, 1]), "size", positive=True))
    elif primitive == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=radius)
    elif primitive == "cylinder":
        mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=segments)
    elif primitive == "cone":
        mesh = trimesh.creation.cone(radius=radius, height=height, sections=segments)
        mesh.apply_translation([0, 0, -height / 2])
    else:
        tube_radius = number(arguments.get("tube_radius", 0.15), "tube_radius", positive=True)
        if tube_radius >= radius:
            raise ValueError("torus 的 tube_radius 必须小于 radius")
        mesh = trimesh.creation.torus(
            major_radius=radius, minor_radius=tube_radius, major_sections=segments, minor_sections=16
        )
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=color_rgba(arguments.get("color", DEFAULT_COLOR)))
    mesh.apply_transform(transform_matrix(arguments, np.zeros(3)))
    return mesh


def validate_parts(parts: Dict[str, trimesh.Trimesh]) -> None:
    if not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError(f"模型必须包含 1 至 {MAX_PARTS} 个部件")
    total_faces = 0
    total_vertices = 0
    for name, mesh in parts.items():
        part_name(name)
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0 or len(mesh.vertices) == 0:
            raise ValueError(f"部件 {name} 必须包含三角网格")
        total_faces += len(mesh.faces)
        total_vertices += len(mesh.vertices)
        if total_faces > MAX_FACES or total_vertices > MAX_VERTICES:
            raise ValueError(f"模型最多包含 {MAX_FACES} 个三角面和 {MAX_VERTICES} 个顶点")
        if not np.isfinite(mesh.vertices).all() or np.abs(mesh.vertices).max() > MAX_COORDINATE:
            raise ValueError(f"部件 {name} 的顶点必须有限且坐标绝对值不超过 {MAX_COORDINATE:g}")
        if mesh.faces.min() < 0 or mesh.faces.max() >= len(mesh.vertices):
            raise ValueError(f"部件 {name} 包含无效的顶点索引")
        if not np.isfinite(mesh.area_faces).all() or np.any(mesh.area_faces <= 0):
            raise ValueError(f"部件 {name} 包含退化三角面")
        colors = mesh.visual.vertex_colors if mesh.visual.kind == "vertex" else mesh.visual.face_colors
        if np.any(colors[:, 3] != 255):
            raise ValueError(f"部件 {name} 包含透明颜色，首版仅支持不透明模型")


def prepare_export_parts(parts: Dict[str, trimesh.Trimesh]) -> Dict[str, trimesh.Trimesh]:
    """保留 GLB 的颜色边界，并在发布前拒绝浮点导出造成的明显几何失真。"""

    exported: Dict[str, trimesh.Trimesh] = {}
    for name, mesh in parts.items():
        # GLB/STL 的顶点是 float32；高偏移、小尺寸组合会把有效三角形量化成线段。
        vertices = mesh.vertices.astype(np.float32).astype(np.float64)
        original = mesh.triangles
        quantized = vertices[mesh.faces]
        edges = original[:, [1, 2, 0]] - original
        min_edge = np.linalg.norm(edges, axis=2).min(axis=1)
        error = np.linalg.norm(quantized - original, axis=2).max(axis=1)
        cross = np.cross(original[:, 1] - original[:, 0], original[:, 2] - original[:, 0])
        quantized_cross = np.cross(quantized[:, 1] - quantized[:, 0], quantized[:, 2] - quantized[:, 0])
        if np.any(error > min_edge * 0.01) or np.any(np.sum(cross * quantized_cross, axis=1) <= 0):
            raise ValueError(f"部件 {name} 的坐标超出 GLB/STL 导出精度，请移近原点或放大过小的细节")
        export_mesh = mesh.copy()
        if mesh.visual.kind == "face":
            colors = mesh.visual.face_colors
            if np.any(colors != colors[0]):
                # GLB 只能保存顶点色，拆开共享顶点才不会把不同面的颜色平均。
                export_mesh.unmerge_vertices()
                export_mesh.visual = trimesh.visual.ColorVisuals(
                    mesh=export_mesh, vertex_colors=np.repeat(colors, 3, axis=0)
                )
        exported[name] = export_mesh
    validate_parts(exported)
    return exported


def apply_operations(parts: Dict[str, trimesh.Trimesh], operations: List[Dict[str, Any]]) -> None:
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        raise ValueError("operations 必须包含 1 至 64 项修改")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("每项修改必须是对象")
        op = operation.get("op")
        allowed = {
            "add": {"op", "part"},
            "remove": {"op", "target"},
            "transform": {"op", "target", "position", "rotation", "scale"},
            "color": {"op", "target", "color"},
        }
        if not isinstance(op, str) or op not in allowed:
            raise ValueError("op 必须是 add、remove、transform 或 color")
        check_keys(operation, allowed[op], "修改操作")
        if op == "add":
            part = operation.get("part")
            if not isinstance(part, dict):
                raise ValueError("add 必须提供 part 对象")
            name = part_name(part.get("name"))
            if name in parts:
                raise ValueError(f"部件名重复：{name}")
            parts[name] = create_part(part)
        else:
            target = operation.get("target")
            if not isinstance(target, str) or (target not in parts and target != "*"):
                raise ValueError(f"找不到部件：{target}，请先 inspect_3d_model 查看部件名")
            if op == "remove":
                if target == "*":
                    raise ValueError("remove 需要指定一个部件，不能使用 *")
                del parts[target]
            else:
                selected = parts if target == "*" else {target: parts[target]}
                if op == "color":
                    rgba = color_rgba(operation.get("color"))
                    for mesh in selected.values():
                        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=rgba)
                else:
                    if not operation.keys() & {"position", "rotation", "scale"}:
                        raise ValueError("transform 至少需要 position、rotation 或 scale")
                    bounds = np.array([mesh.bounds for mesh in selected.values()])
                    center = (bounds[:, 0].min(axis=0) + bounds[:, 1].max(axis=0)) / 2
                    matrix = transform_matrix(operation, center)
                    for mesh in selected.values():
                        mesh.apply_transform(matrix)
        # 每一步控制网格规模，避免多次新增在最终校验前占用大量内存。
        validate_parts(parts)

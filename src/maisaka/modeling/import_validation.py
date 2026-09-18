"""在 GLB 加载器分配数组前校验结构、引用和累计资源规模。"""

from typing import Any, Dict, List, Tuple

import json
import math
import struct

from .geometry import MAX_FACES, MAX_PARTS, MAX_VERTICES

# 场景可以有不带网格的父节点，不能把节点上限直接设为部件上限。
_MAX_NODES = MAX_PARTS * 4
_MAX_RESOURCES = MAX_PARTS * 16
_MAX_DECODED_BYTES = 32 * 1024 * 1024
_COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_TYPE_SHAPES = {
    "SCALAR": (1, 1),
    "VEC2": (1, 2),
    "VEC3": (1, 3),
    "VEC4": (1, 4),
    "MAT2": (2, 2),
    "MAT3": (3, 3),
    "MAT4": (4, 4),
}
_DECODER_EXTENSIONS = {"KHR_draco_mesh_compression", "EXT_meshopt_compression", "EXT_mesh_gpu_instancing"}


def _object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"GLB {label} 必须是对象")
    extensions = value.get("extensions", {})
    if not isinstance(extensions, dict):
        raise ValueError(f"GLB {label}.extensions 必须是对象")
    if _DECODER_EXTENSIONS.intersection(extensions):
        raise ValueError("首版不支持需要扩展解码的 GLB，请导出标准三角网格")
    return value


def _objects(document: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    values = document.get(key, [])
    if not isinstance(values, list):
        raise ValueError(f"GLB {key} 必须是数组")
    return [_object(value, key) for value in values]


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"GLB {label} 必须是不小于 {minimum} 的整数")
    return value


def _index(value: Any, values: List[Any], label: str) -> int:
    index = _integer(value, label)
    if index >= len(values):
        raise ValueError(f"GLB {label} 引用不存在的资源")
    return index


def _check_bytes(size: int) -> None:
    if size > _MAX_DECODED_BYTES:
        raise ValueError("GLB 累计解码数据超过 32 MiB 限制")


def _budget(vertices: int, faces: int, parts: int, size: int) -> None:
    if parts > MAX_PARTS or vertices > MAX_VERTICES or faces > MAX_FACES:
        raise ValueError(f"GLB 累计网格最多包含 {MAX_PARTS} 个部件、{MAX_VERTICES} 个顶点和 {MAX_FACES} 个三角面")
    _check_bytes(size)


def _reject_constant(value: str) -> None:
    raise ValueError(f"GLB JSON 包含非有限数值：{value}")


def _document(data: bytes) -> Tuple[Dict[str, Any], int]:
    if len(data) < 20:
        raise ValueError("GLB 文件头不完整")
    magic, version, length, json_length, chunk_type = struct.unpack_from("<4sIIII", data)
    if magic != b"glTF" or version != 2 or length != len(data) or chunk_type != 0x4E4F534A:
        raise ValueError("仅支持合法的 GLB 2.0 文件")
    if not 0 < json_length <= len(data) - 20 or json_length % 4:
        raise ValueError("GLB 场景数据长度无效")
    try:
        document = json.loads(data[20 : 20 + json_length], parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("GLB 场景 JSON 无效") from exc
    document = _object(document, "场景数据")
    offset = 20 + json_length
    binary_length = 0
    if offset < len(data):
        if len(data) - offset < 8:
            raise ValueError("GLB 二进制块文件头不完整")
        binary_length, binary_type = struct.unpack_from("<II", data, offset)
        if binary_type != 0x004E4942 or binary_length % 4 or offset + 8 + binary_length != len(data):
            raise ValueError("GLB 二进制块类型或长度无效")
    return document, binary_length


def _span(
    views: List[Dict[str, Any]],
    reference: Any,
    offset: Any,
    count: int,
    item_size: int,
    component_size: int,
    label: str,
    *,
    allow_stride: bool = True,
) -> None:
    view = views[_index(reference, views, f"{label}.bufferView")]
    offset = _integer(offset, f"{label}.byteOffset")
    stride = view.get("byteStride", item_size)
    if not allow_stride and "byteStride" in view:
        raise ValueError(f"GLB {label} 不能使用交错缓冲区")
    if stride < item_size or stride % component_size or (view.get("byteOffset", 0) + offset) % component_size:
        raise ValueError(f"GLB {label} 的字节步长或对齐无效")
    if offset + (count - 1) * stride + item_size > view["byteLength"]:
        raise ValueError(f"GLB {label} 的声明数据超出缓冲区范围")


def _validate_accessors(document: Dict[str, Any], binary_length: int) -> List[Dict[str, Any]]:
    buffers = _objects(document, "buffers")
    if any("uri" in buffer for buffer in buffers):
        raise ValueError("GLB 必须自包含，不能引用外部缓冲区")
    if len(buffers) > 1:
        raise ValueError("GLB 只能声明一个自包含二进制缓冲区")
    for buffer in buffers:
        length = _integer(buffer.get("byteLength"), "buffer.byteLength", minimum=1)
        if not length <= binary_length <= length + 3:
            raise ValueError("GLB 缓冲区声明长度与二进制块不匹配")

    views = _objects(document, "bufferViews")
    accessors = _objects(document, "accessors")
    if len(views) > _MAX_RESOURCES or len(accessors) > _MAX_RESOURCES:
        raise ValueError("GLB 缓冲区视图或访问器数量超出限制")
    decoded_bytes = 0
    for view in views:
        buffer = buffers[_index(view.get("buffer"), buffers, "bufferView.buffer")]
        offset = _integer(view.get("byteOffset", 0), "bufferView.byteOffset")
        length = _integer(view.get("byteLength"), "bufferView.byteLength", minimum=1)
        if offset + length > buffer["byteLength"]:
            raise ValueError("GLB bufferView 超出缓冲区范围")
        if "byteStride" in view:
            stride = _integer(view["byteStride"], "bufferView.byteStride", minimum=4)
            if stride > 252 or stride % 4:
                raise ValueError("GLB bufferView.byteStride 必须是 4 至 252 范围内的 4 的倍数")
        # 加载器会为重复引用同一缓冲区的每个视图切片，必须累计而非取最大值。
        decoded_bytes += length
        _check_bytes(decoded_bytes)

    for accessor in accessors:
        count = _integer(accessor.get("count"), "accessor.count", minimum=1)
        if count > MAX_FACES * 3:
            raise ValueError("GLB 数据数量超出限制")
        component = _integer(accessor.get("componentType"), "accessor.componentType")
        kind = accessor.get("type")
        if component not in _COMPONENT_BYTES or not isinstance(kind, str) or kind not in _TYPE_SHAPES:
            raise ValueError("GLB 访问器分量类型或维度无效")
        if "normalized" in accessor and not isinstance(accessor["normalized"], bool):
            raise ValueError("GLB accessor.normalized 必须是布尔值")
        component_size = _COMPONENT_BYTES[component]
        columns, rows = _TYPE_SHAPES[kind]
        # 矩阵每列按 4 字节对齐；普通向量无需列填充。
        item_size = columns * (((rows * component_size + 3) // 4) * 4 if columns > 1 else rows * component_size)
        decoded_bytes += count * item_size
        _check_bytes(decoded_bytes)
        offset = _integer(accessor.get("byteOffset", 0), "accessor.byteOffset")
        if "bufferView" in accessor:
            _span(views, accessor["bufferView"], offset, count, item_size, component_size, "accessor")
        elif offset:
            raise ValueError("GLB 未指定 bufferView 的访问器不能设置 byteOffset")
        if "sparse" in accessor:
            sparse = _object(accessor["sparse"], "accessor.sparse")
            sparse_count = _integer(sparse.get("count"), "sparse.count", minimum=1)
            if sparse_count > count:
                raise ValueError("GLB 稀疏访问器数量超出访问器范围")
            indices = _object(sparse.get("indices"), "sparse.indices")
            values = _object(sparse.get("values"), "sparse.values")
            index_type = _integer(indices.get("componentType"), "sparse.indices.componentType")
            if index_type not in (5121, 5123, 5125):
                raise ValueError("GLB 稀疏索引必须使用无符号整数")
            index_size = _COMPONENT_BYTES[index_type]
            _span(
                views,
                indices.get("bufferView"),
                indices.get("byteOffset", 0),
                sparse_count,
                index_size,
                index_size,
                "sparse.indices",
                allow_stride=False,
            )
            _span(
                views,
                values.get("bufferView"),
                values.get("byteOffset", 0),
                sparse_count,
                item_size,
                component_size,
                "sparse.values",
                allow_stride=False,
            )
    return accessors


def _validate_meshes(document: Dict[str, Any], accessors: List[Dict[str, Any]]) -> List[Tuple[int, int, int, int]]:
    meshes = _objects(document, "meshes")
    if not 1 <= len(meshes) <= MAX_PARTS:
        raise ValueError(f"GLB 必须包含 1 至 {MAX_PARTS} 个网格")
    materials = _objects(document, "materials")
    totals = [0, 0, 0, 0]
    budgets: List[Tuple[int, int, int, int]] = []
    for mesh in meshes:
        primitives = _objects(mesh, "primitives")
        if not primitives:
            raise ValueError("GLB 网格必须包含 primitive")
        vertices = faces = size = 0
        for primitive in primitives:
            mode = _integer(primitive.get("mode", 4), "primitive.mode")
            if mode != 4 or primitive.get("targets"):
                raise ValueError("GLB 仅支持静态三角面，不支持线段、点云或形变目标")
            attributes = _object(primitive.get("attributes"), "primitive.attributes")
            position = accessors[_index(attributes.get("POSITION"), accessors, "POSITION")]
            if position["type"] != "VEC3" or position["componentType"] != 5126:
                raise ValueError("GLB POSITION 必须是浮点 VEC3")
            vertex_count = position["count"]
            for reference in attributes.values():
                attribute = accessors[_index(reference, accessors, "primitive.attributes")]
                if attribute["count"] != vertex_count:
                    raise ValueError("GLB 顶点属性数量与 POSITION 不一致")
                columns, rows = _TYPE_SHAPES[attribute["type"]]
                size += attribute["count"] * columns * rows * _COMPONENT_BYTES[attribute["componentType"]]
            index_count = vertex_count
            if "indices" in primitive:
                indices = accessors[_index(primitive["indices"], accessors, "primitive.indices")]
                if indices["type"] != "SCALAR" or indices["componentType"] not in (5121, 5123, 5125):
                    raise ValueError("GLB 三角面索引必须是无符号整数 SCALAR")
                index_count = indices["count"]
                size += index_count * _COMPONENT_BYTES[indices["componentType"]]
            if index_count % 3:
                raise ValueError("GLB 三角面索引或非索引顶点数量必须是 3 的倍数")
            if "material" in primitive:
                _index(primitive["material"], materials, "primitive.material")
            vertices += vertex_count
            faces += index_count // 3
            _budget(vertices, faces, len(primitives), size)
        budget = (vertices, faces, len(primitives), size)
        budgets.append(budget)
        # 所有声明的网格都会解码，包括未出现在当前场景中的网格。
        totals = [total + count for total, count in zip(totals, budget, strict=True)]
        _budget(*totals)
    return budgets


def _validate_nodes(document: Dict[str, Any], budgets: List[Tuple[int, int, int, int]]) -> None:
    nodes = _objects(document, "nodes")
    if len(nodes) > _MAX_NODES:
        raise ValueError(f"GLB 节点总数不能超过 {_MAX_NODES}")
    children: List[List[int]] = []
    parents = [-1] * len(nodes)
    for index, node in enumerate(nodes):
        if "mesh" in node:
            _index(node["mesh"], budgets, "node.mesh")
        descendants = node.get("children", [])
        if not isinstance(descendants, list):
            raise ValueError("GLB node.children 必须是数组")
        for child in descendants:
            child = _index(child, nodes, "node.children")
            if parents[child] != -1:
                raise ValueError("GLB 节点不能有重复的父节点引用")
            parents[child] = index
        children.append(descendants)
        for key, length in (("matrix", 16), ("translation", 3), ("rotation", 4), ("scale", 3)):
            if key in node:
                values = node[key]
                if not isinstance(values, list) or len(values) != length:
                    raise ValueError(f"GLB node.{key} 必须包含 {length} 个有限数字")
                for value in values:
                    try:
                        finite = (
                            not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                        )
                    except OverflowError:
                        finite = False
                    if not finite:
                        raise ValueError(f"GLB node.{key} 必须包含 {length} 个有限数字")
    # 先检查全部节点的环，再遍历所选场景，避免非法图导致加载器重复展开。
    pending = [index for index, parent in enumerate(parents) if parent == -1]
    visited = 0
    while pending:
        visited += 1
        pending.extend(children[pending.pop()])
    if visited != len(nodes):
        raise ValueError("GLB 节点图不能包含循环")
    scenes = _objects(document, "scenes")
    selected = _index(document.get("scene", 0), scenes, "scene")
    for scene in scenes:
        roots = scene.get("nodes", [])
        if not isinstance(roots, list):
            raise ValueError("GLB scene.nodes 必须是数组")
        seen = set()
        for root in roots:
            root = _index(root, nodes, "scene.nodes")
            if root in seen or parents[root] != -1:
                raise ValueError("GLB 场景根节点不能重复或带有父节点")
            seen.add(root)
    totals = [0, 0, 0, 0]
    pending = list(scenes[selected].get("nodes", []))
    while pending:
        index = pending.pop()
        node = nodes[index]
        if "mesh" in node:
            # 同一网格的每个节点实例都将被复制，不能按唯一访问器去重计数。
            totals = [total + count for total, count in zip(totals, budgets[node["mesh"]], strict=True)]
            _budget(*totals)
        pending.extend(children[index])
    if totals[2] == 0:
        raise ValueError("GLB 当前场景必须包含三角网格")


def validate_glb(data: bytes) -> None:
    """仅检查 GLB 声明及缓冲区范围，不调用加载器或分配声明的几何数组。"""
    document, binary_length = _document(data)
    if document.get("images") or document.get("textures"):
        raise ValueError("首版不支持纹理贴图，请导入纯色 GLB")
    if document.get("skins") or document.get("animations"):
        raise ValueError("首版不支持骨骼或动画，请先导出静态网格")
    if document.get("extensionsRequired"):
        raise ValueError("首版不支持需要扩展解码的 GLB，请导出标准三角网格")
    asset = _object(document.get("asset"), "asset")
    if asset.get("version") != "2.0":
        raise ValueError("仅支持合法的 GLB 2.0 文件")
    accessors = _validate_accessors(document, binary_length)
    budgets = _validate_meshes(document, accessors)
    _validate_nodes(document, budgets)

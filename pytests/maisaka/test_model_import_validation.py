from copy import deepcopy
from typing import Any, Dict, Tuple

import json
import struct

import numpy as np
import pytest
import trimesh

from src.maisaka.modeling.geometry import MAX_FACES, MAX_PARTS, MAX_VERTICES
from src.maisaka.modeling.import_validation import validate_glb


def glb(document: Any, binary: bytes = b"") -> bytes:
    raw = json.dumps(document).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    chunks = struct.pack("<II", len(raw), 0x4E4F534A) + raw
    if binary:
        binary += b"\x00" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def triangle(vertex_count: int = 3) -> Tuple[Dict[str, Any], bytes]:
    binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) * (vertex_count // 3)
    return {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(binary)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": vertex_count, "type": "VEC3"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }, binary


def test_accepts_standard_triangle_and_shared_mesh_instances() -> None:
    document, binary = triangle()
    validate_glb(glb(document, binary))
    document["nodes"] = [{"children": [1, 2]}, {"mesh": 0}, {"mesh": 0, "translation": [3, 0, 0]}]
    validate_glb(glb(document, binary))


def test_accepts_trimesh_export_with_64_parts_parent_and_solid_material() -> None:
    scene = trimesh.Scene()
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(baseColorFactor=[255, 0, 0, 255])
    )
    for index in range(MAX_PARTS):
        transform = np.eye(4)
        transform[0, 3] = index * 2
        scene.add_geometry(mesh.copy(), node_name=f"box_{index}", transform=transform)
    validate_glb(scene.export(file_type="glb"))


def test_accepts_interleaved_attributes_and_multiple_primitives() -> None:
    document, _ = triangle()
    binary = struct.pack("<18f", 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1)
    document["buffers"][0]["byteLength"] = len(binary)
    document["bufferViews"][0].update(byteLength=len(binary), byteStride=24)
    document["accessors"].append({"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": 3, "type": "VEC3"})
    primitive = document["meshes"][0]["primitives"][0]
    primitive["attributes"]["NORMAL"] = 1
    document["meshes"][0]["primitives"].append(deepcopy(primitive))
    validate_glb(glb(document, binary))


def test_shared_accessor_counts_for_each_declared_mesh_even_if_unused() -> None:
    document, binary = triangle(40002)
    document["meshes"].append(deepcopy(document["meshes"][0]))
    # 第二个网格没有节点引用，但加载器仍然解码它。
    with pytest.raises(ValueError, match="累计网格"):
        validate_glb(glb(document, binary))


def test_shared_mesh_counts_for_each_node_instance() -> None:
    document, binary = triangle(40002)
    document["nodes"].append({"mesh": 0})
    document["scenes"][0]["nodes"].append(1)
    with pytest.raises(ValueError, match="累计网格"):
        validate_glb(glb(document, binary))


def test_shared_indices_count_faces_for_each_primitive() -> None:
    document, binary = triangle()
    index_count = 45003
    indices = struct.pack("<H", 0) * index_count
    document["buffers"][0]["byteLength"] += len(indices)
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(indices)})
    document["accessors"].append({"bufferView": 1, "componentType": 5123, "count": index_count, "type": "SCALAR"})
    primitive = document["meshes"][0]["primitives"][0]
    primitive["indices"] = 1
    document["meshes"][0]["primitives"].append(deepcopy(primitive))
    with pytest.raises(ValueError, match="累计网格"):
        validate_glb(glb(document, binary + indices))


def test_small_file_cannot_allocate_many_unreferenced_zero_accessors() -> None:
    document, binary = triangle()
    document["accessors"] += [{"componentType": 5126, "count": MAX_FACES * 3, "type": "MAT4"} for _ in range(6)]
    data = glb(document, binary)
    assert len(data) < 2000
    with pytest.raises(ValueError, match="累计解码数据"):
        validate_glb(data)


def test_overlapping_buffer_views_count_copied_bytes() -> None:
    document, _ = triangle()
    binary = b"\x00" * (1024 * 1024)
    document["buffers"][0]["byteLength"] = len(binary)
    document["bufferViews"] = [{"buffer": 0, "byteLength": len(binary)} for _ in range(33)]
    with pytest.raises(ValueError, match="累计解码数据"):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize("kind", ["meshes", "primitives", "instances", "nodes"])
def test_limits_declared_parts_instances_and_total_nodes(kind: str) -> None:
    document, binary = triangle()
    if kind == "meshes":
        document["meshes"] *= MAX_PARTS + 1
    elif kind == "primitives":
        document["meshes"][0]["primitives"] *= MAX_PARTS + 1
    elif kind == "instances":
        document["nodes"] *= MAX_PARTS + 1
        document["scenes"][0]["nodes"] = list(range(MAX_PARTS + 1))
    else:
        document["nodes"] += [{} for _ in range(MAX_PARTS * 4)]
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize(
    "extra,match",
    [
        ({"images": [{}]}, "贴图"),
        ({"textures": [{}]}, "贴图"),
        ({"skins": [{}]}, "骨骼"),
        ({"animations": [{}]}, "动画"),
        ({"extensionsRequired": ["KHR_draco_mesh_compression"]}, "扩展"),
        ({"buffers": [{"uri": "../external.bin", "byteLength": 36}]}, "自包含"),
        ({"buffers": [{"uri": "data:application/octet-stream;base64,AAAA", "byteLength": 36}]}, "自包含"),
    ],
)
def test_preserves_unsupported_resource_checks(extra: Dict[str, Any], match: str) -> None:
    document, binary = triangle()
    document.update(extra)
    with pytest.raises(ValueError, match=match):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize("change", [{"mode": 5}, {"targets": [{}]}, {"extensions": {"KHR_draco_mesh_compression": {}}}])
def test_rejects_unsupported_primitives_and_optional_decoder_extensions(change: Dict[str, Any]) -> None:
    document, binary = triangle()
    document["meshes"][0]["primitives"][0].update(change)
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize(
    "change",
    [
        {"count": True},
        {"count": -1},
        {"count": MAX_FACES * 3 + 1},
        {"count": 6},
        {"type": "MAT1000000"},
        {"type": []},
        {"type": "MAT4"},
        {"componentType": 9999},
        {"bufferView": -1},
        {"bufferView": 2},
        {"byteOffset": 2},
        {"byteOffset": 36},
    ],
)
def test_rejects_invalid_accessor_dimensions_and_spans(change: Dict[str, Any]) -> None:
    document, binary = triangle()
    document["accessors"][0].update(change)
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize(
    "change",
    [
        {"buffer": True},
        {"buffer": 1},
        {"byteLength": 37},
        {"byteOffset": -1},
        {"byteStride": 0},
        {"byteStride": 8},
        {"byteStride": 256},
    ],
)
def test_rejects_invalid_buffer_views(change: Dict[str, Any]) -> None:
    document, binary = triangle()
    document["bufferViews"][0].update(change)
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize(
    "key,value",
    [
        ("accessors", {}),
        ("bufferViews", [None]),
        ("meshes", [False]),
        ("nodes", "bad"),
        ("scenes", None),
        ("scene", True),
    ],
)
def test_malformed_structures_raise_value_error(key: str, value: Any) -> None:
    document, binary = triangle()
    document[key] = value
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


@pytest.mark.parametrize(
    "nodes",
    [
        [{"mesh": 0, "children": [0]}],
        [{"children": [1, 1]}, {"mesh": 0}],
        [{"mesh": 0, "children": [9]}],
        [{"mesh": -1}],
        [{"mesh": 0, "scale": [1, 2]}],
    ],
)
def test_invalid_node_graphs_raise_value_error(nodes: Any) -> None:
    document, binary = triangle()
    document["nodes"] = nodes
    with pytest.raises(ValueError):
        validate_glb(glb(document, binary))


def test_glb_headers_chunks_and_json_are_validated() -> None:
    document, binary = triangle()
    data = glb(document, binary)
    malformed = [
        b"",
        data[:19],
        data[:-1],
        b"bad!" + data[4:],
        data[:4] + struct.pack("<I", 1) + data[8:],
        glb([]),
        glb(document, binary) + b"junk",
    ]
    for candidate in malformed:
        with pytest.raises(ValueError):
            validate_glb(candidate)


def test_vertex_limit_includes_unindexed_vertices() -> None:
    document, binary = triangle(MAX_VERTICES + 3)
    with pytest.raises(ValueError, match="累计网格"):
        validate_glb(glb(document, binary))

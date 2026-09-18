"""按真实聊天流隔离的三维模型工作区。所有方法均由工具层在线程中执行。"""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import json
import re
import struct

import numpy as np
import trimesh

from src.common.version import PROJECT_ROOT

from .geometry import (
    DEFAULT_COLOR,
    MAX_FACES,
    MAX_PARTS,
    MAX_VERTICES,
    apply_operations,
    color_rgba,
    create_part,
    part_name,
    prepare_export_parts,
    validate_parts,
)
from .import_validation import validate_glb
from .renderer import render_scene

MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_SCENE_BYTES = 32 * 1024 * 1024
IMPORT_FORMATS = {".obj", ".stl", ".ply", ".glb"}


class ModelWorkspace:
    def __init__(self, session_id: str, root: Optional[Path] = None) -> None:
        if not isinstance(session_id, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", session_id) is None:
            raise ValueError("三维建模需要有效的当前聊天流 session_id")
        # 直接使用运行时已经注册的聊天流 ID，不重新计算资源归属。
        self.root = Path(root) if root is not None else PROJECT_ROOT / "data" / "3d_models"
        self.directory = self.root / session_id

    def _path(self, *components: str) -> Path:
        base = self.root.resolve()
        directory = self.directory.resolve()
        if directory != base / self.directory.name:
            raise ValueError("模型工作区不能通过符号链接指向其他聊天流")
        path = directory.joinpath(*components).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("模型文件路径不能超出当前聊天流的工作区")
        return path

    def _model_directory(self, model_id: str) -> Path:
        if not isinstance(model_id, str) or re.fullmatch(r"[0-9a-f]{32}", model_id) is None:
            raise ValueError("model_id 必须使用建模工具返回的 32 位模型编号")
        return self._path("models", model_id)

    @staticmethod
    def _read_bytes(path: Path, limit: int) -> bytes:
        if not path.is_file():
            raise ValueError(f"模型文件不存在：{path.name}")
        if path.stat().st_size > limit:
            raise ValueError(f"模型文件不能超过 {limit // (1024 * 1024)} MiB")
        with path.open("rb") as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"模型文件不能超过 {limit // (1024 * 1024)} MiB")
        return data

    def _display_path(self, path: Path) -> str:
        if path.is_relative_to(PROJECT_ROOT):
            return path.relative_to(PROJECT_ROOT).as_posix()
        return path.as_posix()

    def create_model(self, name: str, parts: List[Dict[str, Any]]) -> Dict[str, Any]:
        name = part_name(name)
        if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
            raise ValueError(f"parts 必须包含 1 至 {MAX_PARTS} 个部件")
        meshes: Dict[str, trimesh.Trimesh] = {}
        for specification in parts:
            if not isinstance(specification, dict):
                raise ValueError("每个部件必须是对象")
            key = part_name(specification.get("name"))
            if key in meshes:
                raise ValueError(f"部件名重复：{key}")
            meshes[key] = create_part(specification)
            validate_parts(meshes)
        return self._save(name, meshes)

    def inspect_model(self, model_id: Optional[str] = None, source_file: Optional[str] = None) -> Dict[str, Any]:
        if (model_id is None) == (source_file is None):
            raise ValueError("model_id 与 source_file 必须且只能提供一个")
        if model_id is not None:
            metadata, parts = self._load(model_id)
            return self._describe(metadata, parts)
        if not isinstance(source_file, str) or not source_file.strip():
            raise ValueError("source_file 必须是相对于当前聊天流 imports 目录的文件名")
        relative = Path(source_file)
        if relative.is_absolute() or relative.drive or ".." in relative.parts or ":" in source_file:
            raise ValueError("source_file 只能使用 imports 目录内的相对路径")
        import_root = self._path("imports")
        source = self._path("imports", source_file)
        if not source.is_relative_to(import_root):
            raise ValueError("导入文件不能通过符号链接离开 imports 目录")
        extension = source.suffix.lower()
        if extension not in IMPORT_FORMATS:
            raise ValueError("仅支持导入 OBJ、STL、PLY 和自包含 GLB 三角网格")
        data = self._read_bytes(source, MAX_IMPORT_BYTES)
        self._check_import(data, extension)
        # BytesIO 和空 resolver 阻止加载器顺着材质引用访问本地文件或网络。
        scene = trimesh.load_scene(
            BytesIO(data),
            file_type=extension[1:],
            resolver={},
            allow_remote=False,
            process=False,
            skip_materials=extension == ".obj",
            group_material=False,
            split_objects=True,
        )
        parts: Dict[str, trimesh.Trimesh] = {}
        if not 1 <= len(scene.graph.nodes_geometry) <= MAX_PARTS:
            raise ValueError(f"导入模型必须包含 1 至 {MAX_PARTS} 个网格部件")
        for index, node in enumerate(scene.graph.nodes_geometry, 1):
            transform, geometry_name = scene.graph[node]
            mesh = scene.geometry[geometry_name].copy()
            if not isinstance(mesh, trimesh.Trimesh):
                raise ValueError("首版仅支持三角网格，不支持点云、曲线和骨骼")
            mesh.apply_transform(transform)
            if isinstance(mesh.visual, trimesh.visual.TextureVisuals):
                mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=mesh.visual.material.main_color)
            elif not mesh.visual.defined:
                mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=color_rgba(DEFAULT_COLOR))
            # 导入名称保留可读部分，索引保证场景实例之间不会重名。
            label = re.sub(r"[\x00-\x1f]", "", str(node)).strip()[:48] or "mesh"
            parts[f"part_{index}_{label}"] = mesh
            validate_parts(parts)
        return self._save(source.stem[:64], parts, source_file=relative.as_posix())

    @staticmethod
    def _check_import(data: bytes, extension: str) -> None:
        if extension == ".obj":
            text = data.decode("utf-8-sig")
            vertices = faces = 0
            for line in text.splitlines():
                words = line.split()
                if not words:
                    continue
                if words[0] in {"mtllib", "usemtl"}:
                    raise ValueError("OBJ 首版仅导入几何与顶点色；请移除材质引用，或转为无贴图 GLB")
                vertices += words[0] == "v"
                if words[0] == "f":
                    faces += max(len(words) - 3, 0)
            if vertices > MAX_VERTICES or faces > MAX_FACES:
                raise ValueError(f"导入模型最多包含 {MAX_FACES} 个三角面和 {MAX_VERTICES} 个顶点")
        elif extension == ".stl":
            if len(data) >= 84 and 84 + struct.unpack_from("<I", data, 80)[0] * 50 == len(data):
                face_count = struct.unpack_from("<I", data, 80)[0]
            else:
                face_count = sum(line.lstrip().startswith(b"facet ") for line in data.splitlines())
            if face_count > MAX_FACES:
                raise ValueError(f"STL 最多包含 {MAX_FACES} 个三角面")
        elif extension == ".ply":
            header_end = data.find(b"end_header")
            if header_end < 0 or header_end > 64 * 1024:
                raise ValueError("PLY 文件头无效")
            header = data[:header_end].decode("ascii")
            for line in header.splitlines():
                words = line.split()
                if len(words) == 3 and words[:2] in (["element", "vertex"], ["element", "face"]):
                    limit = MAX_VERTICES if words[1] == "vertex" else MAX_FACES
                    if not 0 <= int(words[2]) <= limit:
                        raise ValueError(f"PLY {words[1]} 数量超出限制 {limit}")
        elif extension == ".glb":
            validate_glb(data)

    def edit_model(self, model_id: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        metadata, parts = self._load(model_id)
        apply_operations(parts, operations)
        return self._save(metadata["name"], parts, parent_model_id=model_id)

    def render_model(
        self,
        model_id: str,
        width: int = 640,
        height: int = 480,
        azimuth: float = 45.0,
        elevation: float = 30.0,
        background: str = "#F1F5F9",
    ) -> Tuple[Dict[str, Any], bytes]:
        metadata, parts = self._load(model_id)
        png = render_scene(
            parts, width=width, height=height, azimuth=azimuth, elevation=elevation, background=background
        )
        output = self._path("renders", f"{model_id}_{uuid4().hex}.png")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(png)
        return {
            "model_id": model_id,
            "name": metadata["name"],
            "render_file": self._display_path(output),
            "width": width,
            "height": height,
            "azimuth": azimuth,
            "elevation": elevation,
        }, png

    def _save(
        self,
        name: str,
        parts: Dict[str, trimesh.Trimesh],
        *,
        parent_model_id: Optional[str] = None,
        source_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        validate_parts(parts)
        model_id = uuid4().hex
        metadata: Dict[str, Any] = {
            "version": 1,
            "model_id": model_id,
            "name": name,
            "parent_model_id": parent_model_id,
            "source_file": source_file,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meshes = []
        scene = trimesh.Scene()
        export_parts = prepare_export_parts(parts)
        for key, mesh in parts.items():
            color_kind = "vertex" if mesh.visual.kind == "vertex" else "face"
            colors = mesh.visual.vertex_colors if color_kind == "vertex" else mesh.visual.face_colors
            meshes.append(
                {
                    "name": key,
                    "vertices": mesh.vertices.tolist(),
                    "faces": mesh.faces.tolist(),
                    "color_kind": color_kind,
                    "colors": colors.tolist(),
                }
            )
            scene.add_geometry(export_parts[key], node_name=key, geom_name=key)
        document = json.dumps({"metadata": metadata, "parts": meshes}, ensure_ascii=False, allow_nan=False)
        if len(document.encode("utf-8")) > MAX_SCENE_BYTES:
            raise ValueError("模型场景文件过大")
        model_directory = self._model_directory(model_id)
        model_directory.parent.mkdir(parents=True, exist_ok=True)
        # 同目录临时目录写全后再发布；失败时不会留下看似有效的模型版本。
        with TemporaryDirectory(prefix=".building-", dir=model_directory.parent) as temporary:
            staging = Path(temporary) / model_id
            staging.mkdir()
            (staging / "scene.json").write_text(document, encoding="utf-8")
            (staging / "model.glb").write_bytes(scene.export(file_type="glb"))
            (staging / "model.stl").write_bytes(scene.to_mesh().export(file_type="stl"))
            staging.rename(model_directory)
        return self._describe(metadata, parts)

    def _load(self, model_id: str) -> Tuple[Dict[str, Any], Dict[str, trimesh.Trimesh]]:
        self._model_directory(model_id)
        scene_path = self._path("models", model_id, "scene.json")
        document = json.loads(self._read_bytes(scene_path, MAX_SCENE_BYTES))
        if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
            raise ValueError("模型场景文件结构无效")
        metadata = document["metadata"]
        if metadata.get("version") != 1 or metadata.get("model_id") != model_id:
            raise ValueError("模型场景版本或编号不匹配")
        part_name(metadata.get("name"))
        entries = document.get("parts")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PARTS:
            raise ValueError("模型场景部件列表无效")
        parts: Dict[str, trimesh.Trimesh] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("模型场景部件结构无效")
            key = part_name(entry.get("name"))
            if key in parts:
                raise ValueError(f"模型场景中部件名重复：{key}")
            vertices = np.asarray(entry.get("vertices"), dtype=np.float64)
            faces = np.asarray(entry.get("faces"))
            color_kind = entry.get("color_kind")
            colors = np.asarray(entry.get("colors"))
            if vertices.ndim != 2 or vertices.shape[1] != 3:
                raise ValueError(f"部件 {key} 的顶点结构无效")
            if faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer):
                raise ValueError(f"部件 {key} 的三角面结构无效")
            if color_kind not in {"vertex", "face"}:
                raise ValueError(f"部件 {key} 的颜色类型无效")
            expected_colors = len(vertices) if color_kind == "vertex" else len(faces)
            if colors.shape != (expected_colors, 4) or not np.issubdtype(colors.dtype, np.integer):
                raise ValueError(f"部件 {key} 的颜色结构无效")
            if np.any(colors < 0) or np.any(colors > 255):
                raise ValueError(f"部件 {key} 的颜色超出范围")
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            if color_kind == "vertex":
                mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colors)
            else:
                mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, face_colors=colors)
            parts[key] = mesh
        validate_parts(parts)
        return metadata, parts

    def _describe(self, metadata: Dict[str, Any], parts: Dict[str, trimesh.Trimesh]) -> Dict[str, Any]:
        directory = self._model_directory(metadata["model_id"])
        bounds = np.array([mesh.bounds for mesh in parts.values()])
        result = dict(metadata)
        result.update(
            {
                "coordinate_system": "右手坐标系，Z 轴向上；长度单位自定，旋转单位为度",
                "bounds": [bounds[:, 0].min(axis=0).tolist(), bounds[:, 1].max(axis=0).tolist()],
                "part_count": len(parts),
                "face_count": sum(len(mesh.faces) for mesh in parts.values()),
                "vertex_count": sum(len(mesh.vertices) for mesh in parts.values()),
                "parts": [
                    {
                        "name": key,
                        "bounds": mesh.bounds.tolist(),
                        "size": mesh.extents.tolist(),
                        "faces": len(mesh.faces),
                        "vertices": len(mesh.vertices),
                        "watertight": bool(mesh.is_watertight),
                    }
                    for key, mesh in parts.items()
                ],
                "files": {
                    extension: self._display_path(directory / f"model.{extension}") for extension in ("glb", "stl")
                },
                "import_directory": self._display_path(self._path("imports")),
                "notes": "GLB 保留部件与颜色，STL 仅保留几何。部件为组合网格，未经布尔融合或打印可用性校验。",
            }
        )
        return result

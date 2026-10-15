from pathlib import Path

from src.common.paths import get_instance_root


PROJECT_ROOT = get_instance_root()


class StoredImagePathError(ValueError):
    """图片存储路径不在项目目录内。"""


def resolve_stored_image_path(stored_path: str | Path) -> Path:
    """将数据库中的图片路径解析为项目内的真实路径。"""
    if not str(stored_path).strip():
        raise StoredImagePathError("图片路径不能为空")
    path = Path(stored_path)
    resolved_path = path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    try:
        resolved_path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise StoredImagePathError(f"图片路径不在项目目录内: {stored_path}") from exc
    return resolved_path


def serialize_stored_image_path(file_path: str | Path) -> str:
    """将项目内图片路径序列化为相对项目根目录的存储路径。"""
    if not str(file_path).strip():
        raise StoredImagePathError("图片路径不能为空")
    resolved_path = Path(file_path).resolve()
    try:
        return resolved_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise StoredImagePathError(f"图片路径不在项目目录内: {file_path}") from exc


def stored_image_paths_equal(left: str | Path, right: str | Path) -> bool:
    """按项目内真实路径语义比较两个图片存储路径。"""
    return resolve_stored_image_path(left) == resolve_stored_image_path(right)

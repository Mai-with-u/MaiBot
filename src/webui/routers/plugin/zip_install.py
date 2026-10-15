"""ZIP 插件安装：隔离解压并校验，通过后才发布到 plugins。"""

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO, Dict, List, Tuple
from zipfile import BadZipFile, ZipFile, ZipInfo

from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile
import shutil
import stat

from src.plugin_runtime.runner.manifest_validator import ManifestValidator

from .management import _reserve_plugin_operation
from .support import (
    get_plugin_candidate_paths,
    get_plugins_dir,
    require_plugin_token,
    resolve_installed_plugin_path,
)

router = APIRouter()
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
MAX_ZIP_ENTRIES = 10000
_WINDOWS_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)
}


def _archive_entries(archive: ZipFile) -> List[Tuple[ZipInfo, PurePosixPath]]:
    """在写入任何文件前检查完整目录表，拒绝路径别名和特殊文件。"""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES or sum(info.file_size for info in infos) > MAX_EXTRACTED_BYTES:
        raise HTTPException(status_code=400, detail="ZIP 解压后超过 300 MB 或文件数量超过 10000")
    entries: List[Tuple[ZipInfo, PurePosixPath]] = []
    seen = set()
    for info in infos:
        name = info.orig_filename
        path = PurePosixPath(name)
        parts = name.rstrip("/").split("/")
        mode = stat.S_IFMT(info.external_attr >> 16)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(
                part in {"", ".", ".."}
                or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.split(".")[0].casefold() in _WINDOWS_RESERVED_NAMES
                or part.casefold() in {".git", ".maibot-release.json"}
                for part in parts
            )
            or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
            or bool(info.flag_bits & 1)
        ):
            raise HTTPException(status_code=400, detail=f"ZIP 包含非法路径、链接或加密文件：{name}")
        key = str(path).casefold()
        if key in seen:
            raise HTTPException(status_code=400, detail=f"ZIP 包含重复路径：{name}")
        seen.add(key)
        entries.append((info, path))
    return entries


def install_zip_archive(source: BinaryIO) -> Dict[str, Any]:
    source.seek(0, 2)
    if source.tell() > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="ZIP 文件不能超过 100 MB")
    source.seek(0)
    plugins_root = get_plugins_dir()
    plugins_root.mkdir(parents=True, exist_ok=True)
    # 临时目录与目标同处 plugins，确保最终重命名不会跨文件系统；失败自动清理。
    with TemporaryDirectory(prefix=".zip-install-", dir=plugins_root) as temporary:
        staging = Path(temporary)
        try:
            with ZipFile(source) as archive:
                entries = _archive_entries(archive)
                manifests = [path for info, path in entries if not info.is_dir() and path.name == "_manifest.json"]
                if PurePosixPath("_manifest.json") in manifests:
                    root = PurePosixPath(".")
                else:
                    candidates = [path.parent for path in manifests if len(path.parts) == 2]
                    if len(candidates) != 1:
                        raise HTTPException(
                            status_code=400, detail="ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json"
                        )
                    root = candidates[0]
                    if any(not path.is_relative_to(root) for _, path in entries):
                        raise HTTPException(status_code=400, detail="ZIP 必须只包含一个插件目录")
                for info, path in entries:
                    destination = staging.joinpath(*path.parts)
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as uploaded, destination.open("xb") as output:
                            shutil.copyfileobj(uploaded, output, length=1024 * 1024)
        except (BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as exc:
            raise HTTPException(status_code=400, detail=f"ZIP 解压失败：{exc}") from exc

        candidate = staging.joinpath(*root.parts)
        validator = ManifestValidator(log_errors=False)
        manifest = validator.load_from_plugin_path(candidate)
        if manifest is None:
            raise HTTPException(status_code=400, detail=f"插件校验失败：{'；'.join(validator.errors)}")
        with _reserve_plugin_operation(manifest.id, "install"):
            target, old_target = get_plugin_candidate_paths(manifest.id)
            if target.exists() or old_target.exists() or resolve_installed_plugin_path(manifest.id) is not None:
                raise HTTPException(status_code=409, detail=f"插件 {manifest.id} 已安装，请先卸载后再从 ZIP 安装")
            candidate.rename(target)
        return {"success": True, "plugin_id": manifest.id, "message": f"{manifest.name} 安装成功，请重启麦麦使其生效"}


@router.post("/install-zip")
def install_plugin_zip(file: UploadFile = File(...), maibot_session: str | None = Cookie(default=None)):
    """同步端点在线程池读取上传文件并解压，避免阻塞 WebUI 事件循环。"""
    require_plugin_token(maibot_session)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请选择 ZIP 文件")
    try:
        return install_zip_archive(file.file)
    finally:
        file.file.close()

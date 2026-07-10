"""MaiBot 数据导入导出路由。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import json
import shutil
import tempfile
import uuid
import zipfile

from src.common.logger import get_logger
from src.config.config import MMC_VERSION
from src.webui.dependencies import require_auth

logger = get_logger("webui_data_transfer")

router = APIRouter(prefix="/data-transfer", tags=["data-transfer"], dependencies=[Depends(require_auth)])

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXPORT_DIRS: dict[str, Path] = {
    "config": _PROJECT_ROOT / "config",
    "data": _PROJECT_ROOT / "data",
    "plugins": _PROJECT_ROOT / "plugins",
    "logs": _PROJECT_ROOT / "logs",
}
_REQUIRED_EXPORT_PARTS = ("config", "data")
_OPTIONAL_EXPORT_PARTS = ("plugins", "logs")
_ALLOWED_IMPORT_PARTS = set(_EXPORT_DIRS)
_TRANSFER_TEMP_DIR = Path(tempfile.gettempdir()) / "maibot_webui_transfer"
_CHUNK_SIZE = 1024 * 1024

TransferJobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class _ExportCancelled(Exception):
    """导出任务已被用户取消。"""


class DataExportRequest(BaseModel):
    """数据导出请求。"""

    include_plugins: bool = False
    include_logs: bool = False


class DataImportResponse(BaseModel):
    """数据导入任务创建响应。"""

    job_id: str
    status: TransferJobStatus


class DataTransferJobResponse(BaseModel):
    """数据导入导出任务状态。"""

    job_id: str
    kind: Literal["export", "import"]
    status: TransferJobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    total_files: int = 0
    processed_files: int = 0
    total_bytes: int = 0
    processed_bytes: int = 0
    filename: str | None = None
    download_url: str | None = None
    manifest: dict[str, Any] | None = None
    error: str | None = None


class _TransferJob:
    """进度任务的内部可变状态。"""

    def __init__(self, job_id: str, kind: Literal["export", "import"]) -> None:
        self.job_id = job_id
        self.kind = kind
        self.status: TransferJobStatus = "pending"
        self.progress = 0
        self.message = "等待处理"
        self.total_files = 0
        self.processed_files = 0
        self.total_bytes = 0
        self.processed_bytes = 0
        self.filename: str | None = None
        self.file_path: Path | None = None
        self.manifest: dict[str, Any] | None = None
        self.error: str | None = None
        self.cancel_requested = False

    def to_response(self) -> DataTransferJobResponse:
        download_url = None
        if self.kind == "export" and self.status == "completed":
            download_url = f"/api/webui/data-transfer/export/{self.job_id}/download"

        return DataTransferJobResponse(
            job_id=self.job_id,
            kind=self.kind,
            status=self.status,
            progress=self.progress,
            message=self.message,
            total_files=self.total_files,
            processed_files=self.processed_files,
            total_bytes=self.total_bytes,
            processed_bytes=self.processed_bytes,
            filename=self.filename,
            download_url=download_url,
            manifest=self.manifest,
            error=self.error,
        )


_jobs: dict[str, _TransferJob] = {}


def _new_job(kind: Literal["export", "import"]) -> _TransferJob:
    _TRANSFER_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    job = _TransferJob(job_id=uuid.uuid4().hex, kind=kind)
    _jobs[job.job_id] = job
    return job


def _get_job_or_404(job_id: str, kind: Literal["export", "import"] | None = None) -> _TransferJob:
    job = _jobs.get(job_id)
    if job is None or (kind is not None and job.kind != kind):
        raise HTTPException(status_code=404, detail="未找到指定的数据迁移任务")
    return job


def _mark_export_cancelled(job: _TransferJob) -> None:
    job.status = "cancelled"
    job.progress = 0
    job.message = "导出已取消"
    job.error = None


def _raise_if_cancelled(job: _TransferJob) -> None:
    if not job.cancel_requested:
        return
    _mark_export_cancelled(job)
    raise _ExportCancelled


def _iter_export_files(root: Path, archive_root: str, job: _TransferJob) -> list[tuple[Path, str, int]]:
    if not root.exists():
        return []
    if root.is_file():
        _raise_if_cancelled(job)
        stat = root.stat()
        return [(root, f"{archive_root}/{root.name}", stat.st_size)]

    files: list[tuple[Path, str, int]] = []
    root_resolved = root.resolve()
    for file_path in root.rglob("*"):
        _raise_if_cancelled(job)
        if not file_path.is_file() or file_path.is_symlink():
            continue
        try:
            resolved_path = file_path.resolve()
            resolved_path.relative_to(root_resolved)
            relative_path = resolved_path.relative_to(root_resolved).as_posix()
            files.append((resolved_path, f"{archive_root}/{relative_path}", resolved_path.stat().st_size))
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"跳过无法导出的文件: {file_path}, error={exc}")
    return files


def _build_manifest(parts: list[str], files: list[tuple[Path, str, int]]) -> dict[str, Any]:
    part_stats: dict[str, dict[str, int]] = {
        part: {"file_count": 0, "total_bytes": 0}
        for part in parts
    }
    for _, archive_name, file_size in files:
        part = archive_name.split("/", 1)[0]
        if part in part_stats:
            part_stats[part]["file_count"] += 1
            part_stats[part]["total_bytes"] += file_size

    return {
        "format": "maibot-data-archive",
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "maibot_version": MMC_VERSION,
        "included": parts,
        "parts": part_stats,
    }


def _update_progress(job: _TransferJob) -> None:
    if job.status == "cancelled":
        return
    if job.total_bytes > 0:
        job.progress = min(99, int(job.processed_bytes * 100 / job.total_bytes))
    elif job.total_files > 0:
        job.progress = min(99, int(job.processed_files * 100 / job.total_files))
    else:
        job.progress = 99


def _write_archive_file(archive: zipfile.ZipFile, file_path: Path, archive_name: str, job: _TransferJob) -> int:
    written_bytes = 0
    with file_path.open("rb") as source_file, archive.open(archive_name, "w") as target_file:
        while True:
            _raise_if_cancelled(job)
            chunk = source_file.read(_CHUNK_SIZE)
            if not chunk:
                break
            target_file.write(chunk)
            written_bytes += len(chunk)
            job.processed_bytes += len(chunk)
            _update_progress(job)
    return written_bytes


def _run_export_job(job_id: str, request: DataExportRequest) -> None:
    job = _get_job_or_404(job_id, "export")
    archive_path: Path | None = None
    try:
        _raise_if_cancelled(job)
        job.status = "running"
        job.message = "正在扫描需要导出的文件"

        parts = list(_REQUIRED_EXPORT_PARTS)
        if request.include_plugins:
            parts.append("plugins")
        if request.include_logs:
            parts.append("logs")

        files: list[tuple[Path, str, int]] = []
        for part in parts:
            _raise_if_cancelled(job)
            files.extend(_iter_export_files(_EXPORT_DIRS[part], part, job))

        job.total_files = len(files)
        job.total_bytes = sum(file_size for _, _, file_size in files)
        manifest = _build_manifest(parts, files)
        job.manifest = manifest

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"maibot-data-{timestamp}.zip"
        archive_path = _TRANSFER_TEMP_DIR / f"{job.job_id}.zip"
        job.filename = filename
        job.file_path = archive_path

        job.message = "正在写入压缩包"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for file_path, archive_name, _ in files:
                _raise_if_cancelled(job)
                _write_archive_file(archive, file_path, archive_name, job)
                job.processed_files += 1
                _update_progress(job)

        _raise_if_cancelled(job)
        job.status = "completed"
        job.progress = 100
        job.message = "导出完成"
    except _ExportCancelled:
        if archive_path is not None:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"清理已取消导出文件失败: {archive_path}, error={exc}")
    except HTTPException as exc:
        logger.warning(f"导出 MaiBot 数据失败: {exc.detail}")
        job.status = "failed"
        job.error = str(exc.detail)
        job.message = "导出失败"
    except Exception as exc:
        logger.exception(f"导出 MaiBot 数据失败: {exc}")
        job.status = "failed"
        job.error = str(exc)
        job.message = "导出失败"


def _safe_zip_member_path(member_name: str) -> tuple[str, str]:
    normalized_name = member_name.replace("\\", "/").lstrip("/")
    if not normalized_name or normalized_name.endswith("/"):
        return "", ""
    path = Path(normalized_name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail=f"压缩包包含非法路径: {member_name}")
    top_level = path.parts[0]
    if normalized_name != "manifest.json" and top_level not in _ALLOWED_IMPORT_PARTS:
        raise HTTPException(status_code=400, detail=f"压缩包包含不支持的顶层目录: {top_level}")
    return top_level, "/".join(path.parts[1:])


def _load_archive_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        with archive.open("manifest.json") as manifest_file:
            manifest = json.loads(manifest_file.read().decode("utf-8"))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="压缩包缺少 manifest.json") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="manifest.json 不是合法 JSON") from exc

    if manifest.get("format") != "maibot-data-archive" or manifest.get("format_version") != 1:
        raise HTTPException(status_code=400, detail="不支持的数据包格式")
    return manifest


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    for member in archive.infolist():
        _safe_zip_member_path(member.filename)
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise HTTPException(status_code=400, detail=f"压缩包不允许包含符号链接: {member.filename}")


def _resolve_import_target(top_level: str, relative_path: str) -> Path:
    root = _EXPORT_DIRS[top_level].resolve()
    target_path = (root / relative_path).resolve()
    try:
        target_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"压缩包包含非法路径: {top_level}/{relative_path}") from exc
    return target_path


def _run_import_job(job_id: str, archive_path: Path, enabled_parts: set[str]) -> None:
    job = _get_job_or_404(job_id, "import")
    try:
        job.status = "running"
        job.message = "正在校验压缩包"

        with zipfile.ZipFile(archive_path, "r", allowZip64=True) as archive:
            manifest = _load_archive_manifest(archive)
            _validate_archive_members(archive)
            job.manifest = manifest

            entries = []
            for member in archive.infolist():
                top_level, relative_path = _safe_zip_member_path(member.filename)
                if not top_level or not relative_path or member.is_dir() or top_level not in enabled_parts:
                    continue
                entries.append((member, top_level, relative_path))

            job.total_files = len(entries)
            job.total_bytes = sum(max(0, member.file_size) for member, _, _ in entries)
            job.message = "正在导入文件"

            for member, top_level, relative_path in entries:
                target_path = _resolve_import_target(top_level, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, target_path.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file, length=_CHUNK_SIZE)
                job.processed_files += 1
                job.processed_bytes += max(0, member.file_size)
                _update_progress(job)

        job.status = "completed"
        job.progress = 100
        job.message = "导入完成"
    except HTTPException as exc:
        logger.warning(f"导入 MaiBot 数据包失败: {exc.detail}")
        job.status = "failed"
        job.error = str(exc.detail)
        job.message = "导入失败"
    except zipfile.BadZipFile as exc:
        logger.warning(f"导入 MaiBot 数据包失败，文件不是合法 zip: {exc}")
        job.status = "failed"
        job.error = "上传文件不是合法的 zip 压缩包"
        job.message = "导入失败"
    except Exception as exc:
        logger.exception(f"导入 MaiBot 数据包失败: {exc}")
        job.status = "failed"
        job.error = str(exc)
        job.message = "导入失败"
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"清理导入临时文件失败: {archive_path}, error={exc}")


async def _save_upload_file(file: UploadFile, target_path: Path) -> None:
    with target_path.open("wb") as output_file:
        while True:
            chunk = await file.read(_CHUNK_SIZE)
            if not chunk:
                break
            output_file.write(chunk)


@router.post("/export", response_model=DataTransferJobResponse)
async def create_data_export(request: DataExportRequest, background_tasks: BackgroundTasks) -> DataTransferJobResponse:
    """创建 MaiBot 数据导出任务。"""
    job = _new_job("export")
    background_tasks.add_task(_run_export_job, job.job_id, request)
    return job.to_response()


@router.get("/jobs/{job_id}", response_model=DataTransferJobResponse)
async def get_data_transfer_job(job_id: str) -> DataTransferJobResponse:
    """查询导入或导出任务进度。"""
    return _get_job_or_404(job_id).to_response()


@router.get("/export/{job_id}/download", response_model=None)
async def download_data_export(job_id: str) -> FileResponse:
    """下载已完成的数据导出压缩包。"""
    job = _get_job_or_404(job_id, "export")
    if job.status != "completed" or job.file_path is None or not job.file_path.is_file():
        raise HTTPException(status_code=400, detail="导出任务尚未完成或文件已失效")
    return FileResponse(
        job.file_path,
        media_type="application/zip",
        filename=job.filename or "maibot-data.zip",
    )


@router.post("/export/{job_id}/cancel", response_model=DataTransferJobResponse)
async def cancel_data_export(job_id: str) -> DataTransferJobResponse:
    """取消正在执行的数据导出任务。"""
    job = _get_job_or_404(job_id, "export")
    if job.status in {"completed", "failed", "cancelled"}:
        return job.to_response()
    job.cancel_requested = True
    _mark_export_cancelled(job)
    return job.to_response()


@router.post("/import", response_model=DataImportResponse)
async def create_data_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    import_config: bool = Form(True),
    import_data: bool = Form(True),
    import_plugins: bool = Form(False),
    import_logs: bool = Form(False),
) -> DataImportResponse:
    """上传 MaiBot 数据压缩包并创建导入任务。"""
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 格式的数据包")

    enabled_parts = {
        part
        for part, enabled in {
            "config": import_config,
            "data": import_data,
            "plugins": import_plugins,
            "logs": import_logs,
        }.items()
        if enabled
    }
    if not enabled_parts:
        raise HTTPException(status_code=400, detail="请至少选择一个导入范围")

    job = _new_job("import")
    upload_path = _TRANSFER_TEMP_DIR / f"{job.job_id}-upload.zip"
    await _save_upload_file(file, upload_path)
    await file.close()

    background_tasks.add_task(_run_import_job, job.job_id, upload_path, enabled_parts)
    return DataImportResponse(job_id=job.job_id, status=job.status)


@router.delete("/jobs/{job_id}", response_model=dict[str, bool])
async def delete_data_transfer_job(job_id: str) -> dict[str, bool]:
    """清理任务记录和已生成的临时文件。"""
    job = _get_job_or_404(job_id)
    if job.file_path is not None:
        try:
            job.file_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"清理数据迁移任务文件失败: {job.file_path}, error={exc}")
    _jobs.pop(job_id, None)
    return {"success": True}

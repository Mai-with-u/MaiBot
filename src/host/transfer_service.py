"""MaiBot 数据导入导出路由。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.types import Receive, Scope, Send

import asyncio
import anyio
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile

from src.common.logger import get_logger

logger = get_logger("webui_data_transfer")


_REQUIRED_EXPORT_PARTS = ("config", "data")
_OPTIONAL_EXPORT_PARTS = ("plugins", "logs")
_ALLOWED_IMPORT_PARTS = {"config", "data", "plugins", "logs"}
_EXCLUDED_EXPORT_PATHS = {
    "data/.a_memorix_runtime_writer.lock",
    "data/a-memorix/.a_memorix_runtime_writer.lock",
}
_EXCLUDED_EXPORT_DIR_PREFIXES = ("data/prompt_imgs/",)
_CHUNK_SIZE = 1024 * 1024
_EXPORT_RETENTION_SECONDS = 24 * 60 * 60
_TEMP_ENTRY_PATTERN = re.compile(r"(?P<job_id>[0-9a-f]{32})(?:\.zip|-upload\.zip|-snapshots-[a-z0-9_]+)")

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
    completed_at: float | None = None
    expires_at: float | None = None
    archive_bytes: int = 0


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
        self.worker_active = False
        self.active_downloads = 0
        self.expires_at: float | None = None
        self.completed_at: float | None = None
        self.archive_bytes = 0

    def to_response(self) -> DataTransferJobResponse:
        download_url = None
        if self.kind == "export" and self.status == "completed" and self.file_path is not None:
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
            completed_at=self.completed_at,
            expires_at=self.expires_at,
            archive_bytes=self.archive_bytes,
        )


class _ExportDownloadResponse(FileResponse):
    """下载完成或连接中断时都释放清理保护。"""

    def __init__(self, path: Path, *, job: _TransferJob, on_finish: Any, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self.job = job
        self.on_finish = on_finish

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            # FileResponse 不主动消费 http.disconnect；部分 ASGI 服务器在连接断开后
            # 不会立刻让 send 报错，因此必须监听断开并取消仍在读取大文件的响应。
            async with anyio.create_task_group() as tasks:
                async def watch_disconnect() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            tasks.cancel_scope.cancel()
                            return

                tasks.start_soon(watch_disconnect)
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            await asyncio.shield(asyncio.to_thread(self.on_finish, self.job))


class TransferService:
    """每个 Bot 独立的传输任务、历史和目录；由宿主控制生命周期。"""

    def __init__(self, root: Path, transfer_dir: Path, version: str, run_operation: Any = None, prepare_export: Any = None, apply_import: Any = None) -> None:
        self._PROJECT_ROOT = root
        self._EXPORT_DIRS = {name: root / name for name in _ALLOWED_IMPORT_PARTS}
        self._TRANSFER_TEMP_DIR = transfer_dir
        self._temp_cleanup_lock = threading.RLock()
        self._jobs: Dict[str, _TransferJob] = {}
        self.version = version
        self.run_operation = run_operation
        self.prepare_export = prepare_export
        self.apply_import = apply_import
        self.router = APIRouter(prefix="/data-transfer", tags=["data-transfer"])
        self.router.add_api_route("/export", response_model=DataTransferJobResponse, endpoint=self.create_data_export, methods=["POST"])
        self.router.add_api_route("/jobs/{job_id}", response_model=DataTransferJobResponse, endpoint=self.get_data_transfer_job, methods=["GET"])
        self.router.add_api_route("/exports", response_model=List[DataTransferJobResponse], endpoint=self.list_data_exports, methods=["GET"])
        self.router.add_api_route("/export/{job_id}/download", response_model=None, endpoint=self.download_data_export, methods=["GET"])
        self.router.add_api_route("/export/{job_id}/cancel", response_model=DataTransferJobResponse, endpoint=self.cancel_data_export, methods=["POST"])
        self.router.add_api_route("/import", response_model=DataImportResponse, endpoint=self.create_data_import, methods=["POST"])
        self.router.add_api_route("/jobs/{job_id}", response_model=dict[str, bool], endpoint=self.delete_data_transfer_job, methods=["DELETE"])

    async def _execute(self, operation: Any, job_id: str, *args: Any) -> None:
        job = self._get_job_or_404(job_id)
        job.worker_active = True
        try:
            if self.run_operation is None:
                await asyncio.to_thread(operation, job_id, *args)
            else:
                await self.run_operation(operation, job_id, *args)
        except Exception as exc:
            detail = f"数据传输已完成，但恢复 Bot 失败: {exc}" if job.status == "completed" else str(exc)
            job.status, job.error, job.message = "failed", detail, "传输失败"
            logger.exception("宿主数据传输失败")
        finally:
            job.worker_active = False

    def _save_export_history(self, job: _TransferJob) -> None:
        """持久化已完成导出，刷新页面或重启后仍能恢复下载入口和保留期限。"""
        record_path = self._TRANSFER_TEMP_DIR / f"{job.job_id}.export.json"
        temporary_path = record_path.with_suffix(".tmp")
        temporary_path.write_text(job.to_response().model_dump_json(), encoding="utf-8")
        os.replace(temporary_path, record_path)


    def _restore_export_history(self) -> None:
        for record_path in self._TRANSFER_TEMP_DIR.glob("*.export.json"):
            job_id = record_path.name.removesuffix(".export.json")
            if re.fullmatch(r"[0-9a-f]{32}", job_id) is None or job_id in self._jobs or record_path.is_symlink():
                continue
            record = DataTransferJobResponse.model_validate_json(record_path.read_text(encoding="utf-8"))
            if record.job_id != job_id or record.kind != "export" or record.status != "completed":
                raise ValueError(f"导出历史记录无效: {record_path}")
            job = _TransferJob(job_id, "export")
            job.status = "completed"
            job.progress = 100
            job.message = record.message
            job.filename = record.filename
            job.manifest = record.manifest
            job.total_files = record.total_files
            job.processed_files = record.processed_files
            job.total_bytes = record.total_bytes
            job.processed_bytes = record.processed_bytes
            job.completed_at = record.completed_at
            job.expires_at = record.expires_at
            job.archive_bytes = record.archive_bytes
            archive_path = self._TRANSFER_TEMP_DIR / f"{job_id}.zip"
            if archive_path.is_file() and not archive_path.is_symlink():
                job.file_path = archive_path
            else:
                job.message = "导出文件已过期或已删除"
            self._jobs[job_id] = job
        self._recover_legacy_export_history()


    def _recover_legacy_export_history(self) -> None:
        """为升级前已完整生成的包补历史记录；半成品不能获得下载入口。"""
        for archive_path in self._TRANSFER_TEMP_DIR.glob("*.zip"):
            job_id = archive_path.stem
            if re.fullmatch(r"[0-9a-f]{32}", job_id) is None or job_id in self._jobs or archive_path.is_symlink():
                continue
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    manifest = self._load_archive_manifest(archive)
                    entries = [item for item in archive.infolist() if item.filename != "manifest.json"]
                    expected_files = sum(part["file_count"] for part in manifest["parts"].values())
                    expected_bytes = sum(part["total_bytes"] for part in manifest["parts"].values())
                    if len(entries) != expected_files or sum(item.file_size for item in entries) != expected_bytes:
                        continue
            except (OSError, zipfile.BadZipFile, HTTPException, KeyError, TypeError, ValueError) as exc:
                logger.warning(f"旧导出包无法恢复历史记录: {archive_path.name}, error={exc}")
                continue
            stat = archive_path.stat()
            job = _TransferJob(job_id, "export")
            job.status = "completed"
            job.progress = 100
            job.message = "导出完成"
            job.file_path = archive_path
            job.filename = f"maibot-data-{datetime.fromtimestamp(stat.st_mtime).strftime('%Y%m%d-%H%M%S')}.zip"
            job.manifest = manifest
            job.total_files = job.processed_files = expected_files
            job.total_bytes = job.processed_bytes = expected_bytes
            job.completed_at = stat.st_mtime
            job.expires_at = stat.st_mtime + _EXPORT_RETENTION_SECONDS
            job.archive_bytes = stat.st_size
            self._save_export_history(job)
            self._jobs[job_id] = job


    def _remove_transfer_temp_path(self, path: Path) -> bool:
        """仅删除迁移专用临时目录中的直接子项，并显式记录清理失败。"""
        try:
            if path.is_symlink() or path.resolve().parent != self._TRANSFER_TEMP_DIR.resolve():
                raise ValueError(f"数据迁移临时路径超出清理范围: {path}")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.warning(f"清理数据迁移临时文件失败: {path}, error={exc}")
            return False


    def cleanup_transfer_temp_files(self) -> int:
        """清理失败任务、过期下载文件和重启后遗留的迁移临时项。"""
        with self._temp_cleanup_lock:
            if not self._TRANSFER_TEMP_DIR.exists():
                return 0
            self._restore_export_history()
            now = time.time()
            deleted = 0
            for path in self._TRANSFER_TEMP_DIR.iterdir():
                match = _TEMP_ENTRY_PATTERN.fullmatch(path.name)
                if match is None or path.is_symlink():
                    continue
                job = self._jobs.get(match["job_id"])
                if job is not None:
                    # 取消状态可能先于后台写入停止；下载传输期间也不能删除包。
                    if job.worker_active or job.status in {"pending", "running"} or job.active_downloads:
                        continue
                    if job.status == "completed" and job.expires_at is not None and now < job.expires_at:
                        continue
                elif now - path.stat().st_mtime < _EXPORT_RETENTION_SECONDS:
                    # 重启丢失内存任务记录后，仍给新近生成的包保留一段时间。
                    continue
                if self._remove_transfer_temp_path(path):
                    deleted += 1
                    if job is not None and job.file_path == path:
                        job.file_path = None
                        if job.status == "completed":
                            job.message = "导出文件已过期，请重新导出"
            if deleted:
                logger.info(f"数据迁移临时文件清理完成: {deleted} 项")
            return deleted


    def _new_job(self, kind: Literal["export", "import"]) -> _TransferJob:
        self._TRANSFER_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        job = _TransferJob(job_id=uuid.uuid4().hex, kind=kind)
        self._jobs[job.job_id] = job
        return job


    def _get_job_or_404(self, job_id: str, kind: Literal["export", "import"] | None = None) -> _TransferJob:
        if job_id not in self._jobs and kind == "export":
            with self._temp_cleanup_lock:
                self._restore_export_history()
        job = self._jobs.get(job_id)
        if job is None or (kind is not None and job.kind != kind):
            raise HTTPException(status_code=404, detail="未找到指定的数据迁移任务")
        return job


    def _mark_export_cancelled(self, job: _TransferJob) -> None:
        job.status = "cancelled"
        job.progress = 0
        job.message = "导出已取消"
        job.error = None


    def _raise_if_cancelled(self, job: _TransferJob) -> None:
        if not job.cancel_requested:
            return
        self._mark_export_cancelled(job)
        raise _ExportCancelled


    def _iter_export_files(self,
        root: Path, archive_root: str, job: _TransferJob, excluded_paths: Optional[Set[Path]] = None,
    ) -> list[tuple[Path, str, int]]:
        if not root.exists():
            return []
        if root.is_file():
            self._raise_if_cancelled(job)
            archive_name = f"{archive_root}/{root.name}"
            if archive_name in _EXCLUDED_EXPORT_PATHS or archive_name.startswith(_EXCLUDED_EXPORT_DIR_PREFIXES):
                return []
            stat = root.stat()
            return [(root, archive_name, stat.st_size)]

        files: list[tuple[Path, str, int]] = []
        root_resolved = root.resolve()
        for file_path in root.rglob("*"):
            self._raise_if_cancelled(job)
            if excluded_paths and any(path == file_path or path in file_path.parents for path in excluded_paths):
                continue
            if not file_path.is_file() or file_path.is_symlink():
                continue
            try:
                resolved_path = file_path.resolve()
                resolved_path.relative_to(root_resolved)
                relative_path = resolved_path.relative_to(root_resolved).as_posix()
                archive_name = f"{archive_root}/{relative_path}"
                # 运行时写者锁不包含业务数据，Windows 下持锁读取还会触发 PermissionError。
                # 推理预览图片只用于查看历史推理，默认不随业务数据导出。
                if archive_name in _EXCLUDED_EXPORT_PATHS or archive_name.startswith(_EXCLUDED_EXPORT_DIR_PREFIXES):
                    continue
                files.append((resolved_path, archive_name, resolved_path.stat().st_size))
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning(f"跳过无法导出的文件: {file_path}, error={exc}")
        return files


    def _build_manifest(self, parts: list[str], files: list[tuple[Path, str, int]]) -> dict[str, Any]:
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
            "maibot_version": self.version,
            "included": parts,
            "parts": part_stats,
        }


    def _update_progress(self, job: _TransferJob) -> None:
        if job.status == "cancelled":
            return
        if job.total_bytes > 0:
            job.progress = min(99, int(job.processed_bytes * 100 / job.total_bytes))
        elif job.total_files > 0:
            job.progress = min(99, int(job.processed_files * 100 / job.total_files))
        else:
            job.progress = 99


    def _write_archive_file(self, archive: zipfile.ZipFile, file_path: Path, archive_name: str, job: _TransferJob) -> int:
        written_bytes = 0
        try:
            with file_path.open("rb") as source_file, archive.open(archive_name, "w", force_zip64=True) as target_file:
                while True:
                    self._raise_if_cancelled(job)
                    chunk = source_file.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    target_file.write(chunk)
                    written_bytes += len(chunk)
                    job.processed_bytes += len(chunk)
                    self._update_progress(job)
        except OSError as exc:
            # Windows 字节区域锁可能直到 read 时才报错，补齐路径以便准确定位。
            raise OSError(exc.errno, f"导出文件失败 ({archive_name}): {exc.strerror}", str(file_path)) from exc
        return written_bytes


    def _run_export_job(self, job_id: str, request: DataExportRequest) -> None:
        job = self._get_job_or_404(job_id, "export")
        archive_path: Path | None = None
        snapshot_directory = None
        job.worker_active = True
        try:
            self._raise_if_cancelled(job)
            job.status = "running"
            job.message = "正在扫描需要导出的文件"

            parts = list(_REQUIRED_EXPORT_PARTS)
            if request.include_plugins:
                parts.append("plugins")
            if request.include_logs:
                parts.append("logs")

            snapshot_directory = tempfile.TemporaryDirectory(prefix=f"{job_id}-snapshots-", dir=self._TRANSFER_TEMP_DIR)
            # 宿主已确认整个 Bot 进程树退出，数据库和图快照不再被并发修改。
            for root in self._EXPORT_DIRS.values():
                if root.is_symlink() or root.is_junction() or not root.resolve().is_relative_to(self._PROJECT_ROOT.resolve()):
                    raise ValueError("导出目录不能指向实例以外或使用目录链接")
            files, excluded_paths = [], set()
            if self.prepare_export is not None:
                config_files, config_excluded = self.prepare_export(Path(snapshot_directory.name))
                files.extend(config_files)
                excluded_paths.update(config_excluded)
            job.message = "正在扫描需要导出的文件"
            for part in parts:
                self._raise_if_cancelled(job)
                files.extend(self._iter_export_files(self._EXPORT_DIRS[part], part, job, excluded_paths))

            job.total_files = len(files)
            job.total_bytes = sum(file_size for _, _, file_size in files)
            manifest = self._build_manifest(parts, files)
            job.manifest = manifest

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"maibot-data-{timestamp}.zip"
            archive_path = self._TRANSFER_TEMP_DIR / f"{job.job_id}.zip"
            job.filename = filename
            job.file_path = archive_path

            job.message = "正在写入压缩包"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for file_path, archive_name, _ in files:
                    self._raise_if_cancelled(job)
                    self._write_archive_file(archive, file_path, archive_name, job)
                    job.processed_files += 1
                    self._update_progress(job)

            self._raise_if_cancelled(job)
            job.expires_at = time.time() + _EXPORT_RETENTION_SECONDS
            job.status = "completed"
            job.progress = 100
            job.message = "导出完成"
            job.completed_at = time.time()
            job.archive_bytes = archive_path.stat().st_size
            with self._temp_cleanup_lock:
                self._save_export_history(job)
        except _ExportCancelled:
            pass
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
        finally:
            try:
                if snapshot_directory is not None:
                    snapshot_directory.cleanup()
            finally:
                if job.status != "completed" and archive_path is not None:
                    if self._remove_transfer_temp_path(archive_path):
                        job.file_path = None
                job.worker_active = False


    def _safe_zip_member_path(self, member_name: str) -> tuple[str, str]:
        normalized_name = member_name.replace("\\", "/")
        if normalized_name.startswith("/") or ":" in normalized_name:
            raise HTTPException(status_code=400, detail=f"压缩包包含非法路径: {member_name}")
        if not normalized_name or normalized_name.endswith("/"):
            return "", ""
        path = Path(normalized_name)
        if path.is_absolute() or ".." in path.parts:
            raise HTTPException(status_code=400, detail=f"压缩包包含非法路径: {member_name}")
        top_level = path.parts[0]
        if normalized_name != "manifest.json" and top_level not in _ALLOWED_IMPORT_PARTS:
            raise HTTPException(status_code=400, detail=f"压缩包包含不支持的顶层目录: {top_level}")
        return top_level, "/".join(path.parts[1:])


    def _load_archive_manifest(self, archive: zipfile.ZipFile) -> dict[str, Any]:
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


    def _validate_archive_members(self, archive: zipfile.ZipFile) -> None:
        for member in archive.infolist():
            self._safe_zip_member_path(member.filename)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(status_code=400, detail=f"压缩包不允许包含符号链接: {member.filename}")


    def _resolve_import_target(self, top_level: str, relative_path: str) -> Path:
        root = self._EXPORT_DIRS[top_level].resolve()
        target_path = (root / relative_path).resolve()
        try:
            target_path.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"压缩包包含非法路径: {top_level}/{relative_path}") from exc
        return target_path


    def _run_import_job(self, job_id: str, archive_path: Path, enabled_parts: set[str]) -> None:
        job = self._get_job_or_404(job_id, "import")
        try:
            job.status = "running"
            job.message = "正在校验压缩包"

            if self.apply_import is None:
                raise RuntimeError("数据导入必须由宿主事务服务执行")
            self.apply_import(archive_path, enabled_parts, job)

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


    async def _save_upload_file(self, file: UploadFile, target_path: Path) -> None:
        async with await anyio.open_file(target_path, "wb") as output_file:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                await output_file.write(chunk)


    def create_data_export(self, request: DataExportRequest, background_tasks: BackgroundTasks) -> DataTransferJobResponse:
        """创建 MaiBot 数据导出任务。"""
        job = self._new_job("export")
        background_tasks.add_task(self._execute, self._run_export_job, job.job_id, request)
        return job.to_response()


    def get_data_transfer_job(self, job_id: str) -> DataTransferJobResponse:
        """查询导入或导出任务进度。"""
        return self._get_job_or_404(job_id).to_response()


    def list_data_exports(self) -> List[DataTransferJobResponse]:
        """列出已完成导出，包括已过期记录；扫描仅访问小型历史元数据。"""
        with self._temp_cleanup_lock:
            self._restore_export_history()
            jobs = sorted(
                (job for job in self._jobs.values() if job.kind == "export" and job.status == "completed"),
                key=lambda job: job.completed_at or 0,
                reverse=True,
            )
            for job in jobs:
                if job.file_path is not None and not job.file_path.is_file():
                    job.file_path = None
                    job.message = "导出文件已过期或已删除"
            return [job.to_response() for job in jobs]


    def download_data_export(self, job_id: str) -> FileResponse:
        """下载已完成的数据导出压缩包。"""
        with self._temp_cleanup_lock:
            job = self._get_job_or_404(job_id, "export")
            if job.status != "completed" or job.file_path is None or not job.file_path.is_file():
                raise HTTPException(status_code=400, detail="导出任务尚未完成或文件已失效")
            job.active_downloads += 1
            job.expires_at = time.time() + _EXPORT_RETENTION_SECONDS
            return _ExportDownloadResponse(
                job.file_path,
                job=job, on_finish=self._finish_export_download,
                media_type="application/zip",
                filename=job.filename or "maibot-data.zip",
            )


    def _finish_export_download(self, job: _TransferJob) -> None:
        with self._temp_cleanup_lock:
            job.active_downloads -= 1
            job.expires_at = time.time() + _EXPORT_RETENTION_SECONDS
            if job.completed_at is not None:
                self._save_export_history(job)


    def cancel_data_export(self, job_id: str) -> DataTransferJobResponse:
        """取消正在执行的数据导出任务。"""
        job = self._get_job_or_404(job_id, "export")
        if job.status in {"completed", "failed", "cancelled"}:
            return job.to_response()
        job.cancel_requested = True
        self._mark_export_cancelled(job)
        return job.to_response()


    async def create_data_import(self,
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

        job = self._new_job("import")
        upload_path = self._TRANSFER_TEMP_DIR / f"{job.job_id}-upload.zip"
        await self._save_upload_file(file, upload_path)
        await file.close()

        background_tasks.add_task(self._execute, self._run_import_job, job.job_id, upload_path, enabled_parts)
        return DataImportResponse(job_id=job.job_id, status=job.status)


    def delete_data_transfer_job(self, job_id: str) -> dict[str, bool]:
        """清理任务记录和已生成的临时文件。"""
        with self._temp_cleanup_lock:
            self._restore_export_history()
            job = self._get_job_or_404(job_id)
            if job.worker_active or job.status in {"pending", "running"} or job.active_downloads:
                raise HTTPException(status_code=409, detail="任务仍在处理或下载中，请稍后删除")
            if job.file_path is not None and not self._remove_transfer_temp_path(job.file_path):
                raise HTTPException(status_code=500, detail="删除导出文件失败，请查看日志")
            (self._TRANSFER_TEMP_DIR / f"{job_id}.export.json").unlink(missing_ok=True)
            self._jobs.pop(job_id, None)
        return {"success": True}

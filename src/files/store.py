"""文件索引和字节存储。所有方法为同步接口，由调用方在线程池执行。"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Iterator, List, Optional

from sqlalchemy import delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import hashlib
import json
import os
import re
import secrets
import stat
import time

from .config import FilesConfig
from .models import FileAsset, FileBinding, FileParse


class FileLimitError(ValueError):
    """大小、并发或存储额度不足。"""


class FileBusyError(RuntimeError):
    """同一文件正在传输，不能删除、清理或重复启动传输。"""


class FileStore:
    def __init__(self, root: Path, engine: Engine, config: FilesConfig,
                 config_provider: Optional[Callable[[], FilesConfig]] = None) -> None:
        self.root = root.resolve()
        self.engine = engine
        self._config = config
        self._config_provider = config_provider
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def config(self) -> FilesConfig:
        return self._config_provider() if self._config_provider else self._config

    @config.setter
    def config(self, value: FilesConfig) -> None:
        self._config = value

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        # SQLite 的写锁把配额检查和预留放在同一事务中，跨线程/事件循环也不能超卖。
        with Session(self.engine, expire_on_commit=False) as session:
            session.connection().exec_driver_sql("PRAGMA busy_timeout=10000")
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def path(self, file_ref: str, *, partial: bool = False) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", file_ref) is None:
            raise ValueError("无效文件引用")
        path = self.root / (file_ref + (".part" if partial else ".blob"))
        if path.is_symlink() or path.is_junction():
            raise PermissionError("文件存储中不允许链接或目录联接")
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise PermissionError("附件必须是普通文件")
        return path

    @staticmethod
    def normalize_name(name: str) -> str:
        # 原文件名只用于展示和下载响应，磁盘路径完全由主程序生成。
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        name = "".join(c for c in name if c >= " " and c != "\x7f").strip()
        if not name or len(name) > 255:
            raise ValueError("文件名不能为空或超过 255 个字符")
        return name

    @staticmethod
    def _authorize(session: Session, asset: FileAsset, session_id: Optional[str], admin: bool) -> None:
        if asset.state == "deleted":
            raise FileNotFoundError("附件已删除")
        if admin:
            return
        if not session_id or session.exec(select(FileBinding).where(
            FileBinding.file_ref == asset.file_ref, FileBinding.session_id == session_id,
        )).first() is None:
            raise PermissionError("当前聊天没有该附件的访问权限")

    def _get(self, session: Session, file_ref: str, session_id: Optional[str], admin: bool) -> FileAsset:
        self.path(file_ref)
        asset = session.get(FileAsset, file_ref)
        if asset is None:
            raise FileNotFoundError("附件不存在")
        self._authorize(session, asset, session_id, admin)
        return asset

    @staticmethod
    def public(asset: FileAsset) -> Dict[str, Any]:
        return asset.model_dump(include={
            "file_ref", "name", "mime_type", "size", "sha256", "origin", "state", "error",
            "created_at", "accessed_at",
        })

    def get(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> FileAsset:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._get(session, file_ref, session_id, admin)

    def list(self, session_id: Optional[str], *, admin: bool = False, offset: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        if not session_id and not admin:
            raise PermissionError("必须指定已授权的聊天")
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("分页参数无效")
        with Session(self.engine) as session:
            query = select(FileAsset).where(FileAsset.state != "deleted")
            if session_id:
                query = query.join(FileBinding).where(FileBinding.session_id == session_id)
            assets = session.exec(query.order_by(FileAsset.created_at.desc(), FileAsset.file_ref).offset(offset).limit(limit)).all()
            results = []
            for asset in assets:
                item = self.public(asset)
                if admin:
                    item["session_ids"] = list(session.exec(select(FileBinding.session_id).where(FileBinding.file_ref == asset.file_ref)).all())
                parsed = session.get(FileParse, asset.file_ref)
                item["parse_state"] = parsed.state if parsed is not None else "pending"
                item["parse_error"] = parsed.error if parsed is not None else ""
                results.append(item)
            return results

    def register_remote(self, session_id: str, message_id: str, name: str, size: int, mime_type: str,
                        source: Dict[str, Any], source_key: str) -> FileAsset:
        name = self.normalize_name(name)
        now = time.time()
        with self.transaction() as session:
            asset = session.exec(select(FileAsset).where(FileAsset.source_key == source_key)).first()
            if asset is None:
                asset = FileAsset(file_ref=secrets.token_hex(16), name=name, size=max(0, size),
                                  mime_type=mime_type or "application/octet-stream", source=json.dumps(source),
                                  source_key=source_key, created_at=now, accessed_at=now)
                session.add(asset)
                session.flush()
            if asset.state == "deleted":
                # 人工删除后的重复通知不能重新创建授权和文件。
                raise FileNotFoundError("附件已被管理员删除")
            if session.exec(select(FileBinding).where(FileBinding.file_ref == asset.file_ref,
                                                     FileBinding.session_id == session_id)).first() is None:
                session.add(FileBinding(file_ref=asset.file_ref, session_id=session_id,
                                        message_id=message_id, created_at=now))
            return asset

    def _reserve(self, session: Session, asset: FileAsset) -> str:
        if asset.transfer_id:
            raise FileBusyError("附件正在传输")
        maximum = self.config.max_file_mib * 1024 * 1024
        if asset.size > maximum:
            raise FileLimitError(f"文件超过 {self.config.max_file_mib} MiB 上限")
        active = session.exec(select(func.count()).select_from(FileAsset).where(FileAsset.transfer_id != "")).one()
        if active >= self.config.max_concurrent_transfers:
            raise FileLimitError("文件传输并发数已满，请稍后重试")
        used = session.exec(select(func.coalesce(func.sum(FileAsset.size), 0)).where(FileAsset.state == "ready")).one()
        reserved = session.exec(select(func.coalesce(func.sum(FileAsset.reserved_bytes), 0))).one()
        # 远程声明大小不可信，始终预留单文件最大空间；接收完成后释放未使用部分。
        if used + reserved + maximum > self.config.max_storage_gib * 1024 ** 3:
            raise FileLimitError("文件存储配额不足，请清理文件或调整配额")
        asset.transfer_id = secrets.token_hex(32)
        asset.reserved_bytes = maximum
        asset.transfer_started = time.time()
        asset.state = "downloading" if asset.origin == "remote" else "uploading"
        asset.error = ""
        self.path(asset.file_ref, partial=True).touch(exist_ok=False)
        session.add(asset)
        return asset.transfer_id

    def begin_upload(self, session_id: str, name: str, mime_type: str = "application/octet-stream") -> FileAsset:
        now = time.time()
        asset = FileAsset(file_ref=secrets.token_hex(16), name=self.normalize_name(name), mime_type=mime_type,
                          origin="local", created_at=now, accessed_at=now)
        try:
            with self.transaction() as session:
                self._reserve(session, asset)
                session.flush()
                session.add(FileBinding(file_ref=asset.file_ref, session_id=session_id, created_at=now))
            return asset
        except BaseException:
            self.path(asset.file_ref, partial=True).unlink(missing_ok=True)
            raise

    def begin_download(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> FileAsset:
        started = False
        try:
            with self.transaction() as session:
                asset = self._get(session, file_ref, session_id, admin)
                if asset.origin != "remote":
                    raise ValueError("本地原件不存在，无法从平台重新下载")
                if asset.state == "ready":
                    return asset
                self._reserve(session, asset)
                started = True
                return asset
        except BaseException:
            if started:
                self.path(file_ref, partial=True).unlink(missing_ok=True)
            raise

    def append(self, file_ref: str, transfer_id: str, chunk: bytes) -> None:
        with self.transaction() as session:
            asset = session.get(FileAsset, file_ref)
            if asset is None or not transfer_id or not secrets.compare_digest(asset.transfer_id, transfer_id):
                raise PermissionError("文件传输凭证无效或已取消")
            path = self.path(file_ref, partial=True)
            if path.stat().st_size + len(chunk) > asset.reserved_bytes:
                raise FileLimitError(f"实际文件大小超过 {self.config.max_file_mib} MiB 上限")
            with path.open("ab") as handle:
                handle.write(chunk)

    def finish(self, file_ref: str, transfer_id: str) -> FileAsset:
        with self.transaction() as session:
            asset = session.get(FileAsset, file_ref)
            if asset is None or not transfer_id or not secrets.compare_digest(asset.transfer_id, transfer_id):
                raise PermissionError("文件传输凭证无效或已取消")
            path = self.path(file_ref, partial=True)
            size = path.stat().st_size
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(256 * 1024), b""):
                    digest.update(chunk)
            os.replace(path, self.path(file_ref))
            asset.size, asset.sha256 = size, digest.hexdigest()
            asset.state, asset.error = "ready", ""
            asset.reserved_bytes, asset.transfer_id = 0, ""
            asset.accessed_at = time.time()
            session.add(asset)
            return asset

    def fail(self, file_ref: str, transfer_id: str, error: str) -> None:
        with self.transaction() as session:
            asset = session.get(FileAsset, file_ref)
            if asset is None or not transfer_id or asset.transfer_id != transfer_id:
                return
            self.path(file_ref, partial=True).unlink(missing_ok=True)
            self.path(file_ref).unlink(missing_ok=True)
            asset.state, asset.error = "failed", error[:2000]
            asset.transfer_id, asset.reserved_bytes = "", 0
            session.add(asset)

    def touch(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> None:
        with self.transaction() as session:
            asset = self._get(session, file_ref, session_id, admin)
            asset.accessed_at = time.time()
            session.add(asset)

    def read_bytes(self, file_ref: str, session_id: Optional[str], *, admin: bool = False) -> bytes:
        # 在事务内取得快照，清理与读取不交错；数据量有单文件上限。
        with self.transaction() as session:
            asset = self._get(session, file_ref, session_id, admin)
            if asset.state != "ready":
                raise FileNotFoundError("附件内容尚未就绪")
            data = self.path(file_ref).read_bytes()
            if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
                raise RuntimeError("附件内容校验失败")
            asset.accessed_at = time.time()
            session.add(asset)
            return data

    def record_parse(self, file_ref: str, encoding: str, error: str = "") -> None:
        with self.transaction() as session:
            session.merge(FileParse(file_ref=file_ref, encoding=encoding, state="failed" if error else "ready",
                                    error=error[:2000], updated_at=time.time()))

    def grant(self, file_ref: str, target_session: str) -> None:
        """仅由管理员转发入口调用；普通模型和 SDK 不提供此方法。"""
        with self.transaction() as session:
            self._get(session, file_ref, None, True)
            if session.exec(select(FileBinding).where(FileBinding.file_ref == file_ref,
                                                     FileBinding.session_id == target_session)).first() is None:
                session.add(FileBinding(file_ref=file_ref, session_id=target_session, created_at=time.time()))

    def remove(self, file_ref: str, *, cache_only: bool = False) -> None:
        with self.transaction() as session:
            asset = self._get(session, file_ref, None, True)
            if asset.transfer_id or asset.open_readers or asset.leased_until > time.time():
                raise FileBusyError("附件正在传输，请在完成后清理")
            if cache_only and asset.origin != "remote":
                raise ValueError("本地上传和生成的原件不能作为缓存清理")
            self.path(file_ref).unlink(missing_ok=True)
            asset.state, asset.error = ("remote" if cache_only else "deleted"), ""
            session.add(asset)
            session.exec(delete(FileParse).where(FileParse.file_ref == file_ref))

    def cleanup(self, *, startup: bool = False) -> Dict[str, int]:
        now = time.time()
        expired = interrupted = 0
        with self.transaction() as session:
            for asset in session.exec(select(FileAsset).where(FileAsset.state != "deleted")).all():
                if startup:
                    asset.open_readers = 0
                    asset.leased_until = 0
                if asset.transfer_id and (startup or now - asset.transfer_started > self.config.transfer_timeout + 60):
                    self.path(asset.file_ref, partial=True).unlink(missing_ok=True)
                    self.path(asset.file_ref).unlink(missing_ok=True)
                    asset.transfer_id, asset.reserved_bytes = "", 0
                    asset.state, asset.error = "failed", "文件传输已中断，请重新发起"
                    interrupted += 1
                if asset.origin == "remote" and asset.state == "ready" and not asset.open_readers and asset.leased_until <= now and now - asset.accessed_at > self.config.cache_days * 86400:
                    self.path(asset.file_ref).unlink(missing_ok=True)
                    asset.state = "remote"
                    session.exec(delete(FileParse).where(FileParse.file_ref == asset.file_ref))
                    expired += 1
                session.add(asset)
            if startup:
                # 崩溃可能发生在创建临时文件后、索引提交前，只清理本服务生成的孤立路径。
                indexed = {asset.file_ref: asset for asset in session.exec(select(FileAsset)).all()}
                for path in self.root.iterdir():
                    if path.suffix not in (".part", ".blob") or re.fullmatch(r"[0-9a-f]{32}", path.stem) is None:
                        continue
                    asset = indexed.get(path.stem)
                    if path.suffix == ".part" or asset is None or asset.state != "ready":
                        self.path(path.stem, partial=path.suffix == ".part").unlink(missing_ok=True)
        return {"expired": expired, "interrupted": interrupted}

    def lease(self, file_ref: str, session_id: Optional[str], *, admin: bool = False, seconds: int = 300) -> FileAsset:
        """短期固定已就绪的不可变内容，避免远端抓取期间被清理或删除。"""
        with self.transaction() as session:
            asset = self._get(session, file_ref, session_id, admin)
            if asset.state != "ready":
                raise FileNotFoundError("附件尚未下载")
            asset.leased_until = max(asset.leased_until, time.time() + seconds)
            asset.accessed_at = time.time()
            session.add(asset)
            return asset

    @contextmanager
    def reader(self, file_ref: str) -> Iterator[BinaryIO]:
        """已验证传输凭证后的读取租约，整个响应结束前都禁止清理。"""
        with self.transaction() as session:
            asset = self._get(session, file_ref, None, True)
            if asset.state != "ready":
                raise FileNotFoundError("附件尚未就绪")
            handle = self.path(file_ref).open("rb")
            asset.open_readers += 1
            session.add(asset)
        try:
            yield handle
        finally:
            handle.close()
            with self.transaction() as session:
                asset = self._get(session, file_ref, None, True)
                asset.open_readers -= 1
                asset.accessed_at = time.time()
                session.add(asset)

    def usage(self) -> Dict[str, int]:
        with Session(self.engine) as session:
            used = session.exec(select(func.coalesce(func.sum(FileAsset.size), 0)).where(FileAsset.state == "ready")).one()
            reserved = session.exec(select(func.coalesce(func.sum(FileAsset.reserved_bytes), 0))).one()
            return {"used_bytes": used, "reserved_bytes": reserved,
                    "max_bytes": self.config.max_storage_gib * 1024 ** 3,
                    "max_file_bytes": self.config.max_file_mib * 1024 ** 2}

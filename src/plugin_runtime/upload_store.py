"""Owner-scoped, single-use WebUI uploads. Files never travel through JSON/RPC."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from PIL import Image
from threading import Lock
from typing import Any, Dict

import asyncio
import hashlib
import io
import secrets
import shutil
import sqlite3
import time
import warnings

from src.common.paths import get_instance_root
from src.plugin_runtime.runner.plugin_paths import build_plugin_paths

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_PENDING_PER_PLUGIN = 8
MAX_PENDING_TOTAL = 32
MAX_UPLOADS_PER_PLUGIN = 2
MAX_UPLOADS_TOTAL = 4
PROJECT_ROOT = get_instance_root()


class UploadCapacityError(ValueError):
    """上传接收或暂存预算已用尽，调用者应返回 429。"""


class UploadBudget:
    """跨事件循环共享的即时配额；仅在锁内更新计数，不排队接收文件。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: Dict[str, int] = {}

    @contextmanager
    def reserve(self, owner: str) -> Iterator[None]:
        with self._lock:
            count = self._active.get(owner, 0)
            if count >= MAX_UPLOADS_PER_PLUGIN or sum(self._active.values()) >= MAX_UPLOADS_TOTAL:
                raise UploadCapacityError("上传处理中，请稍后再试")
            self._active[owner] = count + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._active[owner] - 1
                if remaining:
                    self._active[owner] = remaining
                else:
                    del self._active[owner]


upload_budget = UploadBudget()


def validate_image(raw: bytes) -> str:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("文件为空或超过 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("仅支持 JPEG、PNG、静态 WebP")
                # PIL 的不同格式子类不共享 n_frames 属性，使用所有格式支持的 seek 检查动画。
                try:
                    image.seek(1)
                except EOFError:
                    image.seek(0)
                else:
                    raise ValueError("仅支持静态图片")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("图片超过 4000 万像素")
                image.load()
                return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image.format]
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片解码尺寸超限") from exc


class UploadStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS uploads (token TEXT PRIMARY KEY, owner TEXT, suffix TEXT, expires REAL, claimed INTEGER DEFAULT 0, sha TEXT)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "uploads.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def stage(self, owner: str, raw: bytes) -> str:
        build_plugin_paths(owner, PROJECT_ROOT)  # also validates the identifier
        suffix = validate_image(raw)
        token = secrets.token_hex(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db)
            total, owned = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(owner = ?), 0) FROM uploads WHERE claimed = 0",
                (owner,),
            ).fetchone()
            # 配合单文件 20 MiB 上限，暂存总量最多 640 MiB、单插件最多 160 MiB。
            if total >= MAX_PENDING_TOTAL or owned >= MAX_PENDING_PER_PLUGIN:
                raise UploadCapacityError("未领取图片过多，请领取或等待暂存文件过期")
            try:
                (self.root / token).write_bytes(raw)
                db.execute(
                    "INSERT INTO uploads VALUES (?,?,?,?,0,?)",
                    (token, owner, suffix, time.time() + 3600, hashlib.sha256(raw).hexdigest()),
                )
                db.commit()
            except BaseException:
                (self.root / token).unlink(missing_ok=True)
                raise
        return token

    def _cleanup(self, db: sqlite3.Connection) -> None:
        now = time.time()
        for (token,) in db.execute("SELECT token FROM uploads WHERE expires < ? AND claimed = 0", (now,)):
            (self.root / token).unlink(missing_ok=True)
        db.execute("DELETE FROM uploads WHERE expires < ?", (now,))

    def cleanup(self) -> None:
        """后台定期回收，不依赖下一次上传；与领取、暂存使用同一事务锁。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db)

    def claim(self, owner: str, token: str) -> Dict[str, Any]:
        paths = build_plugin_paths(owner, PROJECT_ROOT)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT suffix,expires,claimed,sha FROM uploads WHERE token=? AND owner=?", (token, owner)
            ).fetchone()
            if row is None or row[1] < time.time() or row[2]:
                raise ValueError("上传凭证不存在、已过期或已领取")
            destination = paths.data_dir / "uploads" / (token + row[0])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.root / token), destination)
            db.execute("UPDATE uploads SET claimed=1 WHERE token=?", (token,))
        return {"path": str(destination), "sha256": row[3], "upload_id": token}


def upload_store() -> UploadStore:
    return UploadStore(PROJECT_ROOT / "temp" / "webui-uploads")


def cleanup_uploads() -> None:
    root = PROJECT_ROOT / "temp" / "webui-uploads"
    if root.exists():
        UploadStore(root).cleanup()


async def claim_upload(plugin_id: str, capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
    del capability
    store = await asyncio.to_thread(upload_store)
    upload = await asyncio.to_thread(store.claim, plugin_id, str(args.get("upload_id", "")))
    return {"success": True, "upload": upload}

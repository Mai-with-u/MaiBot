from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from os import getenv
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Iterator
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from src.common.logger import get_logger
from src.webui.dependencies import require_auth

logger = get_logger("webui.plugin_stats_proxy")
router = APIRouter(dependencies=[Depends(require_auth)])

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_STATS_DB_PATH = Path(
    getenv("MAIBOT_PLUGIN_STATS_DB_PATH", str(_PROJECT_ROOT / "data" / "plugin_stats.db"))
).expanduser()
PLUGIN_STATS_BASE_URL = getenv("MAIBOT_PLUGIN_STATS_BASE_URL", "http://hyybuth.xyz:10059").rstrip("/")
PLUGIN_STATS_TIMEOUT = float(getenv("MAIBOT_PLUGIN_STATS_TIMEOUT", "5"))
PLUGIN_STATS_RETRY_INTERVAL = float(getenv("MAIBOT_PLUGIN_STATS_RETRY_INTERVAL", "60"))
_DB_LOCK = RLock()
_REMOTE_STATE_LOCK = RLock()
_REMOTE_UNAVAILABLE_UNTIL = 0.0


class VoteRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)


class RatingRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=300)
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def validate_rating_or_comment(self) -> "RatingRequest":
        has_rating = "rating" in self.model_fields_set and self.rating is not None
        has_comment = "comment" in self.model_fields_set
        if not has_rating and not has_comment:
            raise ValueError("rating 和 comment 至少需要提供一个")
        return self


class DownloadRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1, max_length=200)
    user_id: str | None = Field(None, min_length=1, max_length=300)
    fingerprint: str | None = Field(None, min_length=1, max_length=300)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    PLUGIN_STATS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(PLUGIN_STATS_DB_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        _initialize_schema(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS plugin_stats (
            plugin_id TEXT PRIMARY KEY,
            likes INTEGER NOT NULL DEFAULT 0 CHECK (likes >= 0),
            dislikes INTEGER NOT NULL DEFAULT 0 CHECK (dislikes >= 0),
            downloads INTEGER NOT NULL DEFAULT 0 CHECK (downloads >= 0),
            rating REAL NOT NULL DEFAULT 0,
            rating_count INTEGER NOT NULL DEFAULT 0 CHECK (rating_count >= 0)
        );

        CREATE TABLE IF NOT EXISTS plugin_user_state (
            plugin_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            like_count INTEGER NOT NULL DEFAULT 0 CHECK (like_count BETWEEN 0 AND 1),
            disliked INTEGER NOT NULL DEFAULT 0 CHECK (disliked IN (0, 1)),
            rating INTEGER CHECK (rating BETWEEN 1 AND 5),
            comment TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (plugin_id, user_id),
            FOREIGN KEY (plugin_id) REFERENCES plugin_stats(plugin_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_plugin_user_state_recent
        ON plugin_user_state(plugin_id, updated_at DESC);
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(plugin_stats)")}
    if "rating" not in columns:
        connection.execute("ALTER TABLE plugin_stats ADD COLUMN rating REAL NOT NULL DEFAULT 0")
    if "rating_count" not in columns:
        connection.execute(
            "ALTER TABLE plugin_stats ADD COLUMN rating_count INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_plugin(connection: sqlite3.Connection, plugin_id: str) -> None:
    connection.execute("INSERT OR IGNORE INTO plugin_stats(plugin_id) VALUES (?)", (plugin_id,))


def _stats_for_plugin(connection: sqlite3.Connection, plugin_id: str) -> dict[str, Any]:
    _ensure_plugin(connection, plugin_id)
    row = connection.execute(
        """
        SELECT plugin_id, likes, dislikes, downloads, rating, rating_count
        FROM plugin_stats WHERE plugin_id = ?
        """,
        (plugin_id,),
    ).fetchone()
    recent_rows = connection.execute(
        """
        SELECT user_id, rating, comment, updated_at AS created_at
        FROM plugin_user_state
        WHERE plugin_id = ? AND (rating IS NOT NULL OR comment <> '')
        ORDER BY updated_at DESC LIMIT 20
        """,
        (plugin_id,),
    ).fetchall()
    return {
        "plugin_id": str(row["plugin_id"]),
        "likes": int(row["likes"]),
        "dislikes": int(row["dislikes"]),
        "downloads": int(row["downloads"]),
        "rating": float(row["rating"] or 0),
        "rating_count": int(row["rating_count"]),
        "recent_ratings": [dict(recent_row) for recent_row in recent_rows],
    }


def _sync_stats(connection: sqlite3.Connection, plugin_id: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else payload
    _ensure_plugin(connection, plugin_id)
    allowed = {
        "likes": int,
        "dislikes": int,
        "downloads": int,
        "rating": float,
        "rating_count": int,
    }
    updates: list[str] = []
    values: list[Any] = []
    for field, converter in allowed.items():
        if field in stats and stats[field] is not None:
            try:
                value = converter(stats[field])
            except (TypeError, ValueError):
                continue
            updates.append(f"{field} = ?")
            values.append(max(0, value))
    if updates:
        values.append(plugin_id)
        connection.execute(
            f"UPDATE plugin_stats SET {', '.join(updates)} WHERE plugin_id = ?", values
        )
    recent = stats.get("recent_ratings")
    if isinstance(recent, list):
        for item in recent:
            if not isinstance(item, dict) or not item.get("user_id"):
                continue
            connection.execute(
                """
                INSERT INTO plugin_user_state(plugin_id, user_id, rating, comment, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, user_id) DO UPDATE SET
                    rating = excluded.rating,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (
                    plugin_id,
                    str(item["user_id"]),
                    item.get("rating"),
                    str(item.get("comment") or ""),
                    str(item.get("created_at") or _utc_now()),
                ),
            )


def _sync_user_state(
    connection: sqlite3.Connection,
    plugin_id: str,
    user_id: str,
    payload: Any,
    *,
    is_user_state_response: bool = False,
) -> None:
    if not isinstance(payload, dict):
        return
    _ensure_plugin(connection, plugin_id)
    current = connection.execute(
        "SELECT like_count, disliked, rating, comment FROM plugin_user_state WHERE plugin_id = ? AND user_id = ?",
        (plugin_id, user_id),
    ).fetchone()
    liked = int(bool(payload.get("liked"))) if "liked" in payload else int(current["like_count"] if current else 0)
    disliked = int(bool(payload.get("disliked"))) if "disliked" in payload else int(current["disliked"] if current else 0)
    rating = payload.get("user_rating", current["rating"] if current else None)
    comment = payload.get("user_comment", current["comment"] if current else "")
    if is_user_state_response:
        rating = payload.get("rating", rating)
        comment = payload.get("comment", comment)
    connection.execute(
        """
        INSERT INTO plugin_user_state(plugin_id, user_id, like_count, disliked, rating, comment, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plugin_id, user_id) DO UPDATE SET
            like_count = excluded.like_count,
            disliked = excluded.disliked,
            rating = excluded.rating,
            comment = excluded.comment,
            updated_at = excluded.updated_at
        """,
        (plugin_id, user_id, liked, disliked, rating, str(comment or ""), _utc_now()),
    )


def _sync_remote_response(
    path: str, payload: dict[str, Any], plugin_id: str | None = None, user_id: str | None = None
) -> None:
    with _DB_LOCK, _connect() as connection:
        if path == "/stats/summary":
            stats_map = payload.get("stats")
            if isinstance(stats_map, dict):
                for stats_id, stats in stats_map.items():
                    _sync_stats(connection, str(stats_id), stats)
            return
        if plugin_id:
            _sync_stats(connection, plugin_id, payload)
        if plugin_id and user_id and path in {"/stats/user-state", "/stats/like", "/stats/dislike", "/stats/rate"}:
            _sync_user_state(
                connection,
                plugin_id,
                user_id,
                payload,
                is_user_state_response=path == "/stats/user-state",
            )


async def _remote_request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    global _REMOTE_UNAVAILABLE_UNTIL

    with _REMOTE_STATE_LOCK:
        if monotonic() < _REMOTE_UNAVAILABLE_UNTIL:
            return None

    url = f"{PLUGIN_STATS_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=PLUGIN_STATS_TIMEOUT) as client:
            response = await client.request(method, url, json=payload)
        if not response.is_success:
            logger.warning(f"远程插件统计不可用，改用本地统计: {url} - HTTP {response.status_code}")
            with _REMOTE_STATE_LOCK:
                _REMOTE_UNAVAILABLE_UNTIL = monotonic() + PLUGIN_STATS_RETRY_INTERVAL
            return None
        data = response.json()
        if not isinstance(data, dict) or data.get("success") is False:
            logger.warning(f"远程插件统计响应无效，改用本地统计: {url}")
            with _REMOTE_STATE_LOCK:
                _REMOTE_UNAVAILABLE_UNTIL = monotonic() + PLUGIN_STATS_RETRY_INTERVAL
            return None
        with _REMOTE_STATE_LOCK:
            _REMOTE_UNAVAILABLE_UNTIL = 0.0
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"远程插件统计请求失败，改用本地统计: {url} - {type(exc).__name__}: {exc!r}")
        with _REMOTE_STATE_LOCK:
            _REMOTE_UNAVAILABLE_UNTIL = monotonic() + PLUGIN_STATS_RETRY_INTERVAL
        return None


def _local_user_state(plugin_id: str, user_id: str) -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT like_count, disliked, rating, comment FROM plugin_user_state WHERE plugin_id = ? AND user_id = ?",
            (plugin_id, user_id),
        ).fetchone()
    return {
        "success": True,
        "liked": bool(row and row["like_count"]),
        "disliked": bool(row and row["disliked"]),
        "rating": row["rating"] if row else None,
        "comment": str(row["comment"] or "") if row else "",
    }


@router.get("/stats-proxy/stats/user-state")
async def get_plugin_user_state(plugin_id: str, user_id: str) -> dict[str, Any]:
    query = f"plugin_id={quote(plugin_id, safe='')}&user_id={quote(user_id, safe='')}"
    remote = await _remote_request("GET", f"/stats/user-state?{query}")
    if remote is not None:
        _sync_remote_response("/stats/user-state", remote, plugin_id, user_id)
        return remote
    return _local_user_state(plugin_id, user_id)


@router.get("/stats-proxy/stats/summary")
async def get_plugin_stats_summary() -> dict[str, Any]:
    remote = await _remote_request("GET", "/stats/summary")
    if remote is not None:
        _sync_remote_response("/stats/summary", remote)
        return remote
    with _DB_LOCK, _connect() as connection:
        plugin_ids = [str(row["plugin_id"]) for row in connection.execute("SELECT plugin_id FROM plugin_stats")]
        stats = {plugin_id: _stats_for_plugin(connection, plugin_id) for plugin_id in plugin_ids}
    return {"success": True, "stats": stats, "source": "local"}


@router.get("/stats-proxy/stats/{plugin_id}")
async def get_plugin_stats(plugin_id: str) -> dict[str, Any]:
    remote = await _remote_request("GET", f"/stats/{quote(plugin_id, safe='')}")
    if remote is not None:
        _sync_remote_response(f"/stats/{plugin_id}", remote, plugin_id)
        return remote
    with _DB_LOCK, _connect() as connection:
        stats = _stats_for_plugin(connection, plugin_id)
    return {"success": True, "stats": stats, "source": "local"}


def _local_toggle_like(request: VoteRequest) -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_plugin(connection, request.plugin_id)
        current = connection.execute(
            "SELECT like_count, disliked FROM plugin_user_state WHERE plugin_id = ? AND user_id = ?",
            (request.plugin_id, request.user_id),
        ).fetchone()
        next_liked = not bool(current and current["like_count"])
        connection.execute(
            """
            INSERT INTO plugin_user_state(plugin_id, user_id, like_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plugin_id, user_id) DO UPDATE SET
                like_count = excluded.like_count,
                updated_at = excluded.updated_at
            """,
            (request.plugin_id, request.user_id, int(next_liked), _utc_now()),
        )
        connection.execute(
            "UPDATE plugin_stats SET likes = MAX(0, likes + ?) WHERE plugin_id = ?",
            (1 if next_liked else -1, request.plugin_id),
        )
        stats = _stats_for_plugin(connection, request.plugin_id)
    return {
        "success": True,
        "liked": next_liked,
        "disliked": bool(current and current["disliked"]),
        "likes": stats["likes"],
        "dislikes": stats["dislikes"],
        "source": "local",
    }


@router.post("/stats-proxy/stats/like")
async def like_plugin(request: VoteRequest) -> dict[str, Any]:
    remote = await _remote_request("POST", "/stats/like", request.model_dump())
    if remote is not None:
        _sync_remote_response("/stats/like", remote, request.plugin_id, request.user_id)
        return remote
    return _local_toggle_like(request)


def _local_toggle_dislike(request: VoteRequest) -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_plugin(connection, request.plugin_id)
        current = connection.execute(
            "SELECT disliked, like_count FROM plugin_user_state WHERE plugin_id = ? AND user_id = ?",
            (request.plugin_id, request.user_id),
        ).fetchone()
        next_disliked = not bool(current and current["disliked"])
        connection.execute(
            """
            INSERT INTO plugin_user_state(plugin_id, user_id, disliked, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plugin_id, user_id) DO UPDATE SET
                disliked = excluded.disliked,
                updated_at = excluded.updated_at
            """,
            (request.plugin_id, request.user_id, int(next_disliked), _utc_now()),
        )
        connection.execute(
            "UPDATE plugin_stats SET dislikes = MAX(0, dislikes + ?) WHERE plugin_id = ?",
            (1 if next_disliked else -1, request.plugin_id),
        )
        stats = _stats_for_plugin(connection, request.plugin_id)
    return {
        "success": True,
        "liked": bool(current and current["like_count"]),
        "disliked": next_disliked,
        "likes": stats["likes"],
        "dislikes": stats["dislikes"],
        "source": "local",
    }


@router.post("/stats-proxy/stats/dislike")
async def dislike_plugin(request: VoteRequest) -> dict[str, Any]:
    remote = await _remote_request("POST", "/stats/dislike", request.model_dump())
    if remote is not None:
        _sync_remote_response("/stats/dislike", remote, request.plugin_id, request.user_id)
        return remote
    return _local_toggle_dislike(request)


def _local_rate(request: RatingRequest) -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_plugin(connection, request.plugin_id)
        current = connection.execute(
            "SELECT rating, comment FROM plugin_user_state WHERE plugin_id = ? AND user_id = ?",
            (request.plugin_id, request.user_id),
        ).fetchone()
        rating = request.rating if "rating" in request.model_fields_set else (current["rating"] if current else None)
        comment = request.comment if "comment" in request.model_fields_set else (current["comment"] if current else "")
        connection.execute(
            """
            INSERT INTO plugin_user_state(plugin_id, user_id, rating, comment, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plugin_id, user_id) DO UPDATE SET
                rating = excluded.rating,
                comment = excluded.comment,
                updated_at = excluded.updated_at
            """,
            (request.plugin_id, request.user_id, rating, str(comment or ""), _utc_now()),
        )
        aggregate = connection.execute(
            "SELECT COALESCE(AVG(rating), 0) AS rating, COUNT(rating) AS rating_count FROM plugin_user_state WHERE plugin_id = ?",
            (request.plugin_id,),
        ).fetchone()
        connection.execute(
            "UPDATE plugin_stats SET rating = ?, rating_count = ? WHERE plugin_id = ?",
            (float(aggregate["rating"] or 0), int(aggregate["rating_count"]), request.plugin_id),
        )
        stats = _stats_for_plugin(connection, request.plugin_id)
    return {
        "success": True,
        "user_rating": rating,
        "user_comment": str(comment or ""),
        "comment": str(comment or ""),
        "rating": stats["rating"],
        "rating_count": stats["rating_count"],
        "source": "local",
    }


@router.post("/stats-proxy/stats/rate")
async def rate_plugin(request: RatingRequest) -> dict[str, Any]:
    payload = request.model_dump(exclude_unset=True)
    if payload.get("rating") is None:
        payload.pop("rating", None)
    remote = await _remote_request("POST", "/stats/rate", payload)
    if remote is not None:
        _sync_remote_response("/stats/rate", remote, request.plugin_id, request.user_id)
        return remote
    return _local_rate(request)


@router.post("/stats-proxy/stats/download")
async def record_plugin_download(request: DownloadRequest) -> dict[str, Any]:
    remote = await _remote_request("POST", "/stats/download", request.model_dump())
    if remote is not None:
        _sync_remote_response("/stats/download", remote, request.plugin_id)
        return remote
    with _DB_LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_plugin(connection, request.plugin_id)
        connection.execute(
            "UPDATE plugin_stats SET downloads = downloads + 1 WHERE plugin_id = ?",
            (request.plugin_id,),
        )
        downloads = int(connection.execute(
            "SELECT downloads FROM plugin_stats WHERE plugin_id = ?", (request.plugin_id,)
        ).fetchone()["downloads"])
    return {"success": True, "counted": True, "downloads": downloads, "source": "local"}

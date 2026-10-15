"""MaiBot 首页资讯服务。

内容即内容目录下的 Markdown 文件：**一个文件一条资讯**。文件顶部可选 front matter
描述标题、来源、时间、置顶等元信息，正文即资讯正文（Markdown）。

设计要点：
- 只依赖 FastAPI / Pydantic，不引入 YAML 等额外依赖（front matter 语法由本服务约定）。
- 按文件 mtime + size 做进程内缓存：改完文件立即生效，不需要重启服务。
- 既能独立运行（``uvicorn news_app:app``），也能作为 router 挂到别的 FastAPI 应用上
  （线上与插件统计服务共用 10059 端口，见 README）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import logging
import os
import re

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

logger = logging.getLogger("maibot.news")

DEFAULT_CONTENT_DIR = Path(__file__).resolve().parent / "content"
CONTENT_DIR = Path(os.getenv("MAIBOT_NEWS_CONTENT_DIR", str(DEFAULT_CONTENT_DIR))).resolve()
DEFAULT_LIMIT = int(os.getenv("MAIBOT_NEWS_DEFAULT_LIMIT", "20"))
MAX_LIMIT = int(os.getenv("MAIBOT_NEWS_MAX_LIMIT", "50"))
SUMMARY_MAX_LENGTH = int(os.getenv("MAIBOT_NEWS_SUMMARY_MAX_LENGTH", "140"))
CACHE_MAX_AGE_SECONDS = int(os.getenv("MAIBOT_NEWS_CACHE_MAX_AGE_SECONDS", "60"))

_TRUE_VALUES = {"1", "true", "yes", "on", "是"}

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*\S)[ \t]*$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE)
_BULLET_LINE_RE = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_ORDERED_LINE_RE = re.compile(r"^[ \t]*\d+\.[ \t]+", re.MULTILINE)
_HR_LINE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)")
_WHITESPACE_RE = re.compile(r"\s+")


class NewsItem(BaseModel):
    """单条资讯。"""

    id: str = Field(..., description="资讯 ID（默认取文件名）")
    title: str = Field(..., description="标题")
    summary: str = Field("", description="摘要（未在 front matter 中提供时由正文首段生成）")
    content: str = Field("", description="正文 Markdown")
    source: str = Field("", description="来源")
    published_at: str = Field("", description="发布时间（ISO 8601）")
    pinned: bool = Field(False, description="是否置顶")
    url: str = Field("", description="相关链接")


class NewsListResponse(BaseModel):
    """资讯列表响应。"""

    success: bool = True
    updated_at: str = Field(..., description="本次读取时间（ISO 8601）")
    total: int = Field(..., description="资讯总数")
    items: list[NewsItem] = Field(default_factory=list, description="资讯列表")


class NewsServiceError(RuntimeError):
    """资讯内容目录不可用。"""


# 进程内缓存：文件路径 -> ((mtime_ns, size), (置顶, 时间戳, 资讯))
_CACHE: dict[str, tuple[tuple[int, int], tuple[bool, float, NewsItem]]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """拆分 front matter 与正文，只支持 ``key: value`` 形式。"""

    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text

    metadata: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        metadata[key.strip().lower()] = value.strip().strip('"').strip("'")

    return metadata, text[match.end() :]


def _parse_datetime(value: str | None) -> datetime | None:
    """解析 front matter 中的时间，支持 ``YYYY-MM-DD``、``YYYY/MM/DD`` 与 ISO 8601。"""

    if not value:
        return None

    normalized = value.strip().replace("/", "-")
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logger.warning("资讯时间格式无法解析，按文件修改时间处理: %s", value)
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _plain_text(markdown_text: str) -> str:
    """把 Markdown 正文压成纯文本，用于生成摘要。"""

    text = _CODE_BLOCK_RE.sub(" ", markdown_text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HEADING_LINE_RE.sub("", text)
    text = _QUOTE_LINE_RE.sub("", text)
    text = _BULLET_LINE_RE.sub("", text)
    text = _ORDERED_LINE_RE.sub("", text)
    text = _HR_LINE_RE.sub(" ", text)
    text = _EMPHASIS_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _build_summary(metadata: dict[str, str], body: str) -> str:
    """摘要优先取 front matter，缺省时用正文首段。"""

    declared = metadata.get("summary", "").strip()
    if declared:
        return declared

    plain = _plain_text(body)
    if len(plain) <= SUMMARY_MAX_LENGTH:
        return plain
    return f"{plain[:SUMMARY_MAX_LENGTH].rstrip()}…"


def _build_title(metadata: dict[str, str], body: str, fallback: str) -> str:
    """标题优先取 front matter，其次正文首个标题，最后退回文件名。"""

    declared = metadata.get("title", "").strip()
    if declared:
        return declared

    heading = _HEADING_RE.search(body)
    if heading:
        return _plain_text(heading.group(1)) or fallback
    return fallback


def _build_item(path: Path, text: str, mtime: float) -> tuple[bool, float, NewsItem]:
    metadata, body = _parse_front_matter(text)
    published = _parse_datetime(metadata.get("date") or metadata.get("published_at"))
    published_at = published or datetime.fromtimestamp(mtime, tz=timezone.utc)
    pinned = metadata.get("pinned", "").strip().lower() in _TRUE_VALUES
    item = NewsItem(
        id=metadata.get("id", "").strip() or path.stem,
        title=_build_title(metadata, body, path.stem),
        summary=_build_summary(metadata, body),
        content=body.strip(),
        source=metadata.get("source", "").strip(),
        published_at=published_at.isoformat(),
        pinned=pinned,
        url=metadata.get("url", "").strip(),
    )
    return pinned, published_at.timestamp(), item


def _load_item(path: Path) -> tuple[bool, float, NewsItem] | None:
    """读取单个资讯文件，按 mtime + size 命中缓存。"""

    try:
        stat = path.stat()
    except OSError as exc:
        logger.warning("资讯文件不可访问，已跳过 %s: %s", path, exc)
        return None

    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("资讯文件读取失败，已跳过 %s: %s", path, exc)
        return None

    entry = _build_item(path, text, stat.st_mtime)
    _CACHE[str(path)] = (signature, entry)
    return entry


def load_news_items() -> list[NewsItem]:
    """读取全部资讯，置顶优先、时间倒序。"""

    if not CONTENT_DIR.is_dir():
        raise NewsServiceError(f"资讯内容目录不存在: {CONTENT_DIR}")

    entries: list[tuple[bool, float, NewsItem]] = []
    known_paths: set[str] = set()
    for path in sorted(CONTENT_DIR.glob("*.md")):
        known_paths.add(str(path))
        entry = _load_item(path)
        if entry is not None:
            entries.append(entry)

    # 清理已被删除文件的缓存，避免长期运行后残留
    for stale_path in set(_CACHE) - known_paths:
        _CACHE.pop(stale_path, None)

    entries.sort(key=lambda entry: (0 if entry[0] else 1, -entry[1], entry[2].id))
    return [entry[2] for entry in entries]


def load_news_item(news_id: str) -> NewsItem | None:
    """按 ID 查找单条资讯。"""

    for item in load_news_items():
        if item.id == news_id:
            return item
    return None


router = APIRouter(prefix="/news")


@router.get("", response_model=NewsListResponse, summary="获取资讯列表")
@router.get("/", response_model=NewsListResponse, include_in_schema=False)
def get_news_list(
    response: Response,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="返回条数上限"),
    include_content: bool = Query(True, description="是否返回正文 Markdown"),
) -> NewsListResponse:
    """获取资讯列表。

    内容直接来自内容目录下的 Markdown 文件，改文件即生效，无需重启服务。
    """

    try:
        items = load_news_items()
    except NewsServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    total = len(items)
    limited = items[:limit]
    if not include_content:
        limited = [item.model_copy(update={"content": ""}) for item in limited]

    response.headers["Cache-Control"] = f"max-age={CACHE_MAX_AGE_SECONDS}"
    return NewsListResponse(updated_at=_utc_now_iso(), total=total, items=limited)


@router.get("/health", summary="健康检查")
def get_health() -> dict[str, Any]:
    """健康检查：返回内容目录与当前资讯条数。"""

    try:
        items = load_news_items()
    except NewsServiceError as exc:
        return {"success": False, "content_dir": str(CONTENT_DIR), "error": str(exc)}

    return {
        "success": True,
        "content_dir": str(CONTENT_DIR),
        "total": len(items),
        "updated_at": _utc_now_iso(),
    }


@router.get("/{news_id}", response_model=NewsItem, summary="获取单条资讯")
def get_news_item(news_id: str, request: Request) -> NewsItem:
    """按 ID 获取单条资讯正文。"""

    try:
        item = load_news_item(news_id.strip())
    except NewsServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if item is None:
        raise HTTPException(status_code=404, detail=f"资讯不存在: {news_id}")

    return item


# 独立运行时使用：uvicorn news_app:app --host 0.0.0.0 --port 10061
app = FastAPI(title="MaiBot News Server")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "news_app:app",
        host=os.getenv("MAIBOT_NEWS_HOST", "0.0.0.0"),
        port=int(os.getenv("MAIBOT_NEWS_PORT", "10061")),
        reload=False,
    )

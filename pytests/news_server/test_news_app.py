"""资讯服务单元测试：内容解析、排序、缓存与接口行为。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import importlib
import sys

import pytest
from fastapi.testclient import TestClient

NEWS_SERVER_DIR = Path(__file__).resolve().parents[2] / "news_server"
sys.path.insert(0, str(NEWS_SERVER_DIR))

import news_app  # noqa: E402


@pytest.fixture(name="client")
def client_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    monkeypatch.setattr(news_app, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(news_app, "_CACHE", {})
    return TestClient(news_app.app)


def write_news(content_dir: Path, name: str, text: str) -> Path:
    path = content_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_list_parses_front_matter_and_body(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    write_news(
        content_dir,
        "a.md",
        "---\ntitle: 标题甲\nsource: 来源甲\ndate: 2026-01-02T10:00:00+08:00\n---\n\n正文第一段。\n\n正文第二段。\n",
    )

    response = client.get("/news")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["id"] == "a"
    assert item["title"] == "标题甲"
    assert item["source"] == "来源甲"
    assert item["summary"] == "正文第一段。 正文第二段。"
    assert item["content"].startswith("正文第一段。")
    assert item["pinned"] is False
    assert datetime.fromisoformat(item["published_at"]).year == 2026


def test_title_falls_back_to_heading_then_filename(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    write_news(content_dir, "with-heading.md", "# 正文标题\n\n内容。\n")
    write_news(content_dir, "plain-name.md", "没有标题的正文。\n")

    items = {item["id"]: item for item in client.get("/news").json()["items"]}

    assert items["with-heading"]["title"] == "正文标题"
    assert items["plain-name"]["title"] == "plain-name"


def test_summary_declared_and_truncated(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    write_news(content_dir, "declared.md", "---\nsummary: 手写摘要\n---\n\n正文不一样。\n")
    write_news(content_dir, "long.md", f"{'字' * 400}\n")

    items = {item["id"]: item for item in client.get("/news").json()["items"]}

    assert items["declared"]["summary"] == "手写摘要"
    assert len(items["long"]["summary"]) <= news_app.SUMMARY_MAX_LENGTH + 1
    assert items["long"]["summary"].endswith("…")


def test_pinned_first_then_latest(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    write_news(content_dir, "old-pinned.md", "---\npinned: true\ndate: 2020-01-01\n---\n\n置顶。\n")
    write_news(content_dir, "newer.md", "---\ndate: 2026-05-01\n---\n\n较新。\n")
    write_news(content_dir, "newest.md", "---\ndate: 2026-06-01\n---\n\n最新。\n")

    ids = [item["id"] for item in client.get("/news").json()["items"]]

    assert ids == ["old-pinned", "newest", "newer"]


def test_limit_and_include_content(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    for index in range(5):
        write_news(content_dir, f"n{index}.md", f"---\ndate: 2026-01-0{index + 1}\n---\n\n正文 {index}。\n")

    trimmed = client.get("/news", params={"limit": 2, "include_content": False}).json()

    assert trimmed["total"] == 5
    assert len(trimmed["items"]) == 2
    assert all(item["content"] == "" for item in trimmed["items"])
    assert all(item["summary"] for item in trimmed["items"])


def test_single_item_and_404(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    write_news(content_dir, "solo.md", "---\ntitle: 单条\nurl: https://example.com\n---\n\n正文。\n")

    found = client.get("/news/solo")
    missing = client.get("/news/nope")

    assert found.status_code == 200
    assert found.json()["title"] == "单条"
    assert found.json()["url"] == "https://example.com"
    assert missing.status_code == 404


def test_cache_invalidated_on_file_change(client: TestClient, tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    path = write_news(content_dir, "cache.md", "---\ntitle: 旧标题\n---\n\n旧正文。\n")
    assert client.get("/news/cache").json()["title"] == "旧标题"

    path.write_text("---\ntitle: 新标题\n---\n\n新正文。\n", encoding="utf-8")

    assert client.get("/news/cache").json()["title"] == "新标题"


def test_health_and_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing_dir = tmp_path / "not-exist"
    monkeypatch.setattr(news_app, "CONTENT_DIR", missing_dir)
    monkeypatch.setattr(news_app, "_CACHE", {})
    client = TestClient(news_app.app)

    assert client.get("/news/health").json()["success"] is False
    assert client.get("/news").status_code == 503


def test_import_uses_env_content_dir_and_tolerates_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内容目录可配置；目录缺失时导入仍成功，接口返回 503 而不是崩溃。"""

    missing_dir = tmp_path / "not-exist-yet"
    monkeypatch.setenv("MAIBOT_NEWS_CONTENT_DIR", str(missing_dir))

    reloaded = importlib.reload(news_app)

    assert reloaded.CONTENT_DIR == missing_dir.resolve()
    assert TestClient(reloaded.app).get("/news").status_code == 503

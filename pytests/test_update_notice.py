from pathlib import Path

from src.common.update_notice import build_debug_update_notice


def test_build_debug_update_notice_uses_current_entry_and_previous_version(tmp_path: Path) -> None:
    changelog_path = tmp_path / "changelog.md"
    changelog_path.write_text(
        "# 更新日志\n\n"
        "# [1.1.0] - 2026-7-17\n\n当前版本内容\n\n"
        "# [1.0.12] - 2026-7-9\n\n上一个版本内容\n",
        encoding="utf-8",
    )

    notice = build_debug_update_notice("1.1.0", changelog_path)

    assert notice.current_version == "1.1.0"
    assert notice.from_version == "1.0.12"
    assert notice.versions == ["1.1.0"]
    assert "当前版本内容" in notice.content
    assert "上一个版本内容" not in notice.content


def test_build_debug_update_notice_handles_missing_changelog_entry(tmp_path: Path) -> None:
    notice = build_debug_update_notice("1.1.0", tmp_path / "missing.md")

    assert notice.from_version == "0.0.0"
    assert notice.versions == []
    assert "未找到可展示的更新日志条目" in notice.content

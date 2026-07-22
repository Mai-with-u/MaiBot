from pathlib import Path

import json

from src.plugin_runtime.docker_layout_migration import migrate_plugin_layout


def _write_plugin(source_root: Path, folder_name: str, plugin_id: str) -> Path:
    """创建迁移测试使用的最小插件源码目录。"""

    plugin_path = source_root / folder_name
    plugin_path.mkdir(parents=True)
    (plugin_path / "plugin.py").write_text("", encoding="utf-8")
    (plugin_path / "_manifest.json").write_text(json.dumps({"id": plugin_id}), encoding="utf-8")
    return plugin_path


def test_migrate_clearly_identified_legacy_data_directory(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")
    old_data_path = source_root / "SugarJelly.trace_plugin"
    old_data_path.mkdir()
    (old_data_path / "records.json").write_text("[]", encoding="utf-8")

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.moved == ["SugarJelly.trace_plugin"]
    assert not old_data_path.exists()
    assert (data_root / "SugarJelly.trace_plugin" / "records.json").read_text(encoding="utf-8") == "[]"
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["completed"] is True


def test_skip_source_directory_whose_name_equals_plugin_id(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    plugin_path = _write_plugin(source_root, "grok-search-plugin", "grok-search-plugin")
    (plugin_path / "runtime.db").write_text("legacy", encoding="utf-8")

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.moved == []
    assert report.skipped_ambiguous[0]["plugin_id"] == "grok-search-plugin"
    assert (plugin_path / "runtime.db").read_text(encoding="utf-8") == "legacy"
    assert not (data_root / "grok-search-plugin").exists()


def test_skip_old_candidate_that_contains_source_marker(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")
    ambiguous_path = source_root / "SugarJelly.trace_plugin"
    ambiguous_path.mkdir()
    (ambiguous_path / "plugin.py").write_text("", encoding="utf-8")

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.moved == []
    assert report.skipped_ambiguous[0]["plugin_id"] == "SugarJelly.trace_plugin"
    assert ambiguous_path.exists()


def test_move_old_data_when_existing_destination_is_empty(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")
    old_data_path = source_root / "SugarJelly.trace_plugin"
    old_data_path.mkdir()
    (old_data_path / "records.json").write_text("old", encoding="utf-8")
    (data_root / "SugarJelly.trace_plugin").mkdir(parents=True)

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.moved == ["SugarJelly.trace_plugin"]
    assert (data_root / "SugarJelly.trace_plugin" / "records.json").read_text(encoding="utf-8") == "old"


def test_remove_empty_old_directory_when_destination_has_data(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")
    old_data_path = source_root / "SugarJelly.trace_plugin"
    old_data_path.mkdir()
    new_data_path = data_root / "SugarJelly.trace_plugin"
    new_data_path.mkdir(parents=True)
    (new_data_path / "records.json").write_text("new", encoding="utf-8")

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.removed_empty == ["SugarJelly.trace_plugin"]
    assert not old_data_path.exists()
    assert (new_data_path / "records.json").read_text(encoding="utf-8") == "new"


def test_skip_when_old_and_new_data_directories_are_nonempty(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")
    old_data_path = source_root / "SugarJelly.trace_plugin"
    old_data_path.mkdir()
    (old_data_path / "records.json").write_text("old", encoding="utf-8")
    new_data_path = data_root / "SugarJelly.trace_plugin"
    new_data_path.mkdir(parents=True)
    (new_data_path / "records.json").write_text("new", encoding="utf-8")

    report = migrate_plugin_layout(source_root, data_root, report_path)

    assert report is not None
    assert report.moved == []
    assert report.conflicts[0]["plugin_id"] == "SugarJelly.trace_plugin"
    assert (old_data_path / "records.json").read_text(encoding="utf-8") == "old"
    assert (new_data_path / "records.json").read_text(encoding="utf-8") == "new"


def test_skip_migration_when_source_and_data_roots_are_same(tmp_path: Path) -> None:
    shared_root = tmp_path / "plugins"
    report_path = tmp_path / "data" / "migration.json"
    shared_root.mkdir()

    report = migrate_plugin_layout(shared_root, shared_root, report_path)

    assert report is None
    assert not report_path.exists()


def test_completed_report_makes_migration_one_time(tmp_path: Path) -> None:
    source_root = tmp_path / "plugins"
    data_root = tmp_path / "plugin-data"
    report_path = tmp_path / "data" / "migration.json"
    source_root.mkdir()
    _write_plugin(source_root, "trace_plugin", "SugarJelly.trace_plugin")

    first_report = migrate_plugin_layout(source_root, data_root, report_path)
    old_data_path = source_root / "SugarJelly.trace_plugin"
    old_data_path.mkdir()
    second_report = migrate_plugin_layout(source_root, data_root, report_path)

    assert first_report is not None
    assert second_report is not None
    assert second_report.moved == first_report.moved
    assert old_data_path.exists()

"""统计缓存文件：按实例目录隔离、原子落盘、从 local_store 迁移、重启后读回详细统计快照。"""

import json

import pytest

from src.services import statistics_cache_files, statistics_service
from src.services.statistics_cache_files import StatisticsCacheFile
from src.webui.schemas.statistics import DetailedStatisticsData


class _FakeLocalStore(dict):
    """只在内存里的 local_storage，避免测试读写仓库里的 data/local_store.json。"""


@pytest.fixture
def instance_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    return tmp_path


def test_save_writes_compact_file_under_instance_directory(instance_dir):
    cache = StatisticsCacheFile("demo.json")

    assert cache.save({"value": 1, "名称": "麦麦"})

    path = instance_dir / "data" / "statistics" / "demo.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1, "名称": "麦麦"}
    # 紧凑 JSON，不带缩进
    assert "\n" not in path.read_text(encoding="utf-8")
    # 临时文件已经原子替换掉，不会残留
    assert [item.name for item in path.parent.iterdir()] == ["demo.json"]


def test_each_instance_reads_its_own_file(tmp_path, monkeypatch):
    first, second = tmp_path / "bot-a", tmp_path / "bot-b"
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(first))
    StatisticsCacheFile("demo.json").save({"bot": "a"})
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(second))
    StatisticsCacheFile("demo.json").save({"bot": "b"})

    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(first))
    assert StatisticsCacheFile("demo.json").load() == {"bot": "a"}
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(second))
    assert StatisticsCacheFile("demo.json").load() == {"bot": "b"}


def test_load_keeps_value_in_memory_and_reloads_after_clear(instance_dir):
    cache = StatisticsCacheFile("demo.json")
    cache.save({"round": 1})
    (instance_dir / "data" / "statistics" / "demo.json").write_text('{"round": 2}', encoding="utf-8")

    assert cache.load() == {"round": 1}
    cache.clear_memory()
    assert cache.load() == {"round": 2}


def test_corrupt_file_is_treated_as_missing_cache(instance_dir):
    path = instance_dir / "data" / "statistics" / "demo.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"broken"', encoding="utf-8")

    assert StatisticsCacheFile("demo.json").load() is None


def test_legacy_local_store_value_is_migrated_then_removed(instance_dir, monkeypatch):
    legacy = _FakeLocalStore(last_full_statistics={"timestamp": 1.0}, deploy_time=2.0)
    monkeypatch.setattr("src.manager.local_store_manager.local_storage", legacy)
    cache = StatisticsCacheFile("full.json", legacy_key="last_full_statistics")

    assert cache.load() == {"timestamp": 1.0}
    assert "last_full_statistics" not in legacy
    assert legacy["deploy_time"] == 2.0
    assert json.loads((instance_dir / "data" / "statistics" / "full.json").read_text(encoding="utf-8")) == {
        "timestamp": 1.0
    }


def test_legacy_value_is_kept_when_file_cannot_be_written(instance_dir, monkeypatch):
    legacy = _FakeLocalStore(last_full_statistics={"timestamp": 1.0})
    monkeypatch.setattr("src.manager.local_store_manager.local_storage", legacy)
    # data/statistics 被一个普通文件占住，目录建不出来
    (instance_dir / "data").mkdir()
    (instance_dir / "data" / "statistics").write_text("", encoding="utf-8")
    cache = StatisticsCacheFile("full.json", legacy_key="last_full_statistics")

    assert cache.load() == {"timestamp": 1.0}
    assert legacy["last_full_statistics"] == {"timestamp": 1.0}


def test_detailed_snapshot_survives_restart(instance_dir, monkeypatch):
    cache = StatisticsCacheFile("detailed_snapshot.json")
    monkeypatch.setattr(statistics_service, "detailed_statistics_cache", cache)
    monkeypatch.setattr(statistics_service, "_detailed_statistics_snapshot", None)
    snapshot = DetailedStatisticsData(generated_at="2026-11-05T18:00:00", periods=[], trends={}, metrics={})

    statistics_service.store_detailed_statistics_snapshot(snapshot)

    # 模拟重启：进程内的快照和缓存都清空，只剩磁盘上的文件
    monkeypatch.setattr(statistics_service, "_detailed_statistics_snapshot", None)
    cache.clear_memory()
    assert statistics_service.get_detailed_statistics_snapshot() == snapshot


def test_default_cache_files_use_expected_names():
    assert statistics_cache_files.full_statistics_cache.legacy_key == "last_full_statistics"
    assert statistics_cache_files.dashboard_statistics_cache.legacy_key == "webui_dashboard_statistics_cache"
    assert statistics_cache_files.detailed_statistics_cache.filename == "detailed_snapshot.json"

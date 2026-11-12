from pathlib import Path
from typing import Any, Dict, List, Tuple

import copy
import hashlib
import tomllib

import pytest
import tomlkit

from src.common import atomic_file
from src.config.shared_models import CATALOG_VERSION, merge_catalog, read_catalog
from src.host import shared_models
from src.host.registry import Registry
from src.host.shared_models import save_effective_config


@pytest.fixture
def workspace(tmp_path: Path) -> Tuple[Path, List[Path], Dict[str, Any]]:
    registry = Registry(tmp_path)
    registry.create()
    catalog_path = tmp_path / "host" / "model_catalog.toml"
    catalog = {
        "inner": {"version": CATALOG_VERSION},
        "api_providers": [{"name": name} for name in ("removed-provider", "kept-provider")],
        "models": [
            {"name": name, "api_provider": provider, "model_identifier": name, "model_type": "llm"}
            for name, provider in (
                ("removed-model", "removed-provider"),
                ("removed-model-2", "removed-provider"),
                ("kept-model", "kept-provider"),
            )
        ],
        "web": {"services": {"removed-model": {"api_key": "test"}}},
    }
    catalog_path.write_text(tomlkit.dumps(catalog), encoding="utf-8")
    paths = []
    for name in ("current", "stopped", "unaffected"):
        directory = tmp_path / "bots" / name
        path = directory / "config" / "model_config.toml"
        path.parent.mkdir(parents=True)
        bot = registry.add_bot(name, directory, initialization="initialized", owned=True)
        if name == "stopped":
            registry.set_desired_state(bot.bot_id, "stopped")
        references = '["kept-model"]' if name == "unaffected" else (
            '["removed-model", "kept-model", "removed-model-2", "removed-model"]'
        )
        path.write_text(
            '# instance comment\n[inner]\nversion = "1.0.0"\n'
            '[model_task_config.replyer]\n'
            f'model_list = {references} # keep this comment\n'
            'temperature = 0.42\n'
            '[model_task_config.planner.nested]\nmodel_list = ["removed-model", "already-missing"]\n'
            '[model_task_config.web_search]\nmodel_list = ["removed-model"]\n',
            encoding="utf-8",
        )
        paths.append(path)
    submitted = merge_catalog(tomllib.loads(paths[0].read_text(encoding="utf-8")), catalog)
    return catalog_path, paths, submitted


@pytest.mark.parametrize("deletion", ["model", "provider", "provider-with-models"])
def test_deletion_cleans_all_instances_in_one_commit(workspace, monkeypatch, deletion):
    catalog_path, paths, submitted = workspace
    if deletion == "model":
        submitted["models"] = [item for item in submitted["models"] if item["name"] != "removed-model"]
    else:
        submitted["api_providers"] = [item for item in submitted["api_providers"] if item["name"] != "removed-provider"]
        if deletion == "provider-with-models":
            submitted["models"] = [item for item in submitted["models"] if item["api_provider"] != "removed-provider"]
    submitted["model_task_config"]["replyer"]["temperature"] = 0.8
    original_submission = copy.deepcopy(submitted)
    unaffected = paths[2].read_bytes()
    # 未受影响实例不应参加事务，也不应因解析和序列化改写格式。
    document = tomlkit.parse(unaffected.decode("utf-8"))
    document["model_task_config"]["planner"]["nested"]["model_list"] = ["already-missing"]
    paths[2].write_text(tomlkit.dumps(document), encoding="utf-8")
    unaffected = paths[2].read_bytes()
    commit = shared_models.commit_config_files
    commits = []

    def record_commit(host, files):
        commits.append(set(files))
        commit(host, files)

    monkeypatch.setattr(shared_models, "commit_config_files", record_commit)
    revision = save_effective_config(catalog_path, paths[0], submitted, None)

    assert commits == [{catalog_path, paths[0], paths[1]}]
    assert revision == hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    assert submitted == original_submission
    assert paths[2].read_bytes() == unaffected
    for index, path in enumerate(paths[:2]):
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        tasks = data["model_task_config"]
        expected = ["kept-model", "removed-model-2"] if deletion == "model" else ["kept-model"]
        assert tasks["replyer"]["model_list"] == expected
        assert tasks["replyer"]["temperature"] == (0.8 if index == 0 else 0.42)
        assert tasks["planner"]["nested"]["model_list"] == ["already-missing"]
        assert tasks["web_search"]["model_list"] == ["removed-model"]
        assert "# instance comment" in text
        if index != 0:
            assert "# keep this comment" in text
    catalog = read_catalog(catalog_path)
    assert {item["name"] for item in catalog["models"]} == (
        {"kept-model", "removed-model-2"} if deletion == "model" else {"kept-model"}
    )


@pytest.mark.parametrize("failed_file", ["catalog", "current", "other"])
def test_deletion_rolls_back_every_file_on_write_failure(workspace, monkeypatch, failed_file):
    catalog_path, paths, submitted = workspace
    originals = {path: path.read_bytes() for path in [catalog_path, *paths]}
    submitted["models"] = [item for item in submitted["models"] if item["name"] != "removed-model"]
    target_path = {"catalog": catalog_path, "current": paths[0], "other": paths[1]}[failed_file]
    replace = atomic_file._replace_once
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if target == target_path and not failed:
            failed = True
            raise OSError("interrupted deletion")
        replace(source, target)

    monkeypatch.setattr(atomic_file, "_replace_once", fail_once)
    with pytest.raises(OSError, match="interrupted deletion"):
        save_effective_config(catalog_path, paths[0], submitted, None)
    assert failed
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert not (catalog_path.parent / "model-config-transaction.json").exists()


@pytest.mark.parametrize("invalid_change", ["new-reference", "web-service", "revision"])
def test_invalid_save_does_not_write_any_files(workspace, invalid_change):
    catalog_path, paths, submitted = workspace
    originals = {path: path.read_bytes() for path in [catalog_path, *paths]}
    submitted["models"] = [item for item in submitted["models"] if item["name"] != "removed-model"]
    revision = None
    if invalid_change == "new-reference":
        submitted["model_task_config"]["replyer"]["model_list"].append("new-missing-model")
    elif invalid_change == "web-service":
        submitted["web"]["services"] = {}
    else:
        revision = "stale-revision"
    expected_error = {
        "new-reference": "new-missing-model",
        "web-service": "联网任务引用的服务",
        "revision": "公共模型配置已改变",
    }[invalid_change]
    with pytest.raises(ValueError, match=expected_error):
        save_effective_config(catalog_path, paths[0], submitted, revision)
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_deleting_last_model_leaves_empty_task_lists(workspace):
    catalog_path, paths, submitted = workspace
    submitted["models"] = []
    save_effective_config(catalog_path, paths[0], submitted, None)
    assert read_catalog(catalog_path)["models"] == []
    for path in paths:
        tasks = tomllib.loads(path.read_text(encoding="utf-8"))["model_task_config"]
        assert tasks["replyer"]["model_list"] == []
        assert tasks["planner"]["nested"]["model_list"] == ["already-missing"]
        assert tasks["web_search"]["model_list"] == ["removed-model"]

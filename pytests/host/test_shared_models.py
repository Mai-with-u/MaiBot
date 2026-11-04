from pathlib import Path


import pytest
import tomlkit

from src.config.shared_models import CATALOG_VERSION, SHARED_MODELS_ENV, merge_catalog


def migrate_test_workspace(root, bots):
    """模型用例也走完整工作区迁移，补齐无关的最小旧配置。"""
    from src.host.workspace_migration import migrate_workspace_config
    host = root / "host"
    host.mkdir(exist_ok=True)
    config = host / "config.toml"
    if not config.exists():
        config.write_text("[webui]\n", encoding="utf-8")
    for bot in bots:
        path = bot.directory / "config" / "bot_config.toml"
        if not path.exists():
            path.write_text("", encoding="utf-8")
    migrate_workspace_config(root, bots)


def catalog():
    return {"inner": {"version": CATALOG_VERSION}, "api_providers": [{"name": "shared", "base_url": "https://example.com/v1", "api_key": "secret"}],
            "models": [{"name": "shared-model", "api_provider": "shared", "model_identifier": "example", "model_type": "llm"}]}


def test_configuration_upgrade_does_not_materialize_shared_entries(tmp_path: Path, monkeypatch):
    from src.config.config import MODEL_CONFIG_PATH, MODEL_CONFIG_VERSION, ModelConfig, load_config_from_file

    doc = tomlkit.parse(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    shared = {"inner": {"version": CATALOG_VERSION}, "models": doc["models"], "api_providers": doc["api_providers"]}
    shared_path = tmp_path / "model_catalog.toml"
    shared_path.write_text(tomlkit.dumps(shared), encoding="utf-8")
    monkeypatch.setenv(SHARED_MODELS_ENV, str(shared_path))
    doc["models"] = []
    doc["api_providers"] = []
    doc["inner"]["version"] = "1.17.24"
    local_path = tmp_path / "model_config.toml"
    local_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    loaded, _ = load_config_from_file(ModelConfig, local_path, MODEL_CONFIG_VERSION, True)
    assert loaded.models
    saved = tomlkit.parse(local_path.read_text(encoding="utf-8"))
    assert not saved.get("models") and not saved.get("api_providers")
    reloaded, _ = load_config_from_file(ModelConfig, local_path, MODEL_CONFIG_VERSION, True)
    assert [item.name for item in reloaded.models] == [item.name for item in loaded.models]


def test_global_model_edit_updates_catalog_and_preserves_other_bot_tasks(tmp_path):
    from src.host.registry import Registry
    from src.host.shared_models import _revision, save_effective_config

    registry = Registry(tmp_path)
    registry.create()
    bots = []
    for name in ['a', 'b']:
        directory = tmp_path / name
        (directory / 'config').mkdir(parents=True)
        data = catalog()
        data['model_task_config'] = {'planner': {'model_list': ['shared-model'], 'temperature': 0.5}}
        (directory / 'config' / 'model_config.toml').write_text(tomlkit.dumps(data), encoding='utf-8')
        bots.append(registry.add_bot(name, directory, owned=True, initialization='initialized'))
    migrate_test_workspace(tmp_path, bots)
    shared_path = tmp_path / 'host' / 'model_catalog.toml'
    first, second = [bot.directory / 'config' / 'model_config.toml' for bot in bots]
    other_before = second.read_bytes()
    submitted = catalog()
    submitted['api_providers'][0]['api_key'] = 'rotated'
    submitted['model_task_config'] = {'planner': {'model_list': ['shared-model'], 'temperature': 0.7}}
    revision = _revision(shared_path)
    new_revision = save_effective_config(shared_path, first, submitted, revision)
    assert new_revision != revision
    assert second.read_bytes() == other_before
    assert 'rotated' in shared_path.read_text(encoding='utf-8')
    assert 'rotated' not in first.read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='重新加载'):
        save_effective_config(shared_path, first, submitted, revision)
    submitted['models'] = []
    submitted['model_task_config']['planner']['model_list'] = []
    with pytest.raises(ValueError, match='b:'):
        save_effective_config(shared_path, first, submitted, new_revision)
    assert _revision(shared_path) == new_revision


def test_multiple_existing_bots_keep_distinct_models(tmp_path):
    from src.host.registry import Registry

    registry = Registry(tmp_path)
    registry.create()
    bots = []
    for name in ['first', 'second']:
        directory = tmp_path / name
        (directory / 'config').mkdir(parents=True)
        data = catalog()
        data['api_providers'][0]['api_key'] = name
        data['model_task_config'] = {'planner': {'model_list': ['shared-model']}}
        (directory / 'config' / 'model_config.toml').write_text(tomlkit.dumps(data), encoding='utf-8')
        bots.append(registry.add_bot(name, directory, owned=True, initialization='initialized'))
    migrate_test_workspace(tmp_path, bots)
    shared_path = tmp_path / 'host' / 'model_catalog.toml'
    shared = tomlkit.parse(shared_path.read_text(encoding='utf-8')).unwrap()
    assert len(shared['api_providers']) == len(shared['models']) == 2
    for bot in bots:
        local = tomlkit.parse((bot.directory / 'config' / 'model_config.toml').read_text(encoding='utf-8')).unwrap()
        model_name = local['model_task_config']['planner']['model_list'][0]
        model = next(item for item in shared['models'] if item['name'] == model_name)
        provider = next(item for item in shared['api_providers'] if item['name'] == model['api_provider'])
        assert provider['api_key'] == bot.name

def test_web_services_migrate_share_and_protect_other_bot_assignments(tmp_path):
    from src.config.shared_models import read_catalog
    from src.host.registry import Registry
    from src.host.shared_models import save_effective_config, _revision

    registry = Registry(tmp_path)
    registry.create()
    bots = []
    for name in ["first", "second"]:
        directory = tmp_path / name
        (directory / "config").mkdir(parents=True)
        data = {"inner": {"version": "1.17.17"},
                "web": {"preview_chars": 2000, "services": {
                    "search": {"adapter": "tavily", "api_key": name}}},
                "model_task_config": {"web_search": {"model_list": ["search"]}}}
        (directory / "config" / "model_config.toml").write_text(tomlkit.dumps(data), encoding="utf-8")
        bots.append(registry.add_bot(name, directory, owned=True, initialization="initialized"))
    path = tmp_path / "host" / "model_catalog.toml"
    old = catalog()
    old["inner"]["version"] = CATALOG_VERSION
    path.write_text(tomlkit.dumps(old), encoding="utf-8")
    migrate_test_workspace(tmp_path, bots)
    shared = read_catalog(path)
    assert len(shared["web"]["services"]) == 2
    locals_ = []
    for bot in bots:
        local = tomlkit.parse((bot.directory / "config" / "model_config.toml").read_text(encoding="utf-8")).unwrap()
        assert local["web"] == {"preview_chars": 2000}
        effective = merge_catalog(local, shared)
        selected = local["model_task_config"]["web_search"]["model_list"][0]
        assert effective["web"]["services"][selected]["api_key"] == bot.name
        locals_.append(local)
    submitted = merge_catalog(locals_[0], shared)
    other_service = locals_[1]["model_task_config"]["web_search"]["model_list"][0]
    submitted["web"]["services"][other_service]["api_key"] = "updated"
    local_path = bots[0].directory / "config" / "model_config.toml"
    save_effective_config(path, local_path, submitted, _revision(path))
    assert merge_catalog(locals_[1], read_catalog(path))["web"]["services"][other_service]["api_key"] == "updated"
    del submitted["web"]["services"][other_service]
    with pytest.raises(ValueError, match="second"):
        save_effective_config(path, local_path, submitted, _revision(path))
    before = path.read_bytes()
    migrate_test_workspace(tmp_path, bots)
    assert path.read_bytes() == before

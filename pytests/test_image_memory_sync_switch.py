from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import asyncio
import sys

import pytest


@pytest.fixture
def image_module(monkeypatch, tmp_path):
    config = SimpleNamespace(
        a_memorix=SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            image_memory=SimpleNamespace(enabled=True),
        )
    )
    memory = SimpleNamespace(image_memory=AsyncMock(return_value={"success": True}))
    notify = Mock()
    # 加载完整图片模块，只替换宿主依赖，避免启动模型或连接实际数据库。
    hosts = {
        "sqlmodel": {"select": Mock()},
        "src.common.data_models.image_data_model": {"MaiImage": object},
        "src.common.database.database": {"get_db_session": MagicMock()},
        "src.common.database.database_model": {"Images": object, "ImageType": SimpleNamespace(IMAGE="image")},
        "src.common.logger": {"get_logger": Mock(return_value=Mock())},
        "src.config.config": {"config_manager": Mock(), "global_config": config},
        "src.prompt.prompt_manager": {"prompt_manager": Mock()},
        "src.services.llm_service": {"LLMServiceClient": Mock()},
        "src.services.memory_service": {"memory_service": memory},
        "src.maisaka.visual.chat_history_refresher": {"log_tracked_image_recognition_completed": notify},
    }
    monkeypatch.setenv("MAIBOT_INSTANCE_DIR", str(tmp_path))
    for name, attributes in hosts.items():
        dependency = ModuleType(name)
        dependency.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, dependency)
    name = "src.chat.image_system._memory_sync_switch_test"
    path = Path(__file__).resolve().parents[1] / "src/chat/image_system/image_manager.py"
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module, config, memory, notify


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_enabled,image_enabled", [(False, False), (False, True), (True, False), (True, True)])
async def test_description_sync_respects_memory_switches(image_module, monkeypatch, plugin_enabled, image_enabled):
    module, config, memory, notify = image_module
    config.a_memorix.plugin.enabled = plugin_enabled
    config.a_memorix.image_memory.enabled = image_enabled
    manager = module.image_manager
    lookup = Mock(return_value=SimpleNamespace(description="cached caption"))
    monkeypatch.setattr(manager, "_get_image_record", lookup)
    image_hash = "image-with-description"
    build = asyncio.create_task(asyncio.sleep(0))
    await build
    manager._pending_description_tasks[image_hash] = build

    manager._finalize_description_build(image_hash, build)
    sync_tasks = list(manager._description_sync_tasks)
    await asyncio.gather(*sync_tasks)

    if plugin_enabled and image_enabled:
        assert len(sync_tasks) == 1
        memory.image_memory.assert_awaited_once_with(
            action="describe", content_hash=image_hash, text="[图片：cached caption]"
        )
    else:
        assert sync_tasks == []
        memory.image_memory.assert_not_called()
        lookup.assert_not_called()
    notify.assert_called_once_with(image_hash)
    assert image_hash not in manager._pending_description_tasks
    module.logger.warning.assert_not_called()

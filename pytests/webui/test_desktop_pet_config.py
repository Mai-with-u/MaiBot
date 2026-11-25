from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.desktop_pet import conversation
from src.webui.routers import config as config_routes


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [0, None, [], "x" * 201])
async def test_desktop_pet_config_rejects_invalid_present_binding(monkeypatch, session_id):
    update = Mock()
    monkeypatch.setattr(config_routes, "update_bot_config_section", update)

    with pytest.raises(HTTPException) as error:
        await config_routes.update_desktop_pet_config({"conversation_session_id": session_id})

    assert error.value.status_code == 422
    update.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_pet_config_persists_resolved_session_id(monkeypatch):
    saved = []
    monkeypatch.setattr(
        conversation,
        "resolve_conversation",
        lambda session_id: SimpleNamespace(session_id="canonical-session"),
    )

    def update(section, data):
        saved.append((section, data))
        return {"success": True}

    async def run_direct(coroutine):
        return await coroutine

    monkeypatch.setattr(config_routes, "update_bot_config_section", update)
    monkeypatch.setattr(config_routes.config_manager, "reload_config", AsyncMock(return_value=True))
    monkeypatch.setattr(config_routes, "run_on_main_loop", run_direct)

    result = await config_routes.update_desktop_pet_config(
        {
            "conversation_session_id": "  selected-session  ",
            "enabled": True,
        }
    )

    assert result == {"success": True}
    assert saved == [("desktop_pet", {"conversation_session_id": "canonical-session", "enabled": True})]

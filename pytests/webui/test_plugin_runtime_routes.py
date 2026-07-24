from src.plugin_runtime.host.component_registry import ComponentTypes, HomeCardEntry
from src.webui.routers.plugin.runtime_routes import _serialize_home_card_entry


def test_home_card_title_visibility_defaults_to_visible() -> None:
    component = HomeCardEntry(
        name="default_card",
        component_type=ComponentTypes.HOME_CARD.value,
        plugin_id="test_plugin",
        metadata={"title": "默认卡片"},
    )

    assert _serialize_home_card_entry(component)["show_title"] is True


def test_home_card_title_can_be_hidden() -> None:
    component = HomeCardEntry(
        name="titleless_card",
        component_type=ComponentTypes.HOME_CARD.value,
        plugin_id="test_plugin",
        metadata={"title": "管理卡片", "show_title": False},
    )

    payload = _serialize_home_card_entry(component)

    assert payload["title"] == "管理卡片"
    assert payload["show_title"] is False

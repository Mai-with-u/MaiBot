from importlib import import_module

from src.config.config import config_manager

_CLIENT_MODULE_BY_TYPE: dict[str, str] = {
    "openai": ".openai_client",
    "gemini": ".gemini_client",
}

_LOADED_CLIENT_TYPES: set[str] = set()


def ensure_client_type_loaded(client_type: str) -> None:
    if client_type in _LOADED_CLIENT_TYPES:
        return
    module_name = _CLIENT_MODULE_BY_TYPE.get(client_type)
    if not module_name:
        return
    import_module(module_name, package=__name__)
    _LOADED_CLIENT_TYPES.add(client_type)


def ensure_configured_clients_loaded() -> None:
    for provider in config_manager.get_model_config().api_providers:
        ensure_client_type_loaded(provider.client_type)


ensure_configured_clients_loaded()

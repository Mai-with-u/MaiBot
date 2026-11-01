from pathlib import Path
from types import SimpleNamespace

from src.plugin_runtime.integration import PluginRuntimeManager


def test_registered_paths_allow_registration_during_path_resolution():
    registered = {"first": object(), "second": object()}
    supervisor = SimpleNamespace(_registered_plugins=registered)

    def resolve(_supervisor, plugin_id):
        registered["new"] = object()
        return Path(plugin_id)

    manager = SimpleNamespace(supervisors=[supervisor], _get_plugin_path_for_supervisor=resolve)
    paths = list(PluginRuntimeManager._iter_registered_plugin_paths(manager))
    assert paths == [("first", Path("first")), ("second", Path("second"))]

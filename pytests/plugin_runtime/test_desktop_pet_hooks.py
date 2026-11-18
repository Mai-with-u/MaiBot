from src.desktop_pet.hooks import register_desktop_pet_hook_specs
from src.plugin_runtime.host.hook_spec_registry import HookSpecRegistry


def test_desktop_pet_hooks_are_observe_only():
    specs = register_desktop_pet_hook_specs(HookSpecRegistry())
    assert {spec.name for spec in specs} == {
        "desktop_pet.ready", "desktop_pet.closed", "desktop_pet.clicked", "desktop_pet.drag_started", "desktop_pet.drag_ended",
        "desktop_pet.visibility_changed", "desktop_pet.skin_changed",
    }
    assert all(spec.allow_observe and not spec.allow_blocking for spec in specs)
    assert all(not spec.allow_abort and not spec.allow_kwargs_mutation for spec in specs)

"""桌宠对插件公开的观察型事件。"""

from typing import List

from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry


def register_desktop_pet_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    events = {
        "ready": ("桌宠窗口已就绪", {}),
        "closed": ("桌宠窗口已退出", {"reason": {"type": "string"}}),
        "clicked": ("用户点击桌宠", {}),
        "drag_started": ("用户开始实际拖动桌宠", {
            "gesture_id": {"type": "string"},
            "offset_x": {"type": "integer"},
            "offset_y": {"type": "integer"},
            "scale": {"type": "number"},
        }),
        "drag_ended": ("用户结束或取消拖动桌宠", {"gesture_id": {"type": "string"}}),
        "visibility_changed": ("桌宠可见状态改变", {"visible": {"type": "boolean"}}),
        "skin_changed": ("桌宠皮肤已切换", {"skin_id": {"type": "string"}}),
    }
    return registry.register_hook_specs([
        HookSpec(
            name=f"desktop_pet.{name}", description=description,
            parameters_schema={"type": "object", "properties": properties, "required": list(properties)},
            default_timeout_ms=5000, allow_blocking=False, allow_observe=True,
            allow_abort=False, allow_kwargs_mutation=False,
        )
        for name, (description, properties) in events.items()
    ])

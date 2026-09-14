from __future__ import annotations

from typing import Any, Iterator, Sequence, Tuple


def build_chat_external_ref(*, chat_id: str, message_id: str, component_path: str) -> str:
    return f"chat:{chat_id}:{message_id}:{component_path}"


def build_package_external_ref(*, installation_id: str, occurrence_id: str) -> str:
    return f"package:{installation_id}:{occurrence_id}"


def iter_message_image_components(
    components: Sequence[Any],
    *,
    include_emoji: bool = False,
    prefix: str = "",
) -> Iterator[Tuple[str, Any]]:
    """按持久消息树的稳定下标生成图片组件路径。"""

    for index, component in enumerate(components):
        path = f"{prefix}.{index}" if prefix else str(index)
        component_type = str(getattr(component, "type", "") or "").lower()
        class_name = type(component).__name__.lower()
        is_image = component_type == "image" or class_name == "imagecomponent"
        is_emoji = component_type == "emoji" or "emoji" in class_name
        if is_image or (include_emoji and is_emoji):
            yield path, component
        nested = getattr(component, "forward_components", None)
        if nested is None:
            nested = getattr(component, "components", None)
        if nested is None:
            nested = getattr(component, "content", None)
        if isinstance(nested, (list, tuple)):
            yield from iter_message_image_components(nested, include_emoji=include_emoji, prefix=path)

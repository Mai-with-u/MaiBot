from src.common.data_models.message_component_data_model import MessageSequence, TextComponent


def extract_learning_text(sequence: MessageSequence) -> str:
    """只取发送者本次顶层文字，不渲染或解析引用、转发、媒体等组件。"""

    return " ".join(
        component.text for component in sequence.components if isinstance(component, TextComponent)
    ).strip()

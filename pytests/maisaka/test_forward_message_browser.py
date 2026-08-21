from src.common.data_models.message_component_data_model import (
    ForwardComponent,
    ForwardNodeComponent,
    MessageSequence,
    TextComponent,
)
from src.maisaka.context.messages import (
    _build_complex_message_prompt_text,
    build_full_complex_message_content_from_sequence,
)


def _forward_node(
    sender: str,
    message_id: str,
    *components: TextComponent | ForwardNodeComponent,
) -> ForwardNodeComponent:
    return ForwardNodeComponent(
        [
            ForwardComponent(
                user_nickname=sender,
                message_id=message_id,
                content=list(components),
            )
        ]
    )


def test_full_complex_message_returns_path_instead_of_expanding_nested_forward() -> None:
    inner_forward = _forward_node("内层用户", "inner", TextComponent("内层正文"))
    outer_forward = _forward_node("外层用户", "outer", inner_forward)

    content = build_full_complex_message_content_from_sequence(MessageSequence([outer_forward]))

    assert "【外层用户】: [嵌套转发消息，path=[0, 0]" in content
    assert "内层正文" not in content


def test_full_complex_message_expands_selected_nested_forward_one_level() -> None:
    deepest_forward = _forward_node("最内层用户", "deepest", TextComponent("最内层正文"))
    inner_forward = _forward_node("内层用户", "inner", TextComponent("内层正文"), deepest_forward)
    outer_forward = _forward_node("外层用户", "outer", inner_forward)

    content = build_full_complex_message_content_from_sequence(MessageSequence([outer_forward]), [0, 0])

    assert "【内层用户】: 内层正文 [嵌套转发消息，path=[0, 0, 0]" in content
    assert "最内层正文" not in content


def test_full_complex_message_preserves_mixed_components_around_nested_forward() -> None:
    inner_forward = _forward_node("内层用户", "inner", TextComponent("内层正文"))
    outer_forward = _forward_node(
        "外层用户",
        "outer",
        TextComponent("转发前"),
        inner_forward,
        TextComponent("转发后"),
    )

    content = build_full_complex_message_content_from_sequence(MessageSequence([outer_forward]))

    assert "【外层用户】: 转发前 [嵌套转发消息，path=[0, 0]" in content
    assert "转发后" in content
    assert "内层正文" not in content


def test_full_complex_message_keeps_adjacent_plain_components_inline() -> None:
    forward = _forward_node("用户", "message", TextComponent("第一段"), TextComponent("第二段"))

    content = build_full_complex_message_content_from_sequence(MessageSequence([forward]))

    assert "【用户】: 第一段 第二段" in content


def test_full_complex_message_rejects_invalid_path() -> None:
    forward = _forward_node("用户", "message", TextComponent("正文"))

    try:
        build_full_complex_message_content_from_sequence(MessageSequence([forward]), [0, 0])
    except ValueError as exc:
        assert "第 2 级索引 0 超出范围" in str(exc)
    else:
        raise AssertionError("无效的嵌套转发路径应抛出 ValueError")


def test_complex_message_prompt_keeps_nested_forward_as_placeholder() -> None:
    inner_forward = _forward_node("内层用户", "inner", TextComponent("内层正文"))
    outer_forward = _forward_node("外层用户", "outer", inner_forward)

    prompt_text = _build_complex_message_prompt_text(MessageSequence([outer_forward]))

    assert "外层用户：[转发消息]" in prompt_text
    assert "内层正文" not in prompt_text

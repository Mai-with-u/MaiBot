from io import BytesIO

import base64

from PIL import Image as PILImage

from src.llm_models.model_client.openai_client import _convert_messages
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_item_text
from src.maisaka.visual.message_limiter import IMAGE_LIMIT_PLACEHOLDER, limit_latest_images_in_messages


def _build_png_base64() -> str:
    """生成一张最小的合法 PNG 图片。"""

    buffer = BytesIO()
    PILImage.new("RGB", (1, 1), (255, 0, 0)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_multi_text_user_message_is_sent_as_single_string() -> None:
    """多段纯文本的 user 消息必须以字符串发送，避免百炼等兼容服务返回 400 invalid string。"""

    planner_prefix = '<message msg_id="m1" time="12:00:00" user="Alice">\n'
    user_item = (
        ContextItemBuilder()
        .set_role(RoleType.User)
        .add_text_content(planner_prefix)
        .add_text_content("@Bob")
        .add_text_content(" 你好 world")
        .build()
    )

    assert _convert_messages([user_item]) == [
        {"role": "user", "content": f"{planner_prefix}@Bob 你好 world"},
    ]
    # 拼接结果应与内部可见文本保持一致，不额外插入或吞掉空白
    assert _convert_messages([user_item])[0]["content"] == get_item_text(user_item)


def test_multi_text_system_message_is_sent_as_single_string() -> None:
    system_item = (
        ContextItemBuilder()
        .set_role(RoleType.System)
        .add_text_content("你是麦麦。\n")
        .add_text_content("请简短回复。")
        .build()
    )

    assert _convert_messages([system_item]) == [
        {"role": "system", "content": "你是麦麦。\n请简短回复。"},
    ]


def test_user_message_with_image_replaced_by_placeholder_is_sent_as_string() -> None:
    """超出图片配额后图片被替换为文本占位，此时整条消息变为纯文本，应发送字符串。"""

    old_emoji_item = (
        ContextItemBuilder()
        .set_role(RoleType.User)
        .add_text_content('<message msg_id="m1" time="12:00:00" user="Alice">\n')
        .add_text_content("[消息类型]表情包")
        .add_image_content("png", _build_png_base64())
        .build()
    )
    limited_items = limit_latest_images_in_messages([old_emoji_item], max_image_num=0)

    assert _convert_messages(limited_items) == [
        {
            "role": "user",
            "content": f'<message msg_id="m1" time="12:00:00" user="Alice">\n[消息类型]表情包{IMAGE_LIMIT_PLACEHOLDER}',
        },
    ]


def test_user_message_with_image_is_still_sent_as_content_array() -> None:
    image_base64 = _build_png_base64()
    user_item = (
        ContextItemBuilder()
        .set_role(RoleType.User)
        .add_text_content("[消息类型]表情包")
        .add_text_content("看看这个")
        .add_image_content("png", image_base64)
        .build()
    )

    assert _convert_messages([user_item]) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[消息类型]表情包"},
                {"type": "text", "text": "看看这个"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
            ],
        },
    ]


def test_single_text_user_message_keeps_string_content() -> None:
    user_item = ContextItemBuilder().set_role(RoleType.User).add_text_content("你好").build()

    assert _convert_messages([user_item]) == [{"role": "user", "content": "你好"}]

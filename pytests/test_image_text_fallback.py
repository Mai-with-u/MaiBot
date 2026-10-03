"""图片降级为纯文本兜底（400 unsupported image）测试。"""

from io import BytesIO
import base64

from PIL import Image

from src.llm_models.exceptions import RespNotOkException
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemMeta,
    ContextTextPart,
    UserMessageItem,
)
from src.llm_models.utils import IMAGE_TEXT_PLACEHOLDER, replace_images_with_text
from src.llm_models.utils_model import LLMOrchestrator


def _meta() -> ContextItemMeta:
    """构造测试用 Item 元数据。"""

    return ContextItemMeta.create(item_id="item-1")


def _image_part() -> ContextImagePart:
    """构造测试用 JPEG 图片片段。"""

    image = Image.new("RGB", (100, 100), "white")
    output_buffer = BytesIO()
    image.save(output_buffer, format="JPEG")
    return ContextImagePart(
        image_format="jpeg",
        image_base64=base64.b64encode(output_buffer.getvalue()).decode("utf-8"),
    )


def test_replace_images_with_text_in_user_item() -> None:
    """用户消息中的图片片段应被替换为文本占位符。"""

    item = UserMessageItem(
        meta=_meta(),
        parts=(ContextTextPart("文本"), _image_part(), ContextTextPart("更多文本")),
    )

    replaced = replace_images_with_text([item])[0]

    assert isinstance(replaced, UserMessageItem)
    assert all(isinstance(part, ContextTextPart) for part in replaced.parts)
    assert replaced.parts[0].text == "文本"
    assert replaced.parts[1].text == IMAGE_TEXT_PLACEHOLDER
    assert replaced.parts[2].text == "更多文本"


def test_replace_images_with_text_in_assistant_item_clears_replay() -> None:
    """助手消息中的图片被替换时应清除 replay fragment。"""

    item = AssistantMessageItem(
        meta=_meta(),
        parts=(_image_part(),),
        replay="should-be-cleared",
    )

    replaced = replace_images_with_text([item])[0]

    assert isinstance(replaced, AssistantMessageItem)
    assert replaced.replay is None
    assert all(isinstance(part, ContextTextPart) for part in replaced.parts)


def test_replace_images_with_text_keeps_text_only_items() -> None:
    """纯文本消息应原样保留。"""

    item = UserMessageItem(meta=_meta(), parts=(ContextTextPart("纯文本"),))

    replaced = replace_images_with_text([item])[0]

    assert replaced == item


def test_replace_images_with_text_handles_empty_list() -> None:
    """空消息列表应返回空列表。"""

    assert replace_images_with_text([]) == []


def test_is_unsupported_image_error_matches_keyword() -> None:
    """含 unsupported image 关键词的错误应被识别。"""

    error = RespNotOkException(
        400,
        "input[49].image[0]: You have uploaded an unsupported image. "
        "Please make sure your image is valid and has one of the following formats: webp, png, jpeg, and gif.",
    )

    assert LLMOrchestrator._is_unsupported_image_error(error) is True


def test_is_unsupported_image_error_ignores_other_errors() -> None:
    """不含 unsupported image 关键词的错误不应被识别。"""

    error = RespNotOkException(400, "The reasoning_text in the thinking mode must be passed back to the API.")

    assert LLMOrchestrator._is_unsupported_image_error(error) is False


def test_is_unsupported_image_error_matches_cause() -> None:
    """错误链底层异常含 unsupported image 时也应被识别。"""

    # 模拟真实场景：RespNotOkException.message 由 _build_api_status_message 拼接
    # error.message 与 response.text，其中 response.text 含完整 API 错误响应。
    error = RespNotOkException(
        400,
        "BadRequestError | Error code: 400 - {'error': {'message': 'input[49].image[0]: "
        "You have uploaded an unsupported image. Please make sure your image is valid...'}}",
    )

    assert LLMOrchestrator._is_unsupported_image_error(error) is True

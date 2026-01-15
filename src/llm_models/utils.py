import base64
import io

from PIL import Image
from datetime import datetime

from src.common.logger import get_logger
from src.common.database.database import db  # 确保 db 被导入用于 create_tables
from src.common.database.database_model import LLMUsage
from src.config.model_configs import ModelInfo
from .payload_content.message import Message, MessageBuilder
from .model_client.base_client import UsageRecord

logger = get_logger("消息压缩工具")


def compress_messages(messages: list[Message], img_target_size: int = 1 * 1024 * 1024) -> list[Message]:
    """
    压缩消息列表中的图片
    :param messages: 消息列表
    :param img_target_size: 图片目标大小，默认1MB
    :return: 压缩后的消息列表
    """

    def reformat_static_image(image_data: bytes) -> bytes:
        """
        将静态图片转换为JPEG格式
        :param image_data: 图片数据
        :return: 转换后的图片数据
        """
        try:
            image = Image.open(io.BytesIO(image_data))

            # 仅在非动图时进行格式转换
            if (
                not getattr(image, "is_animated", False)
                and image.format
                and (image.format.upper() in ["JPEG", "JPG", "PNG", "WEBP"])
            ):
                reformated_image_data = io.BytesIO()
                img_to_save = image
                if img_to_save.mode in ("RGBA", "LA", "P"):
                    img_to_save = img_to_save.convert("RGB")
                img_to_save.save(reformated_image_data, format="JPEG", quality=95, optimize=True)
                image_data = reformated_image_data.getvalue()

            return image_data
        except Exception as e:
            logger.error(f"图片转换格式失败: {str(e)}")
            return image_data

    def rescale_image(image_data: bytes, scale: float) -> tuple[bytes, tuple[int, int] | None, tuple[int, int] | None]:
        """
        缩放图片
        :param image_data: 图片数据
        :param scale: 缩放比例
        :return: 缩放后的图片数据
        """
        try:
            image = Image.open(io.BytesIO(image_data))

            # 原始尺寸
            original_size = (image.width, image.height)

            # 计算新的尺寸，防止为0
            new_w = max(1, int(original_size[0] * scale))
            new_h = max(1, int(original_size[1] * scale))
            new_size = (new_w, new_h)

            output_buffer = io.BytesIO()

            if getattr(image, "is_animated", False):
                # 动态图片，处理所有帧
                frames = []
                new_size = (max(1, new_size[0] // 2), max(1, new_size[1] // 2))  # 动图，缩放尺寸再打折
                for frame_idx in range(getattr(image, "n_frames", 1)):
                    image.seek(frame_idx)
                    new_frame = image.copy()
                    new_frame = new_frame.resize(new_size, Image.Resampling.LANCZOS)
                    frames.append(new_frame)

                # 保存到缓冲区
                frames[0].save(
                    output_buffer,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    optimize=True,
                    duration=image.info.get("duration", 100),
                    loop=image.info.get("loop", 0),
                )
            else:
                # 静态图片，直接缩放保存
                resized_image = image.resize(new_size, Image.Resampling.LANCZOS)
                if resized_image.mode in ("RGBA", "LA", "P"):
                    resized_image = resized_image.convert("RGB")
                resized_image.save(output_buffer, format="JPEG", quality=95, optimize=True)

            return output_buffer.getvalue(), original_size, new_size

        except Exception as e:
            logger.error(f"图片缩放失败: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
            return image_data, None, None

    def compress_base64_image(base64_data: str, target_size: int = 1 * 1024 * 1024) -> str:
        original_b64_data_size = len(base64_data)  # 计算原始数据大小

        image_data = base64.b64decode(base64_data)

        # 先尝试转换格式为JPEG
        image_data = reformat_static_image(image_data)
        base64_data = base64.b64encode(image_data).decode("utf-8")
        if len(base64_data) <= target_size:
            # 如果转换后小于目标大小，直接返回
            logger.info(f"成功将图片转为JPEG格式，编码后大小: {len(base64_data) / 1024:.1f}KB")
            return base64_data

        # 如果转换后仍然大于目标大小，进行尺寸压缩
        scale = min(1.0, target_size / len(base64_data))
        image_data, original_size, new_size = rescale_image(image_data, scale)
        base64_data = base64.b64encode(image_data).decode("utf-8")

        if original_size and new_size:
            logger.info(
                f"压缩图片: {original_size[0]}x{original_size[1]} -> {new_size[0]}x{new_size[1]}\n"
                f"压缩前大小: {original_b64_data_size / 1024:.1f}KB, 压缩后大小: {len(base64_data) / 1024:.1f}KB"
            )

        return base64_data

    compressed_messages = []
    for message in messages:
        if isinstance(message.content, list):
            # 检查content，如有图片则压缩
            message_builder = MessageBuilder()
            for content_item in message.content:
                if isinstance(content_item, tuple):
                    # 图片，进行压缩
                    message_builder.add_image_content(
                        content_item[0],
                        compress_base64_image(content_item[1], target_size=img_target_size),
                    )
                else:
                    message_builder.add_text_content(content_item)
            compressed_messages.append(message_builder.build())
        else:
            compressed_messages.append(message)

    return compressed_messages


class LLMUsageRecorder:
    """
    LLM使用情况记录器
    """

    def __init__(self):
        try:
            # 使用 Peewee 创建表，safe=True 表示如果表已存在则不会抛出错误
            db.create_tables([LLMUsage], safe=True)
            # logger.debug("LLMUsage 表已初始化/确保存在。")
        except Exception as e:
            logger.error(f"创建 LLMUsage 表失败: {str(e)}")

    def record_usage_to_database(
        self,
        model_info: ModelInfo,
        model_usage: UsageRecord,
        user_id: str,
        request_type: str,
        endpoint: str,
        time_cost: float = 0.0,
    ):
        input_cost = (model_usage.prompt_tokens / 1000000) * model_info.price_in
        output_cost = (model_usage.completion_tokens / 1000000) * model_info.price_out
        total_cost = round(input_cost + output_cost, 6)
        try:
            # 使用 Peewee 模型创建记录
            LLMUsage.create(
                model_name=model_info.model_identifier,
                model_assign_name=model_info.name,
                model_api_provider=model_info.api_provider,
                user_id=user_id,
                request_type=request_type,
                endpoint=endpoint,
                prompt_tokens=model_usage.prompt_tokens or 0,
                completion_tokens=model_usage.completion_tokens or 0,
                total_tokens=model_usage.total_tokens or 0,
                cost=total_cost or 0.0,
                time_cost=round(time_cost or 0.0, 3),
                status="success",
                timestamp=datetime.now(),  # Peewee 会处理 DateTimeField
            )
            logger.debug(
                f"Token使用情况 - 模型: {model_usage.model_name}, "
                f"用户: {user_id}, 类型: {request_type}, "
                f"提示词: {model_usage.prompt_tokens}, 完成: {model_usage.completion_tokens}, "
                f"总计: {model_usage.total_tokens}"
            )
        except Exception as e:
            logger.error(f"记录token使用情况失败: {str(e)}")


llm_usage_recorder = LLMUsageRecorder()

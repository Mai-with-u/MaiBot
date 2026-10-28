from math import ceil
from pathlib import Path
from typing import Optional, Union

import base64
import io

from PIL import Image as PILImage, ImageOps as PILImageOps, ImageSequence
import numpy as np

from src.common.logger import get_logger

logger = get_logger("image_utils")

MODEL_MIN_IMAGE_SIDE = 64
MODEL_MAX_UPSCALED_IMAGE_SIDE = 2048


class ImageUtils:
    @staticmethod
    def normalize_image_base64_for_model(
        image_base64: str,
        image_format: str,
        *,
        min_side: int = MODEL_MIN_IMAGE_SIDE,
        max_upscaled_side: int = MODEL_MAX_UPSCALED_IMAGE_SIDE,
    ) -> tuple[str, str, bool]:
        """确保发给视觉模型的图片不低于常见最小识别尺寸。"""
        if min_side <= 0:
            raise ValueError("模型图片最小边长必须大于0")

        image_bytes = base64.b64decode(image_base64, validate=True)
        with PILImage.open(io.BytesIO(image_bytes)) as image:
            normalized_image = PILImageOps.exif_transpose(image)
            width, height = normalized_image.size
            if width <= 0 or height <= 0:
                raise ValueError("图片尺寸无效，无法发送给视觉模型")
            if width >= min_side and height >= min_side:
                return image_base64, image_format, False

            if normalized_image.mode in ("RGBA", "LA") or (
                normalized_image.mode == "P" and "transparency" in normalized_image.info
            ):
                working_image = normalized_image.convert("RGBA")
                canvas_mode = "RGBA"
                background_color = (255, 255, 255, 0)
            else:
                working_image = normalized_image.convert("RGB")
                canvas_mode = "RGB"
                background_color = (255, 255, 255)

            scale = max(1, ceil(min_side / min(width, height)))
            if max_upscaled_side > 0:
                max_scale = max(1, max_upscaled_side // max(width, height))
                scale = min(scale, max_scale)

            resized_width = max(1, width * scale)
            resized_height = max(1, height * scale)
            resized_image = working_image.resize((resized_width, resized_height), PILImage.Resampling.NEAREST)

            canvas_width = max(min_side, resized_width)
            canvas_height = max(min_side, resized_height)
            if (canvas_width, canvas_height) != resized_image.size:
                canvas = PILImage.new(canvas_mode, (canvas_width, canvas_height), background_color)
                paste_box = ((canvas_width - resized_width) // 2, (canvas_height - resized_height) // 2)
                if resized_image.mode == "RGBA":
                    canvas.paste(resized_image, paste_box, resized_image)
                else:
                    canvas.paste(resized_image, paste_box)
                resized_image = canvas

            output_buffer = io.BytesIO()
            resized_image.save(output_buffer, format="PNG")
            resized_base64 = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
            return resized_base64, "png", True

    @staticmethod
    def gif_2_static_image(gif_bytes: bytes, similarity_threshold: float = 1000.0, max_frames: int = 15) -> bytes:
        """
        将GIF图片水平拼接为静态图像，跳过相似帧

        Args:
            gif_bytes (bytes): 输入的GIF图片字节数据
            similarity_threshold (float): 判定帧相似的阈值 (MSE)，越大表示要求差异越大才算不同帧，默认1000.0
            max_frames (int): 最大抽取的帧数，默认15
        Returns:
            bytes: 拼接后的静态图像字节数据，格式为JPEG
        Raises:
            ValueError: 如果输入的GIF无效或无法处理
            MemoryError: 如果处理过程中内存不足
            Exception: 其他异常
        """
        with PILImage.open(io.BytesIO(gif_bytes)) as gif_image:
            if not gif_image.format or gif_image.format.lower() != "gif":
                logger.error("输入的图片不是有效的GIF格式")
                raise ValueError("输入的图片不是有效的GIF格式")
            # --- 流式迭代并选择帧（避免一次性加载所有帧） ---
            selected_frames: list[PILImage.Image] = []
            last_selected_frame_np = None
            frame_index = 0

            for frame in ImageSequence.Iterator(gif_image):
                # 确保是RGB格式方便比较
                frame_rgb = frame.convert("RGB")
                frame_np = np.array(frame_rgb, dtype=np.float32)

                if frame_index == 0:
                    selected_frames.append(frame_rgb.copy())
                    last_selected_frame_np = frame_np
                else:
                    # 计算和上一张选中帧的差异（均方误差 MSE）
                    mse = np.mean((frame_np - last_selected_frame_np) ** 2)
                    # logger.debug(f"帧 {frame_index} 与上一选中帧的 MSE: {mse}")
                    if mse > similarity_threshold:
                        selected_frames.append(frame_rgb.copy())
                        last_selected_frame_np = frame_np
                        if len(selected_frames) >= max_frames:
                            break
                frame_index += 1

        if not selected_frames:
            logger.error("未能抽取到任何有效帧")
            raise ValueError("未能抽取到任何有效帧")

        # 获取选中的第一帧的尺寸（假设所有帧尺寸一致）
        frame_width, frame_height = selected_frames[0].size
        # 防止除以零
        if frame_height == 0:
            raise ValueError("帧高度为0，无法计算缩放尺寸")

        # 计算目标尺寸，保持宽高比
        target_height = 200  # 固定高度
        target_width = int((target_height / frame_height) * frame_width)
        # 宽度也不能是0
        if target_width == 0:
            logger.warning(f"计算出的目标宽度为0 (原始尺寸 {frame_width}x{frame_height})，调整为1")
            target_width = 1
        # 调整所有选中帧的大小
        resized_frames = [
            frame.resize((target_width, target_height), PILImage.Resampling.LANCZOS) for frame in selected_frames
        ]

        # 创建拼接图像
        total_width = target_width * len(resized_frames)
        combined_image = PILImage.new("RGB", (total_width, target_height))
        # 水平拼接图像
        for idx, frame in enumerate(resized_frames):
            combined_image.paste(frame, (idx * target_width, 0))
        buffer = io.BytesIO()
        combined_image.save(buffer, format="JPEG", quality=85)  # 保存为JPEG
        return buffer.getvalue()

    @staticmethod
    def compress_image_to_size(image_bytes: bytes, target_size: int) -> bytes:
        """将图片压缩到目标大小以内，失败时保持原图数据。"""
        if not image_bytes:
            raise ValueError("输入的图片字节数据无效")
        if target_size <= 0 or len(image_bytes) <= target_size:
            return image_bytes

        try:
            with PILImage.open(io.BytesIO(image_bytes)) as image:
                image.seek(0)
                working_image = ImageUtils._prepare_image_for_receive_compression(image)
        except Exception as exc:
            logger.warning(f"接收图片压缩失败，无法识别图片格式: {exc}")
            return image_bytes

        compressed = ImageUtils._compress_static_image_to_size(working_image, target_size)
        if len(compressed) < len(image_bytes):
            return compressed
        return image_bytes

    @staticmethod
    def _prepare_image_for_receive_compression(image: PILImage.Image) -> PILImage.Image:
        """将任意图片整理成适合接收链路压缩的 RGB 静态图。"""
        normalized_image = PILImageOps.exif_transpose(image)
        if normalized_image.mode in ("RGBA", "LA") or (
            normalized_image.mode == "P" and "transparency" in normalized_image.info
        ):
            alpha_image = normalized_image.convert("RGBA")
            background = PILImage.new("RGB", alpha_image.size, (255, 255, 255))
            background.paste(alpha_image, mask=alpha_image.getchannel("A"))
            return background
        return normalized_image.convert("RGB")

    @staticmethod
    def _compress_static_image_to_size(image: PILImage.Image, target_size: int) -> bytes:
        """通过降低质量和缩放尺寸压缩静态图片。"""
        working_image = image.copy()
        quality = 85
        last_output = b""

        for _ in range(16):
            output_buffer = io.BytesIO()
            working_image.save(output_buffer, format="JPEG", quality=quality, optimize=True)
            output_bytes = output_buffer.getvalue()
            last_output = output_bytes
            if len(output_bytes) <= target_size:
                return output_bytes

            if quality > 55:
                quality = max(55, quality - 10)
                continue

            scale = max(0.1, min(0.95, (target_size / len(output_bytes)) ** 0.5 * 0.95))
            new_width = max(1, int(working_image.width * scale))
            new_height = max(1, int(working_image.height * scale))
            if (new_width, new_height) == working_image.size:
                break
            working_image = working_image.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

        return last_output

    @staticmethod
    def image_bytes_to_base64(image_bytes: bytes) -> str:
        """
        将图片字节数据转换为Base64编码字符串

        Args:
            image_bytes (bytes): 输入的图片字节数据
        Returns:
            str: Base64编码的图片字符串
        Raises:
            ValueError: 如果输入的图片字节数据无效
        """
        if not image_bytes:
            logger.error("输入的图片字节数据无效")
            raise ValueError("输入的图片字节数据无效")
        return base64.b64encode(image_bytes).decode("utf-8")

    @staticmethod
    def image_path_to_base64(image_path: Union[str, Path]) -> Optional[str]:
        """读取图片文件并转换为 Base64 编码字符串"""
        try:
            path = Path(image_path)
            if not path.exists():
                logger.error(f"图片文件不存在: {path}")
                return None
            image_bytes = path.read_bytes()
            return base64.b64encode(image_bytes).decode("utf-8")
        except Exception as e:
            logger.error(f"读取图片文件失败: {e}")
            return None

    @staticmethod
    def base64_to_image(base64_str: str, save_path: Union[str, Path]) -> bool:
        """将 Base64 编码字符串解码并保存为图片文件"""
        try:
            image_bytes = base64.b64decode(base64_str)
            path = Path(save_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
            return True
        except Exception as e:
            logger.error(f"保存图片文件失败: {e}")
            return False

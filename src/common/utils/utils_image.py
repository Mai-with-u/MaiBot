from pathlib import Path
from PIL import Image as PILImage, ImageSequence
from typing import Optional, Union

import base64
import io
import numpy as np

from src.common.logger import get_logger

logger = get_logger("image")


class ImageUtils:
    @staticmethod
    def gif_2_static_image(gif_bytes: bytes, similarity_threshold: float = 1000.0, max_frames: int = 15) -> bytes:
        """
        将GIF图片水平拼接为静态图像，跳过相似帧

        Args:
            gif_bytes (bytes): 输入的GIF图片字节数据
            similarity_threshold (float): 判定帧相似的阈值 (MSE)，越小表示要求差异越大才算不同帧，默认1000.0
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
                frame_np = np.array(frame_rgb)

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

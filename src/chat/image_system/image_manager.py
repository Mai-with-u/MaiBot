from datetime import datetime
from pathlib import Path
from rich.traceback import install
from sqlmodel import select
from typing import Optional

import base64
import hashlib

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.data_models.image_data_model import MaiImage
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

install(extra_lines=3)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.absolute().resolve()
DATA_DIR = PROJECT_ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"

logger = get_logger("image")


def _ensure_image_dir_exists():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


vlm = LLMRequest(model_set=model_config.model_task_config.vlm, request_type="image")


class ImageManager:
    def __init__(self):
        _ensure_image_dir_exists()

        logger.info("图片管理器初始化完成")

    async def get_image_description(
        self, *, image_hash: Optional[str] = None, image_bytes: Optional[bytes] = None
    ) -> str:
        """
        获取图片描述的封装方法

        如果图片已存在于数据库中，则直接返回描述

        如果不存在，则**保存图片**并**生成描述**后返回

        Args:
            image_hash (Optional[str]): 图片的哈希值，如果提供则优先使用该
            image_bytes (Optional[bytes]): 图片的字节数据，如果提供则在数据库中找不到哈希值时使用该数据生成描述
        Returns:
            return (str): 图片描述，如果发生错误或无法生成描述则返回空字符串
        Raises:
            ValueError: 如果未提供有效的图片哈希值或图片字节数据
            Exception: 在查询数据库、保存图片或生成描述过程中发生的其他异常
        """
        if image_hash:
            hash_str = image_hash
        elif not image_bytes:
            raise ValueError("必须提供图片哈希值或图片字节数据")
        else:
            hash_str = hashlib.sha256(image_bytes).hexdigest()

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=hash_str, image_type=ImageType.IMAGE).limit(1)
                if record := session.exec(statement).first():
                    return record.description
        except Exception as e:
            logger.error(f"查询图片描述时发生错误: {e}")

        if not image_bytes:
            logger.warning("图片哈希值未找到，且未提供图片字节数据，返回无描述")
            return ""
        logger.info(f"图片描述未找到，哈希值: {hash_str}，准备生成新描述")
        try:
            image = await self.save_image_and_process(image_bytes)
            return image.description
        except Exception as e:
            logger.error(f"生成图片描述时发生错误: {e}")
            return ""

    def get_image_from_db(self, image_hash: str) -> Optional[MaiImage]:
        """
        从数据库中根据图片哈希值获取图片记录

        """
        with get_db_session() as session:
            statement = select(Images).filter_by(image_hash=image_hash, image_type=ImageType.IMAGE).limit(1)
            if record := session.exec(statement).first():
                if record.no_file_flag:
                    logger.warning(f"数据库记录标记为文件不存在，哈希值: {image_hash}")
                    return None
                return MaiImage.from_db_instance(record)
            logger.info(f"未找到哈希值为 {image_hash} 的图片记录")
            return None

    def register_image_to_db(self, image: MaiImage) -> bool:
        """
        将图片对象注册到数据库中
        Args:
            image (MaiImage): 包含图片信息的 MaiImage 对象，必须包含有效的 full_path 和 image_format
        Returns:
            return (bool): 注册成功返回 True，失败返回 False
        """
        # sourcery skip: extract-method
        if not image or not isinstance(image, MaiImage):
            logger.error("无效的图片对象，无法注册到数据库")
            return False
        if not image.full_path.exists():
            logger.error(f"图片文件不存在，无法注册到数据库: {image.full_path}")
            return False

        try:
            with get_db_session() as session:
                record = image.to_db_instance()
                record.is_registered = True
                record.register_time = record.last_used_time = datetime.now()
                session.add(record)
                session.flush()  # 确保记录被写入数据库以获取ID
                record_id = record.id
                logger.info(f"成功保存图片记录到数据库: ID: {record_id}，路径: {record.full_path}")
        except Exception as e:
            logger.error(f"保存图片记录到数据库时发生错误: {e}")
            return False
        return True

    def update_image_description(self, image: MaiImage) -> bool:
        """
        更新图片描述

        Args:
            image (MaiImage): 包含新描述的图片对象，必须包含有效的 file_hash 和 full_path
        Returns:
            return (bool): 更新成功返回 True，失败返回 False
        """
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=image.file_hash, image_type=ImageType.IMAGE).limit(1)
                record = session.exec(statement).first()
                if not record:
                    logger.error(f"未找到哈希值为 {image.file_hash} 的图片记录，无法更新描述")
                    return False
                record.description = image.description
                record.last_used_time = datetime.now()
                session.add(record)
                logger.info(f"成功更新图片描述: {image.file_hash}，新描述: {image.description}")
        except Exception as e:
            logger.error(f"更新图片描述时发生错误: {e}")
            return False
        return True

    def delete_image(self, image: MaiImage) -> bool:
        """
        删除图片记录和对应的文件

        Args:
            image (MaiImage): 包含要删除图片信息的对象，必须包含有效的 file_hash 和 full_path
        Returns:
            return (bool): 删除成功返回 True，失败返回 False
        """
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=image.file_hash, image_type=ImageType.IMAGE).limit(1)
                record = session.exec(statement).first()
                if not record:
                    logger.error(f"未找到哈希值为 {image.file_hash} 的图片记录，无法删除")
                    return False
                session.delete(record)
                logger.info(f"成功删除图片记录: {image.file_hash}")

            if image.full_path.exists():
                image.full_path.unlink()
                logger.info(f"成功删除图片文件: {image.full_path}")
            else:
                logger.warning(f"图片文件不存在，无法删除: {image.full_path}")
        except Exception as e:
            logger.error(f"删除图片时发生错误: {e}")
            if image.full_path.exists():
                logger.warning(f"图片文件未被删除: {image.full_path}")
            return False
        return True

    async def save_image_and_process(self, image_bytes: bytes) -> MaiImage:
        """
        保存图片并生成描述

        Args:
            image_bytes (bytes): 图片的字节数据
        Returns:
            return (MaiImage): 包含图片信息的 MaiImage 对象
        Raises:
            Exception: 如果在保存或处理过程中发生错误
        """
        hash_str = hashlib.sha256(image_bytes).hexdigest()

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=hash_str).limit(1)
                if record := session.exec(statement).first():
                    logger.info(f"图片已存在于数据库中，哈希值: {hash_str}")
                    record.last_used_time = datetime.now()
                    record.query_count += 1
                    session.add(record)
                    session.flush()
                    return MaiImage.from_db_instance(record)
        except Exception as e:
            logger.error(f"查询图片记录时发生错误: {e}")
            raise e

        logger.info(f"图片不存在于数据库中，准备保存新图片，哈希值: {hash_str}")
        tmp_file_path = IMAGE_DIR / f"{hash_str}.tmp"
        with tmp_file_path.open("wb") as f:
            f.write(image_bytes)
        mai_image = MaiImage(full_path=(IMAGE_DIR / f"{hash_str}.tmp"), image_bytes=image_bytes)
        await mai_image.calculate_hash_format()
        desc = await self._generate_image_description(image_bytes, mai_image.image_format)
        mai_image.description = desc
        mai_image.vlm_processed = True
        try:
            self.register_image_to_db(mai_image)
        except Exception as e:
            logger.error(f"保存新图片记录到数据库时发生错误: {e}")
            raise e
        return mai_image

    def cleanup_invalid_descriptions_in_db(self):
        """
        清理数据库中无效的图片记录

        无效的判定：`description` 为空或仅包含空白字符，或者文件路径不存在
        """
        invalid_values = {"", None}
        invalid_counter: int = 0
        null_path_counter: int = 0
        logger.info("开始清理数据库中无效的图片记录...")

        try:
            with get_db_session() as session:
                for record in session.exec(select(Images)).yield_per(100):
                    if record.description in invalid_values:
                        if record.full_path and Path(record.full_path).exists():
                            try:
                                Path(record.full_path).unlink()
                                logger.info(f"已删除无效描述的图片文件: {record.full_path}")
                            except Exception as e:
                                logger.error(f"删除无效描述的图片文件时发生错误: {e}")
                        session.delete(record)
                        invalid_counter += 1
                    elif record.full_path and not Path(record.full_path).exists():
                        session.delete(record)
                        null_path_counter += 1
        except Exception as e:
            logger.error(f"清理数据库中无效图片记录时发生错误: {e}")

        logger.info(f"清理完成: {invalid_counter} 条无效描述记录，{null_path_counter} 条文件路径不存在记录")

    async def _generate_image_description(self, image_bytes: bytes, image_format: str) -> str:
        prompt = global_config.personality.visual_style
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        description, _ = await vlm.generate_response_for_image(prompt, image_base64, image_format, 0.4)
        if not description:
            logger.warning("VLM未能生成图片描述")
        return description or ""


image_manager = ImageManager()

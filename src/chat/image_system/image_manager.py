import asyncio
import base64
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from rich.traceback import install
from sqlmodel import select

from src.common.data_models.image_data_model import MaiImage
from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from src.common.utils.image_path import resolve_stored_image_path, serialize_stored_image_path
from src.config.config import config_manager
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

install(extra_lines=3)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.absolute().resolve()
DATA_DIR = PROJECT_ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"

logger = get_logger("image")


def _ensure_image_dir_exists() -> None:
    """确保图片缓存目录存在。"""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def _is_vlm_task_configured() -> bool:
    """判断是否配置了可用于图片识别的视觉模型任务。"""

    try:
        vlm_models = config_manager.get_model_config().model_task_config.vlm.model_list
        return any(str(model_name).strip() for model_name in vlm_models)
    except Exception as exc:
        logger.warning(f"读取 VLM 模型配置失败，跳过图片识别: {exc}")
        return False


vlm = LLMServiceClient(task_name="vlm", request_type="image")


class ImageManager:
    """图片描述管理器。"""

    def __init__(self) -> None:
        """初始化图片管理器。"""
        _ensure_image_dir_exists()
        self._pending_description_tasks: Dict[str, asyncio.Task[None]] = {}
        self.cleanup_legacy_image_registration_records()

        logger.info("图片管理器初始化完成")

    def _get_image_record(self, image_hash: str) -> Optional[Images]:
        """根据哈希获取图片记录。"""
        with get_db_session() as session:
            statement = select(Images).filter_by(image_hash=image_hash, image_type=ImageType.IMAGE).limit(1)
            record = session.exec(statement).first()
            if record is not None:
                # 返回会话外使用的只读记录，避免在会话关闭后触发属性刷新。
                session.expunge(record)
            return record

    def _normalize_image_registration_fields(self, record: Images) -> bool:
        """Normalize accidental emoji registration fields on image records."""
        if record.image_type != ImageType.IMAGE:
            return False
        if not record.is_registered and record.register_time is None:
            return False

        record.is_registered = False
        record.register_time = None
        return True

    async def get_image_description(
        self,
        *,
        image_hash: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        wait_for_build: bool = True,
    ) -> str:
        """
        获取图片描述的封装方法

        如果图片已存在于数据库中，则直接返回描述

        如果不存在，则**保存图片**并**生成描述**后返回

        Args:
            image_hash (Optional[str]): 图片的哈希值，如果提供则优先使用该
            image_bytes (Optional[bytes]): 图片的字节数据，如果提供则在数据库中找不到哈希值时使用该数据生成描述
            wait_for_build (bool): 未命中缓存时是否同步等待描述构建完成
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
            if record := self._get_image_record(hash_str):
                if record.vlm_processed and record.description:
                    return record.description
        except Exception as e:
            logger.error(f"查询图片描述时发生错误: {e}")

        if not image_bytes:
            logger.warning("图片哈希值未找到，且未提供图片字节数据，返回无描述")
            return ""
        try:
            saved_image = await self.ensure_image_saved(image_bytes)
        except Exception as e:
            logger.error(f"保存图片文件时发生错误: {e}")
            return ""
        if not _is_vlm_task_configured():
            logger.info("未配置 VLM 模型，跳过图片识别")
            return ""
        if not wait_for_build:
            self._schedule_description_build(hash_str, image_bytes, saved_image=saved_image)
            return ""
        logger.info(f"图片描述未找到，哈希值: {hash_str}，准备生成新描述")
        try:
            image = await self.build_image_description(image_bytes, saved_image=saved_image)
            return image.description
        except Exception as e:
            logger.error(f"生成图片描述时发生错误: {e}")
            return ""

    def _schedule_description_build(
        self,
        image_hash: str,
        image_bytes: bytes,
        *,
        saved_image: Optional[MaiImage] = None,
    ) -> None:
        """调度图片描述后台构建任务。

        Args:
            image_hash: 图片哈希值。
            image_bytes: 图片字节数据。
            saved_image: 已保存的图片对象，避免后台任务重复执行保存流程。
        """
        if not _is_vlm_task_configured():
            logger.info("未配置 VLM 模型，跳过图片后台识别任务")
            return

        if image_hash in self._pending_description_tasks:
            return

        task = asyncio.create_task(
            self._build_description_in_background(image_hash, image_bytes, saved_image=saved_image)
        )
        self._pending_description_tasks[image_hash] = task
        task.add_done_callback(lambda finished_task: self._finalize_description_build(image_hash, finished_task))

    async def _build_description_in_background(
        self,
        image_hash: str,
        image_bytes: bytes,
        *,
        saved_image: Optional[MaiImage] = None,
    ) -> None:
        """在后台构建并缓存图片描述。

        Args:
            image_hash: 图片哈希值。
            image_bytes: 图片字节数据。
            saved_image: 已保存的图片对象，避免重复查询和更新访问计数。
        """
        try:
            logger.debug(f"图片描述后台构建已开始，哈希值: {image_hash}")
            await self.build_image_description(image_bytes, saved_image=saved_image)
            logger.debug(f"图片描述后台构建完成，哈希值: {image_hash}")
        except Exception as exc:
            logger.warning(f"图片描述后台构建失败，哈希值: {image_hash}，错误: {exc}")

    def _finalize_description_build(self, image_hash: str, task: asyncio.Task[None]) -> None:
        """回收图片描述后台构建任务。

        Args:
            image_hash: 图片哈希值。
            task: 已完成的后台任务。
        """
        self._pending_description_tasks.pop(image_hash, None)
        try:
            task.result()
        except Exception as exc:
            logger.debug(f"图片描述后台任务结束时捕获异常，哈希值: {image_hash}，错误: {exc}")
            return

        try:
            from src.maisaka.visual.chat_history_refresher import log_tracked_image_recognition_completed

            log_tracked_image_recognition_completed(image_hash)
        except Exception as exc:
            logger.debug(f"通知 MaiSaka 图片识别完成状态失败，image_hash={image_hash}: {exc}")

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
                record.is_registered = False
                record.register_time = None
                record.last_used_time = datetime.now()
                session.add(record)
                session.flush()  # 确保记录被写入数据库以获取ID
                record_id = record.id
                logger.debug(f"保存图片: ID: {record_id}，路径: {record.full_path}")
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
                self._normalize_image_registration_fields(record)
                record.description = image.description
                record.last_used_time = datetime.now()
                record.vlm_processed = image.vlm_processed
                session.add(record)
                logger.info(f"理解图片: {image.description}")
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

    async def ensure_image_saved(self, image_bytes: bytes) -> MaiImage:
        """先保存图片记录，确保后续可以按哈希回填图片内容。"""
        hash_str = hashlib.sha256(image_bytes).hexdigest()

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=hash_str, image_type=ImageType.IMAGE).limit(1)
                if record := session.exec(statement).first():
                    self._normalize_image_registration_fields(record)
                    record_path = resolve_stored_image_path(record.full_path)
                    if not record.no_file_flag and record_path.is_file():
                        logger.info(f"图片已存在于数据库中，哈希值: {hash_str}")
                        record.last_used_time = datetime.now()
                        record.query_count += 1
                        session.add(record)
                        session.flush()
                        return MaiImage.from_db_instance(record)
                    logger.info(f"图片记录存在但文件缺失，准备重新保存图片文件，哈希值: {hash_str}")
        except Exception as e:
            logger.error(f"查询图片记录时发生错误: {e}")
            raise e

        logger.debug(f"图片不存在或文件缺失，准备保存图片文件，哈希值: {hash_str}")
        tmp_file_path = IMAGE_DIR / f"{hash_str}.tmp"
        with tmp_file_path.open("wb") as f:
            f.write(image_bytes)
        mai_image = MaiImage(full_path=tmp_file_path, image_bytes=image_bytes)
        await mai_image.calculate_hash_format()
        if not self._upsert_saved_image_record(mai_image):
            raise RuntimeError(f"保存图片记录到数据库失败: {hash_str}")
        return mai_image

    def _upsert_saved_image_record(self, image: MaiImage) -> bool:
        """保存图片记录，或在文件被清理后恢复同 hash 记录的文件状态。"""
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=image.file_hash, image_type=ImageType.IMAGE).limit(1)
                record = session.exec(statement).first()
                if record is None:
                    record = image.to_db_instance()
                    record.is_registered = False
                    record.register_time = None
                    record.query_count = 0
                else:
                    self._normalize_image_registration_fields(record)
                    image.description = record.description
                    image.vlm_processed = record.vlm_processed
                    record.full_path = serialize_stored_image_path(image.full_path)

                record.no_file_flag = False
                record.last_used_time = datetime.now()
                session.add(record)
                session.flush()
                logger.info(f"保存图片: ID: {record.id}，路径: {record.full_path}")
        except Exception as e:
            logger.error(f"保存图片记录到数据库时发生错误: {e}")
            return False
        return True

    async def build_image_description(
        self,
        image_bytes: bytes,
        *,
        saved_image: Optional[MaiImage] = None,
    ) -> MaiImage:
        """在图片已保存的前提下生成或补齐图片描述。"""
        mai_image = saved_image or await self.ensure_image_saved(image_bytes)
        if not mai_image.image_format:
            await mai_image.calculate_hash_format()
        if mai_image.vlm_processed and mai_image.description:
            return mai_image
        if not _is_vlm_task_configured():
            logger.info(f"未配置 VLM 模型，跳过图片识别: {mai_image.file_hash}")
            return mai_image

        desc = await self._generate_image_description(image_bytes, mai_image.image_format)
        mai_image.description = desc
        mai_image.vlm_processed = True
        if not self.update_image_description(mai_image):
            raise RuntimeError(f"更新图片描述失败: {mai_image.file_hash}")
        return mai_image

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
        return await self.build_image_description(image_bytes)

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
                        if record.full_path and resolve_stored_image_path(record.full_path).exists():
                            try:
                                resolve_stored_image_path(record.full_path).unlink()
                                logger.info(f"已删除无效描述的图片文件: {record.full_path}")
                            except Exception as e:
                                logger.error(f"删除无效描述的图片文件时发生错误: {e}")
                        session.delete(record)
                        invalid_counter += 1
                    elif record.full_path and not resolve_stored_image_path(record.full_path).exists():
                        session.delete(record)
                        null_path_counter += 1
        except Exception as e:
            logger.error(f"清理数据库中无效图片记录时发生错误: {e}")

        logger.info(f"清理完成: {invalid_counter} 条无效描述记录，{null_path_counter} 条文件路径不存在记录")

    def cleanup_legacy_image_registration_records(self) -> None:
        """Clean up legacy image records with mistaken registration fields."""
        fixed_counter = 0
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_type=ImageType.IMAGE)
                for record in session.exec(statement).yield_per(100):
                    if not self._normalize_image_registration_fields(record):
                        continue
                    session.add(record)
                    fixed_counter += 1
        except Exception as e:
            logger.error(f"Failed to clean image registration state: {e}")
            return

        if fixed_counter:
            logger.info(f"Cleaned mistaken registration state on {fixed_counter} image records")

    async def _generate_image_description(self, image_bytes: bytes, image_format: str) -> str:
        prompt_template = prompt_manager.get_prompt("image_description")
        prompt = await prompt_manager.render_prompt(prompt_template)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        generation_result = await vlm.generate_response_for_image(
            prompt,
            image_base64,
            image_format,
        )
        description = generation_result.response
        if not description:
            logger.warning("VLM未能生成图片描述")
        return description or ""


image_manager = ImageManager()

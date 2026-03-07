from datetime import datetime
from pathlib import Path
from PIL import Image as PILImage
from rich.traceback import install
from typing import Optional, List

import asyncio
import hashlib
import io
import traceback

from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from . import BaseDatabaseDataModel


install(extra_lines=3)

logger = get_logger("emoji")


class BaseImageDataModel(BaseDatabaseDataModel[Images]):
    def __init__(self, full_path: str | Path, image_bytes: Optional[bytes] = None):
        if not full_path:
            # 创建时候即检测文件路径合法性
            raise ValueError("表情包路径不能为空")
        if Path(full_path).is_dir() or not Path(full_path).exists():
            raise FileNotFoundError(f"表情包路径无效: {full_path}")
        resolved_path = Path(full_path).absolute().resolve()
        self.full_path: Path = resolved_path
        self.dir_path: Path = resolved_path.parent.resolve()
        self.file_name: str = resolved_path.name
        self.file_hash: str = None  # type: ignore

        self.image_bytes: Optional[bytes] = image_bytes

        self.image_format: str = ""  # 图片格式

    def read_image_bytes(self, path: Path) -> bytes:
        """
        同步读取图片文件的字节内容

        Args:
            path (Path): 图片文件的完整路径
        Returns:
            return (bytes): 图片文件的字节内容
        Raises:
            FileNotFoundError: 如果文件不存在则抛出该异常
            Exception: 其他读取文件时发生的异常
        """
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError as e:
            logger.error(f"[读取图片文件] 文件未找到: {path}")
            raise e
        except Exception as e:
            logger.error(f"[读取图片文件] 读取文件时发生错误: {e}")
            raise e

    def get_image_format(self, image_bytes: bytes) -> str:
        """
        获取图片的格式

        Args:
            image_bytes (bytes): 图片的字节内容

        Returns:
            return (str): 图片的格式（小写）

        Raises:
            ValueError: 如果无法识别图片格式
            Exception: 其他读取图片格式时发生的异常
        """
        try:
            with PILImage.open(io.BytesIO(image_bytes)) as img:
                if not img.format:
                    raise ValueError("无法识别图片格式")
                return img.format.lower()
        except Exception as e:
            logger.error(f"[获取图片格式] 读取图片格式时发生错误: {e}")
            raise e

    async def calculate_hash_format(self) -> bool:
        """
        异步计算表情包的哈希值和格式，初始化后应该执行此方法来确保对象的哈希值和格式正确

        Returns:
            return (bool): 如果成功计算哈希值和格式则返回True，否则返回False
        """
        try:
            # 计算哈希值
            logger.debug(f"[初始化] 计算 {self.file_name} 的哈希值...")
            if not self.image_bytes:
                logger.debug(f"[初始化] 正在读取文件: {self.full_path}")
                image_bytes = await asyncio.to_thread(self.read_image_bytes, self.full_path)
            else:
                image_bytes = self.image_bytes
            self.file_hash = hashlib.sha256(image_bytes).hexdigest()
            logger.debug(f"[初始化] {self.file_name} 计算哈希值成功: {self.file_hash}")

            # 用PIL读取图片格式
            logger.debug(f"[初始化] 读取 {self.file_name} 的图片格式...")
            self.image_format = await asyncio.to_thread(self.get_image_format, image_bytes)
            logger.debug(f"[初始化] {self.file_name} 读取图片格式成功: {self.image_format}")

            # 比对文件扩展名和实际格式
            file_ext = self.file_name.split(".")[-1].lower()
            if file_ext != self.image_format:
                logger.warning(
                    f"[初始化] {self.file_name} 文件扩展名与实际格式不符: ext`{file_ext}`!=`{self.image_format}`"
                )
                # 重命名文件以匹配实际格式
                new_file_name = ".".join(self.file_name.split(".")[:-1] + [self.image_format])
                new_full_path = self.dir_path / new_file_name
                self.full_path.rename(new_full_path)
                self.full_path = new_full_path

            return True
        except Exception as e:
            logger.error(f"[初始化] 初始化图片时发生错误: {e}")
            logger.error(traceback.format_exc())
            return False


class MaiEmoji(BaseImageDataModel):
    """麦麦的表情包对象，仅当**图片文件存在**时才应该创建此对象，数据库记录如果标记为文件不存在`(no_file_flag = True)`则不应该调用 `from_db_instance` 方法来创建此对象"""

    def __init__(self, full_path: str | Path, image_bytes: Optional[bytes] = None):
        # self.embedding = []
        self.description: str = ""
        self.emotion: List[str] = []
        self.query_count = 0
        self.register_time: Optional[datetime] = None
        self.last_used_time: Optional[datetime] = None
        super().__init__(full_path, image_bytes)

    @classmethod
    def from_db_instance(cls, db_record: Images):
        """从数据库记录创建 MaiEmoji 对象，如果记录标记为文件不存在则**抛出异常**

        调用者应该对数据库记录进行检查，如果 `no_file_flag` 为 True 则不应该调用此方法

        Args:
            db_record (Images): 数据库中的图片记录
        Returns:
            return (MaiEmoji): 包含图片信息的 MaiEmoji 对象
        Raises:
            ValueError: 如果数据库记录标记为文件不存在则抛出该异常
        """
        if db_record.no_file_flag:
            raise ValueError(f"数据库记录 {db_record.image_hash} 标记为文件不存在，无法创建 MaiEmoji 对象")
        obj = cls(db_record.full_path)
        obj.file_hash = db_record.image_hash
        obj.description = db_record.description
        if db_record.emotion:
            obj.emotion = db_record.emotion.split(",")
        obj.query_count = db_record.query_count
        obj.last_used_time = db_record.last_used_time
        obj.register_time = db_record.register_time
        return obj

    def to_db_instance(self) -> Images:
        emotion_str = ",".join(self.emotion) if self.emotion else None
        return Images(
            image_hash=self.file_hash,
            description=self.description,
            full_path=str(self.full_path),
            image_type=ImageType.EMOJI,
            emotion=emotion_str,
            query_count=self.query_count,
            last_used_time=self.last_used_time,
            register_time=self.register_time,
        )


class MaiImage(BaseImageDataModel):
    """麦麦图片数据模型，仅当**图片文件存在**时才应该创建此对象，数据库记录如果标记为文件不存在`(no_file_flag = True)`则不应该调用 `from_db_instance` 方法来创建此对象"""

    def __init__(self, full_path: str | Path, image_bytes: Optional[bytes] = None):
        self.description: str = ""
        self.vlm_processed: bool = False
        super().__init__(full_path, image_bytes)

    @classmethod
    def from_db_instance(cls, db_record: Images):
        """从数据库记录创建 MaiImage 对象，如果记录标记为文件不存在则**抛出异常**

        调用者应该对数据库记录进行检查，如果 `no_file_flag` 为 True 则不应该调用此方法

        Args:
            db_record (Images): 数据库中的图片记录
        Returns:
            return (MaiImage): 包含图片信息的 MaiImage 对象
        Raises:
            ValueError: 如果数据库记录标记为文件不存在则抛出该异常
        """
        if db_record.no_file_flag:
            raise ValueError(f"数据库记录 {db_record.image_hash} 标记为文件不存在，无法创建 MaiImage 对象")
        obj = cls(db_record.full_path)
        obj.file_hash = db_record.image_hash
        obj.full_path = Path(db_record.full_path)
        obj.description = db_record.description
        obj.vlm_processed = db_record.vlm_processed
        return obj

    def to_db_instance(self) -> Images:
        return Images(
            image_hash=self.file_hash,
            description=self.description,
            full_path=str(self.full_path),
            image_type=ImageType.IMAGE,
            vlm_processed=self.vlm_processed,
        )

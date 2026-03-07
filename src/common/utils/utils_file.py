from pathlib import Path
from sqlmodel import select

import hashlib

from src.common.logger import get_logger
from src.common.database.database_model import BinaryData
from src.common.database.database import get_db_session

logger = get_logger("file_utils")


class FileUtils:
    @staticmethod
    def save_binary_to_file(file_path: Path, data: bytes):
        """
        将字节数据保存到指定文件路径

        Args:
            file_path (Path): 目标文件路径
            data (bytes): 要保存的字节数据
        Raises:
            IOError: 如果写入文件时发生错误
        """
        try:
            file_path = file_path.absolute().resolve()
            with file_path.open("wb") as f:
                f.write(data)
            with get_db_session() as session:
                # 计算数据哈希
                data_hash = hashlib.sha256(data).hexdigest()
                # 创建 BinaryData 记录
                binary_data_record = BinaryData(data_hash=data_hash, full_path=str(file_path))
                session.add(binary_data_record)
                session.commit()
        except Exception as e:
            logger.error(f"保存文件 {file_path} 失败: {e}")
            raise e

    @staticmethod
    def get_file_path_by_hash(data_hash: str) -> Path:
        """
        根据数据哈希获取文件路径

        Args:
            data_hash (str): 数据的哈希值

        Returns:
            Path: 对应的数据文件路径
        """
        with get_db_session() as session:
            statement = select(BinaryData).filter_by(data_hash=data_hash).limit(1)
            if binary_data := session.exec(statement).first():
                return Path(binary_data.full_path)
            else:
                raise FileNotFoundError(f"未找到哈希值为 {data_hash} 的数据文件记录")

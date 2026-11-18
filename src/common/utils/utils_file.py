from pathlib import Path
from sqlmodel import Session, select
from tempfile import TemporaryDirectory

import hashlib

from src.common.database.database import get_db_session
from src.common.database.database_model import BinaryData
from src.common.logger import get_logger
from src.common.paths import get_instance_root

from .audio_format import detect_audio_format

logger = get_logger("file_utils")


class FileUtils:
    @staticmethod
    def store_voice(session: Session, data: bytes) -> str:
        """按内容保存语音文件，索引与引用它的消息在同一事务提交。"""
        digest = hashlib.sha256(data).hexdigest()
        record = session.exec(select(BinaryData).filter_by(data_hash=digest).limit(1)).first()
        root = get_instance_root()
        if record is not None and (root / record.full_path).is_file():
            return digest

        extension, _ = detect_audio_format(data)
        relative_path = Path("data", "voices", f"{digest}.{extension or 'bin'}")
        path = root / relative_path
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=path.parent) as staging:
                staged = Path(staging) / path.name
                staged.write_bytes(data)
                staged.replace(path)
        if record is None:
            record = BinaryData(data_hash=digest, full_path=relative_path.as_posix())
        else:
            record.full_path = relative_path.as_posix()
        session.add(record)
        session.flush([record])
        return digest

    @staticmethod
    def read_voice(data_hash: str) -> bytes:
        """读取指定哈希对应的语音原件。"""
        path = FileUtils.get_file_path_by_hash(data_hash)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != data_hash:
            raise ValueError(f"语音文件内容与记录的哈希不一致: {path}")
        return data

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
        with get_db_session(auto_commit=False) as session:
            statement = select(BinaryData).filter_by(data_hash=data_hash).limit(1)
            if binary_data := session.exec(statement).first():
                return get_instance_root() / binary_data.full_path
            else:
                raise FileNotFoundError(f"未找到哈希值为 {data_hash} 的数据文件记录")

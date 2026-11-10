from datetime import datetime
from typing import Dict, Optional

import json

from src.common.database.database_model import Jargon, JargonCreatedBy
from src.common.logger import get_logger

from . import BaseDatabaseDataModel

logger = get_logger("jargon_data_model")


class MaiJargon(BaseDatabaseDataModel[Jargon]):
    """Jargon 数据模型，与数据库模型 Jargon 互转。"""

    def __init__(
        self,
        content: str,
        meaning: str,
        item_id: Optional[int] = None,
        evidence_messages: Optional[str] = None,
        session_id_list: Optional[Dict[str, int]] = None,
        count: int = 0,
        is_jargon: Optional[bool] = False,
        is_complete: bool = False,
        is_global: bool = False,
        last_inference_count: int = 0,
        created_by: JargonCreatedBy = JargonCreatedBy.AI,
        created_timestamp: Optional[datetime] = None,
        updated_timestamp: Optional[datetime] = None,
    ):
        self.item_id = item_id
        """自增主键ID"""
        self.content = content
        """黑话内容"""
        self.evidence_messages = evidence_messages
        """黑话证据消息引用，格式为二维列表的 JSON 字符串"""
        self.meaning = meaning
        """黑话含义"""
        self.session_id_list = session_id_list or {}
        """会话ID字典，区分是否为全局黑话，格式为{"session_id": session_count, ...}，如果为空表示全局黑话"""
        self.count = count
        """使用次数"""
        self.is_jargon = is_jargon
        """是否为黑话，False表示为白话"""
        self.is_complete = is_complete
        """是否为已经完成全部推断（count > 100后不再推断）"""
        self.is_global = is_global
        """是否为全局黑话（独立于session_id_dict）"""
        self.last_inference_count = last_inference_count
        """上一次进行推断时的count值，用于判断是否需要重新推断"""
        self.created_by = created_by
        """创建来源，AI 表示自动学习，MANUAL 表示手动创建"""
        self.created_timestamp = created_timestamp or datetime.now()
        """创建时间"""
        self.updated_timestamp = updated_timestamp or self.created_timestamp
        """更新时间"""

    @classmethod
    def from_db_instance(cls, db_record: Jargon) -> "MaiJargon":
        """从数据库模型创建 MaiJargon 实例。"""
        json_list: Dict[str, int] = {}
        try:
            # 解析存储的字符串为字典
            json_list = json.loads(db_record.session_id_dict)
        except Exception as e:
            logger.error(f"Error parsing session_id_list: {e}")
        result = cls(
            item_id=db_record.id,
            content=db_record.content,
            meaning=db_record.meaning,
            evidence_messages=db_record.evidence_messages,
            session_id_list=json_list,
            count=db_record.count,
            is_jargon=db_record.is_jargon,
            is_complete=db_record.is_complete,
            is_global=db_record.is_global,
            last_inference_count=db_record.last_inference_count,
            created_by=db_record.created_by,
            created_timestamp=db_record.created_timestamp,
            updated_timestamp=db_record.updated_timestamp,
        )
        # 数据库中的 NULL 是原始身份的一部分，不能在读取时伪造为当前时间。
        result.created_timestamp = db_record.created_timestamp
        result.updated_timestamp = db_record.updated_timestamp
        return result

    def to_db_instance(self) -> Jargon:
        """将 MaiJargon 转换为数据库模型 Jargon。"""
        dumped_session_id_list = json.dumps(self.session_id_list)
        return Jargon(
            content=self.content,
            evidence_messages=self.evidence_messages,
            meaning=self.meaning,
            session_id_dict=dumped_session_id_list,
            count=self.count,
            is_jargon=self.is_jargon,
            is_complete=self.is_complete,
            is_global=self.is_global,
            last_inference_count=self.last_inference_count,
            created_by=self.created_by,
            created_timestamp=self.created_timestamp,
            updated_timestamp=self.updated_timestamp,
        )

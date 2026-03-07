from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

import json

from src.common.database.database_model import PersonInfo

from . import BaseDatabaseDataModel


@dataclass
class GroupCardnameInfo:
    group_id: str
    group_cardname: str


class MaiPersonInfo(BaseDatabaseDataModel[PersonInfo]):
    def __init__(
        self,
        *,
        is_known: bool,
        person_id: str,
        platform: str,
        user_id: str,
        user_nickname: str,
        know_counts: int,
        person_name: Optional[str] = None,
        name_reason: Optional[str] = None,
        group_cardname_list: Optional[List[GroupCardnameInfo]] = None,
        memory_points: Optional[List[str]] = None,
        first_known_time: Optional[datetime] = None,
        last_known_time: Optional[datetime] = None,
    ):
        self.is_known = is_known
        """标记是否为已知用户，已知用户指在数据库中存在记录的用户"""
        self.person_id: str = person_id
        """用户专有ID"""
        self.person_name: Optional[str] = person_name
        """用户名称"""
        self.name_reason: Optional[str] = name_reason
        """用户名称的来源或变更原因说明"""
        self.platform: str = platform
        """平台标识"""
        self.user_id: str = user_id
        """用户在平台上的ID"""
        self.user_nickname: str = user_nickname
        """用户在平台上的昵称"""
        self.group_cardname_list: Optional[List[GroupCardnameInfo]] = group_cardname_list
        """用户在不同群中的昵称列表"""
        self.memory_points: Optional[List[str]] = memory_points
        """与用户相关的记忆点列表"""
        self.know_counts: int = know_counts
        """已知用户被认识的次数"""
        self.first_known_time: Optional[datetime] = first_known_time
        """第一次被认识的时间"""
        self.last_known_time: Optional[datetime] = last_known_time
        """最后一次被认识的时间"""

    @classmethod
    def from_db_instance(cls, db_record: "PersonInfo"):
        nickname_json = json.loads(db_record.group_cardname) if db_record.group_cardname else None
        group_cardname_list = [GroupCardnameInfo(**item) for item in nickname_json] if nickname_json else None
        memory_points = json.loads(db_record.memory_points) if db_record.memory_points else None
        return cls(
            is_known=db_record.is_known,
            person_id=db_record.person_id,
            person_name=db_record.person_name,
            name_reason=db_record.name_reason,
            platform=db_record.platform,
            user_id=db_record.user_id,
            user_nickname=db_record.user_nickname,
            group_cardname_list=group_cardname_list,
            memory_points=memory_points,
            know_counts=db_record.know_counts,
            first_known_time=db_record.first_known_time,
            last_known_time=db_record.last_known_time,
        )

    def to_db_instance(self) -> "PersonInfo":
        group_cardname = (
            json.dumps([gc.__dict__ for gc in self.group_cardname_list]) if self.group_cardname_list else None
        )
        return PersonInfo(
            is_known=self.is_known,
            person_id=self.person_id,
            person_name=self.person_name,
            name_reason=self.name_reason,
            platform=self.platform,
            user_id=self.user_id,
            user_nickname=self.user_nickname,
            group_cardname=group_cardname,
            memory_points=json.dumps(self.memory_points) if self.memory_points else None,
            know_counts=self.know_counts,
            first_known_time=self.first_known_time,
            last_known_time=self.last_known_time,
        )

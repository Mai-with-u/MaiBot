from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, List, Mapping, Optional, Sequence
from uuid import uuid4

import json

from src.common.database.database_model import PersonInfo

from . import BaseDatabaseDataModel


@dataclass
class GroupCardnameInfo:
    group_id: str
    group_cardname: str


def _normalize_group_cardname_item(raw_item: Mapping[str, Any]) -> Optional[GroupCardnameInfo]:
    """将单条群名片数据规范化为统一结构。

    Args:
        raw_item: 原始群名片字典，必须包含 `group_id` 和 `group_cardname`。

    Returns:
        Optional[GroupCardnameInfo]: 规范化后的群名片信息；若数据不完整则返回 ``None``。
    """
    group_id = str(raw_item.get("group_id") or "").strip()
    group_cardname = str(raw_item.get("group_cardname") or "").strip()
    if not group_id or not group_cardname:
        return None
    return GroupCardnameInfo(group_id=group_id, group_cardname=group_cardname)


def parse_group_cardname_json(group_cardname_json: Optional[str]) -> Optional[List[GroupCardnameInfo]]:
    """解析数据库中的群名片 JSON 字段。

    Args:
        group_cardname_json: 数据库存储的群名片 JSON 字符串。

    Returns:
        Optional[List[GroupCardnameInfo]]: 解析并规范化后的群名片列表；若字段为空或无有效项则返回 ``None``。

    Raises:
        json.JSONDecodeError: 当 JSON 文本格式非法时抛出。
        TypeError: 当输入值类型不符合 `json.loads()` 要求时抛出。
    """
    if not group_cardname_json:
        return None

    raw_items = json.loads(group_cardname_json)
    if not isinstance(raw_items, list):
        return None

    normalized_items: List[GroupCardnameInfo] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        if normalized_item := _normalize_group_cardname_item(raw_item):
            normalized_items.append(normalized_item)

    return normalized_items or None


def dump_group_cardname_records(
    group_cardname_records: Optional[Sequence[GroupCardnameInfo | Mapping[str, Any]]],
) -> str:
    """将群名片列表序列化为数据库使用的标准 JSON 字符串。

    Args:
        group_cardname_records: 待序列化的群名片列表，支持 `GroupCardnameInfo`
            对象和包含 `group_id` / `group_cardname` 的字典。

    Returns:
        str: 统一使用 `group_cardname` 键名的 JSON 字符串。
    """
    normalized_items: List[GroupCardnameInfo] = []
    for raw_item in group_cardname_records or []:
        if isinstance(raw_item, GroupCardnameInfo):
            normalized_items.append(raw_item)
            continue
        if isinstance(raw_item, Mapping):
            if normalized_item := _normalize_group_cardname_item(raw_item):
                normalized_items.append(normalized_item)

    return json.dumps([asdict(item) for item in normalized_items], ensure_ascii=False)


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
        group_cardname_list: Optional[List[GroupCardnameInfo]] = None,
        first_known_time: Optional[datetime] = None,
        last_known_time: Optional[datetime] = None,
        individual_id: Optional[str] = None,
    ) -> None:
        self.is_known = is_known
        """标记是否为已知用户，已知用户指在数据库中存在记录的用户"""
        self.individual_id: str = individual_id if individual_id is not None else uuid4().hex
        self.person_id: str = person_id
        """用户专有ID"""
        self.person_name: Optional[str] = person_name
        """用户名称"""
        self.platform: str = platform
        """平台标识"""
        self.user_id: str = user_id
        """用户在平台上的ID"""
        self.user_nickname: str = user_nickname
        """用户在平台上的昵称"""
        self.group_cardname_list: Optional[List[GroupCardnameInfo]] = group_cardname_list
        """用户在不同群中的昵称列表"""
        self.know_counts: int = know_counts
        """已知用户被认识的次数"""
        self.first_known_time: Optional[datetime] = first_known_time
        """第一次被认识的时间"""
        self.last_known_time: Optional[datetime] = last_known_time
        """最后一次被认识的时间"""

    @classmethod
    def from_db_instance(cls, db_record: "PersonInfo") -> "MaiPersonInfo":
        """从数据库记录构造人物信息数据模型。

        Args:
            db_record: 数据库中的人物信息记录。

        Returns:
            MaiPersonInfo: 转换后的数据模型对象。
        """
        group_cardname_list = parse_group_cardname_json(db_record.group_cardname)
        return cls(
            is_known=db_record.individual.is_known,
            person_id=db_record.person_id,
            individual_id=db_record.individual_id,
            person_name=db_record.individual.display_name,
            platform=db_record.platform,
            user_id=db_record.user_id,
            user_nickname=db_record.user_nickname,
            group_cardname_list=group_cardname_list,
            know_counts=db_record.know_counts,
            first_known_time=db_record.first_known_time,
            last_known_time=db_record.last_known_time,
        )

    def to_db_instance(self) -> "PersonInfo":
        """将当前数据模型转换为数据库记录对象。

        Returns:
            PersonInfo: 可直接写入数据库的模型实例。
        """
        group_cardname = dump_group_cardname_records(self.group_cardname_list)
        return PersonInfo(
            is_known=self.is_known,
            person_id=self.person_id,
            individual_id=self.individual_id,
            person_name=self.person_name,
            platform=self.platform,
            user_id=self.user_id,
            user_nickname=self.user_nickname,
            group_cardname=group_cardname,
            know_counts=self.know_counts,
            first_known_time=self.first_known_time,
            last_known_time=self.last_known_time,
        )

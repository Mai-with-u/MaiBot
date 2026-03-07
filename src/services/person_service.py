"""个人信息服务模块

提供个人信息查询的核心功能。
"""

from typing import Any

from src.common.logger import get_logger
from src.person_info.person_info import Person

logger = get_logger("person_service")


def get_person_id(platform: str, user_id: int | str) -> str:
    """根据平台和用户ID获取person_id

    Args:
        platform: 平台名称，如 "qq", "telegram" 等
        user_id: 用户ID

    Returns:
        str: 唯一的person_id（MD5哈希值）
    """
    try:
        return Person(platform=platform, user_id=str(user_id)).person_id
    except Exception as e:
        logger.error(f"[PersonService] 获取person_id失败: platform={platform}, user_id={user_id}, error={e}")
        return ""


async def get_person_value(person_id: str, field_name: str, default: Any = None) -> Any:
    """根据person_id和字段名获取某个值

    Args:
        person_id: 用户的唯一标识ID
        field_name: 要获取的字段名，如 "nickname", "impression" 等
        default: 当字段不存在或获取失败时返回的默认值

    Returns:
        Any: 字段值或默认值
    """
    try:
        person = Person(person_id=person_id)
        value = getattr(person, field_name)
        return value if value is not None else default
    except Exception as e:
        logger.error(f"[PersonService] 获取用户信息失败: person_id={person_id}, field={field_name}, error={e}")
        return default


def get_person_id_by_name(person_name: str) -> str:
    """根据用户名获取person_id

    Args:
        person_name: 用户名

    Returns:
        str: person_id，如果未找到返回空字符串
    """
    try:
        person = Person(person_name=person_name)
        return person.person_id
    except Exception as e:
        logger.error(f"[PersonService] 根据用户名获取person_id失败: person_name={person_name}, error={e}")
        return ""

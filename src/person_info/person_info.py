from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import text
from sqlmodel import col, select

import hashlib
import json
import time

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.common.data_models.person_info_data_model import dump_group_cardname_records, parse_group_cardname_json
from src.common.database.database import get_db_session
from src.common.database.database_model import Individual, PersonInfo
from src.common.logger import get_logger
from src.config.config import global_config
from src.services.memory_service import memory_service

from .identity import person_id_for, resolve_person_id, resolve_user_id

logger = get_logger("person_info")


def _to_group_cardname_records(group_cardname_json: Optional[str]) -> list[dict[str, str]]:
    """将数据库中的群名片 JSON 转换为 `Person` 内部使用的结构。

    Args:
        group_cardname_json: 数据库存储的群名片 JSON 字符串。

    Returns:
        list[dict[str, str]]: 统一使用 `group_cardname` 键名的群名片列表。

    Raises:
        json.JSONDecodeError: 当 JSON 文本格式非法时抛出。
        TypeError: 当输入值类型不符合 `json.loads()` 要求时抛出。
    """
    group_cardname_list = parse_group_cardname_json(group_cardname_json)
    if not group_cardname_list:
        return []

    return [
        {
            "group_id": group_cardname.group_id,
            "group_cardname": group_cardname.group_cardname,
        }
        for group_cardname in group_cardname_list
    ]


def get_person_id(platform: str, user_id: Union[int, str]) -> str:
    """获取唯一id"""
    return person_id_for(platform, resolve_user_id(platform, str(user_id)))


def get_person_id_by_person_name(person_name: str) -> str:
    """根据用户名获取用户ID"""
    try:
        with get_db_session(auto_commit=False) as session:
            statement = select(PersonInfo.person_id).join(Individual, PersonInfo.individual_id == Individual.individual_id).where(col(Individual.display_name) == person_name).limit(2)
            person_ids = session.exec(statement).all()
            return str(person_ids[0]) if len(person_ids) == 1 else ""
    except Exception as e:
        logger.error(f"根据用户名 {person_name} 获取用户ID时出错: {e}")
        return ""


def resolve_person_id_for_memory(
    *,
    person_name: str = "",
    platform: str = "",
    user_id: Union[int, str, None] = None,
    strict_known: bool = False,
) -> str:
    """解析长期记忆检索/写入使用的人物 ID。

    解析顺序：
    1. 有 `platform + user_id` 时只按真实 ID 确认身份，不让同名人物覆盖它
    2. 没有 ID 时才按唯一的 `person_name` 映射数据库中的 `person_id`
    3. 若 `strict_known=True`，则要求该 `person_id` 已被认识
    """
    clean_platform = str(platform or "").strip()
    clean_user_id = str(user_id or "").strip()
    if clean_platform and clean_user_id:
        candidate = get_person_id(clean_platform, clean_user_id)
        if strict_known and not is_person_known(person_id=candidate):
            return ""
        return candidate

    clean_name = str(person_name or "").strip()
    if clean_name:
        candidate = get_person_id_by_person_name(clean_name)
        if candidate and (not strict_known or is_person_known(person_id=candidate)):
            return candidate

    return ""


def is_person_known(
    person_id: Optional[str] = None,
    user_id: Optional[str] = None,
    platform: Optional[str] = None,
    person_name: Optional[str] = None,
) -> bool:  # sourcery skip: extract-duplicate-method
    if person_id:
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()
            return person.individual.is_known if person else False
    elif user_id and platform:
        person_id = get_person_id(platform, user_id)
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()
            return person.individual.is_known if person else False
    elif person_name:
        person_id = get_person_id_by_person_name(person_name)
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()
            return person.individual.is_known if person else False
    else:
        return False


def calculate_string_similarity(s1: str, s2: str) -> float:
    """
    计算两个字符串的相似度

    Args:
        s1: 第一个字符串
        s2: 第二个字符串

    Returns:
        float: 相似度，范围0-1，1表示完全相同
    """
    if s1 == s2:
        return 1.0

    if not s1 or not s2:
        return 0.0

    # 计算Levenshtein距离

    distance = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))

    # 计算相似度：1 - (编辑距离 / 最大长度)
    similarity = 1 - (distance / max_len if max_len > 0 else 0)
    return similarity


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    计算两个字符串的编辑距离

    Args:
        s1: 第一个字符串
        s2: 第二个字符串

    Returns:
        int: 编辑距离
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


class Person:
    @classmethod
    def register_person(
        cls,
        platform: str,
        user_id: str,
        nickname: str,
        group_id: Optional[str] = None,
        group_nick_name: Optional[str] = None,
    ) -> Optional["Person"]:
        """
        注册新用户的类方法
        必须输入 platform、user_id 和 nickname 参数

        Args:
            platform: 平台名称
            user_id: 用户ID
            nickname: 用户昵称
            group_id: 群号（可选，仅在群聊时提供）
            group_nick_name: 群昵称（可选，仅在群聊时提供）

        Returns:
            Person: 新注册的Person实例
        """
        if not platform or not user_id or not nickname:
            logger.error("注册用户失败：platform、user_id 和 nickname 都是必需参数")
            return None

        # 生成唯一的person_id
        user_id = resolve_user_id(platform, user_id)
        person_id = get_person_id(platform, user_id)

        with get_db_session() as session:
            exists = session.exec(select(PersonInfo.id).where(PersonInfo.person_id == person_id)).first() is not None
        if exists:
            logger.debug(f"用户 {nickname} 已存在")
            person = Person(person_id=person_id)
            # 如果是群聊，更新群昵称
            if group_id and group_nick_name:
                person.add_group_nick_name(group_id, group_nick_name)
            return person

        # 创建Person实例
        person = cls.__new__(cls)

        # 设置基本属性
        person._loaded_individual_profile = None
        person.person_id = person_id
        person.platform = platform
        person.user_id = user_id
        person.nickname = nickname

        # 初始化默认值
        person.is_known = True  # 注册后立即标记为已认识
        person.person_name = nickname  # 使用nickname作为初始person_name
        person.know_times = 1
        person.know_since = time.time()
        person.last_know = time.time()
        person.group_cardname_list = []  # 初始化群名片列表

        # 如果是群聊，添加群昵称
        if group_id and group_nick_name:
            person.group_cardname_list.append({"group_id": group_id, "group_cardname": group_nick_name})

        # 同步到数据库
        person.sync_to_database()

        logger.info(f"成功注册新用户：{person_id}，平台：{platform}，昵称：{nickname}")

        return person

    def _is_bot_self(self, platform: str, user_id: str) -> bool:
        """判断给定的平台和用户ID是否是机器人自己

        这个函数统一处理所有平台（包括 QQ、Telegram、WebUI 等）的机器人识别逻辑。

        Args:
            platform: 消息平台（如 "qq", "telegram", "webui" 等）
            user_id: 用户ID

        Returns:
            bool: 如果是机器人自己则返回 True，否则返回 False
        """
        from src.chat.utils.utils import is_bot_self

        return is_bot_self(platform, user_id)

    def __init__(self, platform: str = "", user_id: str = "", person_id: str = "", person_name: str = ""):
        if platform and user_id:
            user_id = resolve_user_id(platform, user_id)
        # 使用统一的机器人识别函数（支持多平台，包括 WebUI）
        if self._is_bot_self(platform, user_id):
            self.is_known = True
            self.person_id = get_person_id(platform, user_id)
            self.user_id = user_id
            self.platform = platform
            self.nickname = global_config.bot.nickname
            self.person_name = global_config.bot.nickname
            self.group_cardname_list: list[dict[str, str]] = []
            return

        self.user_id = ""
        self.platform = ""

        if person_id:
            self.person_id = resolve_person_id(person_id)
        elif person_name:
            self.person_id = get_person_id_by_person_name(person_name)
            if not self.person_id:
                self.is_known = False
                logger.warning(f"根据用户名 {person_name} 获取用户ID时，不存在用户{person_name}")
                return
        elif platform and user_id:
            self.person_id = get_person_id(platform, user_id)
            self.user_id = user_id
            self.platform = platform
        else:
            logger.error("Person 初始化失败，缺少必要参数")
            raise ValueError("Person 初始化失败，缺少必要参数")

        with get_db_session() as session:
            exists = session.exec(select(PersonInfo.id).where(PersonInfo.person_id == self.person_id)).first() is not None
        if not exists:
            self.is_known = False
            logger.debug(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")
            self.person_name = f"未知用户{self.person_id[:4]}"
            return
            # raise ValueError(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")

        self.is_known = False

        # 初始化默认值
        self.nickname = ""
        self.person_name: Optional[str] = None
        self._loaded_individual_profile: Optional[Tuple[str, str, bool]] = None
        self.know_times = 0
        self.know_since = None
        self.last_know: Optional[float] = None
        self.group_cardname_list: list[dict[str, str]] = []  # 群名片列表，存储 {"group_id": str, "group_cardname": str}

        # 从数据库加载数据
        self.load_from_database()

    def add_group_nick_name(self, group_id: str, group_nick_name: str):
        """
        添加或更新群昵称

        Args:
            group_id: 群号
            group_nick_name: 群昵称
        """
        if not group_id or not group_nick_name:
            return

        # 检查是否已存在该群号的记录
        for item in self.group_cardname_list:
            if item.get("group_id") == group_id:
                # 更新现有记录
                item["group_cardname"] = group_nick_name
                self.sync_to_database()
                logger.debug(f"更新用户 {self.person_id} 在群 {group_id} 的群昵称为 {group_nick_name}")
                return

        # 添加新记录
        self.group_cardname_list.append({"group_id": group_id, "group_cardname": group_nick_name})
        self.sync_to_database()
        logger.debug(f"添加用户 {self.person_id} 在群 {group_id} 的群昵称 {group_nick_name}")

    def load_from_database(self):
        """从数据库加载个人信息数据"""
        try:
            with get_db_session() as session:
                statement = select(PersonInfo).where(col(PersonInfo.person_id) == self.person_id).limit(1)
                record = session.exec(statement).first()

                if record:
                    self.user_id = record.user_id or ""
                    self.platform = record.platform or ""
                    self.is_known = record.individual.is_known
                    self.nickname = record.user_nickname or ""
                    self.person_name = record.individual.display_name
                    self.individual_id = record.individual_id
                    self._loaded_individual_profile = (
                        record.individual_id, self.person_name, self.is_known,
                    )
                    self.know_since = record.first_known_time.timestamp() if record.first_known_time else None
                    self.last_know = record.last_known_time.timestamp() if record.last_known_time else None
                    self.know_times = record.know_counts or 0

                    # 处理 group_cardname 字段（JSON 格式的列表）
                    if record.group_cardname:
                        try:
                            self.group_cardname_list = _to_group_cardname_records(record.group_cardname)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"解析用户 {self.person_id} 的group_cardname字段失败，使用默认值")
                            self.group_cardname_list = []
                    else:
                        self.group_cardname_list = []

                    logger.debug(f"已从数据库加载用户 {self.person_id} 的信息")
                else:
                    self.sync_to_database()
                    logger.info(f"用户 {self.person_id} 在数据库中不存在，使用默认值并创建")

        except Exception as e:
            logger.error(f"从数据库加载用户 {self.person_id} 信息时出错: {e}")
            # 出错时保持默认值

    def sync_to_database(self) -> None:
        """将所有属性同步回数据库"""
        if not self.is_known and self._loaded_individual_profile is None:
            return
        try:
            group_cardname_value = dump_group_cardname_records(self.group_cardname_list)
            first_known_time = datetime.fromtimestamp(self.know_since) if self.know_since else None
            last_known_time = datetime.fromtimestamp(self.last_know) if self.last_know else None

            saved_profile = self._loaded_individual_profile
            with get_db_session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                statement = select(PersonInfo).where(col(PersonInfo.person_id) == self.person_id).limit(1)
                record = session.exec(statement).first()

                if record:
                    record.person_id = self.person_id
                    record.platform = self.platform
                    record.user_id = self.user_id
                    record.user_nickname = self.nickname
                    loaded = self._loaded_individual_profile
                    desired = (self.person_name, self.is_known)
                    if loaded is not None and desired != loaded[1:]:
                        individual = record.individual
                        current = (individual.individual_id, individual.display_name, individual.is_known)
                        if current != loaded:
                            raise ValueError("人物资料或归属已变化，请重新加载后修改")
                        if not self.person_name or not self.person_name.strip():
                            raise ValueError("人物名称不能为空")
                        individual.display_name = self.person_name.strip()
                        individual.is_known = self.is_known
                        session.add(individual)
                        saved_profile = (individual.individual_id, individual.display_name, individual.is_known)
                    record.know_counts = self.know_times
                    record.first_known_time = first_known_time
                    record.last_known_time = last_known_time
                    record.group_cardname = group_cardname_value
                    session.add(record)
                else:
                    record = PersonInfo(
                        person_id=self.person_id,
                        is_known=self.is_known,
                        platform=self.platform,
                        user_id=self.user_id,
                        user_nickname=self.nickname,
                        person_name=self.person_name,
                        know_counts=self.know_times,
                        first_known_time=first_known_time,
                        last_known_time=last_known_time,
                        group_cardname=group_cardname_value,
                    )
                    session.add(record)
                    session.flush()
                    saved_profile = (
                        record.individual_id, self.person_name or self.nickname, self.is_known,
                    )

            if saved_profile is not None:
                self.individual_id = saved_profile[0]
                self._loaded_individual_profile = saved_profile
            logger.debug(f"已同步用户 {self.person_id} 的信息到数据库")

        except Exception as e:
            logger.error(f"同步用户 {self.person_id} 信息到数据库时出错: {e}")
            raise


async def store_person_memory_from_answer(
    person_name: str,
    memory_content: str,
    chat_id: str,
    *,
    person_id: str = "",
    evidence_source: str = "user_supported",
    evidence_message_ids: Optional[List[str]] = None,
    fact_claim: Optional[Dict[str, Any]] = None,
) -> None:
    """将人物事实写入长期记忆系统。

    Args:
        person_name: 人物名称
        memory_content: 记忆内容
        chat_id: 聊天ID
    """
    clean_content = str(memory_content or "").strip()
    if not clean_content:
        logger.debug("人物事实写回跳过：memory_content 为空")
        return

    clean_chat_id = str(chat_id or "").strip()
    if not clean_chat_id:
        logger.warning("人物事实写回失败：chat_id 为空")
        return

    clean_person_name = str(person_name or "").strip()
    clean_person_id = person_id.strip()
    try:
        # 从 chat_id 获取 session
        session = _chat_manager.get_session_by_session_id(clean_chat_id)
        if not session:
            logger.warning(f"无法获取session for chat_id: {clean_chat_id}")
            return

        session_platform = str(getattr(session, "platform", "") or "").strip()
        session_user_id = str(getattr(session, "user_id", "") or "").strip()
        session_group_id = str(getattr(session, "group_id", "") or "").strip()

        person_id = clean_person_id or resolve_person_id_for_memory(
            person_name=clean_person_name,
            platform=session_platform,
            user_id=session_user_id,
        )
        if not person_id:
            logger.warning(f"无法确定person_id for person_name: {clean_person_name}, chat_id: {clean_chat_id}")
            return

        person = Person(person_id=person_id)
        if not person.is_known:
            logger.warning(f"用户 {clean_person_name or person_id} (person_id: {person_id}) 尚未认识，跳过写回")
            return

        participant_name = str(person.person_name or person.nickname or clean_person_name or "").strip()

        payload_fingerprint = hashlib.md5(f"{person_id}|{clean_chat_id}|{clean_content}".encode()).hexdigest()
        external_id = f"person_fact:{person_id}:{payload_fingerprint}"

        result = await memory_service.ingest_text(
            external_id=external_id,
            source_type="person_fact",
            text=clean_content,
            chat_id=clean_chat_id,
            person_ids=[person_id],
            participants=[participant_name] if participant_name else [],
            tags=["person_fact"],
            metadata={
                "platform": session_platform,
                "person_id": person_id,
                "person_name": participant_name,
                "writeback_source": "memory_flow_service",
                "evidence_source": str(evidence_source or "user_supported"),
                "evidence_message_ids": evidence_message_ids or [],
                **({"fact_claim": fact_claim} if fact_claim is not None else {}),
            },
            respect_filter=True,
            user_id=session_user_id,
            group_id=session_group_id,
        )

        if getattr(result, "success", False):
            logger.info(
                f"成功写回人物事实到长期记忆: person={participant_name} person_id={person_id} chat_id={clean_chat_id}"
            )
        else:
            logger.warning(
                f"人物事实写回长期记忆失败: person={participant_name} person_id={person_id} "
                f"chat_id={clean_chat_id} detail={getattr(result, 'detail', '')}"
            )

    except Exception as e:
        logger.error(f"存储人物记忆失败: {e}")

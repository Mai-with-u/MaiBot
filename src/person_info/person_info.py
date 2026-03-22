import hashlib
import asyncio
import json
import time
import random
import math

from json_repair import repair_json
from typing import Union, Optional, Dict, List
from datetime import datetime

from sqlalchemy import or_
from sqlmodel import col, select

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import PersonInfo
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.services.memory_service import memory_service


logger = get_logger("person_info")

relation_selection_model = LLMRequest(
    model_set=model_config.model_task_config.tool_use, request_type="relation_selection"
)


def get_person_id(platform: str, user_id: Union[int, str]) -> str:
    """获取唯一id"""
    if "-" in platform:
        platform = platform.split("-")[1]
    components = [platform, str(user_id)]
    key = "_".join(components)
    return hashlib.md5(key.encode()).hexdigest()


def get_person_id_by_person_name(person_name: str) -> str:
    """根据用户名获取用户ID"""
    clean_name = str(person_name or "").strip()
    if not clean_name:
        return ""
    try:
        with get_db_session() as session:
            statement = (
                select(PersonInfo)
                .where(
                    or_(
                        col(PersonInfo.person_name) == clean_name,
                        col(PersonInfo.user_nickname) == clean_name,
                    )
                )
                .limit(1)
            )
            record = session.exec(statement).first()
            if record and record.person_id:
                return record.person_id

            statement = (
                select(PersonInfo)
                .where(PersonInfo.group_cardname.contains(clean_name))
                .limit(1)
            )
            record = session.exec(statement).first()
        return record.person_id if record else ""
    except Exception as e:
        logger.error(f"根据用户名 {clean_name} 获取用户ID时出错: {e}")
        return ""


def resolve_person_id_for_memory(
    *,
    person_name: str = "",
    platform: str = "",
    user_id: Optional[Union[int, str]] = None,
) -> str:
    """统一人物记忆链路中的 person_id 解析。

    优先使用已知的人物名称/别名，其次退回到平台 + user_id 的稳定 ID。
    """
    name_token = str(person_name or "").strip()
    if name_token:
        resolved = get_person_id_by_person_name(name_token)
        if resolved:
            return resolved

    platform_token = str(platform or "").strip()
    user_token = str(user_id or "").strip()
    if platform_token and user_token:
        return get_person_id(platform_token, user_token)
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
            return person.is_known if person else False
    elif user_id and platform:
        person_id = get_person_id(platform, user_id)
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()
            return person.is_known if person else False
    elif person_name:
        person_id = get_person_id_by_person_name(person_name)
        with get_db_session() as session:
            statement = select(PersonInfo).where(col(PersonInfo.person_id) == person_id).limit(1)
            person = session.exec(statement).first()
            return person.is_known if person else False
    else:
        return False


def get_category_from_memory(memory_point: str) -> Optional[str]:
    """从记忆点中获取分类"""
    # 按照最左边的:符号进行分割，返回分割后的第一个部分作为分类
    if not isinstance(memory_point, str):
        return None
    parts = memory_point.split(":", 1)
    return parts[0].strip() if len(parts) > 1 else None


def get_weight_from_memory(memory_point: str) -> float:
    """从记忆点中获取权重"""
    # 按照最右边的:符号进行分割，返回分割后的最后一个部分作为权重
    if not isinstance(memory_point, str):
        return -math.inf
    parts = memory_point.rsplit(":", 1)
    if len(parts) <= 1:
        return -math.inf
    try:
        return float(parts[-1].strip())
    except Exception:
        return -math.inf


def get_memory_content_from_memory(memory_point: str) -> str:
    """从记忆点中获取记忆内容"""
    # 按:进行分割，去掉第一段和最后一段，返回中间部分作为记忆内容
    if not isinstance(memory_point, str):
        return ""
    parts = memory_point.split(":")
    return ":".join(parts[1:-1]).strip() if len(parts) > 2 else ""


def extract_categories_from_response(response: str) -> list[str]:
    """从response中提取所有<>包裹的内容"""
    if not isinstance(response, str):
        return []

    import re

    pattern = r"<([^<>]+)>"
    matches = re.findall(pattern, response)
    return matches


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
    ):
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
        person_id = get_person_id(platform, user_id)

        if is_person_known(person_id=person_id):
            logger.debug(f"用户 {nickname} 已存在")
            person = Person(person_id=person_id)
            # 如果是群聊，更新群昵称
            if group_id and group_nick_name:
                person.add_group_nick_name(group_id, group_nick_name)
            return person

        # 创建Person实例
        person = cls.__new__(cls)

        # 设置基本属性
        person.person_id = person_id
        person.platform = platform
        person.user_id = user_id
        person.nickname = nickname

        # 初始化默认值
        person.is_known = True  # 注册后立即标记为已认识
        person.person_name = nickname  # 使用nickname作为初始person_name
        person.name_reason = "用户注册时设置的昵称"
        person.know_times = 1
        person.know_since = time.time()
        person.last_know = time.time()
        person.memory_points = []
        person.group_nick_name = []  # 初始化群昵称列表

        # 如果是群聊，添加群昵称
        if group_id and group_nick_name:
            person.add_group_nick_name(group_id, group_nick_name)

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
        # 使用统一的机器人识别函数（支持多平台，包括 WebUI）
        if self._is_bot_self(platform, user_id):
            self.is_known = True
            self.person_id = get_person_id(platform, user_id)
            self.user_id = user_id
            self.platform = platform
            self.nickname = global_config.bot.nickname
            self.person_name = global_config.bot.nickname
            self.group_nick_name: list[dict[str, str]] = []
            return

        self.user_id = ""
        self.platform = ""

        if person_id:
            self.person_id = person_id
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

        if not is_person_known(person_id=self.person_id):
            self.is_known = False
            logger.debug(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")
            self.person_name = f"未知用户{self.person_id[:4]}"
            return
            # raise ValueError(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")

        self.is_known = False

        # 初始化默认值
        self.nickname = ""
        self.person_name: Optional[str] = None
        self.name_reason: Optional[str] = None
        self.know_times = 0
        self.know_since = None
        self.last_know: Optional[float] = None
        self.memory_points = []
        self.group_nick_name: list[dict[str, str]] = []  # 群昵称列表，存储 {"group_id": str, "group_nick_name": str}

        # 从数据库加载数据
        self.load_from_database()

    def del_memory(self, category: str, memory_content: str, similarity_threshold: float = 0.95):
        """
        删除指定分类和记忆内容的记忆点

        Args:
            category: 记忆分类
            memory_content: 要删除的记忆内容
            similarity_threshold: 相似度阈值，默认0.95（95%）

        Returns:
            int: 删除的记忆点数量
        """
        if not self.memory_points:
            return 0

        deleted_count = 0
        memory_points_to_keep = []

        for memory_point in self.memory_points:
            # 跳过None值
            if memory_point is None:
                continue
            # 解析记忆点
            parts = memory_point.split(":", 2)  # 最多分割2次，保留记忆内容中的冒号
            if len(parts) < 3:
                # 格式不正确，保留原样
                memory_points_to_keep.append(memory_point)
                continue

            memory_category = parts[0].strip()
            memory_text = parts[1].strip()
            _memory_weight = parts[2].strip()

            # 检查分类是否匹配
            if memory_category != category:
                memory_points_to_keep.append(memory_point)
                continue

            # 计算记忆内容的相似度
            similarity = calculate_string_similarity(memory_content, memory_text)

            # 如果相似度达到阈值，则删除（不添加到保留列表）
            if similarity >= similarity_threshold:
                deleted_count += 1
                logger.debug(f"删除记忆点: {memory_point} (相似度: {similarity:.4f})")
            else:
                memory_points_to_keep.append(memory_point)

        # 更新memory_points
        self.memory_points = memory_points_to_keep

        # 同步到数据库
        if deleted_count > 0:
            self.sync_to_database()
            logger.info(f"成功删除 {deleted_count} 个记忆点，分类: {category}")

        return deleted_count

    def get_all_category(self):
        category_list = []
        for memory in self.memory_points:
            if memory is None:
                continue
            category = get_category_from_memory(memory)
            if category and category not in category_list:
                category_list.append(category)
        return category_list

    def get_memory_list_by_category(self, category: str):
        memory_list = []
        for memory in self.memory_points:
            if memory is None:
                continue
            if get_category_from_memory(memory) == category:
                memory_list.append(memory)
        return memory_list

    def get_random_memory_by_category(self, category: str, num: int = 1):
        memory_list = self.get_memory_list_by_category(category)
        if len(memory_list) < num:
            return memory_list
        return random.sample(memory_list, num)

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
        for item in self.group_nick_name:
            if item.get("group_id") == group_id:
                # 更新现有记录
                item["group_nick_name"] = group_nick_name
                self.sync_to_database()
                logger.debug(f"更新用户 {self.person_id} 在群 {group_id} 的群昵称为 {group_nick_name}")
                return

        # 添加新记录
        self.group_nick_name.append({"group_id": group_id, "group_nick_name": group_nick_name})
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
                    self.is_known = record.is_known or False
                    self.nickname = record.user_nickname or ""
                    self.person_name = record.person_name or self.nickname
                    self.name_reason = record.name_reason or None
                    self.know_times = record.know_counts or 0

                    # 处理points字段（JSON格式的列表）
                    if record.memory_points:
                        try:
                            loaded_points = json.loads(record.memory_points)
                            # 过滤掉None值，确保数据质量
                            if isinstance(loaded_points, list):
                                self.memory_points = [point for point in loaded_points if point is not None]
                            else:
                                self.memory_points = []
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"解析用户 {self.person_id} 的points字段失败，使用默认值")
                            self.memory_points = []
                    else:
                        self.memory_points = []

                    # 处理group_nick_name字段（JSON格式的列表）
                    if record.group_cardname:
                        try:
                            loaded_group_nick_names = json.loads(record.group_cardname)
                            # 确保是列表格式
                            if isinstance(loaded_group_nick_names, list):
                                self.group_nick_name = loaded_group_nick_names
                            else:
                                self.group_nick_name = []
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"解析用户 {self.person_id} 的group_cardname字段失败，使用默认值")
                            self.group_nick_name = []
                    else:
                        self.group_nick_name = []

                    logger.debug(f"已从数据库加载用户 {self.person_id} 的信息")
                else:
                    self.sync_to_database()
                    logger.info(f"用户 {self.person_id} 在数据库中不存在，使用默认值并创建")

        except Exception as e:
            logger.error(f"从数据库加载用户 {self.person_id} 信息时出错: {e}")
            # 出错时保持默认值

    def sync_to_database(self):
        """将所有属性同步回数据库"""
        if not self.is_known:
            return
        try:
            memory_points_value = (
                json.dumps([point for point in self.memory_points if point is not None], ensure_ascii=False)
                if self.memory_points
                else json.dumps([], ensure_ascii=False)
            )
            group_nickname_value = (
                json.dumps(self.group_nick_name, ensure_ascii=False)
                if self.group_nick_name
                else json.dumps([], ensure_ascii=False)
            )
            first_known_time = datetime.fromtimestamp(self.know_since) if self.know_since else None
            last_known_time = datetime.fromtimestamp(self.last_know) if self.last_know else None

            with get_db_session() as session:
                statement = select(PersonInfo).where(col(PersonInfo.person_id) == self.person_id).limit(1)
                record = session.exec(statement).first()

                if record:
                    record.person_id = self.person_id
                    record.is_known = self.is_known
                    record.platform = self.platform
                    record.user_id = self.user_id
                    record.user_nickname = self.nickname
                    record.person_name = self.person_name
                    record.name_reason = self.name_reason
                    record.know_counts = self.know_times
                    record.first_known_time = first_known_time
                    record.last_known_time = last_known_time
                    record.memory_points = memory_points_value
                    record.group_nickname = group_nickname_value
                    session.add(record)
                    logger.debug(f"已同步用户 {self.person_id} 的信息到数据库")
                else:
                    record = PersonInfo(
                        person_id=self.person_id,
                        is_known=self.is_known,
                        platform=self.platform,
                        user_id=self.user_id,
                        user_nickname=self.nickname,
                        person_name=self.person_name,
                        name_reason=self.name_reason,
                        know_counts=self.know_times,
                        first_known_time=first_known_time,
                        last_known_time=last_known_time,
                        memory_points=memory_points_value,
                        group_nickname=group_nickname_value,
                    )
                    session.add(record)
                    logger.debug(f"已创建用户 {self.person_id} 的信息到数据库")

        except Exception as e:
            logger.error(f"同步用户 {self.person_id} 信息到数据库时出错: {e}")

    async def build_relationship(self, chat_content: str = "", info_type=""):
        if not self.is_known:
            return ""
        nickname_str = ""
        if self.person_name != self.nickname:
            nickname_str = f"(ta在{self.platform}上的昵称是{self.nickname})"

        async def _select_traits(query_text: str, traits: List[str], limit: int = 3) -> List[str]:
            clean_traits = [trait.strip() for trait in traits if isinstance(trait, str) and trait.strip()]
            if not clean_traits:
                return []
            if not query_text:
                return clean_traits[:limit]

            numbered_traits = "\n".join(f"{index}. {trait}" for index, trait in enumerate(clean_traits, start=1))
            prompt = f"""当前关注内容：
{query_text}

候选人物信息：
{numbered_traits}

请从候选人物信息中选择与当前关注内容最相关的编号，并用<>包裹输出，不要输出其他内容。
例如：
<1><3>
如果都不相关，请输出<none>"""

            try:
                response, _ = await relation_selection_model.generate_response_async(prompt)
                selected_traits: List[str] = []
                for raw_index in extract_categories_from_response(response):
                    if raw_index == "none":
                        return []
                    try:
                        trait_index = int(raw_index) - 1
                    except ValueError:
                        continue
                    if 0 <= trait_index < len(clean_traits):
                        trait = clean_traits[trait_index]
                        if trait not in selected_traits:
                            selected_traits.append(trait)
                if selected_traits:
                    return selected_traits[:limit]
            except Exception as e:
                logger.debug(f"筛选人物画像信息失败，使用默认画像摘要: {e}")

            return clean_traits[:limit]

        profile = await memory_service.get_person_profile(self.person_id, limit=8)
        relation_parts: List[str] = []
        if profile.summary.strip():
            relation_parts.append(profile.summary.strip())

        query_text = str(chat_content or info_type or "").strip()
        selected_traits = await _select_traits(query_text, profile.traits, limit=3)
        if not selected_traits and not query_text:
            selected_traits = [trait for trait in profile.traits if trait][:2]

        for trait in selected_traits:
            clean_trait = str(trait).strip()
            if clean_trait and clean_trait not in relation_parts:
                relation_parts.append(clean_trait)

        for evidence in profile.evidence:
            content = str(evidence.get("content", "") or "").strip()
            if content and content not in relation_parts:
                relation_parts.append(content)
            if len(relation_parts) >= 4:
                break

        points_info = ""
        if relation_parts:
            points_info = f"你还记得有关{self.person_name}的内容：{'；'.join(relation_parts[:3])}"

        if not (nickname_str or points_info):
            return ""
        return f"{self.person_name}:{nickname_str}{points_info}"


class PersonInfoManager:
    def __init__(self):
        self.person_name_list = {}
        self.qv_name_llm = LLMRequest(model_set=model_config.model_task_config.utils, request_type="relation.qv_name")
        try:
            with get_db_session() as _:
                pass
        except Exception as e:
            logger.error(f"数据库连接或 PersonInfo 表创建失败: {e}")

        # 初始化时读取所有person_name
        try:
            with get_db_session() as session:
                statement = select(PersonInfo.person_id, PersonInfo.person_name).where(
                    col(PersonInfo.person_name).is_not(None)
                )
                for person_id, person_name in session.exec(statement).all():
                    if person_name:
                        self.person_name_list[person_id] = person_name
            logger.debug(f"已加载 {len(self.person_name_list)} 个用户名称")
        except Exception as e:
            logger.error(f"加载 person_name_list 失败: {e}")

    @staticmethod
    def _extract_json_from_text(text: str) -> Dict[str, str]:
        """从文本中提取JSON数据的高容错方法"""
        try:
            fixed_json = repair_json(text)
            if isinstance(fixed_json, str):
                parsed_json = json.loads(fixed_json)
            else:
                parsed_json = fixed_json

            if isinstance(parsed_json, list) and parsed_json:
                parsed_json = parsed_json[0]

            if isinstance(parsed_json, dict):
                return parsed_json

        except Exception as e:
            logger.warning(f"JSON提取失败: {e}")

        logger.warning(f"无法从文本中提取有效的JSON字典: {text}")
        logger.info(f"文本: {text}")
        return {"nickname": "", "reason": ""}

    async def _generate_unique_person_name(self, base_name: str) -> str:
        """生成唯一的 person_name，如果存在重复则添加数字后缀"""
        # 处理空昵称的情况
        if not base_name or base_name.isspace():
            base_name = "空格"

        # 检查基础名称是否已存在
        if base_name not in self.person_name_list.values():
            return base_name

        # 如果存在，添加数字后缀
        counter = 1
        while True:
            new_name = f"{base_name}[{counter}]"
            if new_name not in self.person_name_list.values():
                return new_name
            counter += 1

    async def qv_person_name(
        self, person_id: str, user_nickname: str, user_cardname: str, user_avatar: str, request: str = ""
    ):
        """给某个用户取名"""
        if not person_id:
            logger.debug("取名失败：person_id不能为空")
            return None

        person = Person(person_id=person_id)
        old_name = person.person_name
        old_reason = person.name_reason

        max_retries = 8
        current_try = 0
        existing_names_str = ""
        current_name_set = set(self.person_name_list.values())

        while current_try < max_retries:
            bot_name = global_config.bot.nickname

            qv_name_prompt = f"你是{bot_name}，一个伪装成人类的AI，你不能让别人发现这一点，"
            qv_name_prompt += f"现在你想给一个用户取一个昵称，用户的昵称是{user_nickname}，"
            qv_name_prompt += f"用户的群昵称名是{user_cardname}，"
            if user_avatar:
                qv_name_prompt += f"用户的头像是{user_avatar}，"
            if old_name:
                qv_name_prompt += f"你之前叫他{old_name}，是因为{old_reason}，"

            qv_name_prompt += f"\n其他取名的要求是：{request}，不要太浮夸，简短，"
            qv_name_prompt += "\n请根据以上用户信息，想想你叫他什么比较好，不要太浮夸，请最好使用用户的昵称或群昵称原文，可以稍作修改，优先使用原文。优先使用用户的昵称或者群昵称原文。"

            if existing_names_str:
                qv_name_prompt += f"\n请注意，以下名称已被你尝试过或已知存在，请避免：{existing_names_str}。\n"

            if len(current_name_set) < 50 and current_name_set:
                qv_name_prompt += f"已知的其他昵称有: {', '.join(list(current_name_set)[:10])}等。\n"

            qv_name_prompt += "请用json给出你的想法，并给出理由，示例如下："
            qv_name_prompt += """{
                "nickname": "昵称",
                "reason": "理由"
            }"""
            response, _ = await self.qv_name_llm.generate_response_async(qv_name_prompt)
            # logger.info(f"取名提示词：{qv_name_prompt}\n取名回复：{response}")
            result = self._extract_json_from_text(response)

            if not result or not result.get("nickname"):
                logger.error("生成的昵称为空或结果格式不正确，重试中...")
                current_try += 1
                continue

            generated_nickname = result["nickname"]

            is_duplicate = False
            if generated_nickname in current_name_set:
                is_duplicate = True
                logger.info(f"尝试给用户{user_nickname} {person_id} 取名，但是 {generated_nickname} 已存在，重试中...")
            else:

                def _db_check_name_exists_sync(name_to_check):
                    with get_db_session() as session:
                        statement = select(PersonInfo.person_id).where(col(PersonInfo.person_name) == name_to_check)
                        return session.exec(statement).first() is not None

                if await asyncio.to_thread(_db_check_name_exists_sync, generated_nickname):
                    is_duplicate = True
                    current_name_set.add(generated_nickname)

            if not is_duplicate:
                person.person_name = generated_nickname
                person.name_reason = result.get("reason", "未提供理由")
                person.sync_to_database()

                logger.info(
                    f"成功给用户{user_nickname} {person_id} 取名 {generated_nickname}，理由：{result.get('reason', '未提供理由')}"
                )

                self.person_name_list[person_id] = generated_nickname
                return result
            else:
                if existing_names_str:
                    existing_names_str += "、"
                existing_names_str += generated_nickname
                logger.debug(f"生成的昵称 {generated_nickname} 已存在，重试中...")
                current_try += 1

        # 如果多次尝试后仍未成功，使用唯一的 user_nickname 作为默认值
        unique_nickname = await self._generate_unique_person_name(user_nickname)
        logger.warning(f"在{max_retries}次尝试后未能生成唯一昵称，使用默认昵称 {unique_nickname}")
        person.person_name = unique_nickname
        person.name_reason = "使用用户原始昵称作为默认值"
        person.sync_to_database()
        self.person_name_list[person_id] = unique_nickname
        return {"nickname": unique_nickname, "reason": "使用用户原始昵称作为默认值"}


person_info_manager = PersonInfoManager()


async def store_person_memory_from_answer(person_name: str, memory_content: str, chat_id: str) -> None:
    """将人物事实写入统一长期记忆

    Args:
        person_name: 人物名称
        memory_content: 记忆内容
        chat_id: 聊天ID
    """
    try:
        content = str(memory_content or "").strip()
        if not content:
            logger.debug("人物记忆内容为空，跳过写入")
            return

        # 从 chat_id 获取 session
        session = _chat_manager.get_session_by_session_id(chat_id)
        if not session:
            logger.warning(f"无法获取session for chat_id: {chat_id}")
            return

        platform = session.platform

        # 尝试从person_name查找person_id
        # 首先尝试通过person_name查找
        person_id = resolve_person_id_for_memory(
            person_name=person_name,
            platform=platform,
            user_id=session.user_id,
        )
        if not person_id:
            logger.warning(f"无法确定person_id for person_name: {person_name}, chat_id: {chat_id}")
            return

        # 创建或获取Person对象
        person = Person(person_id=person_id)

        if not person.is_known:
            logger.warning(f"用户 {person_name} (person_id: {person_id}) 尚未认识，无法存储记忆")
            return

        memory_hash = hashlib.sha256(f"{person_id}\n{content}".encode("utf-8")).hexdigest()[:16]
        result = await memory_service.ingest_text(
            external_id=f"person_fact:{person_id}:{memory_hash}",
            source_type="person_fact",
            text=content,
            chat_id=chat_id,
            person_ids=[person_id],
            participants=[person.person_name or person_name],
            timestamp=time.time(),
            tags=["person_fact"],
            metadata={
                "person_id": person_id,
                "person_name": person.person_name or person_name,
                "platform": platform,
                "source": "person_info.store_person_memory_from_answer",
            },
            respect_filter=True,
            user_id=str(session.user_id or "").strip(),
            group_id=str(session.group_id or "").strip(),
        )

        if result.success:
            if result.detail == "chat_filtered":
                logger.debug(f"人物长期记忆被聊天过滤策略跳过: {person_name} (person_id: {person_id})")
            else:
                logger.info(f"成功写入人物长期记忆: {person_name} (person_id: {person_id})")
        else:
            logger.warning(f"写入人物长期记忆失败: {person_name} (person_id: {person_id}) | {result.detail}")

    except Exception as e:
        logger.error(f"存储人物记忆失败: {e}")

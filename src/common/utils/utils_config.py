from typing import Any, Iterator, Optional

import time

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("config_utils")


class ExpressionConfigUtils:
    @staticmethod
    def _find_expression_config_item(session_id: Optional[str] = None):
        if not global_config.expression.learning_list:
            return None

        if session_id:
            for config_item in global_config.expression.learning_list:
                if not ChatConfigUtils.is_wildcard_target(config_item):
                    continue
                if ChatConfigUtils.target_matches_session_with_wildcards(config_item, session_id):
                    return config_item

            for config_item in global_config.expression.learning_list:
                if (
                    ChatConfigUtils.is_default_target(config_item)
                    or ChatConfigUtils.is_wildcard_target(config_item)
                    or ChatConfigUtils.is_platform_default_target(config_item)
                ):
                    continue
                if ChatConfigUtils.target_matches_session(config_item, session_id):
                    return config_item

            for config_item in global_config.expression.learning_list:
                if not ChatConfigUtils.is_platform_default_target(config_item):
                    continue
                if ChatConfigUtils.platform_default_matches_session(config_item, session_id):
                    return config_item

        for config_item in global_config.expression.learning_list:
            if ChatConfigUtils.is_default_target(config_item):
                return config_item

        return None

    @staticmethod
    def get_expression_config_for_chat(session_id: Optional[str] = None) -> tuple[bool, bool]:
        # sourcery skip: use-next
        """
        根据聊天会话 ID 获取表达配置。

        Args:
            session_id: 聊天会话 ID，格式为哈希值

        Returns:
            tuple: (是否使用表达, 是否学习表达)
        """
        config_item = ExpressionConfigUtils._find_expression_config_item(session_id)
        if config_item is None:
            return True, True

        return (
            config_item.use,
            config_item.learn,
        )

    @staticmethod
    def _get_stream_id(platform: str, id_str: str, is_group: bool = False) -> Optional[str]:
        """
        根据平台、ID 字符串和是否为群聊解析已存在的聊天流 ID。

        注意：业务模块不应自行计算 session_id，这里只返回已存在的真实聊天流。
        """
        chat_type = "group" if is_group else "private"
        session_ids = ChatConfigUtils.resolve_existing_session_ids(platform, id_str, chat_type)
        return next(iter(session_ids), None)


class BehaviorConfigUtils:
    @staticmethod
    def _find_behavior_config_item(session_id: Optional[str] = None):
        if not global_config.experimental.behavior_learning_list:
            return None

        is_group_chat = ChatConfigUtils._resolve_is_group_chat(session_id)
        if session_id:
            for config_item in global_config.experimental.behavior_learning_list:
                if ChatConfigUtils.is_default_target(config_item):
                    continue
                if ChatConfigUtils.is_wildcard_target(config_item):
                    if ChatConfigUtils.target_matches_session_with_wildcards(config_item, session_id, is_group_chat):
                        return config_item
                    continue

            for config_item in global_config.experimental.behavior_learning_list:
                if (
                    ChatConfigUtils.is_default_target(config_item)
                    or ChatConfigUtils.is_wildcard_target(config_item)
                    or ChatConfigUtils.is_platform_default_target(config_item)
                ):
                    continue
                if ChatConfigUtils.target_matches_session(config_item, session_id, is_group_chat):
                    return config_item

            for config_item in global_config.experimental.behavior_learning_list:
                if not ChatConfigUtils.is_platform_default_target(config_item):
                    continue
                if ChatConfigUtils.platform_default_matches_session(config_item, session_id, is_group_chat):
                    return config_item

        for config_item in global_config.experimental.behavior_learning_list:
            if ChatConfigUtils.is_default_target(config_item):
                return config_item

        return None

    @staticmethod
    def get_behavior_config_for_chat(session_id: Optional[str] = None) -> tuple[bool, bool]:
        """
        根据聊天会话 ID 获取行为表现配置。

        没有任何匹配配置的新聊天流会自动启用行为表现调用，学习总开关由 experimental.enable_behavior_learning 控制。
        """

        enable_behavior_learning = bool(global_config.experimental.enable_behavior_learning)
        config_item = BehaviorConfigUtils._find_behavior_config_item(session_id)
        if config_item is None:
            return True, enable_behavior_learning

        return (
            config_item.use,
            config_item.learn and enable_behavior_learning,
        )

    @staticmethod
    def resolve_behavior_group_scope(session_id: Optional[str]) -> tuple[set[str], bool]:
        """解析当前会话可共享行为经验的会话范围，以及是否命中全平台全目标通配。"""
        related_session_ids = {session_id} if session_id else set()
        has_global_share = False
        if not session_id:
            return related_session_ids, has_global_share

        for behavior_group in global_config.experimental.behavior_groups:
            target_items = behavior_group.targets
            group_session_ids: set[str] = set()
            contains_current_session = False

            for target_item in target_items:
                platform = str(target_item.platform or "").strip()
                item_id = str(target_item.item_id or "").strip()
                if not platform or not item_id:
                    continue

                target_session_ids = ChatConfigUtils.get_target_session_ids_with_wildcards(target_item)
                group_session_ids.update(target_session_ids)
                if ChatConfigUtils.target_matches_session_with_wildcards(target_item, session_id):
                    contains_current_session = True
                    if platform == "*" and item_id == "*":
                        has_global_share = True

            if contains_current_session:
                related_session_ids.update(group_session_ids)

        return related_session_ids, has_global_share


class JargonConfigUtils:
    @staticmethod
    def _is_global_default_item(config_item) -> bool:
        return ChatConfigUtils.is_default_target(config_item)

    @staticmethod
    def _is_wildcard_item(config_item) -> bool:
        return ChatConfigUtils.is_wildcard_target(config_item)

    @staticmethod
    def get_target_session_ids_with_wildcards(target_item) -> set[str]:
        """获取黑话配置目标对应的已知真实聊天流 ID，允许 platform/item_id 使用 * 通配。"""
        return ChatConfigUtils.get_target_session_ids_with_wildcards(target_item)

    @staticmethod
    def _find_jargon_config_item(session_id: Optional[str] = None):
        if not global_config.jargon.learning_list:
            return None

        is_group_chat = ChatConfigUtils._resolve_is_group_chat(session_id)
        if session_id:
            for config_item in global_config.jargon.learning_list:
                if JargonConfigUtils._is_global_default_item(config_item):
                    continue
                if JargonConfigUtils._is_wildcard_item(config_item):
                    if ChatConfigUtils.target_matches_session_with_wildcards(config_item, session_id, is_group_chat):
                        return config_item
                    continue

            for config_item in global_config.jargon.learning_list:
                if JargonConfigUtils._is_global_default_item(config_item):
                    continue
                if JargonConfigUtils._is_wildcard_item(config_item):
                    continue
                if ChatConfigUtils.is_platform_default_target(config_item):
                    continue
                if ChatConfigUtils.target_matches_session(config_item, session_id):
                    return config_item

            for config_item in global_config.jargon.learning_list:
                if not ChatConfigUtils.is_platform_default_target(config_item):
                    continue
                if ChatConfigUtils.platform_default_matches_session(config_item, session_id, is_group_chat):
                    return config_item

        for config_item in global_config.jargon.learning_list:
            if JargonConfigUtils._is_global_default_item(config_item):
                return config_item

        return None

    @staticmethod
    def get_jargon_config_for_chat(session_id: Optional[str] = None) -> tuple[bool, bool]:
        """根据聊天会话 ID 获取黑话使用与学习开关。"""
        config_item = JargonConfigUtils._find_jargon_config_item(session_id)
        if config_item is None:
            return True, True
        return config_item.use, config_item.learn

    @staticmethod
    def resolve_jargon_group_scope(session_id: Optional[str]) -> tuple[set[str], bool]:
        """解析当前会话可共享黑话的会话范围，以及是否命中全平台全目标通配。"""
        related_session_ids = {session_id} if session_id else set()
        has_global_share = False
        if not session_id:
            return related_session_ids, has_global_share

        for jargon_group in global_config.jargon.jargon_groups:
            target_items = jargon_group.targets
            group_session_ids: set[str] = set()
            contains_current_session = False

            for target_item in target_items:
                platform = str(target_item.platform or "").strip()
                item_id = str(target_item.item_id or "").strip()
                if not platform or not item_id:
                    continue

                target_session_ids = JargonConfigUtils.get_target_session_ids_with_wildcards(target_item)
                group_session_ids.update(target_session_ids)
                if ChatConfigUtils.target_matches_session_with_wildcards(target_item, session_id):
                    contains_current_session = True
                    if platform == "*" and item_id == "*":
                        has_global_share = True

            if contains_current_session:
                related_session_ids.update(group_session_ids)

        return related_session_ids, has_global_share


class ChatConfigUtils:
    @staticmethod
    def _iter_matching_chat_prompts(session_id: str, is_group_chat: Optional[bool]) -> Iterator[str]:
        try:
            from src.chat.message_receive.chat_manager import chat_manager

            chat_stream = chat_manager.get_session_by_session_id(session_id)
        except Exception as e:
            logger.debug(f"解析额外 Prompt 聊天流失败: session_id={session_id} error={e}")
            chat_stream = None

        for chat_prompt_item in global_config.chat.reply_style.chat_prompts:
            if hasattr(chat_prompt_item, "platform"):
                platform = str(chat_prompt_item.platform or "").strip()
                item_id = str(chat_prompt_item.item_id or "").strip()
                rule_type = str(chat_prompt_item.rule_type or "").strip()
                prompt_content = str(chat_prompt_item.prompt or "").strip()
            elif isinstance(chat_prompt_item, str):
                parts = chat_prompt_item.split(":", 3)
                if len(parts) != 4:
                    continue

                platform, item_id, rule_type, prompt_content = parts
                platform = platform.strip()
                item_id = item_id.strip()
                rule_type = rule_type.strip()
                prompt_content = prompt_content.strip()
            else:
                continue

            if not platform or not item_id or not prompt_content:
                continue

            if rule_type == "group":
                config_is_group = True
                target_attrs = ("group_id",)
            elif rule_type == "private":
                config_is_group = False
                target_attrs = ("user_id",)
            elif rule_type == "*":
                config_is_group = None
                target_attrs = ("group_id", "user_id")
            else:
                continue

            if is_group_chat is not None and config_is_group is not None and config_is_group != is_group_chat:
                continue

            if chat_stream is not None:
                chat_stream_platform = str(chat_stream.platform or "").strip()
                target_ids = {
                    str(getattr(chat_stream, attr) or "").strip()
                    for attr in target_attrs
                }
                target_ids.discard("")
                if chat_stream_platform == platform and item_id in target_ids:
                    yield prompt_content
                    continue

            if session_id in ChatConfigUtils.resolve_existing_session_ids(platform, item_id, rule_type):
                yield prompt_content

    @staticmethod
    def get_chat_prompt_for_chat(session_id: str, is_group_chat: Optional[bool]) -> str:
        """根据聊天流 ID 获取匹配的额外 Prompt，允许同一聊天流配置多条。"""
        prompt_contents = ChatConfigUtils.get_chat_prompts_for_chat(session_id, is_group_chat)
        if not prompt_contents:
            return ""

        logger.debug(f"匹配到 {len(prompt_contents)} 条聊天额外 Prompt: session_id={session_id}")
        return "\n".join(prompt_contents)

    @staticmethod
    def get_chat_prompts_for_chat(session_id: str, is_group_chat: Optional[bool]) -> list[str]:
        """根据聊天流 ID 获取匹配的额外 Prompt 列表，允许同一聊天流配置多条。"""
        if not session_id or not global_config.chat.reply_style.chat_prompts:
            return []

        return list(ChatConfigUtils._iter_matching_chat_prompts(session_id, is_group_chat))

    @staticmethod
    def _target_values(target_item) -> tuple[str, str, str]:
        if isinstance(target_item, dict):
            platform = str(target_item.get("platform") or "").strip()
            item_id = str(target_item.get("item_id") or "").strip()
            rule_type = str(target_item.get("type") or target_item.get("rule_type") or "").strip()
            return platform, item_id, rule_type

        platform = str(target_item.platform or "").strip()
        item_id = str(target_item.item_id or "").strip()
        rule_type = str(getattr(target_item, "type", "") or getattr(target_item, "rule_type", "") or "").strip()
        return platform, item_id, rule_type

    @staticmethod
    def is_default_target(target_item) -> bool:
        """判断配置目标是否是 learning_list 的默认兜底项。"""
        platform, item_id, _ = ChatConfigUtils._target_values(target_item)
        return not platform and not item_id

    @staticmethod
    def is_platform_default_target(target_item) -> bool:
        """判断配置目标是否是 learning_list 的平台兜底项。"""
        platform, item_id, _ = ChatConfigUtils._target_values(target_item)
        return bool(platform and platform != "*" and not item_id)

    @staticmethod
    def is_wildcard_target(target_item) -> bool:
        """判断配置目标是否包含 platform/item_id/rule_type 通配符。"""
        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        return platform == "*" or item_id == "*" or rule_type == "*"

    @staticmethod
    def _get_chat_stream(session_id: str):
        try:
            from src.chat.message_receive.chat_manager import chat_manager

            return chat_manager.get_session_by_session_id(session_id)
        except Exception as e:
            logger.debug(f"获取聊天流失败: session_id={session_id} error={e}")
            return None

    @staticmethod
    def _get_stream_id(platform: str, id_str: str, is_group: bool = False) -> Optional[str]:
        """解析已存在的真实聊天流 ID。

        保留该方法仅为旧调用兼容；不要在新代码中用它生成资源归属 ID。
        """

        chat_type = "group" if is_group else "private"
        session_ids = ChatConfigUtils.resolve_existing_session_ids(platform, id_str, chat_type)
        return next(iter(session_ids), None)

    @staticmethod
    def resolve_existing_session_ids(platform: str, item_id: str, rule_type: str) -> set[str]:
        """按配置目标解析系统已知的真实聊天流 ID。"""

        try:
            from src.chat.message_receive.chat_manager import chat_manager

            return chat_manager.resolve_session_ids_by_target(
                platform=str(platform or "").strip(),
                target_id=str(item_id or "").strip(),
                chat_type=str(rule_type or "").strip(),
            )
        except Exception as e:
            logger.debug(
                f"解析配置目标真实聊天流失败: platform={platform} item_id={item_id} rule_type={rule_type} error={e}"
            )
            return set()

    @staticmethod
    def get_target_session_ids_with_wildcards(target_item) -> set[str]:
        """获取配置目标对应的已知真实聊天流 ID，允许 platform/item_id/rule_type 使用 * 通配。"""
        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        if not platform or not item_id:
            return set()

        if not ChatConfigUtils.is_wildcard_target(target_item):
            return ChatConfigUtils.get_target_session_ids(target_item)

        if rule_type == "group":
            target_attrs = ["group_id"]
        elif rule_type == "private":
            target_attrs = ["user_id"]
        elif rule_type == "*":
            target_attrs = ["group_id", "user_id"]
        else:
            return set()

        matched_session_ids: set[str] = set()
        try:
            from src.chat.message_receive.chat_manager import chat_manager

            for chat_stream in chat_manager.sessions.values():
                chat_stream_platform = str(chat_stream.platform or "").strip()
                if not (platform == "*" or chat_stream_platform == platform):
                    continue
                for attr in target_attrs:
                    chat_stream_target_id = str(getattr(chat_stream, attr) or "").strip()
                    if chat_stream_target_id and (item_id == "*" or chat_stream_target_id == item_id):
                        matched_session_ids.add(chat_stream.session_id)
                        break
        except Exception as e:
            logger.debug(f"解析通配配置内存聊天流失败: platform={platform} item_id={item_id} rule_type={rule_type} error={e}")

        try:
            from sqlmodel import select

            from src.common.database.database import get_db_session
            from src.common.database.database_model import ChatSession

            with get_db_session() as session:
                statement = select(ChatSession)
                if platform != "*":
                    statement = statement.where(ChatSession.platform == platform)
                for db_instance in session.exec(statement).all():
                    chat_session_platform = str(db_instance.platform or "").strip()
                    if not (platform == "*" or chat_session_platform == platform):
                        continue
                    for attr in target_attrs:
                        target_id = str(getattr(db_instance, attr) or "").strip()
                        if target_id and (item_id == "*" or target_id == item_id):
                            matched_session_ids.add(db_instance.session_id)
                            break
        except Exception as e:
            logger.debug(f"解析通配配置数据库聊天流失败: platform={platform} item_id={item_id} rule_type={rule_type} error={e}")

        return matched_session_ids

    @staticmethod
    def target_matches_session(target_item, session_id: str, is_group_chat: Optional[bool] = None) -> bool:
        """判断 platform/item_id/rule_type 配置目标是否命中当前聊天流。"""
        if not session_id:
            return False

        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        if not platform or not item_id:
            return False

        if rule_type == "group":
            config_is_group = True
            target_attr = "group_id"
        elif rule_type == "private":
            config_is_group = False
            target_attr = "user_id"
        elif rule_type == "*":
            config_is_group = None
            target_attr = ""
        else:
            return False

        if is_group_chat is not None and config_is_group is not None and config_is_group != is_group_chat:
            return False

        chat_stream = ChatConfigUtils._get_chat_stream(session_id)
        if chat_stream is not None:
            chat_stream_platform = str(chat_stream.platform or "").strip()
            if rule_type == "*":
                target_ids = {
                    str(getattr(chat_stream, "group_id", None) or "").strip(),
                    str(getattr(chat_stream, "user_id", None) or "").strip(),
                }
                target_ids.discard("")
                return chat_stream_platform == platform and item_id in target_ids
            actual_target_id = str(getattr(chat_stream, target_attr) or "").strip()
            return chat_stream_platform == platform and actual_target_id == item_id

        return session_id in ChatConfigUtils.resolve_existing_session_ids(platform, item_id, rule_type)

    @staticmethod
    def platform_default_matches_session(
        target_item,
        session_id: str,
        is_group_chat: Optional[bool] = None,
    ) -> bool:
        """判断平台兜底配置是否命中当前聊天流。"""
        if not session_id:
            return False

        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        if not platform or platform == "*" or item_id:
            return False

        if rule_type == "group":
            config_is_group = True
        elif rule_type == "private":
            config_is_group = False
        else:
            return False

        if is_group_chat is not None and config_is_group != is_group_chat:
            return False

        chat_stream = ChatConfigUtils._get_chat_stream(session_id)
        if chat_stream is not None:
            chat_stream_platform = str(chat_stream.platform or "").strip()
            return chat_stream_platform == platform and bool(chat_stream.is_group_session) == config_is_group

        try:
            from sqlmodel import select

            from src.common.database.database import get_db_session
            from src.common.database.database_model import ChatSession

            with get_db_session() as session:
                statement = select(ChatSession).where(
                    ChatSession.session_id == session_id,
                    ChatSession.platform == platform,
                )
                chat_session = session.exec(statement).first()
                if chat_session is None:
                    return False
                return bool(str(chat_session.group_id or "").strip()) == config_is_group
        except Exception as e:
            logger.debug(f"解析平台兜底配置失败: platform={platform} session_id={session_id} error={e}")
            return False

    @staticmethod
    def target_matches_session_with_wildcards(
        target_item,
        session_id: str,
        is_group_chat: Optional[bool] = None,
    ) -> bool:
        """判断配置目标是否命中当前聊天流，允许 platform/item_id/rule_type 使用 * 通配。"""
        if not session_id:
            return False

        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        if not platform or not item_id:
            return False

        if rule_type == "group":
            config_is_group = True
            target_attr = "group_id"
        elif rule_type == "private":
            config_is_group = False
            target_attr = "user_id"
        elif rule_type == "*":
            config_is_group = None
            target_attr = ""
        else:
            return False

        if is_group_chat is not None and config_is_group is not None and config_is_group != is_group_chat:
            return False

        chat_stream = ChatConfigUtils._get_chat_stream(session_id)
        if chat_stream is None:
            if ChatConfigUtils.is_wildcard_target(target_item):
                return False
            return ChatConfigUtils.target_matches_session(target_item, session_id, is_group_chat)

        chat_stream_platform = str(chat_stream.platform or "").strip()
        if rule_type == "*":
            target_ids = {
                str(getattr(chat_stream, "group_id", None) or "").strip(),
                str(getattr(chat_stream, "user_id", None) or "").strip(),
            }
            target_ids.discard("")
        else:
            target_ids = {str(getattr(chat_stream, target_attr) or "").strip()}
            target_ids.discard("")
        if not target_ids:
            return False

        platform_matches = platform == "*" or chat_stream_platform == platform
        item_matches = item_id == "*" or item_id in target_ids
        return platform_matches and item_matches

    @staticmethod
    def get_target_session_ids(target_item) -> set[str]:
        """获取配置目标对应的已知真实聊天流 ID。"""
        platform, item_id, rule_type = ChatConfigUtils._target_values(target_item)
        if not platform or not item_id:
            return set()

        return ChatConfigUtils.resolve_existing_session_ids(platform, item_id, rule_type)

    @staticmethod
    def _resolve_is_group_chat(session_id: Optional[str]) -> Optional[bool]:
        if not session_id:
            return None

        try:
            from src.chat.message_receive.chat_manager import chat_manager

            chat_stream = chat_manager.get_session_by_session_id(session_id)
        except Exception as e:
            logger.debug(f"解析聊天流类型失败: session_id={session_id} error={e}")
            return None
        if chat_stream is None:
            return None
        return bool(chat_stream.is_group_session)

    @staticmethod
    def get_talk_value(session_id: Optional[str], is_group_chat: Optional[bool] = None) -> float:
        if is_group_chat is None:
            is_group_chat = ChatConfigUtils._resolve_is_group_chat(session_id)

        result = (
            global_config.chat.reply_timing.talk_value
            if is_group_chat is not False
            else global_config.chat.reply_timing.private_talk_value
        ) or 0.0
        if not global_config.chat.reply_timing.enable_talk_value_rules or not global_config.chat.reply_timing.talk_value_rules:
            return result
        local_time = time.localtime()
        now_min = local_time.tm_hour * 60 + local_time.tm_min

        matched_rules = []
        for rule in global_config.chat.reply_timing.talk_value_rules:
            target_priority = ChatConfigUtils._talk_rule_target_priority(rule, session_id, is_group_chat)
            if target_priority is None:
                continue
            matched_rules.append((rule, target_priority))

        matched_value = ChatConfigUtils._select_talk_rule_value(matched_rules, now_min)
        if matched_value is not None:
            return matched_value
        return result  # 如果没有任何规则生效，返回默认值

    @staticmethod
    def _talk_rule_target_priority(rule, session_id: Optional[str], is_group_chat: Optional[bool]) -> Optional[int]:
        platform, item_id, rule_type = ChatConfigUtils._target_values(rule)
        if rule_type == "group":
            config_is_group = True
            target_attr = "group_id"
        elif rule_type == "private":
            config_is_group = False
            target_attr = "user_id"
        elif rule_type == "*":
            config_is_group = None
            target_attr = ""
        else:
            return None

        if is_group_chat is not None and config_is_group is not None and config_is_group != is_group_chat:
            return None

        if not platform and not item_id:
            return 1

        has_wildcard = platform == "*" or item_id == "*" or rule_type == "*"
        if not session_id:
            if has_wildcard and (platform in {"", "*"} and item_id in {"", "*"}):
                return 4
            return None

        chat_stream = ChatConfigUtils._get_chat_stream(session_id)
        if chat_stream is None:
            if platform and item_id and not has_wildcard:
                return 5 if ChatConfigUtils.target_matches_session(rule, session_id, is_group_chat) else None
            return None

        chat_stream_platform = str(chat_stream.platform or "").strip()
        if rule_type == "*":
            target_ids = {
                str(getattr(chat_stream, "group_id", None) or "").strip(),
                str(getattr(chat_stream, "user_id", None) or "").strip(),
            }
            target_ids.discard("")
        else:
            target_ids = {str(getattr(chat_stream, target_attr) or "").strip()}
            target_ids.discard("")
        if not target_ids:
            return None

        platform_matches = not platform or platform == "*" or chat_stream_platform == platform
        item_matches = not item_id or item_id == "*" or item_id in target_ids
        if not platform_matches or not item_matches:
            return None

        if platform and item_id and not has_wildcard:
            return 5
        if has_wildcard:
            return 4
        return 3

    @staticmethod
    def _get_rule_time(rule) -> str:
        if isinstance(rule, dict):
            return str(rule.get("time") or "").strip()
        return str(rule.time or "").strip()

    @staticmethod
    def _get_rule_value(rule) -> float:
        value = rule.get("value") if isinstance(rule, dict) else rule.value
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _talk_rule_time_priority(rule_time: str, now_min: int) -> Optional[int]:
        if not rule_time:
            return 1
        if rule_time == "*":
            return 3

        parsed_range = ChatConfigUtils.parse_range(rule_time)
        if not parsed_range:
            return None
        start_min, end_min = parsed_range
        if start_min <= end_min:
            return 2 if start_min <= now_min <= end_min else None
        return 2 if now_min >= start_min or now_min <= end_min else None

    @staticmethod
    def _select_talk_rule_value(rules: list[tuple[Any, int]], now_min: int) -> Optional[float]:
        selected_priority = (0, 0)
        selected_value: Optional[float] = None

        for rule, target_priority in rules:
            time_priority = ChatConfigUtils._talk_rule_time_priority(ChatConfigUtils._get_rule_time(rule), now_min)
            if time_priority is None:
                continue
            priority = (target_priority, time_priority)
            if priority <= selected_priority:
                continue
            selected_priority = priority
            selected_value = ChatConfigUtils._get_rule_value(rule)

        return selected_value

    @staticmethod
    def parse_range(range_str: str) -> Optional[tuple[int, int]]:
        """解析 "HH:MM-HH:MM" 到 (start_min, end_min)。"""
        try:
            start_str, end_str = [s.strip() for s in range_str.split("-")]
            sh, sm = [int(x) for x in start_str.split(":")]
            eh, em = [int(x) for x in end_str.split(":")]
            return sh * 60 + sm, eh * 60 + em
        except Exception:
            return None


class AMemorixConfigUtils:
    @staticmethod
    def get_shared_memory_session_ids(session_id: Optional[str]) -> set[str]:
        """获取与当前聊天流共享长期记忆检索范围的真实聊天流 ID。"""
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            return set()

        shared_groups = getattr(global_config.a_memorix, "shared_memory_groups", []) or []
        resolved_session_ids: set[str] = set()
        for group in shared_groups:
            targets = getattr(group, "targets", []) or []
            group_session_ids: set[str] = set()
            for target in targets:
                group_session_ids.update(ChatConfigUtils.get_target_session_ids_with_wildcards(target))
            if clean_session_id in group_session_ids:
                resolved_session_ids.update(group_session_ids)
        return resolved_session_ids or {clean_session_id}

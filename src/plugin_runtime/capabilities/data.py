from pathlib import Path
from typing import Any, Dict, List, Optional

import random
import time

from src.chat.message_receive.chat_manager import BotChatSession, chat_manager
from src.common.data_models.image_data_model import MaiEmoji
from src.common.logger import get_logger
from src.common.utils.utils_image import ImageUtils

logger = get_logger("plugin_runtime.integration")


class RuntimeDataCapabilityMixin:
    @staticmethod
    def _serialize_emoji_payload(emoji: MaiEmoji) -> Optional[Dict[str, str]]:
        emoji_base64 = ImageUtils.image_path_to_base64(str(emoji.full_path))
        if not emoji_base64:
            return None

        matched_emotion = emoji.emotion[0] if emoji.emotion else ""
        return {
            "base64": emoji_base64,
            "description": emoji.description,
            "emotion": matched_emotion,
        }

    @staticmethod
    def _build_emoji_temp_path() -> Path:
        from src.chat.emoji_system.emoji_manager import EMOJI_DIR

        EMOJI_DIR.mkdir(parents=True, exist_ok=True)
        return EMOJI_DIR / f"emoji_cap_{int(time.time() * 1000000)}.png"

    async def _cap_database_query(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import database_service 

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            query_type = args.get("query_type", "get")
            if query_type == "get":
                result = await database_service.db_get(
                    model_class=model_class,
                    filters=args.get("filters"),
                    limit=args.get("limit"),
                    order_by=args.get("order_by"),
                    single_result=args.get("single_result", False),
                )
            elif query_type == "create":
                if not (data := args.get("data")):
                    return {"success": False, "error": "create 需要 data"}
                result = await database_service.db_save(model_class=model_class, data=data)
            elif query_type == "update":
                if not (data := args.get("data")):
                    return {"success": False, "error": "update 需要 data"}
                result = await database_service.db_update(
                    model_class=model_class,
                    data=data,
                    filters=args.get("filters"),
                )
            elif query_type == "delete":
                result = await database_service.db_delete(model_class=model_class, filters=args.get("filters"))
            elif query_type == "count":
                result = await database_service.db_count(model_class=model_class, filters=args.get("filters"))
            else:
                return {"success": False, "error": f"不支持的 query_type: {query_type}"}
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.query] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_database_save(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import database_service 

        model_name: str = args.get("model_name", "")
        data: Optional[Dict[str, Any]] = args.get("data")
        if not model_name or not data:
            return {"success": False, "error": "缺少必要参数 model_name 或 data"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_service.db_save(
                model_class=model_class,
                data=data,
                key_field=args.get("key_field"),
                key_value=args.get("key_value"),
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.save] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_database_get(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import database_service 

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_service.db_get(
                model_class=model_class,
                filters=args.get("filters"),
                limit=args.get("limit"),
                order_by=args.get("order_by"),
                single_result=args.get("single_result", False),
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.get] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_database_delete(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import database_service 

        model_name: str = args.get("model_name", "")
        filters = args.get("filters", {})
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}
        if not filters:
            return {"success": False, "error": "缺少必要参数 filters（不允许无条件删除）"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_service.db_delete(model_class=model_class, filters=filters)
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.delete] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_database_count(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import database_service 

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_service.db_count(model_class=model_class, filters=args.get("filters"))
            return {"success": True, "count": result}
        except Exception as e:
            logger.error(f"[cap.database.count] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _list_sessions(self, platform: str, is_group_session: Optional[bool] = None) -> List[BotChatSession]:
        return [
            session
            for session in chat_manager.sessions.values()
            if (platform == "all_platforms" or session.platform == platform)
            and (is_group_session is None or session.is_group_session == is_group_session)
        ]

    @staticmethod
    def _serialize_stream(stream: BotChatSession) -> Dict[str, Any]:
        return {
            "session_id": stream.session_id,
            "platform": stream.platform,
            "user_id": stream.user_id,
            "group_id": stream.group_id,
            "is_group_session": stream.is_group_session,
        }

    async def _cap_chat_get_all_streams(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        platform: str = args.get("platform", "qq")
        try:
            streams = self._list_sessions(platform=platform)
            return {"success": True, "streams": [self._serialize_stream(item) for item in streams]}
        except Exception as e:
            logger.error(f"[cap.chat.get_all_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_chat_get_group_streams(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        platform: str = args.get("platform", "qq")
        try:
            streams = self._list_sessions(platform=platform, is_group_session=True)
            return {"success": True, "streams": [self._serialize_stream(item) for item in streams]}
        except Exception as e:
            logger.error(f"[cap.chat.get_group_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_chat_get_private_streams(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        platform: str = args.get("platform", "qq")
        try:
            streams = self._list_sessions(platform=platform, is_group_session=False)
            return {"success": True, "streams": [self._serialize_stream(item) for item in streams]}
        except Exception as e:
            logger.error(f"[cap.chat.get_private_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_chat_get_stream_by_group_id(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        group_id: str = args.get("group_id", "")
        if not group_id:
            return {"success": False, "error": "缺少必要参数 group_id"}

        platform: str = args.get("platform", "qq")
        try:
            stream = next(
                (
                    item
                    for item in self._list_sessions(platform=platform, is_group_session=True)
                    if str(item.group_id) == str(group_id)
                ),
                None,
            )
            return {"success": True, "stream": None if stream is None else self._serialize_stream(stream)}
        except Exception as e:
            logger.error(f"[cap.chat.get_stream_by_group_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_chat_get_stream_by_user_id(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        user_id: str = args.get("user_id", "")
        if not user_id:
            return {"success": False, "error": "缺少必要参数 user_id"}

        platform: str = args.get("platform", "qq")
        try:
            stream = next(
                (
                    item
                    for item in self._list_sessions(platform=platform, is_group_session=False)
                    if str(item.user_id) == str(user_id)
                ),
                None,
            )
            return {"success": True, "stream": None if stream is None else self._serialize_stream(stream)}
        except Exception as e:
            logger.error(f"[cap.chat.get_stream_by_user_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _serialize_messages(messages: list) -> List[Any]:
        result: List[Any] = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                result.append(msg.model_dump())
            elif hasattr(msg, "__dict__"):
                result.append(dict(msg.__dict__))
            else:
                result.append(str(msg))
        return result

    async def _cap_message_get_by_time(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import message_service 

        try:
            messages = message_service.get_messages_by_time(
                start_time=float(args.get("start_time", 0.0)),
                end_time=float(args.get("end_time", 0.0)),
                limit=args.get("limit", 0),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
            )
            return {"success": True, "messages": self._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_by_time] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_message_get_by_time_in_chat(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import message_service 

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            messages = message_service.get_messages_by_time_in_chat(
                chat_id=chat_id,
                start_time=float(args.get("start_time", 0.0)),
                end_time=float(args.get("end_time", 0.0)),
                limit=args.get("limit", 0),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
                filter_command=args.get("filter_command", False),
            )
            return {"success": True, "messages": self._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_by_time_in_chat] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_message_get_recent(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import message_service 

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            hours = float(args.get("hours", 24.0))
            if hours < 0:
                return {"success": False, "error": "hours 不能是负数"}
            current_time = time.time()
            messages = message_service.get_messages_by_time_in_chat(
                chat_id=chat_id,
                start_time=current_time - hours * 3600,
                end_time=current_time,
                limit=args.get("limit", 100),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
            )
            return {"success": True, "messages": self._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_recent] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_message_count_new(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import message_service 

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            since = args.get("since")
            start_time = float(since) if since is not None else float(args.get("start_time", 0.0))
            count = message_service.count_new_messages(
                chat_id=chat_id,
                start_time=start_time,
                end_time=args.get("end_time"),
            )
            return {"success": True, "count": count}
        except Exception as e:
            logger.error(f"[cap.message.count_new] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_message_build_readable(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import message_service 

        try:
            messages = args.get("messages")
            if messages is None:
                if not (chat_id := args.get("chat_id", "")):
                    return {"success": False, "error": "缺少必要参数: messages 或 chat_id"}
                messages = message_service.get_messages_by_time_in_chat(
                    chat_id=chat_id,
                    start_time=float(args.get("start_time", 0.0)),
                    end_time=float(args.get("end_time", 0.0)),
                    limit=args.get("limit", 0),
                )

            readable = message_service.build_readable_messages(
                messages=messages,
                replace_bot_name=args.get("replace_bot_name", True),
                timestamp_mode=args.get("timestamp_mode", "relative"),
                truncate=args.get("truncate", False),
            )
            return {"success": True, "text": readable}
        except Exception as e:
            logger.error(f"[cap.message.build_readable] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_person_get_id(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.person_info.person_info import Person

        platform: str = args.get("platform", "")
        user_id = args.get("user_id", "")
        if not platform or not user_id:
            return {"success": False, "error": "缺少必要参数 platform 或 user_id"}

        try:
            pid = Person(platform=platform, user_id=str(user_id)).person_id
            return {"success": True, "person_id": pid}
        except Exception as e:
            logger.error(f"[cap.person.get_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_person_get_value(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.person_info.person_info import Person

        person_id: str = args.get("person_id", "")
        field_name: str = args.get("field_name", "")
        if not person_id or not field_name:
            return {"success": False, "error": "缺少必要参数 person_id 或 field_name"}

        try:
            person = Person(person_id=person_id)
            value = getattr(person, field_name)
            if value is None:
                value = args.get("default")
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.person.get_value] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_person_get_id_by_name(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.person_info.person_info import Person

        person_name: str = args.get("person_name", "")
        if not person_name:
            return {"success": False, "error": "缺少必要参数 person_name"}

        try:
            pid = Person(person_name=person_name).person_id
            return {"success": True, "person_id": pid}
        except Exception as e:
            logger.error(f"[cap.person.get_id_by_name] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_by_description(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.chat.emoji_system.emoji_manager import emoji_manager

        description: str = args.get("description", "")
        if not description:
            return {"success": False, "error": "缺少必要参数 description"}

        try:
            emoji = await emoji_manager.get_emoji_for_emotion(description)
            if emoji is None:
                return {"success": True, "emoji": None}
            serialized = self._serialize_emoji_payload(emoji)
            if serialized is None:
                return {"success": True, "emoji": None}
            return {
                "success": True,
                "emoji": serialized,
            }
        except Exception as e:
            logger.error(f"[cap.emoji.get_by_description] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_random(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.chat.emoji_system.emoji_manager import emoji_manager

        count: int = args.get("count", 1)
        try:
            if count < 0:
                return {"success": False, "error": "count 不能为负数"}

            emojis_source = list(emoji_manager.emojis)
            if count == 0 or not emojis_source:
                return {"success": True, "emojis": []}

            selected = random.sample(emojis_source, min(count, len(emojis_source)))
            emojis: List[Dict[str, str]] = []
            for emoji in selected:
                emoji_manager.update_emoji_usage(emoji)
                serialized = self._serialize_emoji_payload(emoji)
                if serialized is not None:
                    if not serialized["emotion"]:
                        serialized["emotion"] = "随机表情"
                    emojis.append(serialized)
            return {"success": True, "emojis": emojis}
        except Exception as e:
            logger.error(f"[cap.emoji.get_random] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_count(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        try:
            from src.chat.emoji_system.emoji_manager import emoji_manager

            return {"success": True, "count": len(emoji_manager.emojis)}
        except Exception as e:
            logger.error(f"[cap.emoji.get_count] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_emotions(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        try:
            from src.chat.emoji_system.emoji_manager import emoji_manager

            emotions = sorted({emotion for emoji in emoji_manager.emojis for emotion in emoji.emotion})
            return {"success": True, "emotions": emotions}
        except Exception as e:
            logger.error(f"[cap.emoji.get_emotions] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_all(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        try:
            from src.chat.emoji_system.emoji_manager import emoji_manager

            emojis = []
            for emoji in emoji_manager.emojis:
                serialized = self._serialize_emoji_payload(emoji)
                if serialized is not None:
                    if not serialized["emotion"]:
                        serialized["emotion"] = "随机表情"
                    emojis.append(serialized)
            return {"success": True, "emojis": emojis}
        except Exception as e:
            logger.error(f"[cap.emoji.get_all] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_get_info(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        try:
            from src.chat.emoji_system.emoji_manager import emoji_manager
            from src.config.config import global_config

            current_count = len(emoji_manager.emojis)
            return {
                "success": True,
                "info": {
                    "current_count": current_count,
                    "max_count": global_config.emoji.max_reg_num,
                    "available_emojis": current_count,
                },
            }
        except Exception as e:
            logger.error(f"[cap.emoji.get_info] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_register(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.chat.emoji_system.emoji_manager import emoji_manager

        emoji_base64: str = args.get("emoji_base64", "")
        if not emoji_base64:
            return {"success": False, "error": "缺少必要参数 emoji_base64"}

        try:
            count_before = len(emoji_manager.emojis)
            temp_file_path = self._build_emoji_temp_path()
            if not ImageUtils.base64_to_image(emoji_base64, str(temp_file_path)):
                return {"success": False, "message": "无法保存图片文件", "description": None, "emotions": None, "replaced": None, "hash": None}

            register_success = await emoji_manager.register_emoji_by_filename(temp_file_path)
            if not register_success:
                if temp_file_path.exists():
                    temp_file_path.unlink(missing_ok=True)
                return {
                    "success": False,
                    "message": "表情包注册失败，可能因为重复、格式不支持或审核未通过",
                    "description": None,
                    "emotions": None,
                    "replaced": None,
                    "hash": None,
                }

            count_after = len(emoji_manager.emojis)
            replaced = count_after <= count_before
            new_emoji = next(
                (
                    item
                    for item in reversed(emoji_manager.emojis)
                    if temp_file_path.name == item.file_name or temp_file_path.name in str(item.full_path)
                ),
                None,
            )
            return {
                "success": True,
                "message": f"表情包注册成功 {'(替换旧表情包)' if replaced else '(新增表情包)'}",
                "description": None if new_emoji is None else new_emoji.description,
                "emotions": None if new_emoji is None else new_emoji.emotion,
                "replaced": replaced,
                "hash": None if new_emoji is None else new_emoji.file_hash,
            }
        except Exception as e:
            logger.error(f"[cap.emoji.register] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_emoji_delete(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.chat.emoji_system.emoji_manager import emoji_manager

        emoji_hash: str = args.get("emoji_hash", "")
        if not emoji_hash:
            return {"success": False, "error": "缺少必要参数 emoji_hash"}

        try:
            emoji = emoji_manager.get_emoji_by_hash(emoji_hash)
            if emoji is None:
                return {"success": False, "message": f"未找到表情包: {emoji_hash}", "hash": emoji_hash}

            success = emoji_manager.delete_emoji(emoji, not bool(emoji.description and emoji.description.strip()))
            if not success:
                return {"success": False, "message": f"删除表情包失败: {emoji_hash}", "hash": emoji_hash}

            emoji_manager.emojis = [item for item in emoji_manager.emojis if item.file_hash != emoji_hash]
            emoji_manager._emoji_num = len(emoji_manager.emojis)
            return {"success": True, "message": f"成功删除表情包: {emoji_hash}", "hash": emoji_hash}
        except Exception as e:
            logger.error(f"[cap.emoji.delete] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _get_frequency_adjust_value(chat_id: str) -> float:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        heartflow_chat = heartflow_manager.heartflow_chat_list.get(chat_id)
        return 1.0 if heartflow_chat is None else heartflow_chat._talk_frequency_adjust

    async def _cap_frequency_get_current_talk_value(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.common.utils.utils_config import ChatConfigUtils

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            value = self._get_frequency_adjust_value(chat_id) * ChatConfigUtils.get_talk_value(chat_id)
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.frequency.get_current_talk_value] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_frequency_set_adjust(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        chat_id: str = args.get("chat_id", "")
        value = args.get("value")
        if not chat_id or value is None:
            return {"success": False, "error": "缺少必要参数 chat_id 或 value"}

        try:
            heartflow_manager.adjust_talk_frequency(chat_id, float(value))
            return {"success": True}
        except Exception as e:
            logger.error(f"[cap.frequency.set_adjust] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_frequency_get_adjust(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            value = self._get_frequency_adjust_value(chat_id)
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.frequency.get_adjust] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_tool_get_definitions(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.core.component_registry import component_registry as core_registry

        try:
            tools = core_registry.get_llm_available_tools()
            return {
                "success": True,
                "tools": [{"name": name, "definition": info.get_llm_definition()} for name, info in tools.items()],
            }
        except Exception as e:
            logger.error(f"[cap.tool.get_definitions] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_knowledge_search(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        query: str = args.get("query", "")
        if not query:
            return {"success": False, "error": "缺少必要参数 query"}

        limit = args.get("limit", 5)
        try:
            limit_value = max(1, int(limit))
        except (TypeError, ValueError):
            limit_value = 5

        try:
            from src.services.memory_service import memory_service

            result = await memory_service.search(query, limit=limit_value)
            if not result.success:
                return {"success": False, "error": result.error or "长期记忆检索失败"}
            knowledge_info = result.to_text(limit=limit_value)
            content = f"你知道这些知识: {knowledge_info}" if knowledge_info else f"你不太了解有关{query}的知识"
            return {"success": True, "content": content}
        except Exception as e:
            logger.error(f"[cap.knowledge.search] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

import asyncio
from asyncio import Task
from typing import Dict, List, Sequence, Tuple

from rich.traceback import install
from sqlmodel import select

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import Messages
from src.common.data_models.mai_message_data_model import MaiMessage, UserInfo
from src.common.data_models.message_component_data_model import (
    TextComponent,
    ImageComponent,
    EmojiComponent,
    AtComponent,
    ReplyComponent,
    VoiceComponent,
    ForwardNodeComponent,
    StandardMessageComponents,
)


install(extra_lines=3)

logger = get_logger("chat_message")


_UNRESOLVED_IMAGE_PLACEHOLDERS = {"[image]", "[图片]"}
_UNRESOLVED_EMOJI_PLACEHOLDERS = {"[emoji]", "[表情包]"}


def _normalize_placeholder_text(content: str | None) -> str:
    """归一化消息组件中的占位文本，便于判断是否仍需补全。"""

    return (content or "").strip().lower()


def _is_unresolved_image_placeholder(content: str | None) -> bool:
    """判断图片组件内容是否仍是适配器注入的占位文本。"""

    normalized = _normalize_placeholder_text(content)
    return not normalized or normalized in _UNRESOLVED_IMAGE_PLACEHOLDERS


def _is_unresolved_emoji_placeholder(content: str | None) -> bool:
    """判断表情组件内容是否仍是适配器注入的占位文本。"""

    normalized = _normalize_placeholder_text(content)
    return not normalized or normalized in _UNRESOLVED_EMOJI_PLACEHOLDERS


class MsgIDMapping:
    """回复消息内容缓存。"""

    def __init__(self) -> None:
        """初始化消息 ID 到内容的映射缓存。"""
        self.mapping: Dict[str, Tuple[str | Task[str], UserInfo]] = {}


class SessionMessage(MaiMessage):
    
    #便于调试的打印函数
    def __str__(self) -> str:
        """返回适合日志输出的消息摘要。"""
        return self.to_debug_string()

    def __repr__(self) -> str:
        """返回适合调试场景的消息摘要。"""
        return self.to_debug_string()

    def to_debug_string(self) -> str:
        """构建包含引用信息的调试字符串。

        Returns:
            str: 适合记录日志的消息摘要。
        """
        user_info = self.message_info.user_info
        group_info = self.message_info.group_info
        chat_type = "group" if group_info else "private"
        group_id = group_info.group_id if group_info else None
        group_name = group_info.group_name if group_info else None
        component_summaries = [self._summarize_component(component) for component in self.raw_message.components]
        raw_components = ", ".join(component_summaries) if component_summaries else "empty"

        return (
            "SessionMessage("
            f"message_id={self.message_id!r}, "
            f"platform={self.platform!r}, "
            f"chat_type={chat_type!r}, "
            f"group_id={group_id!r}, "
            f"group_name={group_name!r}, "
            f"user_id={user_info.user_id!r}, "
            f"user_nickname={user_info.user_nickname!r}, "
            f"user_cardname={user_info.user_cardname!r}, "
            f"reply_to={self.reply_to!r}, "
            f"processed_plain_text={self._truncate_text(self.processed_plain_text)}, "
            f"raw_components=[{raw_components}]"
            ")"
        )

    @staticmethod
    def _truncate_text(text: str | None, max_length: int = 120) -> str:
        """截断较长文本，避免日志过长。

        Args:
            text: 原始文本。
            max_length: 最大保留长度。

        Returns:
            str: 截断后的文本表示。
        """
        if text is None:
            return "None"
        normalized_text = text.replace("\r", "\\r").replace("\n", "\\n")
        if len(normalized_text) <= max_length:
            return repr(normalized_text)
        return repr(f"{normalized_text[:max_length]}...")

    def _summarize_component(self, component: StandardMessageComponents) -> str:
        """生成单个消息组件的调试摘要。

        Args:
            component: 消息组件对象。

        Returns:
            str: 组件摘要文本。
        """
        if isinstance(component, TextComponent):
            return f"Text(text={self._truncate_text(component.text, 80)})"
        if isinstance(component, ImageComponent):
            return f"Image(content={self._truncate_text(component.content or None, 60)})"
        if isinstance(component, EmojiComponent):
            return f"Emoji(content={self._truncate_text(component.content or None, 60)})"
        if isinstance(component, AtComponent):
            target_name = component.target_user_cardname or component.target_user_nickname or component.target_user_id
            return f"At(target={target_name!r})"
        if isinstance(component, VoiceComponent):
            return f"Voice(content={self._truncate_text(component.content or None, 60)})"
        if isinstance(component, ReplyComponent):
            sender_name = (
                component.target_message_sender_cardname
                or component.target_message_sender_nickname
                or component.target_message_sender_id
            )
            return (
                "Reply("
                f"target_message_id={component.target_message_id!r}, "
                f"target_sender={sender_name!r}, "
                f"target_content={self._truncate_text(component.target_message_content, 80)}"
                ")"
            )
        if isinstance(component, ForwardNodeComponent):
            return f"ForwardNode(count={len(component.forward_components)})"
        return f"{component.__class__.__name__}"
    #便于调试的打印函数end

    async def process(
        self,
        *,
        enable_heavy_media_analysis: bool = True,
        enable_voice_transcription: bool = True,
    ) -> None:
        """处理消息内容并转化为纯文本。

        Args:
            enable_heavy_media_analysis: 是否同步执行图片与表情包描述生成。
            enable_voice_transcription: 是否同步执行语音转写。
        """
        id_content_map = MsgIDMapping()
        tasks = [
            self.process_single_component(
                component,
                id_content_map,
                enable_heavy_media_analysis=enable_heavy_media_analysis,
                enable_voice_transcription=enable_voice_transcription,
            )
            for component in self.raw_message.components
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed_texts: List[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"处理消息组件时发生错误: {result}")
            else:
                processed_texts.append(result)
        self.processed_plain_text = " ".join(processed_texts)

    async def process_single_component(
        self,
        component: StandardMessageComponents,
        id_content_map: MsgIDMapping,
        recursion_depth: int = 0,
        *,
        enable_heavy_media_analysis: bool = True,
        enable_voice_transcription: bool = True,
    ) -> str:
        """按类型处理单个消息组件。

        Args:
            component: 待处理的消息组件。
            id_content_map: 回复消息解析缓存。
            recursion_depth: 当前递归深度。
            enable_heavy_media_analysis: 是否同步执行图片与表情包描述生成。
            enable_voice_transcription: 是否同步执行语音转写。

        Returns:
            str: 组件对应的文本表示。
        """
        if isinstance(component, TextComponent):
            return component.text
        elif isinstance(component, ImageComponent):
            return await self.process_image_component(
                component,
                enable_heavy_media_analysis=enable_heavy_media_analysis,
            )
        elif isinstance(component, EmojiComponent):
            return await self.process_emoji_component(
                component,
                enable_heavy_media_analysis=enable_heavy_media_analysis,
            )
        elif isinstance(component, AtComponent):
            return await self.process_at_component(component)
        elif isinstance(component, VoiceComponent):
            return await self.process_voice_component(
                component,
                enable_voice_transcription=enable_voice_transcription,
            )
        elif isinstance(component, ReplyComponent):
            return await self.process_reply_component(component, id_content_map)
        elif isinstance(component, ForwardNodeComponent):
            return await self.process_forward_component(
                component,
                id_content_map,
                recursion_depth=recursion_depth + 1,
                enable_heavy_media_analysis=enable_heavy_media_analysis,
                enable_voice_transcription=enable_voice_transcription,
            )
        else:
            raise NotImplementedError(f"暂时不支持的消息组件类型: {type(component)}")

    async def process_image_component(
        self,
        component: ImageComponent,
        *,
        enable_heavy_media_analysis: bool = True,
    ) -> str:
        """处理图片组件。

        Args:
            component: 图片组件。
            enable_heavy_media_analysis: 是否同步执行图片描述生成。

        Returns:
            str: 图片组件对应的文本表示。
        """
        if component.content and not _is_unresolved_image_placeholder(component.content):
            return component.content
        from src.chat.image_system.image_manager import image_manager

        # 获取描述
        try:
            desc = await image_manager.get_image_description(
                image_bytes=component.binary_data,
                wait_for_build=enable_heavy_media_analysis,
            )
        except Exception:
            desc = None  # 失败置空

        content = f"[图片：{desc}]" if desc else "[图片]"
        component.content = content
        component.binary_data = b""  # 处理完就丢掉二进制数据，节省内存
        return content

    async def process_emoji_component(
        self,
        component: EmojiComponent,
        *,
        enable_heavy_media_analysis: bool = True,
    ) -> str:
        """处理表情包组件。

        Args:
            component: 表情包组件。
            enable_heavy_media_analysis: 是否同步执行表情包描述生成。

        Returns:
            str: 表情包组件对应的文本表示。
        """
        if component.content and not _is_unresolved_emoji_placeholder(component.content):
            return component.content
        from src.emoji_system.emoji_manager import emoji_manager

        # 获取表情包描述
        try:
            tuple_content = await emoji_manager.get_emoji_description(
                emoji_bytes=component.binary_data,
                wait_for_build=enable_heavy_media_analysis,
            )
        except Exception:
            tuple_content = None  # 失败置空

        if tuple_content:
            desc, _ = tuple_content
            content = f"[表情包: {desc}]"
        else:
            content = "[表情包]"
        component.content = content
        component.binary_data = b""  # 处理完就丢掉二进制数据，节省内存
        return content

    async def process_at_component(self, component: AtComponent) -> str:
        # 如果已经有昵称或备注了，直接使用
        if component.target_user_cardname:
            return f"@{component.target_user_cardname}"
        elif component.target_user_nickname:
            return f"@{component.target_user_nickname}"
        from src.common.utils.utils_person import PersonUtils

        # 查询用户信息
        if person_info := PersonUtils.get_person_info_by_user_id_and_platform(component.target_user_id, self.platform):
            component.target_user_nickname = component.target_user_nickname or person_info.user_nickname
            if self.message_info.group_info and person_info.group_cardname_list:
                for group_card in person_info.group_cardname_list:
                    if group_card.group_id == self.message_info.group_info.group_id:
                        component.target_user_cardname = group_card.group_cardname
                        break
        if component.target_user_cardname:  # 优先使用群备注
            return f"@{component.target_user_cardname}"
        elif component.target_user_nickname:  # 其次使用昵称
            return f"@{component.target_user_nickname}"
        else:  # 最后使用用户ID
            return f"@{component.target_user_id}"

    async def process_voice_component(
        self,
        component: VoiceComponent,
        *,
        enable_voice_transcription: bool = True,
    ) -> str:
        """处理语音组件。

        Args:
            component: 语音组件。
            enable_voice_transcription: 是否同步执行语音转写。

        Returns:
            str: 语音组件对应的文本表示。
        """
        if component.content:  # 先检查是否处理过
            return component.content
        if not enable_voice_transcription:
            component.content = "[语音消息]"
            return component.content
        from src.common.utils.utils_voice import get_voice_text

        text = await get_voice_text(component.binary_data)
        content = "[语音消息，转录失败]" if text is None else f"[语音: {text}]"
        component.content = content
        return content

    async def process_reply_component(
        self,
        component: ReplyComponent,
        id_content_map: MsgIDMapping,
    ) -> str:
        if component.target_message_content:
            return component.target_message_content
        if result_item := id_content_map.mapping.get(component.target_message_id):  # ID映射缓存优先
            content, sender_info = result_item
            if isinstance(content, Task):  # 如果是Task，说明是转发组件传入的占位结果，需要等待其完成
                content = await content  # 获取最终结果
                id_content_map.mapping[component.target_message_id] = (content, sender_info)  # 更新为实际内容
            component.target_message_content = content
            tgt_msg_s_name = sender_info.user_cardname or sender_info.user_nickname or sender_info.user_id
            component.target_message_sender_cardname = sender_info.user_cardname
            component.target_message_sender_nickname = sender_info.user_nickname
            component.target_message_sender_id = sender_info.user_id
            return f"[回复了{tgt_msg_s_name}的消息: {content}]"
        else:  # 尝试从数据库根据消息id查找消息内容
            try:
                with get_db_session() as session:
                    statement = select(Messages).filter_by(message_id=component.target_message_id).limit(1)
                    if db_msg := session.exec(statement).first():
                        component.target_message_content = db_msg.processed_plain_text
                        component.target_message_sender_cardname = db_msg.user_cardname
                        component.target_message_sender_nickname = db_msg.user_nickname
                        component.target_message_sender_id = db_msg.user_id
                        tgt_msg_s_name = db_msg.user_cardname or db_msg.user_nickname or db_msg.user_id
                        return f"[回复了{tgt_msg_s_name}的消息: {db_msg.processed_plain_text}]"
            except Exception as e:
                logger.error(f"查询回复消息时发生错误: {e}")

            return "[回复了一条消息，但原消息已无法访问]"

    async def process_forward_component(
        self,
        component: ForwardNodeComponent,
        id_content_map: MsgIDMapping,
        recursion_depth: int = 0,
        *,
        enable_heavy_media_analysis: bool = True,
        enable_voice_transcription: bool = True,
    ) -> str:
        """处理合并转发组件。

        Args:
            component: 合并转发组件。
            id_content_map: 回复消息解析缓存。
            recursion_depth: 当前递归深度。
            enable_heavy_media_analysis: 是否同步执行图片与表情包描述生成。
            enable_voice_transcription: 是否同步执行语音转写。

        Returns:
            str: 合并转发组件对应的文本表示。
        """
        task_list: List[Task] = []
        node_user_info_list: List[UserInfo] = []
        for node in component.forward_components:
            task = asyncio.create_task(
                self._process_multiple_components(
                    node.content,
                    id_content_map,
                    recursion_depth + 1,
                    enable_heavy_media_analysis=enable_heavy_media_analysis,
                    enable_voice_transcription=enable_voice_transcription,
                )
            )
            node_user_info = UserInfo(node.user_id or "未知用户", node.user_nickname, node.user_cardname)
            # 传入ID缓存映射，方便Reply组件获取并等待处理结果
            id_content_map.mapping[node.message_id] = (task, node_user_info)

            task_list.append(task)
            node_user_info_list.append(node_user_info)

        results = await asyncio.gather(*task_list, return_exceptions=True)  # 并行处理节点内容
        forward_texts = []
        for idx, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error(f"处理转发消息组件时发生错误: {result}")
            else:
                usr_info = node_user_info_list[idx]
                msg_sender_name = usr_info.user_cardname or usr_info.user_nickname or usr_info.user_id or "未知用户"
                forward_texts.append(f"{'-' * recursion_depth * 2} 【{msg_sender_name}】: {result}")
        return "【合并转发消息: \n" + "\n".join(forward_texts) + "\n】"

    async def _process_multiple_components(
        self,
        components: Sequence[StandardMessageComponents],
        id_content_map: MsgIDMapping,
        recursion_depth: int = 0,
        *,
        enable_heavy_media_analysis: bool = True,
        enable_voice_transcription: bool = True,
    ) -> str:
        """并行处理多个消息组件。

        Args:
            components: 待处理的组件序列。
            id_content_map: 回复消息解析缓存。
            recursion_depth: 当前递归深度。
            enable_heavy_media_analysis: 是否同步执行图片与表情包描述生成。
            enable_voice_transcription: 是否同步执行语音转写。

        Returns:
            str: 多个组件拼接后的文本表示。
        """
        tasks = [
            self.process_single_component(
                component,
                id_content_map,
                recursion_depth,
                enable_heavy_media_analysis=enable_heavy_media_analysis,
                enable_voice_transcription=enable_voice_transcription,
            )
            for component in components
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)  # 并行处理多个组件
        processed_texts: List[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"处理消息组件时发生错误: {result}")
            else:
                processed_texts.append(result)
        return " ".join(processed_texts)

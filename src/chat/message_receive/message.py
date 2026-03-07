from asyncio import Task
from datetime import datetime
from maim_message import (
    MessageBase,
    UserInfo as MaimUserInfo,
    GroupInfo as MaimGroupInfo,
    BaseMessageInfo as MaimBaseMessageInfo,
    Seg,
)
from rich.traceback import install
from sqlmodel import select
from typing import List, Dict, Optional, Tuple, Sequence, TYPE_CHECKING

import asyncio
import time

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import Messages
from src.common.data_models.mai_message_data_model import MaiMessage, UserInfo, GroupInfo, MessageInfo
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
from src.common.utils.utils_message import MessageUtils

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession


install(extra_lines=3)

logger = get_logger("chat_message")


class MsgIDMapping:
    def __init__(self):
        self.mapping: Dict[str, Tuple[str | Task, UserInfo]] = {}


class SessionMessage(MaiMessage):
    async def process(self):
        """处理消息内容，识别消息内容并转化为文本（会修改消息组件属性）"""
        tasks = [self.process_single_component(component, MsgIDMapping()) for component in self.raw_message.components]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed_texts: List[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"处理消息组件时发生错误: {result}")
            else:
                processed_texts.append(result)
        self.processed_plain_text = " ".join(processed_texts)

    async def process_single_component(
        self, component: StandardMessageComponents, id_content_map: MsgIDMapping, recursion_depth: int = 0
    ) -> str:
        """按照类型处理单个消息组件，返回处理后的文本内容（会修改消息组件属性）"""
        if isinstance(component, TextComponent):
            return component.text
        elif isinstance(component, ImageComponent):
            return await self.process_image_component(component)
        elif isinstance(component, EmojiComponent):
            return await self.process_emoji_component(component)
        elif isinstance(component, AtComponent):
            return await self.process_at_component(component)
        elif isinstance(component, VoiceComponent):
            return await self.process_voice_component(component)
        elif isinstance(component, ReplyComponent):
            return await self.process_reply_component(component, id_content_map)
        elif isinstance(component, ForwardNodeComponent):
            return await self.process_forward_component(component, id_content_map, recursion_depth=recursion_depth + 1)
        else:
            raise NotImplementedError(f"暂时不支持的消息组件类型: {type(component)}")

    async def process_image_component(self, component: ImageComponent) -> str:
        if component.content:  # 先检查是否处理过
            return component.content
        from src.chat.image_system.image_manager import image_manager

        # 获取描述
        try:
            desc = await image_manager.get_image_description(image_bytes=component.binary_data)
        except Exception:
            desc = None  # 失败置空

        content = f"[图片：{desc}]" if desc else "[一张图片，网卡了加载不出来]"
        component.content = content
        component.binary_data = b""  # 处理完就丢掉二进制数据，节省内存
        return content

    async def process_emoji_component(self, component: EmojiComponent) -> str:
        if component.content:  # 先检查是否处理过
            return component.content
        from src.chat.emoji_system.emoji_manager import emoji_manager

        # 获取表情包描述
        try:
            tuple_content = await emoji_manager.get_emoji_description(emoji_bytes=component.binary_data)
        except Exception:
            tuple_content = None  # 失败置空

        if tuple_content:
            desc, _ = tuple_content
            content = f"[表情包: {desc}]"
        else:
            content = "[一个表情，网卡了加载不出来]"
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

    async def process_voice_component(self, component: VoiceComponent) -> str:
        if component.content:  # 先检查是否处理过
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
        self, component: ForwardNodeComponent, id_content_map: MsgIDMapping, recursion_depth: int = 0
    ) -> str:
        task_list: List[Task] = []
        node_user_info_list: List[UserInfo] = []
        for node in component.forward_components:
            task = asyncio.create_task(
                self._process_multiple_components(node.content, id_content_map, recursion_depth + 1)
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
        self, components: Sequence[StandardMessageComponents], id_content_map: MsgIDMapping, recursion_depth: int = 0
    ) -> str:
        tasks = [self.process_single_component(component, id_content_map, recursion_depth) for component in components]
        results = await asyncio.gather(*tasks, return_exceptions=True)  # 并行处理多个组件
        processed_texts: List[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"处理消息组件时发生错误: {result}")
            else:
                processed_texts.append(result)
        return " ".join(processed_texts)


class MessageSending(MaiMessage):
    """发送状态的消息类，继承 MaiMessage 基类。

    用于构建、处理和发送机器人的回复消息。
    复用 MaiMessage 的 to_maim_message() 和 to_db_instance() 方法，
    额外管理发送专属的会话信息和控制字段。
    """

    def __init__(
        self,
        message_id: str,
        session: "BotChatSession",
        bot_user_info: UserInfo,
        message_segment: Seg,
        sender_info: Optional[UserInfo] = None,
        reply: Optional[MaiMessage] = None,
        display_message: str = "",
        is_head: bool = False,
        is_emoji: bool = False,
        thinking_start_time: float = 0,
        reply_to: Optional[str] = None,
        selected_expressions: Optional[List[int]] = None,
    ):
        # 初始化 MaiMessage 基类
        super().__init__(message_id=message_id, timestamp=datetime.now())

        # 发送专属字段
        self.session = session
        self.sender_info = sender_info
        self.message_segment = message_segment
        self.reply = reply
        self.is_head = is_head
        self.thinking_start_time = thinking_start_time
        self.selected_expressions = selected_expressions
        self.reply_to_message_id: Optional[str] = reply.message_id if reply else None
        self.interest_value: float = 0.0

        # 填充 MaiMessage 标准字段
        self.platform = session.platform
        self.session_id = session.session_id
        self.is_emoji = is_emoji
        self.reply_to = reply_to
        self.display_message = display_message
        self.processed_plain_text = ""

        # 构建 message_info：DB 存储时 user_info 始终为 bot 信息
        # 私聊/群聊的 user_info 差异仅在 to_maim_message() 覆写中处理
        group_info = self._resolve_group_info()
        self.message_info = MessageInfo(user_info=bot_user_info, group_info=group_info)

        # bot_user_info 单独保存，to_maim_message 覆写时还需要
        self.bot_user_info = bot_user_info

        # 将 Seg 转换为 MessageSequence，供基类的 to_db_instance / to_maim_message 使用
        self.raw_message = MessageUtils.from_maim_message_segments_to_MaiSeq(
            MessageBase(message_info=None, message_segment=message_segment)
        )

        self.initialized = True

    def _resolve_group_info(self) -> Optional[GroupInfo]:
        """从 session 中解析群信息"""
        if not self.session.group_id:
            return None
        group_name = ""
        if (
            self.session.context
            and self.session.context.message
            and self.session.context.message.message_info.group_info
        ):
            group_name = self.session.context.message.message_info.group_info.group_name
        return GroupInfo(group_id=self.session.group_id, group_name=group_name)

    async def process(self) -> None:
        """处理消息段，生成 processed_plain_text（使用 SessionMessage 的组件处理能力）"""
        # 同步 message_segment → raw_message（插件可能修改了 message_segment）
        self.raw_message = MessageUtils.from_maim_message_segments_to_MaiSeq(
            MessageBase(message_info=None, message_segment=self.message_segment)
        )
        if self.raw_message and self.raw_message.components:
            tasks = [self._process_component(c) for c in self.raw_message.components]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            texts = []
            for r in results:
                if isinstance(r, BaseException):
                    logger.error(f"处理发送消息组件时发生错误: {r}")
                elif r:
                    texts.append(r)
            self.processed_plain_text = " ".join(texts)

    async def _process_component(self, component: StandardMessageComponents) -> str:
        """简单处理单个标准组件为纯文本描述"""
        if isinstance(component, TextComponent):
            return component.text
        elif isinstance(component, ImageComponent):
            return "[图片]"
        elif isinstance(component, EmojiComponent):
            return "[表情包]"
        elif isinstance(component, VoiceComponent):
            return "[语音]"
        elif isinstance(component, AtComponent):
            return f"[@{component.target_user_id}]"
        elif isinstance(component, ReplyComponent):
            return ""
        else:
            return f"[{type(component).__name__}]"

    def build_reply(self) -> None:
        """构建回复消息段，在 message_segment 前插入 reply 段"""
        if self.reply:
            self.reply_to_message_id = self.reply.message_id
            self.message_segment = Seg(
                type="seglist",
                data=[
                    Seg(type="reply", data=self.reply.message_id),
                    self.message_segment,
                ],
            )
            # 同步更新 raw_message
            self.raw_message = MessageUtils.from_maim_message_segments_to_MaiSeq(
                MessageBase(message_info=None, message_segment=self.message_segment)
            )

    async def to_maim_message(self) -> MessageBase:
        """覆写基类方法：发送消息需要特殊处理 user_info（私聊/群聊差异）"""
        maim_bot_user_info = MaimUserInfo(
            user_id=self.bot_user_info.user_id,
            user_nickname=self.bot_user_info.user_nickname,
            user_cardname=self.bot_user_info.user_cardname,
            platform=self.platform,
        )

        maim_group_info = None
        if self.message_info.group_info:
            maim_group_info = MaimGroupInfo(
                group_id=self.message_info.group_info.group_id,
                group_name=self.message_info.group_info.group_name,
                platform=self.platform,
            )

        # 私聊时 user_info 填接收者信息（sender_info），群聊时填 bot
        if maim_group_info is None and self.sender_info:
            msg_user_info = MaimUserInfo(
                user_id=self.sender_info.user_id,
                user_nickname=self.sender_info.user_nickname,
                user_cardname=self.sender_info.user_cardname,
                platform=self.platform,
            )
        else:
            msg_user_info = maim_bot_user_info

        maim_msg_info = MaimBaseMessageInfo(
            platform=self.platform,
            message_id=self.message_id,
            time=time.time(),
            group_info=maim_group_info,
            user_info=msg_user_info,
        )

        msg_segments = await MessageUtils.from_MaiSeq_to_maim_message_segments(self.raw_message)
        return MessageBase(message_info=maim_msg_info, message_segment=Seg(type="seglist", data=msg_segments))

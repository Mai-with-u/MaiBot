from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional, TYPE_CHECKING, List
from src.common.logger import get_logger
from src.common.data_models.message_data_model import ReplyContentType, ReplyContent, ReplySetModel, ForwardNode
from src.plugin_system.base.component_types import CommandInfo, ComponentType
from src.chat.message_receive.message import MessageRecv
from src.plugin_system.apis import send_api

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages

logger = get_logger("base_command")


class BaseCommand(ABC):
    """Command组件基类

    Command是插件的一种组件类型，用于处理命令请求

    子类可以通过类属性定义命令模式：
    - command_pattern: 命令匹配的正则表达式
    - command_help: 命令帮助信息
    - command_examples: 命令使用示例列表
    """

    command_name: str = ""
    """Command组件的名称"""
    command_description: str = ""
    """Command组件的描述"""
    # 默认命令设置
    command_pattern: str = r""
    """命令匹配的正则表达式"""

    def __init__(self, message: MessageRecv, plugin_config: Optional[dict] = None):
        """初始化Command组件

        Args:
            message: 接收到的消息对象
            plugin_config: 插件配置字典
        """
        self.message = message
        self.matched_groups: Dict[str, str] = {}  # 存储正则表达式匹配的命名组
        self.plugin_config = plugin_config or {}  # 直接存储插件配置字典

        self.log_prefix = "[Command]"

        logger.debug(f"{self.log_prefix} Command组件初始化完成")

    def set_matched_groups(self, groups: Dict[str, str]) -> None:
        """设置正则表达式匹配的命名组

        Args:
            groups: 正则表达式匹配的命名组
        """
        self.matched_groups = groups

    @abstractmethod
    async def execute(self) -> Tuple[bool, Optional[str], int]:
        """执行Command的抽象方法，子类必须实现

        Returns:
            Tuple[bool, Optional[str], int]: (是否执行成功, 可选的回复消息, 拦截消息力度，0代表不拦截，1代表仅不触发回复，replyer可见，2代表不触发回复，replyer不可见)
        """
        pass

    def get_config(self, key: str, default=None):
        """获取插件配置值，使用嵌套键访问

        Args:
            key: 配置键名，使用嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        if not self.plugin_config:
            return default

        # 支持嵌套键访问
        keys = key.split(".")
        current = self.plugin_config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current

    async def send_text(
        self,
        content: str,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        storage_message: bool = True,
    ) -> bool:
        """发送回复消息

        Args:
            content: 回复内容
            set_reply: 是否作为回复发送
            reply_message: 回复的消息对象（当set_reply为True时必填）
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.text_to_stream(
            text=content,
            stream_id=chat_stream.stream_id,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
        )

    async def send_image(
        self,
        image_base64: str,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        storage_message: bool = True,
    ) -> bool:
        """发送图片

        Args:
            image_base64: 图片的base64编码

        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.image_to_stream(
            image_base64,
            chat_stream.stream_id,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
        )

    async def send_emoji(
        self,
        emoji_base64: str,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        storage_message: bool = True,
    ) -> bool:
        """发送表情包

        Args:
            emoji_base64: 表情包的base64编码
            set_reply: 是否作为回复发送
            reply_message: 回复的消息对象（当set_reply为True时必填）
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.emoji_to_stream(
            emoji_base64, chat_stream.stream_id, set_reply=set_reply, reply_message=reply_message
        )

    async def send_command(
        self,
        command_name: str,
        args: Optional[dict] = None,
        display_message: str = "",
        storage_message: bool = True,
    ) -> bool:
        """发送命令消息

        Args:
            command_name: 命令名称
            args: 命令参数
            display_message: 显示消息
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        try:
            # 获取聊天流信息
            chat_stream = self.message.chat_stream
            if not chat_stream or not hasattr(chat_stream, "stream_id"):
                logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
                return False

            # 构造命令数据
            command_data = {"name": command_name, "args": args or {}}

            success = await send_api.command_to_stream(
                command=command_data,
                stream_id=chat_stream.stream_id,
                storage_message=storage_message,
                display_message=display_message,
            )

            if success:
                logger.info(f"{self.log_prefix} 成功发送命令: {command_name}")
            else:
                logger.error(f"{self.log_prefix} 发送命令失败: {command_name}")

            return success

        except Exception as e:
            logger.error(f"{self.log_prefix} 发送命令时出错: {e}")
            return False

    async def send_voice(self, voice_base64: str) -> bool:
        """
        发送语音消息
        Args:
            voice_base64: 语音的base64编码
        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.custom_to_stream(
            message_type="voice",
            content=voice_base64,
            stream_id=chat_stream.stream_id,
            typing=False,
            set_reply=False,
            reply_message=None,
            storage_message=False,
        )

    async def send_hybrid(
        self,
        message_tuple_list: List[Tuple[ReplyContentType | str, str]],
        typing: bool = False,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        storage_message: bool = True,
    ) -> bool:
        """
        发送混合类型消息

        Args:
            message_tuple_list: 包含消息类型和内容的元组列表，格式为 [(内容类型, 内容), ...]
            typing: 是否显示正在输入
            set_reply: 是否计算打字时间
            reply_message: 回复的消息对象
            storage_message: 是否存储消息到数据库
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False
        reply_set = ReplySetModel()
        reply_set.add_hybrid_content_by_raw(message_tuple_list)
        return await send_api.custom_reply_set_to_stream(
            reply_set=reply_set,
            stream_id=chat_stream.stream_id,
            typing=typing,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
        )

    async def send_forward(
        self,
        messages_list: List[Tuple[str, str, List[Tuple[ReplyContentType | str, str]]] | str],
        storage_message: bool = True,
    ) -> bool:
        """转发消息

        Args:
            messages_list: 包含消息信息的列表，当传入自行生成的数据时，元素格式为 (sender_id, nickname, 消息体)；当传入消息ID时，元素格式为 "message_id"
            其中消息体的格式为 [(内容类型, 内容), ...]
            任意长度的消息都需要使用列表的形式传入
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False
        reply_set = ReplySetModel()
        forward_message_nodes: List[ForwardNode] = []
        for message in messages_list:
            if isinstance(message, str):
                forward_message_node = ForwardNode.construct_as_id_reference(message)
            elif isinstance(message, Tuple) and len(message) == 3:
                sender_id, nickname, content_list = message
                single_node_content_list: List[ReplyContent] = []
                for node_content_type, node_content in content_list:
                    reply_node_content = ReplyContent(content_type=node_content_type, content=node_content)
                    single_node_content_list.append(reply_node_content)
                forward_message_node = ForwardNode.construct_as_created_node(
                    user_id=sender_id, user_nickname=nickname, content=single_node_content_list
                )
            else:
                logger.warning(f"{self.log_prefix} 转发消息时遇到无效的消息格式: {message}")
                continue
            forward_message_nodes.append(forward_message_node)
        reply_set.add_forward_content(forward_message_nodes)
        return await send_api.custom_reply_set_to_stream(
            reply_set=reply_set,
            stream_id=chat_stream.stream_id,
            storage_message=storage_message,
            set_reply=False,
            reply_message=None,
        )

    async def send_custom(
        self,
        message_type: str,
        content: str | Dict,
        display_message: str = "",
        typing: bool = False,
        set_reply: bool = False,
        reply_message: Optional["DatabaseMessages"] = None,
        storage_message: bool = True,
    ) -> bool:
        """发送指定类型的回复消息到当前聊天环境

        Args:
            message_type: 消息类型，如"text"、"image"、"emoji"、"voice"等
            content: 消息内容
            display_message: 显示消息（可选）
            typing: 是否显示正在输入
            set_reply: 是否作为回复发送
            reply_message: 回复的消息对象（set_reply 为 True时必填）
            storage_message: 是否存储消息到数据库

        Returns:
            bool: 是否发送成功
        """
        # 获取聊天流信息
        chat_stream = self.message.chat_stream
        if not chat_stream or not hasattr(chat_stream, "stream_id"):
            logger.error(f"{self.log_prefix} 缺少聊天流或stream_id")
            return False

        return await send_api.custom_to_stream(
            message_type=message_type,
            content=content,
            stream_id=chat_stream.stream_id,
            display_message=display_message,
            typing=typing,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
        )

    @classmethod
    def get_command_info(cls) -> "CommandInfo":
        """从类属性生成CommandInfo

        Args:
            name: Command名称，如果不提供则使用类名
            description: Command描述，如果不提供则使用类文档字符串

        Returns:
            CommandInfo: 生成的Command信息对象
        """
        if "." in cls.command_name:
            logger.error(f"Command名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")
            raise ValueError(f"Command名称 '{cls.command_name}' 包含非法字符 '.'，请使用下划线替代")
        return CommandInfo(
            name=cls.command_name,
            component_type=ComponentType.COMMAND,
            description=cls.command_description,
            command_pattern=cls.command_pattern,
        )

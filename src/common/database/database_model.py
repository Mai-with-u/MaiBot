from typing import Optional
from sqlalchemy import Column, Float, Enum as SQLEnum, DateTime
from sqlmodel import SQLModel, Field, LargeBinary
from enum import Enum
from datetime import datetime


class ModelUser(str, Enum):
    SYSTEM = "system"
    PLUGIN = "plugin"


class ImageType(str, Enum):
    EMOJI = "emoji"
    IMAGE = "image"


class ModifiedBy(str, Enum):
    AI = "ai"
    USER = "user"


class Messages(SQLModel, table=True):
    __tablename__ = "mai_messages"  # type: ignore
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 消息元数据
    message_id: str = Field(index=True, max_length=255)  # 消息id
    timestamp: datetime = Field(sa_column=Column(DateTime))  # 消息时间，单位为秒
    platform: str = Field(index=True, max_length=100)  # 顶层平台字段
    # 消息发送者信息
    user_id: str = Field(index=True, max_length=255)  # 发送者用户id
    user_nickname: str = Field(index=True, max_length=255)  # 发送者昵称
    user_cardname: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 发送者备注名
    # 群聊信息（如果有）
    group_id: Optional[str] = Field(index=True, default=None, max_length=255, nullable=True)  # 群组id
    group_name: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 群组名称
    # 被提及/at字段
    is_mentioned: bool = Field(default=False)  # 被提及
    is_at: bool = Field(default=False)  # 被at

    # 消息内部元数据
    session_id: str = Field(index=True, max_length=255)  # 聊天会话id
    reply_to: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 回复的消息id
    is_emoji: bool = Field(default=False)  # 是否为表情包消息
    is_picture: bool = Field(default=False)  # 是否为图片消息
    is_command: bool = Field(default=False)  # 是否为命令
    is_notify: bool = Field(default=False)  # 是否为通知消息

    # 消息内容
    raw_content: bytes = Field(sa_column=Column(LargeBinary))  # msgpack后的原始消息内容
    processed_plain_text: Optional[str] = Field(default=None)  # 平面化处理后的纯文本消息
    display_message: Optional[str] = Field(default=None)  # 显示的消息内容（被放入Prompt）

    # 其他配置
    additional_config: Optional[str] = Field(default=None)  # 额外配置，JSON格式存储


class ModelUsage(SQLModel, table=True):
    __tablename__ = "llm_usage"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 模型相关信息
    model_name: str = Field(index=True, max_length=255)  # 模型实际名称（供应商名称）
    model_assign_name: Optional[str] = Field(index=True, default=None, max_length=255)  # 模型分配名称（用户自定义名称）
    model_api_provider_name: str = Field(index=True, max_length=255)  # 模型API供应商名称

    # 请求相关信息
    endpoint: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 模型API的具体endpoint
    user_type: ModelUser = Field(sa_column=Column(SQLEnum(ModelUser)), default=ModelUser.SYSTEM)  # 模型使用者类型
    request_type: str = Field(max_length=50)  # 内部请求类型，记录哪种模块使用了此模型
    time_cost: float = Field(sa_column=Column(Float))  # 本次请求耗时，单位秒
    timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))  # 请求时间戳

    # Token使用情况
    prompt_tokens: int  # 提示词令牌数
    completion_tokens: int  # 完成词令牌数
    total_tokens: int  # 总令牌数
    cost: float  # 本次请求的费用，单位元


class Images(SQLModel, table=True):
    """用于同时存储表情包和图片的数据库模型。"""

    __tablename__ = "images"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 元信息
    image_hash: str = Field(index=True, max_length=255)  # 图片哈希，使用sha256哈希值，亦作为图片唯一ID
    description: str  # 图片的描述
    full_path: str = Field(max_length=1024)  # 文件的完整路径 (包括文件名)
    image_type: ImageType = Field(sa_column=Column(SQLEnum(ImageType)), default=ImageType.EMOJI)
    """图片类型，例如 'emoji' 或 'image'"""
    emotion: Optional[str] = Field(default=None, nullable=True)  # 表情包的情感标签，逗号分隔

    query_count: int = Field(default=0)  # 被查询次数
    is_registered: bool = Field(default=False)  # 是否已经注册
    is_banned: bool = Field(default=False)  # 被手动禁用

    no_file_flag: bool = Field(default=False)  # 文件不存在标记，如果为True表示文件已经不存在，仅保留描述字段

    record_time: datetime = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 记录时间（数据库记录被创建的时间）
    register_time: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )  # 注册时间（被注册为可用表情包的时间）
    last_used_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))  # 上次使用时间

    vlm_processed: bool = Field(default=False)  # 是否已经过VLM处理


class ActionRecord(SQLModel, table=True):
    """存储动作记录"""

    __tablename__ = "action_records"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 元信息
    action_id: str = Field(index=True, max_length=255)  # 动作ID
    timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))  # 记录时间戳
    session_id: str = Field(index=True, max_length=255)  # 对应的 ChatSession session_id

    # 调用信息
    action_name: str = Field(index=True, max_length=255)  # 动作名称
    action_reasoning: Optional[str] = Field(default=None)  # 动作推理过程
    action_data: Optional[str] = Field(default=None)  # 动作数据，JSON格式存储

    action_builtin_prompt: Optional[str] = Field(default=None)  # 内置动作提示
    action_display_prompt: Optional[str] = Field(default=None)  # 最终输入到Prompt的内容


class CommandRecord(SQLModel, table=True):
    """记录命令执行情况"""

    __tablename__ = "command_records"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 元信息
    timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))  # 记录时间戳
    session_id: str = Field(index=True, max_length=255)  # 对应的 ChatSession session_id

    # 调用信息
    command_name: str = Field(index=True, max_length=255)  # 命令名称
    command_data: Optional[str] = Field(default=None)  # 命令数据，JSON格式存储
    command_result: Optional[str] = Field(default=None)  # 命令执行结果


class OnlineTime(SQLModel, table=True):
    """
    用于存储在线时长记录的模型。
    """

    __tablename__ = "online_time"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))  # 时间戳
    duration_minutes: int = Field()  # 时长，单位秒
    start_timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime))  # 上线时间
    end_timestamp: datetime = Field(sa_column=Column(DateTime))  # 下线时间


class Expression(SQLModel, table=True):
    """用于存储表达方式的模型"""

    __tablename__ = "expressions"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    situation: str = Field(index=True, max_length=255, primary_key=True)  # 情景
    style: str = Field(index=True, max_length=255, primary_key=True)  # 风格

    # context: str  # 上下文
    # up_content: str

    content_list: str  # 内容列表，JSON格式存储
    count: int = Field(default=0)  # 使用次数
    last_active_time: datetime = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 上次使用时间
    create_time: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime))  # 创建时间
    session_id: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 会话ID，区分是否为全局表达方式

    checked: bool = Field(default=False)  # 是否已经被检查过
    rejected: bool = Field(default=False)  # 是否被拒绝但是未更新
    modified_by: Optional[ModifiedBy] = Field(
        default=None, sa_column=Column(SQLEnum(ModifiedBy), nullable=True)
    )  # 最后修改者，标记用户或AI，为空表示未检查


class Jargon(SQLModel, table=True):
    """存黑话的模型"""

    __tablename__ = "jargons"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    content: str = Field(index=True, max_length=255, primary_key=True)  # 黑话内容
    raw_content: Optional[str] = Field(default=None, nullable=True)  # 原始内容，未处理的黑话内容

    meaning: str  # 黑话含义
    session_id: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 会话ID，区分是否为全局黑话

    count: int = Field(default=0)  # 使用次数
    is_jargon: Optional[bool] = Field(default=True)  # 是否为黑话，False表示为白话
    is_complete: bool = Field(default=False)  # 是否为已经完成全部推断（count > 100后不再推断）
    inference_with_context: Optional[str] = Field(default=None, nullable=True)  # 带上下文的推断结果，JSON格式
    inference_with_content_only: Optional[str] = Field(default=None, nullable=True)  # 只基于词条的推断结果，JSON格式


class ChatHistory(SQLModel, table=True):
    """存储聊天历史记录的模型"""

    __tablename__ = "chat_history"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 元信息
    session_id: str = Field(index=True, max_length=255)  # 聊天会话ID
    start_timestamp: datetime = Field(sa_column=Column(DateTime, index=True))  # 聊天开始时间
    end_timestamp: datetime = Field(sa_column=Column(DateTime, index=True))  # 聊天结束时间
    query_count: int = Field(default=0)  # 被检索次数
    query_forget_count: int = Field(default=0)  # 被遗忘检查的次数

    # 历史消息内容
    original_messages: str  # 对话原文
    participants: str  # 参与者列表，JSON格式存储
    theme: str  # 对话主题：这段对话的主要内容，一个简短的标题
    keywords: str  # 关键词：这段对话的关键词，JSON格式存储
    summary: str  # 概括：对这段话的平文本概括


class ThinkingQuestion(SQLModel, table=True):
    """存储思考型问题的模型"""

    __tablename__ = "thinking_questions"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    # 问答对
    question: str  # 问题内容
    context: Optional[str] = Field(default=None, nullable=True)  # 上下文
    found_answer: bool = Field(default=False)  # 是否找到答案
    answer: Optional[str] = Field(default=None, nullable=True)  # 问题答案

    thinking_steps: Optional[str] = Field(default=None, nullable=True)  # 思考步骤，JSON格式存储
    created_timestamp: datetime = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 创建时间
    updated_timestamp: datetime = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 最后更新时间


class BinaryData(SQLModel, table=True):
    """存储二进制数据的模型"""

    __tablename__ = "binary_data"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    data_hash: str = Field(index=True, max_length=255)  # 数据哈希，使用sha256哈希值，亦作为数据唯一ID
    full_path: str = Field(max_length=1024)  # 文件的完整路径 (包括文件名)


class PersonInfo(SQLModel, table=True):
    """存储个人信息的模型"""

    __tablename__ = "person_info"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    is_known: bool = Field(default=False)  # 是否为已知人
    person_id: str = Field(unique=True, index=True, max_length=255)  # 人员ID
    person_name: Optional[str] = Field(default=None, max_length=255, nullable=True)  # 人员名称
    name_reason: Optional[str] = Field(default=None, nullable=True)  # 名称原因

    # 身份元数据
    platform: str = Field(index=True, max_length=100)  # 平台名称
    user_id: str = Field(index=True, max_length=255)  # 用户ID
    user_nickname: str = Field(index=True, max_length=255)  # 用户昵称
    group_cardname: Optional[str] = Field(
        default=None, nullable=True
    )  # 群昵称 (JSON, [{"group_id": str, "group_cardname": str}])

    # 印象
    memory_points: Optional[str] = Field(default=None, nullable=True)  # 记忆要点，JSON格式存储

    # 认识次数和时间
    know_counts: int = Field(default=0)  # 认识次数
    first_known_time: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )  # 首次认识时间
    last_known_time: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))  # 最后认识时间


class ChatSession(SQLModel, table=True):
    """存储聊天会话的模型"""

    __tablename__ = "chat_sessions"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键

    session_id: str = Field(unique=True, index=True, max_length=255)  # 聊天会话ID

    created_timestamp: datetime = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 创建时间
    last_active_timestamp: Optional[datetime] = Field(
        default_factory=datetime.now, sa_column=Column(DateTime, index=True)
    )  # 最后活跃时间

    # 身份元数据
    user_id: Optional[str] = Field(index=True, max_length=255, nullable=True)  # 用户ID
    group_id: Optional[str] = Field(index=True, default=None, max_length=255, nullable=True)  # 群组id
    platform: str = Field(index=True, max_length=100)  # 会话所在平台

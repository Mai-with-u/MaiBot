from typing import Literal, Optional

import re

from .config_base import ConfigBase, Field

RULE_TYPE_OPTION_DESCRIPTIONS = {
    "group": "群聊聊天流，item_id 填群号或群聊 ID",
    "private": "私聊聊天流，item_id 填用户 ID",
}

VISUAL_MODE_OPTION_DESCRIPTIONS = {
    "auto": "根据模型信息自动选择文本或多模态模式",
    "text": "纯文本模式，不向模型发送视觉输入",
    "multimodal": "多模态模式，会向模型发送视觉输入",
}


EMOJI_SELECTION_MODE_OPTION_DESCRIPTIONS = {
    "visual": "视觉选择 (VLM) - 使用视觉模型看图选择表情包",
    "text": "文本选择 (描述匹配) - 使用纯文本模型根据描述选择表情包",
}

"""
须知：
1. 本文件中记录了所有的配置项
2. 所有新增的class都需要继承自ConfigBase
3. 所有新增的class都应在official_configs.py中的Config类中添加字段
4. 对于新增的字段，若为可选项，则应在其后添加Field()并设置default_factory或default
5. 所有的配置项都应该按照如下方法添加字段说明：
class ExampleConfig(ConfigBase):
    example_field: str
    \"""This is an example field\"""
    - 注释前面增加_warp_标记可以实现配置文件中注释在配置项前面单独一行显示
"""
class BotConfig(ConfigBase):
    """机器人配置类"""

    __ui_label__ = "基础"
    __ui_icon__ = "bot"

    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "bot-platform-account",
        },
    )
    """平台"""

    qq_account: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "user",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "bot-platform-account",
        },
    )
    """QQ账号"""

    platforms: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "layers",
        },
    )
    """其他平台"""

    nickname: str = Field(
        default="麦麦",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "user-circle",
        },
    )
    """机器人昵称"""

    alias_names: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "tags",
            "advanced": True,
        },
    )
    """别名列表"""


class PersonalityConfig(ConfigBase):
    """人格配置类"""

    __ui_parent__ = "bot"
    __ui_label__ = "人格"
    __ui_icon__ = "user-circle"

    personality: str = Field(
        default="你是一个大二女大学生，现在正在上网和群友聊天。",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "user-circle",
            "x-textarea-min-height": 40,
            "x-textarea-rows": 1,
        },
    )
    """人格，建议200字以内，描述人格特质和身份特征；可以写完整设定。要求第二人称"""

    reply_style: str = Field(
        default="你的风格平淡简短。可以参考贴吧，知乎和微博的回复风格。不浮夸不长篇大论，不要过分修辞和复杂句。尽量回复的简短一些，平淡一些",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "message-square",
            "x-textarea-min-height": 40,
            "x-textarea-rows": 1,
        },
    )
    """默认表达风格，描述麦麦说话的表达风格，表达习惯，如要修改，可以酌情新增内容，建议1-2行"""

    multiple_reply_style: list[str] = Field(
        default_factory=lambda: [
            "你的风格平淡但不失讽刺，很简短,很白话。可以参考贴吧，微博的回复风格。",
            "用1-2个字进行回复",
            "用1-2个符号进行回复",
            "言辭凝練古雅，穿插《論語》經句卻不晦澀，以文言短句為基，輔以淺白語意，持長者溫和風範，全用繁體字表達，具先秦儒者談吐韻致。",
            "带点翻译腔，但不要太长",
        ],
        json_schema_extra={
            "advanced": True,
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """可选的多种表达风格列表，当配置不为空时可按概率随机替换 reply_style"""

    multiple_probability: float = Field(
        default=0,
        ge=0,
        le=1,
        json_schema_extra={
            "advanced": True,
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """每次构建回复时，从 multiple_reply_style 中随机替换 reply_style 的概率（0.0-1.0）"""

class VisualConfig(ConfigBase):
    """视觉配置类"""

    __ui_label__ = "视觉"
    __ui_icon__ = "image"

    planner_mode: Literal["text", "multimodal", "auto"] = Field(
        default="auto",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "git-branch",
            "x-option-descriptions": VISUAL_MODE_OPTION_DESCRIPTIONS,
            "x-row": "visual-modes",
        },
    )
    """规划器模式，auto根据模型信息自动选择，text为纯文本模式，multimodal为多模态模式"""

    replyer_mode: Literal["text", "multimodal", "auto"] = Field(
        default="auto",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "git-branch",
            "x-option-descriptions": VISUAL_MODE_OPTION_DESCRIPTIONS,
            "x-row": "visual-modes",
        },
    )
    """回复器模式，auto根据模型信息自动选择，text为纯文本模式，multimodal为多模态模式"""


class TalkRulesItem(ConfigBase):
    platform: str = ""
    """平台，与ID一起留空表示全局"""

    item_id: str = ""
    """用户ID，与平台一起留空表示全局"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "x-widget": "select",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""

    time: str = ""
    """时间段，格式为 "HH:MM-HH:MM"，支持跨夜区间"""

    value: float = 0.5
    """聊天频率值，范围0-1"""


class ChatConfig(ConfigBase):
    """聊天配置类"""

    __ui_label__ = "聊天"
    __ui_icon__ = "message-square"

    talk_value: float = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "message-circle",
            "x-row": "talk-values",
            "step": 0.1,
        },
    )
    """聊天频率，越小越沉默，范围0-1"""

    private_talk_value: float = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "message-circle",
            "x-row": "talk-values",
            "step": 0.1,
        },
    )
    """私聊聊天频率，越小越沉默，范围0-1"""

    mentioned_bot_reply: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "at-sign",
            "x-row": "reply-switches",
        },
    )
    """是否启用提及必回复"""

    inevitable_at_reply: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "at-sign",
            "x-row": "reply-switches",
        },
    )
    """是否启用at必回复"""

    enable_at: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "at-sign",
            "advanced": True,
        },
    )
    """是否允许 replyer 使用 at[msg_id] 标记来发送真正的 at 消息"""

    enable_reply_quote: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "quote",
            "advanced": True,
        },
    )
    """是否启用回复时附带引用回复"""

    max_context_size: int = Field(
        default=40,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "context-sizes",
        },
    )
    """上下文长度"""
    
    max_private_context_size: int = Field(
        default=40,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "context-sizes",
        },
    )
    """私聊上下文长度"""

    planner_interrupt_max_consecutive_count: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "pause-circle",
            "advanced": True,
        },
    )
    """Planner 连续被新消息打断的最大次数，0 表示不启用打断"""

    group_chat_prompt: str = Field(
        default=(
            "你正在qq群里聊天，下面是群里正在聊的内容，其中包含聊天记录和聊天中的图片和表情包。\n"
            "回复尽量简短一些。最好一次对一个话题进行回复，但必须考虑不同群友发言之间的交互，免得啰嗦或者回复内容太乱。请注意把握聊天内容。\n"
            "不要总是提及自己的身份背景，根据聊天内容自由发挥，但是要日常不浮夸，不要太关注具体的聊天内容，不要刻意找话题，。\n"
            "不要回复的太频繁！不用刻意回复表情包，只要关注表情包表达的含义。控制回复的频率，不要每个人的消息都回复，只回复你感兴趣的或者主动提及你的。\n"
        ),
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "users",
        },
    )
    """_wrap_群聊通用注意事项"""

    private_chat_prompts: str = Field(
        default=(
            "你正在聊天，下面是正在聊的内容，其中包含聊天记录和聊天中的图片。\n"
            "回复尽量简短一些。请注意把握聊天内容。\n"
            "请考虑对方的发言频率，想法，思考自己何时回复以及回复内容。\n"
        ),
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "user",
        },
    )
    """_wrap_私聊通用注意事项"""

    chat_prompts: list["ExtraPromptItem"] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )

    enable_talk_value_rules: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "settings",
        },
    )
    """是否启用动态发言频率规则"""

    talk_value_rules: list[TalkRulesItem] = Field(
        default_factory=lambda: [
            TalkRulesItem(platform="", item_id="", rule_type="group", time="00:00-08:59", value=0.8),
            TalkRulesItem(platform="", item_id="", rule_type="group", time="09:00-18:59", value=1.0),
        ],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """
    _wrap_思考频率规则列表，支持按聊天流/按日内时段配置。
    """


class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    __ui_label__ = "消息接收"
    __ui_icon__ = "message-square-text"

    image_parse_threshold: int = Field(
        default=5,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "image",
        },
    )
    """
    当消息中图片数量不超过此阈值时，启用图片解析功能，将图片内容解析为文本后再进行处理。
    当消息中图片数量超过此阈值时，为了避免过度解析导致的性能问题，将跳过图片解析，直接进行处理。
    """

    ban_words: set[str] = Field(
        default_factory=lambda: set(),
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "ban",
        },
    )
    """过滤词列表"""

    ban_msgs_regex: set[str] = Field(
        default_factory=lambda: set(),
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "regex",
        },
    )
    """过滤正则表达式列表"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        for pattern in self.ban_msgs_regex:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern in ban_msgs_regex: '{pattern}'") from e
        return super().model_post_init(context)


class TargetItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """平台，与ID一起留空表示全局"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """用户/群ID，与平台一起留空表示全局"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""


class MemoryConfig(ConfigBase):
    """记忆配置类"""

    __ui_parent__ = "a_memorix"


    global_memory: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "globe",
        },
    )
    """是否允许记忆检索在聊天记录中进行全局查询（忽略当前chat_id，仅对 search_chat_history 等工具生效）"""

    global_memory_blacklist: list[TargetItem] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "shield-off",
        },
    )
    """_wrap_全局记忆黑名单，当启用全局记忆时，不将特定聊天流纳入检索"""


    enable_memory_query_tool: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """是否启用 Maisaka 内置长期记忆检索工具 query_memory"""

    memory_query_default_limit: int = Field(
        default=5,
        ge=1,
        le=20,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """Maisaka 内置长期记忆检索工具 query_memory 的默认返回条数"""

    person_fact_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "user-round-pen",
        },
    )
    """是否在发送回复后自动提取并写回人物事实到长期记忆"""

    chat_summary_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "scroll-text",
        },
    )
    """是否在 Maisaka 聊天过程中按消息窗口自动写回聊天摘要到长期记忆"""

    chat_summary_writeback_message_threshold: int = Field(
        default=12,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "messages-square",
            "advanced": True,
        },
    )
    """自动写回聊天摘要的消息窗口阈值"""

    chat_summary_writeback_context_length: int = Field(
        default=50,
        ge=1,
        le=500,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "rows-3",
            "advanced": True,
        },
    )
    """自动写回聊天摘要时，从聊天流中回看的消息条数"""

    feedback_correction_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "message-circle-warning",
            "advanced": True,
        },
    )
    """是否启用反馈驱动的延迟记忆纠错任务"""

    feedback_correction_window_hours: float = Field(
        default=12.0,
        ge=0.1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock-4",
            "advanced": True,
        },
    )
    """反馈窗口时长（小时），以 query_memory 执行时间为起点"""

    feedback_correction_check_interval_minutes: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "timer",
            "advanced": True,
        },
    )
    """反馈纠错定时任务轮询间隔（分钟）"""

    feedback_correction_batch_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "list-ordered",
            "advanced": True,
        },
    )
    """反馈纠错每轮最大处理任务数"""

    feedback_correction_auto_apply_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "gauge",
            "step": 0.01,
            "advanced": True,
        },
    )
    """自动应用纠错动作的最低置信度阈值"""

    feedback_correction_max_feedback_messages: int = Field(
        default=30,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "messages-square",
            "advanced": True,
        },
    )
    """每个纠错任务最多使用的窗口内用户反馈消息数"""

    feedback_correction_prefilter_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "filter",
            "advanced": True,
        },
    )
    """是否启用纠错前置预筛（用于减少不必要的模型调用）"""

    feedback_correction_paragraph_mark_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "sticky-note",
            "advanced": True,
        },
    )
    """是否为受影响 paragraph 写入已纠正旧事实标记"""

    feedback_correction_paragraph_hard_filter_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "eye-off",
            "advanced": True,
        },
    )
    """是否在用户侧查询中硬过滤带有 stale 标记的 paragraph"""

    feedback_correction_profile_refresh_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "user-round-search",
            "advanced": True,
        },
    )
    """是否在反馈纠错后将受影响人物画像加入刷新队列"""

    feedback_correction_profile_force_refresh_on_read: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "refresh-ccw",
            "advanced": True,
        },
    )
    """人物画像处于脏队列时，读取是否强制刷新而不直接复用旧快照"""

    feedback_correction_episode_rebuild_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "clapperboard",
            "advanced": True,
        },
    )
    """是否在反馈纠错后将受影响 source 加入 episode 重建队列"""

    feedback_correction_episode_query_block_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "ban",
            "advanced": True,
        },
    )
    """episode source 处于重建队列时，是否对用户侧查询做屏蔽"""

    feedback_correction_reconcile_interval_minutes: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "repeat",
            "advanced": True,
        },
    )
    """反馈纠错二阶段一致性后台协调任务轮询间隔（分钟）"""

    feedback_correction_reconcile_batch_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "list-restart",
            "advanced": True,
        },
    )
    """反馈纠错二阶段一致性每轮处理 profile/episode 队列的批大小"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置值"""
        if self.feedback_correction_window_hours <= 0:
            raise ValueError(
                f"feedback_correction_window_hours 必须大于0，当前值: {self.feedback_correction_window_hours}"
            )
        if self.feedback_correction_check_interval_minutes < 1:
            raise ValueError(
                "feedback_correction_check_interval_minutes 必须至少为1，"
                f"当前值: {self.feedback_correction_check_interval_minutes}"
            )
        if self.feedback_correction_batch_size < 1:
            raise ValueError(
                f"feedback_correction_batch_size 必须至少为1，当前值: {self.feedback_correction_batch_size}"
            )
        if not 0 <= self.feedback_correction_auto_apply_threshold <= 1:
            raise ValueError(
                "feedback_correction_auto_apply_threshold 必须在 [0, 1] 之间，"
                f"当前值: {self.feedback_correction_auto_apply_threshold}"
            )
        if self.feedback_correction_max_feedback_messages < 1:
            raise ValueError(
                "feedback_correction_max_feedback_messages 必须至少为1，"
                f"当前值: {self.feedback_correction_max_feedback_messages}"
            )
        if self.feedback_correction_reconcile_interval_minutes < 1:
            raise ValueError(
                "feedback_correction_reconcile_interval_minutes 必须至少为1，"
                f"当前值: {self.feedback_correction_reconcile_interval_minutes}"
            )
        if self.feedback_correction_reconcile_batch_size < 1:
            raise ValueError(
                "feedback_correction_reconcile_batch_size 必须至少为1，"
                f"当前值: {self.feedback_correction_reconcile_batch_size}"
            )
        return super().model_post_init(context)


class AMemorixPluginConfig(ConfigBase):
    """A_Memorix 子系统状态"""

    enabled: bool = Field(default=False)
    """是否启用 A_Memorix"""


class AMemorixStorageConfig(ConfigBase):
    """A_Memorix 存储位置"""

    data_dir: str = Field(default="data/a-memorix")
    """数据目录"""


class AMemorixEmbeddingFallbackConfig(ConfigBase):
    """A_Memorix Embedding 回退"""

    enabled: bool = Field(default=True)
    """是否启用回退机制"""

    probe_interval_seconds: int = Field(default=180, ge=10)
    """探测间隔秒数"""

    allow_metadata_only_write: bool = Field(default=True)
    """是否允许仅写入元数据"""


class AMemorixParagraphVectorBackfillConfig(ConfigBase):
    """A_Memorix 段落向量回填"""

    enabled: bool = Field(default=True)
    """是否启用回填任务"""

    interval_seconds: int = Field(default=60, ge=5)
    """回填轮询间隔"""

    batch_size: int = Field(default=64, ge=1)
    """单批回填数量"""

    max_retry: int = Field(default=5, ge=0)
    """最大重试次数"""


class AMemorixEmbeddingConfig(ConfigBase):
    """A_Memorix Embedding 配置"""

    model_name: str = Field(default="auto")
    """Embedding 模型选择"""

    dimension: int = Field(default=1024, ge=1)
    """向量维度"""

    batch_size: int = Field(default=32, ge=1)
    """单批请求大小"""

    max_concurrent: int = Field(default=5, ge=1)
    """最大并发数"""

    enable_cache: bool = Field(default=False)
    """是否启用缓存"""

    quantization_type: Literal["int8"] = Field(default="int8")
    """量化方式，当前 vNext 仅支持 int8(SQ8)"""

    fallback: AMemorixEmbeddingFallbackConfig = Field(default_factory=AMemorixEmbeddingFallbackConfig)
    """Embedding 回退配置"""

    paragraph_vector_backfill: AMemorixParagraphVectorBackfillConfig = Field(
        default_factory=AMemorixParagraphVectorBackfillConfig
    )
    """段落向量回填配置"""


class AMemorixSparseRetrievalConfig(ConfigBase):
    """A_Memorix 稀疏检索配置"""

    enabled: bool = Field(default=True)
    """是否启用稀疏检索"""

    backend: Literal["fts5"] = Field(default="fts5")
    """稀疏检索后端"""

    mode: Literal["auto", "fallback_only", "hybrid"] = Field(default="auto")
    """稀疏检索模式"""

    tokenizer_mode: Literal["jieba", "mixed", "char_2gram"] = Field(default="jieba")
    """分词模式"""

    candidate_k: int = Field(default=80, ge=1)
    """段落候选数"""

    relation_candidate_k: int = Field(default=60, ge=1)
    """关系候选数"""


class AMemorixRetrievalConfig(ConfigBase):
    """A_Memorix 检索配置"""

    top_k_paragraphs: int = Field(default=20, ge=1)
    """段落候选数"""

    top_k_relations: int = Field(default=10, ge=1)
    """关系候选数"""

    top_k_final: int = Field(default=10, ge=1)
    """最终返回条数"""

    alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    """关系融合权重"""

    enable_ppr: bool = Field(default=True)
    """是否启用 PPR"""

    ppr_alpha: float = Field(default=0.85, ge=0.0, le=1.0)
    """PPR alpha"""

    ppr_timeout_seconds: float = Field(default=1.5, ge=0.1)
    """PPR 超时秒数"""

    ppr_concurrency_limit: int = Field(default=4, ge=1)
    """PPR 并发限制"""

    enable_parallel: bool = Field(default=True)
    """是否启用并行检索"""

    sparse: AMemorixSparseRetrievalConfig = Field(default_factory=AMemorixSparseRetrievalConfig)
    """稀疏检索配置"""


class AMemorixThresholdConfig(ConfigBase):
    """A_Memorix 阈值过滤配置"""

    min_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    """最小阈值"""

    max_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    """最大阈值"""

    percentile: int = Field(default=75, ge=0, le=100)
    """动态阈值百分位"""

    min_results: int = Field(default=3, ge=1)
    """最小保留条数"""

    enable_auto_adjust: bool = Field(default=True)
    """是否启用自动阈值调整"""


class AMemorixFilterConfig(ConfigBase):
    """A_Memorix 聊天过滤配置"""

    enabled: bool = Field(default=True)
    """是否启用聊天过滤"""

    mode: Literal["blacklist", "whitelist"] = Field(default="blacklist")
    """过滤模式"""

    chats: list[str] = Field(default_factory=lambda: [])
    """聊天流列表"""


class AMemorixEpisodeConfig(ConfigBase):
    """A_Memorix Episode 配置"""

    enabled: bool = Field(default=True)
    """是否启用 Episode"""

    generation_enabled: bool = Field(default=True)
    """是否启用自动生成"""

    pending_batch_size: int = Field(default=20, ge=1)
    """待处理批大小"""

    pending_max_retry: int = Field(default=3, ge=0)
    """待处理最大重试次数"""

    max_paragraphs_per_call: int = Field(default=20, ge=1)
    """单次最大段落数"""

    max_chars_per_call: int = Field(default=6000, ge=100)
    """单次最大字符数"""

    source_time_window_hours: float = Field(default=24.0, ge=0.0)
    """时间窗口小时数"""

    segmentation_model: str = Field(default="auto")
    """分段模型选择"""


class AMemorixPersonProfileConfig(ConfigBase):
    """A_Memorix 人物画像配置"""

    enabled: bool = Field(default=True)
    """是否启用画像"""

    refresh_interval_minutes: int = Field(default=30, ge=1)
    """刷新间隔分钟数"""

    active_window_hours: float = Field(default=72.0, ge=1.0)
    """活跃窗口小时数"""

    max_refresh_per_cycle: int = Field(default=50, ge=1)
    """单轮最大刷新数"""

    top_k_evidence: int = Field(default=12, ge=1)
    """证据条数"""


class AMemorixMemoryEvolutionConfig(ConfigBase):
    """A_Memorix 记忆演化配置"""

    enabled: bool = Field(default=True)
    """是否启用记忆演化"""

    half_life_hours: float = Field(default=24.0, ge=0.1)
    """半衰期小时数"""

    prune_threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    """裁剪阈值"""

    freeze_duration_hours: float = Field(default=24.0, ge=0.0)
    """冻结时长小时数"""


class AMemorixAdvancedConfig(ConfigBase):
    """A_Memorix 高级运行时配置"""

    enable_auto_save: bool = Field(default=True)
    """是否启用自动保存"""

    auto_save_interval_minutes: int = Field(default=5, ge=1)
    """自动保存间隔"""

    debug: bool = Field(default=False)
    """是否启用调试"""


class AMemorixWebImportConfig(ConfigBase):
    """A_Memorix 导入中心配置"""

    enabled: bool = Field(default=True)
    """是否启用导入中心"""

    max_queue_size: int = Field(default=20, ge=1)
    """最大队列长度"""

    max_files_per_task: int = Field(default=200, ge=1)
    """单任务最大文件数"""

    max_file_size_mb: int = Field(default=20, ge=1)
    """单文件大小上限 MB"""

    max_paste_chars: int = Field(default=200000, ge=100)
    """粘贴字符数上限"""

    default_file_concurrency: int = Field(default=2, ge=1)
    """默认文件并发"""

    default_chunk_concurrency: int = Field(default=4, ge=1)
    """默认分块并发"""


class AMemorixWebTuningConfig(ConfigBase):
    """A_Memorix 调优中心配置"""

    enabled: bool = Field(default=True)
    """是否启用调优中心"""

    max_queue_size: int = Field(default=8, ge=1)
    """最大队列长度"""

    poll_interval_ms: int = Field(default=1200, ge=200)
    """轮询间隔毫秒数"""

    default_intensity: Literal["quick", "standard", "deep"] = Field(default="standard")
    """默认调优强度"""

    default_objective: Literal["precision_priority", "balanced", "recall_priority"] = Field(
        default="precision_priority"
    )
    """默认调优目标"""

    default_top_k_eval: int = Field(default=20, ge=1)
    """默认评估 Top-K"""

    default_sample_size: int = Field(default=24, ge=1)
    """默认样本数"""


class AMemorixWebConfig(ConfigBase):
    """A_Memorix Web 运维配置"""

    import_config: AMemorixWebImportConfig = Field(default_factory=AMemorixWebImportConfig)
    """导入中心配置"""

    tuning: AMemorixWebTuningConfig = Field(default_factory=AMemorixWebTuningConfig)
    """调优中心配置"""


class AMemorixConfig(ConfigBase):
    """A_Memorix 长期记忆子系统配置"""

    __ui_label__ = "长期记忆"
    __ui_icon__ = "brain"

    plugin: AMemorixPluginConfig = Field(default_factory=AMemorixPluginConfig)
    """子系统状态"""

    storage: AMemorixStorageConfig = Field(default_factory=AMemorixStorageConfig)
    """存储位置"""

    embedding: AMemorixEmbeddingConfig = Field(default_factory=AMemorixEmbeddingConfig)
    """Embedding 配置"""

    retrieval: AMemorixRetrievalConfig = Field(default_factory=AMemorixRetrievalConfig)
    """检索配置"""

    threshold: AMemorixThresholdConfig = Field(default_factory=AMemorixThresholdConfig)
    """阈值过滤配置"""

    filter: AMemorixFilterConfig = Field(default_factory=AMemorixFilterConfig)
    """聊天过滤配置"""

    episode: AMemorixEpisodeConfig = Field(default_factory=AMemorixEpisodeConfig)
    """Episode 配置"""

    person_profile: AMemorixPersonProfileConfig = Field(default_factory=AMemorixPersonProfileConfig)
    """人物画像配置"""

    memory: AMemorixMemoryEvolutionConfig = Field(default_factory=AMemorixMemoryEvolutionConfig)
    """记忆演化配置"""

    advanced: AMemorixAdvancedConfig = Field(default_factory=AMemorixAdvancedConfig)
    """高级运行时配置"""

    web: AMemorixWebConfig = Field(default_factory=AMemorixWebConfig)
    """Web 运维配置"""


class LearningItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """平台，与ID一起留空表示全局"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """用户ID，与平台一起留空表示全局"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""

    use_expression: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "message-square",
        },
    )
    """是否使用表达"""

    enable_learning: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "graduation-cap",
        },
    )
    """是否学习表达"""

    enable_jargon_learning: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "book",
        },
    )
    """是否学习黑话"""

class ExpressionGroup(ConfigBase):
    """表达互通组配置类，若列表为空代表全局共享"""

    expression_groups: list[TargetItem] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_表达学习互通组"""


class ExpressionConfig(ConfigBase):
    """表达配置类"""

    __ui_label__ = "表达"
    __ui_icon__ = "pen-tool"

    learning_list: list[LearningItem] = Field(
        default_factory=lambda: [
            LearningItem(
                platform="",
                item_id="",
                rule_type="group",
                use_expression=True,
                enable_learning=True,
                enable_jargon_learning=True,
            )
        ],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """_wrap_表达学习配置列表，支持按聊天流配置"""

    expression_groups: list[ExpressionGroup] = Field(
        default_factory=list,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_表达学习互通组"""

    expression_checked_only: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "check",
        },
    )
    """是否仅选择已检查且未拒绝的表达方式"""

    expression_self_reflect: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "refresh-cw",
        },
    )
    """是否启用自动表达优化"""

    expression_auto_check_interval: int = Field(
        default=900,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
            "advanced": True,
        },
    )
    """表达方式自动检查的间隔时间（秒）"""

    expression_auto_check_count: int = Field(
        default=5,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
            "advanced": True,
        },
    )
    """每次自动检查时随机选取的表达方式数量"""

    expression_auto_check_custom_criteria: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "file-text",
            "advanced": True,
        },
    )
    """表达方式自动检查的额外自定义评估标准"""

    all_global_jargon: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "globe",
        },
    )
    """是否开启全局黑话模式，注意，此功能关闭后，已经记录的全局黑话不会改变，需要手动删除"""

class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    __ui_label__ = "语音"
    __ui_icon__ = "mic"

    enable_asr: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "mic",
        },
    )
    """是否启用语音识别，启用后麦麦可以识别语音消息"""


class EmojiConfig(ConfigBase):
    """表情包配置类"""

    __ui_label__ = "表情包"
    __ui_icon__ = "smile"

    emoji_send_num: int = Field(
        default=25,
        ge=1,
        le=64,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "grid",
            "advanced": True,
        },
    )
    """一次从多少个表情包中选择发送，最大为 64"""

    max_reg_num: int = Field(
        default=64,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """表情包最大注册数量"""

    do_replace: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "refresh-cw",
            "advanced": True,
        },
    )
    """达到最大注册数量时替换旧表情包，关闭则达到最大数量时不会继续收集表情包"""

    check_interval: int = Field(
        default=10,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """表情包检查间隔（分钟）"""

    steal_emoji: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "copy",
        },
    )
    """是否偷取表情包，让麦麦可以将一些表情包据为己有"""

    content_filtration: bool = Field(
        default=False,
        json_schema_extra={
            "advanced": True,
            "x-widget": "switch",
            "x-icon": "filter",
        },
    )
    """是否启用表情包过滤，只有符合该要求的表情包才会被保存"""

    emoji_selection_mode: Literal["visual", "text"] = Field(
        default="visual",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "eye",
            "x-option-descriptions": EMOJI_SELECTION_MODE_OPTION_DESCRIPTIONS,
        },
    )
    """表情包选择模式：visual 使用视觉模型看图选择，text 使用纯文本模型根据描述选择"""


class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    keywords: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "tag",
        },
    )
    """关键词列表"""

    regex: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "regex",
        },
    )
    """正则表达式列表"""

    reaction: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "message-circle",
        },
    )
    """关键词触发的反应"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置"""
        if not self.keywords and not self.regex:
            raise ValueError("关键词规则必须至少包含keywords或regex中的一个")

        if not self.reaction:
            raise ValueError("关键词规则必须包含reaction")

        for pattern in self.regex:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"无效的正则表达式 '{pattern}': {str(e)}") from e
        return super().model_post_init(context)


class KeywordReactionConfig(ConfigBase):
    """关键词配置类"""

    __ui_parent__ = "message_receive"

    keyword_rules: list[KeywordRuleConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """关键词规则列表"""

    regex_rules: list[KeywordRuleConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """正则表达式规则列表"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置"""
        for rule in self.keyword_rules + self.regex_rules:
            if not isinstance(rule, KeywordRuleConfig):
                raise ValueError(f"规则必须是KeywordRuleConfig类型，而不是{type(rule).__name__}")
        return super().model_post_init(context)


class ResponsePostProcessConfig(ConfigBase):
    """回复后处理配置类"""

    __ui_label__ = "后处理"
    __ui_icon__ = "settings"

    enable_response_post_process: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "settings",
        },
    )
    """是否启用回复后处理，包括错别字生成器，回复分割器"""


class ChineseTypoConfig(ConfigBase):
    """中文错别字配置类"""

    __ui_parent__ = "response_post_process"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "type",
        },
    )
    """是否启用中文错别字生成器"""

    error_rate: float = Field(
        default=0.01,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.01,
            "advanced": True,
        },
    )
    """单字替换概率"""

    min_freq: int = Field(
        default=9,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
            "advanced": True,
        },
    )
    """最小字频阈值"""

    tone_error_rate: float = Field(
        default=0.1,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
            "advanced": True,
        },
    )
    """声调错误概率"""

    word_replace_rate: float = Field(
        default=0.006,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.001,
            "advanced": True,
        },
    )
    """整词替换概率"""


class ResponseSplitterConfig(ConfigBase):
    """回复分割器配置类"""

    __ui_parent__ = "response_post_process"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "scissors",
        },
    )
    """是否启用回复分割器"""

    max_length: int = Field(
        default=512,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "ruler",
        },
    )
    """回复允许的最大长度"""

    max_sentence_num: int = Field(
        default=8,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """回复允许的最大句子数"""

    enable_kaomoji_protection: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "smile",
            "advanced": True,
        },
    )
    """是否启用颜文字保护"""

    enable_overflow_return_all: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "maximize",
            "advanced": True,
        },
    )
    """是否在句子数量超出回复允许的最大句子数时一次性返回全部内容"""


class LogConfig(ConfigBase):
    """日志配置类"""

    __ui_label__ = "调试与日志"
    __ui_icon__ = "file-text"

    date_style: str = Field(
        default="m-d H:i:s",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """日期格式"""

    log_level_style: Literal["lite", "compact", "full"] = Field(
        default="lite",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "list",
        },
    )
    """日志等级显示样式"""

    color_text: Literal["none", "title", "full"] = Field(
        default="full",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "palette",
        },
    )
    """控制台日志颜色模式"""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "list-filter",
        },
    )
    """全局日志级别"""

    console_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "terminal",
        },
    )
    """控制台日志级别"""

    file_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="DEBUG",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "file-json",
        },
    )
    """文件日志级别"""

    log_file_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hard-drive",
        },
    )
    """单个日志文件最大字节数"""

    max_log_files: int = Field(
        default=30,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "files",
        },
    )
    """最多保留的主日志文件数量"""

    log_cleanup_days: int = Field(
        default=30,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "calendar-days",
        },
    )
    """主日志文件保留天数"""

    llm_request_snapshot_limit: int = Field(
        default=128,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "archive",
        },
    )
    """失败请求快照最多保留数量"""

    maisaka_prompt_preview_limit: int = Field(
        default=256,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "panel-top",
        },
    )
    """每个会话最多保留的 Maisaka Prompt 预览组数"""

    maisaka_reply_effect_limit: int = Field(
        default=256,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clipboard-check",
        },
    )
    """每个会话最多保留的 Maisaka 回复效果记录数"""

    suppress_libraries: list[str] = Field(
        default_factory=lambda: [
            "faiss",
            "httpx",
            "urllib3",
            "asyncio",
            "websockets",
            "httpcore",
            "requests",
            "sqlalchemy",
            "openai",
            "uvicorn",
            "jieba",
        ],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "volume-x",
            "advanced": True,
        },
    )
    """完全屏蔽日志的第三方库列表"""

    library_log_levels: dict[str, str] = Field(
        default_factory=lambda: {"aiohttp": "WARNING"},
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "sliders-horizontal",
            "advanced": True,
        },
    )
    """特定第三方库的日志级别"""


class TelemetryConfig(ConfigBase):
    """遥测配置类"""

    __ui_parent__ = "debug"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "activity",
        },
    )
    """是否启用遥测"""


class DebugConfig(ConfigBase):
    """调试配置类"""

    __ui_parent__ = "log"
    __ui_label__ = "其他"
    __ui_icon__ = "more-horizontal"

    enable_maisaka_stage_board: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "layout-dashboard",
        },
    )
    """是否启用 Maisaka 阶段看板"""

    show_maisaka_thinking: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """是否显示回复器推理"""

    fold_maisaka_thinking: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "minimize-2",
        },
    )
    """是否折叠 Maisaka 的 prompt 展示入口"""

    show_jargon_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "book",
        },
    )
    """是否显示jargon相关提示词"""

    show_memory_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """是否显示记忆检索相关prompt"""

    enable_reply_effect_tracking: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "activity",
        },
    )
    """是否开启回复效果评分追踪，默认关闭，需要手动打开"""

    record_reply_request: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-json",
        },
    )
    """是否记录 Replyer 请求体，默认关闭"""

    enable_llm_cache_stats: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "chart-no-axes-column",
        },
    )
    """是否记录 LLM prompt cache 调试统计，默认关闭"""


class ExtraPromptItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """平台，留空无效"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """用户ID，留空无效"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""

    prompt: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "file-text",
        },
    )
    """额外的prompt内容"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        if not self.platform and not self.item_id and not self.prompt:
            return super().model_post_init(context)
        if not self.platform or not self.item_id or not self.prompt:
            raise ValueError("ExtraPromptItem 中 platform, id 和 prompt 不能为空")
        return super().model_post_init(context)


class MaimMessageConfig(ConfigBase):
    """maim_message配置类"""

    __ui_parent__ = "debug"

    ws_server_host: str = Field(
        default="127.0.0.1",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "server",
        },
    )
    """旧版基于WS的服务器主机地址"""

    ws_server_port: int = Field(
        default=8000,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """旧版基于WS的服务器端口号"""

    auth_token: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "key",
        },
    )
    """认证令牌，用于旧版API验证，为空则不启用验证"""

    enable_api_server: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "server",
        },
    )
    """是否启用额外的新版API Server"""

    api_server_host: str = Field(
        default="0.0.0.0",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "globe",
        },
    )
    """新版API Server主机地址"""

    api_server_port: int = Field(
        default=8090,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """新版API Server端口号"""

    api_server_use_wss: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "lock",
        },
    )
    """新版API Server是否启用WSS"""

    api_server_cert_file: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "file",
        },
    )
    """新版API Server SSL证书文件路径"""

    api_server_key_file: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "key",
        },
    )
    """新版API Server SSL密钥文件路径"""

    api_server_allowed_api_keys: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "shield",
        },
    )
    """新版API Server允许的API Key列表，为空则允许所有连接"""


class LPMMKnowledgeConfig(ConfigBase):
    """LPMM知识库配置类"""

    __ui_label__ = "知识库"
    __ui_icon__ = "book-open"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """是否启用LPMM知识库"""

    lpmm_mode: Literal["classic", "agent"] = Field(
        default="classic",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "brain",
        },
    )
    """LPMM知识库模式，可选：classic经典模式，agent 模式"""

    rag_synonym_search_top_k: int = Field(
        default=10,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """同义检索TopK"""

    rag_synonym_threshold: float = Field(
        default=0.8,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """同义阈值，相似度高于该值的关系会被当作同义词"""

    info_extraction_workers: int = Field(
        default=3,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "cpu",
        },
    )
    """实体抽取同时执行线程数，非Pro模型不要设置超过5"""

    qa_relation_search_top_k: int = Field(
        default=10,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """关系检索TopK"""

    qa_relation_threshold: float = Field(
        default=0.75,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.05,
        },
    )
    """关系阈值，相似度高于该值的关系会被认为是相关关系"""

    qa_paragraph_search_top_k: int = Field(
        default=1000,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """段落检索TopK（不能过小，可能影响搜索结果）"""

    qa_paragraph_node_weight: float = Field(
        default=0.05,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "weight",
            "step": 0.01,
        },
    )
    """段落节点权重（在图搜索&PPR计算中的权重，当搜索仅使用DPR时，此参数不起作用）"""

    qa_ent_filter_top_k: int = Field(
        default=10,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """实体过滤TopK"""

    qa_ppr_damping: float = Field(
        default=0.8,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """PPR阻尼系数"""

    qa_res_top_k: int = Field(
        default=10,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """最终提供段落TopK"""

    embedding_dimension: int = Field(
        default=1024,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """嵌入向量维度,输出维度"""

    max_embedding_workers: int = Field(
        default=3,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "cpu",
        },
    )
    """嵌入/抽取并发线程数"""

    embedding_chunk_size: int = Field(
        default=4,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """每批嵌入的条数"""

    max_synonym_entities: int = Field(
        default=2000,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """同义边参与的实体数上限，超限则跳过"""

    enable_ppr: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "zap",
        },
    )
    """是否启用PPR，低配机器可关闭"""


class WebUIConfig(ConfigBase):
    """WebUI配置类"""

    __ui_label__ = "WebUI"
    __ui_icon__ = "layout"

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "monitor",
        },
    )
    """是否启用WebUI"""

    host: str = Field(
        default="127.0.0.1",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "globe",
        },
    )
    """WebUI 绑定主机地址"""

    port: int = Field(
        default=8001,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """WebUI 绑定端口"""

    mode: Literal["development", "production"] = Field(
        default="production",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "settings",
        },
    )
    """运行模式：development(开发) 或 production(生产)"""

    anti_crawler_mode: Literal["false", "strict", "loose", "basic"] = Field(
        default="basic",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shield",
        },
    )
    """防爬虫模式：false(禁用) / strict(严格) / loose(宽松) / basic(基础-只记录不阻止)"""

    allowed_ips: str = Field(
        default="127.0.0.1",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "network",
        },
    )
    """IP白名单（逗号分隔，支持精确IP、CIDR格式和通配符）"""

    trusted_proxies: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "server",
        },
    )
    """信任的代理IP列表（逗号分隔），只有来自这些IP的X-Forwarded-For才被信任"""

    trust_xff: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "shield-check",
        },
    )
    """是否启用X-Forwarded-For代理解析（默认false）"""

    secure_cookie: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "cookie",
        },
    )
    """是否启用安全Cookie（仅通过HTTPS传输，默认false）"""

    enable_paragraph_content: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-text",
        },
    )
    """是否在知识图谱中加载段落完整内容（需要加载embedding store，会占用额外内存）"""


class DatabaseConfig(ConfigBase):
    """数据库配置类"""

    __ui_parent__ = "debug"

    save_binary_data: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "save",
            "advanced": True,
        },
    )
    """
    是否将消息中的二进制数据保存为独立文件
    若启用，消息中的语音等二进制数据将会保存为独立文件，并在消息中以特殊标记替代。启用会导致数据文件夹体积增大，但可以实现二次识别等功能。
    若禁用，则消息中的二进制将会在识别后删除，并在消息中使用识别结果替代，无法二次识别
    该配置项仅影响新存储的消息，已有消息不会受到影响
    """


class MCPAuthorizationConfig(ConfigBase):
    """MCP HTTP 认证配置。"""

    mode: Literal["none", "bearer"] = Field(
        default="none",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shield",
        },
    )
    """认证模式，当前支持无认证和静态 Bearer Token"""

    bearer_token: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "password",
            "x-icon": "key",
        },
    )
    """静态 Bearer Token，仅在 `mode=\"bearer\"` 时使用"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证 MCP 认证配置。

        Args:
            context: Pydantic 传入的上下文对象。

        Returns:
            None
        """

        if self.mode == "bearer" and not self.bearer_token.strip():
            raise ValueError("MCP 使用 bearer 认证时必须填写 bearer_token")
        return super().model_post_init(context)


class MCPRootItemConfig(ConfigBase):
    """单个 MCP Root 配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "power",
        },
    )
    """是否启用当前 Root"""

    uri: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "folder",
        },
    )
    """Root URI，通常为 `file://` 路径 URI"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """Root 的显示名称"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证单个 Root 配置。

        Args:
            context: Pydantic 传入的上下文对象。

        Returns:
            None
        """

        if self.enabled and not self.uri.strip():
            raise ValueError("启用的 MCP Root 必须填写 uri")
        return super().model_post_init(context)


class MCPRootsConfig(ConfigBase):
    """MCP Roots 能力配置。"""

    enable: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "folder-tree",
        },
    )
    """是否向 MCP 服务器暴露 Roots 能力"""

    items: list[MCPRootItemConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "folder",
        },
    )
    """Roots 列表"""


class MCPSamplingConfig(ConfigBase):
    """MCP Sampling 能力配置。"""

    enable: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """是否启用 Sampling 能力声明"""

    task_name: str = Field(
        default="planner",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "sparkles",
        },
    )
    """执行 Sampling 请求时使用的主程序模型任务名"""

    include_context_support: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "layers",
        },
    )
    """是否声明支持 `includeContext` 非 `none` 语义"""

    tool_support: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "wrench",
        },
    )
    """是否声明支持在 Sampling 中继续使用工具"""


class MCPElicitationConfig(ConfigBase):
    """MCP Elicitation 能力配置。"""

    enable: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "message-circle-question",
        },
    )
    """是否启用 Elicitation 能力声明"""

    allow_form: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "form-input",
        },
    )
    """是否允许表单模式 Elicitation"""

    allow_url: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "link",
        },
    )
    """是否允许 URL 模式 Elicitation"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证 Elicitation 配置。

        Args:
            context: Pydantic 传入的上下文对象。

        Returns:
            None
        """

        if self.enable and not (self.allow_form or self.allow_url):
            raise ValueError("启用 MCP Elicitation 时至少需要允许一种模式")
        return super().model_post_init(context)


class MCPClientConfig(ConfigBase):
    """MCP 客户端宿主能力配置。"""

    client_name: str = Field(
        default="MaiBot",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "bot",
        },
    )
    """MCP 客户端实现名称"""

    client_version: str = Field(
        default="1.0.0",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "info",
        },
    )
    """MCP 客户端实现版本"""

    roots: MCPRootsConfig = Field(default_factory=MCPRootsConfig)
    """Roots 能力配置"""

    sampling: MCPSamplingConfig = Field(default_factory=MCPSamplingConfig)
    """Sampling 能力配置"""

    elicitation: MCPElicitationConfig = Field(default_factory=MCPElicitationConfig)
    """Elicitation 能力配置"""


class MCPServerItemConfig(ConfigBase):
    """单个 MCP 服务器配置。"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """服务器名称，必须唯一"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "power",
        },
    )
    """是否启用当前 MCP 服务器"""

    transport: Literal["stdio", "streamable_http"] = Field(
        default="stdio",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shuffle",
        },
    )
    """传输方式，可选 `stdio` 或 `streamable_http`"""

    command: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "terminal",
        },
    )
    """stdio 模式下启动服务器的命令"""

    args: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """stdio 模式下的命令参数列表"""

    env: dict[str, str] = Field(
        default_factory=lambda: {},
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "variable",
        },
    )
    """stdio 模式下附加的环境变量"""

    url: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """`streamable_http` 模式下的 MCP 端点地址"""

    headers: dict[str, str] = Field(
        default_factory=lambda: {},
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "file-json",
        },
    )
    """HTTP 模式下附加的请求头"""

    http_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "clock-3",
        },
    )
    """HTTP 请求超时时间，单位秒"""

    read_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """会话读取超时时间，单位秒"""

    authorization: MCPAuthorizationConfig = Field(default_factory=MCPAuthorizationConfig)
    """HTTP 认证配置"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证 MCP 服务器配置。

        Args:
            context: Pydantic 传入的上下文对象。

        Returns:
            None
        """

        if not self.name.strip():
            raise ValueError("MCPServerItemConfig.name 不能为空")

        if self.transport == "stdio" and not self.command.strip():
            raise ValueError(f"MCP 服务器 {self.name} 使用 stdio 时必须填写 command")

        if self.transport == "streamable_http" and not self.url.strip():
            raise ValueError(f"MCP 服务器 {self.name} 使用 streamable_http 时必须填写 url")

        return super().model_post_init(context)


class MCPConfig(ConfigBase):
    """MCP 总配置。"""

    __ui_parent__ = "maisaka"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "zap",
        },
    )
    """是否启用 MCP（Model Context Protocol）"""

    client: MCPClientConfig = Field(default_factory=MCPClientConfig)
    """MCP 客户端宿主能力配置"""

    servers: list[MCPServerItemConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "server",
        },
    )
    """_wrap_MCP 服务器配置列表"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证 MCP 总配置。

        Args:
            context: Pydantic 传入的上下文对象。

        Returns:
            None
        """

        server_names = [server.name.strip() for server in self.servers if server.name.strip()]
        if len(server_names) != len(set(server_names)):
            raise ValueError("MCP 配置中的服务器名称不能重复")
        return super().model_post_init(context)


class PluginRuntimeRenderConfig(ConfigBase):
    """插件运行时浏览器渲染配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "image",
        },
    )
    """是否启用插件运行时浏览器渲染能力"""

    browser_ws_endpoint: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """优先复用的现有 Chromium CDP 地址，可填写 ws/http 端点"""

    executable_path: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "folder",
        },
    )
    """浏览器可执行文件路径，留空时自动探测本机 Chrome/Chromium"""

    browser_install_root: str = Field(
        default="data/playwright-browsers",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hard-drive",
        },
    )
    """Playwright 托管浏览器目录，自动下载 Chromium 时会复用该目录"""

    headless: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "monitor",
        },
    )
    """是否以无头模式启动浏览器"""

    launch_args: list[str] = Field(
        default_factory=lambda: [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            "--no-sandbox",
            "--no-zygote",
        ],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "terminal",
        },
    )
    """浏览器启动参数列表"""

    concurrency_limit: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "layers",
        },
    )
    """同时允许进行的最大渲染任务数"""

    startup_timeout_sec: float = Field(
        default=20.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "clock",
        },
    )
    """浏览器连接或启动超时时间（秒）"""

    render_timeout_sec: float = Field(
        default=15.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """单次渲染默认超时时间（秒）"""

    auto_download_chromium: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "download",
        },
    )
    """未检测到可用浏览器时，是否自动下载 Playwright Chromium"""

    download_connection_timeout_sec: float = Field(
        default=120.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "cloud-lightning",
        },
    )
    """自动下载 Chromium 时的连接超时时间（秒）"""

    restart_after_render_count: int = Field(
        default=200,
        ge=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "refresh-cw",
        },
    )
    """累计渲染指定次数后自动重建本地浏览器，0 表示关闭该策略"""


class PluginRuntimeConfig(ConfigBase):
    """插件运行时配置类"""

    __ui_label__ = "插件运行时"
    __ui_icon__ = "puzzle"

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "power",
        },
    )
    """启用插件系统"""

    health_check_interval_sec: float = Field(
        default=30.0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "activity",
        },
    )
    """健康检查间隔（秒）"""

    max_restart_attempts: int = Field(
        default=3,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "refresh-cw",
        },
    )
    """Runner 崩溃后最大自动重启次数"""

    runner_spawn_timeout_sec: float = Field(
        default=30.0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "clock",
        },
    )
    """等待 Runner 子进程启动并注册的超时时间（秒）"""

    hook_blocking_timeout_sec: float = Field(
        default=30,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """Hook 阻塞步骤的全局超时上限（秒）"""

    ipc_socket_path: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """
    自定义 IPC Socket 路径（仅 Linux/macOS 生效）
    留空则自动生成临时路径
    """

    render: PluginRuntimeRenderConfig = Field(default_factory=PluginRuntimeRenderConfig)
    """浏览器渲染能力配置"""

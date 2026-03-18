from .config_base import ConfigBase, Field
import re
from typing import Optional, Literal

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

    __ui_label__ = "基本信息"
    __ui_icon__ = "bot"

    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """平台"""

    qq_account: int = Field(
        default=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "user",
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
        },
    )
    """别名列表"""


class PersonalityConfig(ConfigBase):
    """人格配置类"""

    __ui_label__ = "人格"
    __ui_icon__ = "user-circle"

    personality: str = Field(
        default="是一个大二在读女大学生，现在正在上网和群友聊天，有时有点攻击性，有时比较温柔",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "user-circle",
        },
    )
    """人格，建议100字以内，描述人格特质和身份特征"""

    reply_style: str = Field(
        default="请不要刻意突出自身学科背景。可以参考贴吧，知乎和微博的回复风格。",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "message-square",
        },
    )
    """默认表达风格，描述麦麦说话的表达风格，表达习惯，如要修改，可以酌情新增内容，建议1-2行"""

    multiple_reply_style: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """可选的多种表达风格列表，当配置不为空时可按概率随机替换 reply_style"""

    multiple_probability: float = Field(
        default=0.3,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """每次构建回复时，从 multiple_reply_style 中随机替换 reply_style 的概率（0.0-1.0）"""

    plan_style: str = Field(
        default=(
            "1.思考**所有**的可用的action中的**每个动作**是否符合当下条件，如果动作使用条件符合聊天内容就使用"
            "2.如果相同的action已经被执行，请不要重复执行该action"
            "3.如果有人对你感到厌烦，请减少回复"
            "4.如果有人在追问你，或者话题没有说完，请你继续回复"
            "5.请分析哪些对话是和你说的，哪些是其他人之间的互动，不要误认为其他人之间的互动是和你说的"
        ),
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "book-open",
        },
    )
    """_wrap_麦麦的说话规则和行为规则"""

    visual_style: str = Field(
        default="请用中文描述这张图片的内容。如果有文字，请把文字描述概括出来，请留意其主题，直观感受，输出为一段平文本，最多30字，请注意不要分点，就输出一段文本",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "image",
        },
    )
    """_wrap_识图提示词，不建议修改"""

    states: list[str] = Field(
        default_factory=lambda: [
            "是一个女大学生，喜欢上网聊天，会刷小红书。",
            "是一个大二心理学生，会刷贴吧和中国知网。",
            "是一个赛博网友，最近很想吐槽人。",
        ],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "shuffle",
        },
    )
    """_wrap_状态列表，用于随机替换personality"""

    state_probability: float = Field(
        default=0.3,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """状态概率，每次构建人格时替换personality的概率"""


class RelationshipConfig(ConfigBase):
    """关系配置类"""

    __ui_parent__ = "debug"

    enable_relationship: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "heart",
        },
    )
    """是否启用关系系统，关系系统被移除，此部分配置暂时无效"""


class TalkRulesItem(ConfigBase):
    platform: str = ""
    """平台，与ID一起留空表示全局"""

    item_id: str = ""
    """用户ID，与平台一起留空表示全局"""

    rule_type: Literal["group", "private"] = "group"
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
            "step": 0.1,
        },
    )
    """聊天频率，越小越沉默，范围0-1"""

    mentioned_bot_reply: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "at-sign",
        },
    )
    """是否启用提及必回复"""

    inevitable_at_reply: bool = Field(default=True)
    """是否启用at必回复"""

    max_context_size: int = Field(
        default=30,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
        },
    )
    """上下文长度"""

    planner_smooth: float = Field(
        default=3,
        ge=0,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "gauge",
            "step": 0.5,
        },
    )
    """规划器平滑，增大数值会减小planner负荷，略微降低反应速度，推荐1-5，0为关闭，必须大于等于0"""

    think_mode: Literal["classic", "deep", "dynamic"] = Field(
        default="dynamic",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "brain",
        },
    )
    """
    思考模式配置
    - classic: 默认think_level为0（轻量回复，不需要思考和回忆）
    - deep: 默认think_level为1（深度回复，需要进行回忆和思考）
    - dynamic: think_level由planner动态给出（根据planner返回的think_level决定）
    """

    plan_reply_log_max_per_chat: int = Field(
        default=1024,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "file-text",
        },
    )
    """每个聊天流最大保存的Plan/Reply日志数量，超过此数量时会自动删除最老的日志"""

    llm_quote: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "quote",
        },
    )
    """是否在 reply action 中启用 quote 参数，启用后 LLM 可以控制是否引用消息"""

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

    __ui_parent__ = "response_post_process"

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
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""


class MemoryConfig(ConfigBase):
    """记忆配置类"""

    __ui_parent__ = "emoji"

    max_agent_iterations: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "layers",
        },
    )
    """记忆思考深度（最低为1）"""

    agent_timeout_seconds: float = Field(
        default=120.0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """最长回忆时间（秒）"""

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

    chat_history_topic_check_message_threshold: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """聊天历史话题检查的消息数量阈值，当累积消息数达到此值时触发话题检查"""

    chat_history_topic_check_time_hours: float = Field(
        default=8.0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """聊天历史话题检查的时间阈值（小时），当距离上次检查超过此时间且消息数达到最小阈值时触发话题检查"""

    chat_history_topic_check_min_messages: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """聊天历史话题检查的时间触发模式下的最小消息数阈值"""

    chat_history_finalize_no_update_checks: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "check-circle",
        },
    )
    """聊天历史话题打包存储的连续无更新检查次数阈值，当话题连续N次检查无新增内容时触发打包存储"""

    chat_history_finalize_message_count: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "package",
        },
    )
    """聊天历史话题打包存储的消息条数阈值，当话题的消息条数超过此值时触发打包存储"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置值"""
        if self.max_agent_iterations < 1:
            raise ValueError(f"max_agent_iterations 必须至少为1，当前值: {self.max_agent_iterations}")
        if self.agent_timeout_seconds <= 0:
            raise ValueError(f"agent_timeout_seconds 必须大于0，当前值: {self.agent_timeout_seconds}")
        if self.chat_history_topic_check_message_threshold < 1:
            raise ValueError(
                f"chat_history_topic_check_message_threshold 必须至少为1，当前值: {self.chat_history_topic_check_message_threshold}"
            )
        if self.chat_history_topic_check_time_hours <= 0:
            raise ValueError(
                f"chat_history_topic_check_time_hours 必须大于0，当前值: {self.chat_history_topic_check_time_hours}"
            )
        if self.chat_history_topic_check_min_messages < 1:
            raise ValueError(
                f"chat_history_topic_check_min_messages 必须至少为1，当前值: {self.chat_history_topic_check_min_messages}"
            )
        if self.chat_history_finalize_no_update_checks < 1:
            raise ValueError(
                f"chat_history_finalize_no_update_checks 必须至少为1，当前值: {self.chat_history_finalize_no_update_checks}"
            )
        if self.chat_history_finalize_message_count < 1:
            raise ValueError(
                f"chat_history_finalize_message_count 必须至少为1，当前值: {self.chat_history_finalize_message_count}"
            )
        return super().model_post_init(context)


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
    """是否启用表达学习"""

    enable_learning: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "graduation-cap",
        },
    )
    """是否启用表达优化学习"""

    enable_jargon_learning: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "book",
        },
    )
    """是否启用jargon学习"""


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
        default=600,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """表达方式自动检查的间隔时间（秒）"""

    expression_auto_check_count: int = Field(
        default=20,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """每次自动检查时随机选取的表达方式数量"""

    expression_auto_check_custom_criteria: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "file-text",
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

    enable_jargon_explanation: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "info",
        },
    )
    """是否在回复前尝试对上下文中的黑话进行解释（关闭可减少一次LLM调用，仅影响回复前的黑话匹配与解释，不影响黑话学习）"""

    jargon_mode: Literal["context", "planner"] = Field(
        default="planner",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "settings",
        },
    )
    """
    黑话解释来源模式
    
    可选：
    - "context"：使用上下文自动匹配黑话
    - "planner"：仅使用Planner在reply动作中给出的unknown_words列表
    """


class ToolConfig(ConfigBase):
    """工具配置类"""

    __ui_parent__ = "emoji"

    enable_tool: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "wrench",
        },
    )
    """是否在聊天中启用工具"""


class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    __ui_parent__ = "emoji"

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

    __ui_label__ = "功能"
    __ui_icon__ = "puzzle"

    emoji_chance: float = Field(
        default=0.4,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "slider",
            "x-icon": "smile",
            "step": 0.1,
        },
    )
    """发送表情包的基础概率"""

    max_reg_num: int = Field(
        default=100,
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
            "x-widget": "switch",
            "x-icon": "filter",
        },
    )
    """是否启用表情包过滤，只有符合该要求的表情包才会被保存"""

    filtration_prompt: str = Field(
        default="符合公序良俗",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "shield",
        },
    )
    """表情包过滤要求，只有符合该要求的表情包才会被保存"""


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

    __ui_parent__ = "response_post_process"

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

    __ui_label__ = "处理"
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
        },
    )
    """单字替换概率"""

    min_freq: int = Field(
        default=9,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
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
        },
    )
    """是否启用颜文字保护"""

    enable_overflow_return_all: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "maximize",
        },
    )
    """是否在句子数量超出回复允许的最大句子数时一次性返回全部内容"""


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

    __ui_label__ = "其他"
    __ui_icon__ = "more-horizontal"

    show_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "eye",
        },
    )
    """是否显示prompt"""

    show_replyer_prompt: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "message-square",
        },
    )
    """是否显示回复器prompt"""

    show_replyer_reasoning: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """是否显示回复器推理"""

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

    show_planner_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "map",
        },
    )
    """是否显示planner的prompt和原始返回结果"""

    show_lpmm_paragraph: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-text",
        },
    )
    """是否显示lpmm找到的相关文段日志"""


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
        if not self.platform or not self.item_id or not self.prompt:
            raise ValueError("ExtraPromptItem 中 platform, id 和 prompt 不能为空")
        return super().model_post_init(context)


class ExperimentalConfig(ConfigBase):
    """实验功能配置类"""

    __ui_parent__ = "debug"

    private_plan_style: str = Field(
        default=(
            "1.思考**所有**的可用的action中的**每个动作**是否符合当下条件，如果动作使用条件符合聊天内容就使用"
            "2.如果相同的内容已经被执行，请不要重复执行"
            "3.某句话如果已经被回复过，不要重复回复"
        ),
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "user",
        },
    )
    """_wrap_私聊说话规则，行为风格（实验性功能）"""

    chat_prompts: list[ExtraPromptItem] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """_wrap_为指定聊天添加额外的prompt配置列表"""


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
        default=8080,
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
        },
    )
    """
    是否将消息中的二进制数据保存为独立文件
    若启用，消息中的语音等二进制数据将会保存为独立文件，并在消息中以特殊标记替代。启用会导致数据文件夹体积增大，但可以实现二次识别等功能。
    若禁用，则消息中的二进制将会在识别后删除，并在消息中使用识别结果替代，无法二次识别
    该配置项仅影响新存储的消息，已有消息不会受到影响
    """


class MaiSakaConfig(ConfigBase):
    """MaiSaka 对话系统配置类"""

    __ui_label__ = "MaiSaka"
    __ui_icon__ = "message-circle"
    __ui_parent__ = "experimental"

    enable_emotion_module: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "heart",
        },
    )
    """启用情绪感知模块"""

    enable_cognition_module: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """启用认知分析模块"""

    enable_timing_module: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "clock",
        },
    )
    """启用时间感知模块（含自我反思功能）"""

    enable_knowledge_module: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "book",
        },
    )
    """启用知识库模块"""

    enable_mcp: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "zap",
        },
    )
    """启用 MCP (Model Context Protocol) 支持"""

    enable_write_file: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-plus",
        },
    )
    """启用文件写入工具"""

    enable_read_file: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-text",
        },
    )
    """启用文件读取工具"""

    enable_list_files: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "list",
        },
    )
    """启用文件列表工具"""

    enable_qq_tools: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "users",
        },
    )
    """启用 QQ 工具（获取聊天记录、发送消息等）"""

    qq_api_base_url: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "server",
        },
    )
    """QQ API 基地址"""

    qq_api_key: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "key",
        },
    )
    """QQ API 密钥"""


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

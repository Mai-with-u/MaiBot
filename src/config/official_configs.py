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

    alias_names: list[str] = Field(default_factory=lambda: [])
    """别名列表"""


class PersonalityConfig(ConfigBase):
    """人格配置类"""

    personality: str = "是一个大二在读女大学生，现在正在上网和群友聊天，有时有点攻击性，有时比较温柔"
    """人格，建议100字以内，描述人格特质和身份特征"""

    reply_style: str = "请不要刻意突出自身学科背景。可以参考贴吧，知乎和微博的回复风格。"
    """默认表达风格，描述麦麦说话的表达风格，表达习惯，如要修改，可以酌情新增内容，建议1-2行"""

    multiple_reply_style: list[str] = Field(default_factory=lambda: [])
    """可选的多种表达风格列表，当配置不为空时可按概率随机替换 reply_style"""

    multiple_probability: float = 0.3
    """每次构建回复时，从 multiple_reply_style 中随机替换 reply_style 的概率（0.0-1.0）"""

    plan_style: str = """1.思考**所有**的可用的action中的**每个动作**是否符合当下条件，如果动作使用条件符合聊天内容就使用
2.如果相同的action已经被执行，请不要重复执行该action
3.如果有人对你感到厌烦，请减少回复
4.如果有人在追问你，或者话题没有说完，请你继续回复
5.请分析哪些对话是和你说的，哪些是其他人之间的互动，不要误认为其他人之间的互动是和你说的"""
    """_wrap_麦麦的说话规则和行为规则"""

    visual_style: str = "请用中文描述这张图片的内容。如果有文字，请把文字描述概括出来，请留意其主题，直观感受，输出为一段平文本，最多30字，请注意不要分点，就输出一段文本"
    """_wrap_识图提示词，不建议修改"""

    states: list[str] = Field(
        default_factory=lambda: [
            "是一个女大学生，喜欢上网聊天，会刷小红书。",
            "是一个大二心理学生，会刷贴吧和中国知网。",
            "是一个赛博网友，最近很想吐槽人。",
        ]
    )
    """_wrap_状态列表，用于随机替换personality"""

    state_probability: float = 0.3
    """状态概率，每次构建人格时替换personality的概率"""


class RelationshipConfig(ConfigBase):
    """关系配置类"""

    enable_relationship: bool = True
    """是否启用关系系统，关系系统被移除，此部分配置暂时无效"""


class TalkRulesItem(ConfigBase):
    platform: str = ""
    """平台，留空表示全局"""

    id: str = ""
    """用户ID"""

    rule_type: Literal["group", "private"] = "group"
    """聊天流类型，group（群聊）或private（私聊）"""

    time: str = ""
    """时间段，格式为 "HH:MM-HH:MM"，支持跨夜区间"""

    value: float = 0.5
    """聊天频率值，范围0-1"""


class ChatConfig(ConfigBase):
    """聊天配置类"""

    talk_value: float = 1
    """聊天频率，越小越沉默，范围0-1"""

    mentioned_bot_reply: bool = True
    """是否启用提及必回复"""

    max_context_size: int = 30
    """上下文长度"""

    planner_smooth: float = 3
    """规划器平滑，增大数值会减小planner负荷，略微降低反应速度，推荐1-5，0为关闭，必须大于等于0"""

    think_mode: Literal["classic", "deep", "dynamic"] = "dynamic"
    """
    思考模式配置
    - classic: 默认think_level为0（轻量回复，不需要思考和回忆）
    - deep: 默认think_level为1（深度回复，需要进行回忆和思考）
    - dynamic: think_level由planner动态给出（根据planner返回的think_level决定）
    """

    plan_reply_log_max_per_chat: int = 1024
    """每个聊天流最大保存的Plan/Reply日志数量，超过此数量时会自动删除最老的日志"""

    llm_quote: bool = False
    """是否在 reply action 中启用 quote 参数，启用后 LLM 可以控制是否引用消息"""

    enable_talk_value_rules: bool = True
    """是否启用动态发言频率规则"""

    talk_value_rules: list[TalkRulesItem] = Field(
        default_factory=lambda: [
            TalkRulesItem(platform="", id="", rule_type="group", time="00:00-08:59", value=0.8),
            TalkRulesItem(platform="", id="", rule_type="group", time="09:00-18:59", value=1.0),
        ]
    )
    """
    _wrap_思考频率规则列表，支持按聊天流/按日内时段配置。
    """


class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    ban_words: set[str] = Field(default_factory=lambda: set())
    """过滤词列表"""

    ban_msgs_regex: set[str] = Field(default_factory=lambda: set())
    """过滤正则表达式列表"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        for pattern in self.ban_msgs_regex:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern in ban_msgs_regex: '{pattern}'") from e
        return super().model_post_init(context)


class TargetItem(ConfigBase):
    platform: str = ""
    """平台，留空表示全局"""

    id: str = ""
    """用户ID"""

    rule_type: Literal["group", "private"] = "group"
    """聊天流类型，group（群聊）或private（私聊）"""


class MemoryConfig(ConfigBase):
    """记忆配置类"""

    max_agent_iterations: int = 5
    """记忆思考深度（最低为1）"""

    agent_timeout_seconds: float = 120.0
    """最长回忆时间（秒）"""

    global_memory: bool = False
    """是否允许记忆检索在聊天记录中进行全局查询（忽略当前chat_id，仅对 search_chat_history 等工具生效）"""

    global_memory_blacklist: list[TargetItem] = Field(default_factory=lambda: [])
    """_wrap_全局记忆黑名单，当启用全局记忆时，不将特定聊天流纳入检索"""

    chat_history_topic_check_message_threshold: int = 80
    """聊天历史话题检查的消息数量阈值，当累积消息数达到此值时触发话题检查"""

    chat_history_topic_check_time_hours: float = 8.0
    """聊天历史话题检查的时间阈值（小时），当距离上次检查超过此时间且消息数达到最小阈值时触发话题检查"""

    chat_history_topic_check_min_messages: int = 20
    """聊天历史话题检查的时间触发模式下的最小消息数阈值"""

    chat_history_finalize_no_update_checks: int = 3
    """聊天历史话题打包存储的连续无更新检查次数阈值，当话题连续N次检查无新增内容时触发打包存储"""

    chat_history_finalize_message_count: int = 5
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
    platform: str = ""
    """平台，留空表示全局"""

    id: str = ""
    """用户ID"""

    rule_type: Literal["group", "private"] = "group"
    """聊天流类型，group（群聊）或private（私聊）"""

    use_expression: bool = True
    """是否启用表达学习"""

    enable_learning: bool = True
    """是否启用表达优化学习"""

    enable_jargon_learning: bool = False
    """是否启用jargon学习"""


class ExpressionGroup(ConfigBase):
    """表达互通组配置类，若列表为空代表全局共享"""

    expression_groups: list[TargetItem] = Field(default_factory=lambda: [])
    """_wrap_表达学习互通组"""


class ExpressionConfig(ConfigBase):
    """表达配置类"""

    learning_list: list[LearningItem] = Field(
        default_factory=lambda: [
            LearningItem(
                platform="",
                id="",
                rule_type="group",
                use_expression=True,
                enable_learning=True,
                enable_jargon_learning=True,
            )
        ]
    )
    """_wrap_表达学习配置列表，支持按聊天流配置"""

    expression_groups: list[ExpressionGroup] = Field(default_factory=list)
    """_wrap_表达学习互通组"""

    expression_checked_only: bool = True
    """是否仅选择已检查且未拒绝的表达方式"""

    expression_self_reflect: bool = True
    """是否启用自动表达优化"""

    expression_auto_check_interval: int = 600
    """表达方式自动检查的间隔时间（秒）"""

    expression_auto_check_count: int = 20
    """每次自动检查时随机选取的表达方式数量"""

    expression_auto_check_custom_criteria: list[str] = Field(default_factory=list)
    """表达方式自动检查的额外自定义评估标准"""

    expression_manual_reflect: bool = False
    """是否启用手动表达优化"""

    manual_reflect_operator_id: Optional[TargetItem] = None
    """手动表达优化操作员ID"""

    allow_reflect: list[TargetItem] = Field(default_factory=list)
    """允许进行表达反思的聊天流ID列表，只有在此列表中的聊天流才会提出问题并跟踪。如果列表为空，则所有聊天流都可以进行表达反思（前提是reflect为true）"""

    all_global_jargon: bool = True
    """是否开启全局黑话模式，注意，此功能关闭后，已经记录的全局黑话不会改变，需要手动删除"""

    enable_jargon_explanation: bool = True
    """是否在回复前尝试对上下文中的黑话进行解释（关闭可减少一次LLM调用，仅影响回复前的黑话匹配与解释，不影响黑话学习）"""

    jargon_mode: Literal["context", "planner"] = "planner"
    """
    黑话解释来源模式
    
    可选：
    - "context"：使用上下文自动匹配黑话
    - "planner"：仅使用Planner在reply动作中给出的unknown_words列表
    """


class ToolConfig(ConfigBase):
    """工具配置类"""

    enable_tool: bool = False
    """是否在聊天中启用工具"""


class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    enable_asr: bool = False
    """是否启用语音识别，启用后麦麦可以识别语音消息"""


class EmojiConfig(ConfigBase):
    """表情包配置类"""

    emoji_chance: float = 0.4
    """发送表情包的基础概率"""

    max_reg_num: int = 100
    """表情包最大注册数量"""

    do_replace: bool = True
    """达到最大注册数量时替换旧表情包，关闭则达到最大数量时不会继续收集表情包"""

    check_interval: int = 10
    """表情包检查间隔（分钟）"""

    steal_emoji: bool = True
    """是否偷取表情包，让麦麦可以将一些表情包据为己有"""

    content_filtration: bool = False
    """是否启用表情包过滤，只有符合该要求的表情包才会被保存"""

    filtration_prompt: str = "符合公序良俗"
    """表情包过滤要求，只有符合该要求的表情包才会被保存"""


class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    keywords: list[str] = Field(default_factory=lambda: [])
    """关键词列表"""

    regex: list[str] = Field(default_factory=lambda: [])
    """正则表达式列表"""

    reaction: str = ""
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

    keyword_rules: list[KeywordRuleConfig] = Field(default_factory=lambda: [])
    """关键词规则列表"""

    regex_rules: list[KeywordRuleConfig] = Field(default_factory=lambda: [])
    """正则表达式规则列表"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置"""
        for rule in self.keyword_rules + self.regex_rules:
            if not isinstance(rule, KeywordRuleConfig):
                raise ValueError(f"规则必须是KeywordRuleConfig类型，而不是{type(rule).__name__}")
        return super().model_post_init(context)


class ResponsePostProcessConfig(ConfigBase):
    """回复后处理配置类"""

    enable_response_post_process: bool = True
    """是否启用回复后处理，包括错别字生成器，回复分割器"""


class ChineseTypoConfig(ConfigBase):
    """中文错别字配置类"""

    enable: bool = True
    """是否启用中文错别字生成器"""

    error_rate: float = 0.01
    """单字替换概率"""

    min_freq: int = 9
    """最小字频阈值"""

    tone_error_rate: float = 0.1
    """声调错误概率"""

    word_replace_rate: float = 0.006
    """整词替换概率"""


class ResponseSplitterConfig(ConfigBase):
    """回复分割器配置类"""

    enable: bool = True
    """是否启用回复分割器"""

    max_length: int = 512
    """回复允许的最大长度"""

    max_sentence_num: int = 8
    """回复允许的最大句子数"""

    enable_kaomoji_protection: bool = False
    """是否启用颜文字保护"""

    enable_overflow_return_all: bool = False
    """是否在句子数量超出回复允许的最大句子数时一次性返回全部内容"""


class TelemetryConfig(ConfigBase):
    """遥测配置类"""

    enable: bool = True
    """是否启用遥测"""


class DebugConfig(ConfigBase):
    """调试配置类"""

    show_prompt: bool = False
    """是否显示prompt"""

    show_replyer_prompt: bool = True
    """是否显示回复器prompt"""

    show_replyer_reasoning: bool = True
    """是否显示回复器推理"""

    show_jargon_prompt: bool = False
    """是否显示jargon相关提示词"""

    show_memory_prompt: bool = False
    """是否显示记忆检索相关prompt"""

    show_planner_prompt: bool = False
    """是否显示planner的prompt和原始返回结果"""

    show_lpmm_paragraph: bool = False
    """是否显示lpmm找到的相关文段日志"""


class ExtraPromptItem(ConfigBase):
    platform: str = ""
    """平台，留空无效"""

    id: str = ""
    """用户ID，留空无效"""

    rule_type: Literal["group", "private"] = "group"
    """聊天流类型，group（群聊）或private（私聊）"""

    prompt: str = ""
    """额外的prompt内容"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        if not self.platform or not self.id or not self.prompt:
            raise ValueError("ExtraPromptItem 中 platform, id 和 prompt 不能为空")
        return super().model_post_init(context)


class ExperimentalConfig(ConfigBase):
    """实验功能配置类"""

    private_plan_style: str = """
1.思考**所有**的可用的action中的**每个动作**是否符合当下条件，如果动作使用条件符合聊天内容就使用
2.如果相同的内容已经被执行，请不要重复执行
3.某句话如果已经被回复过，不要重复回复"""
    """_wrap_私聊说话规则，行为风格（实验性功能）"""

    chat_prompts: list[ExtraPromptItem] = Field(default_factory=lambda: [])
    """_wrap_为指定聊天添加额外的prompt配置列表"""

    lpmm_memory: bool = False
    """是否将聊天历史总结导入到LPMM知识库。开启后，chat_history_summarizer总结出的历史记录会同时导入到知识库"""


class MaimMessageConfig(ConfigBase):
    """maim_message配置类"""

    auth_token: list[str] = Field(default_factory=lambda: [])
    """认证令牌，用于旧版API验证，为空则不启用验证"""

    enable_api_server: bool = False
    """是否启用额外的新版API Server"""

    api_server_host: str = "0.0.0.0"
    """新版API Server主机地址"""

    api_server_port: int = 8090
    """新版API Server端口号"""

    api_server_use_wss: bool = False
    """新版API Server是否启用WSS"""

    api_server_cert_file: str = ""
    """新版API Server SSL证书文件路径"""

    api_server_key_file: str = ""
    """新版API Server SSL密钥文件路径"""

    api_server_allowed_api_keys: list[str] = Field(default_factory=lambda: [])
    """新版API Server允许的API Key列表，为空则允许所有连接"""


class LPMMKnowledgeConfig(ConfigBase):
    """LPMM知识库配置类"""

    enable: bool = True
    """是否启用LPMM知识库"""

    lpmm_mode: Literal["classic", "agent"] = "classic"
    """LPMM知识库模式，可选：classic经典模式，agent 模式"""

    rag_synonym_search_top_k: int = 10
    """同义检索TopK"""

    rag_synonym_threshold: float = 0.8
    """同义阈值，相似度高于该值的关系会被当作同义词"""

    info_extraction_workers: int = 3
    """实体抽取同时执行线程数，非Pro模型不要设置超过5"""

    qa_relation_search_top_k: int = 10
    """关系检索TopK"""

    qa_relation_threshold: float = 0.75
    """关系阈值，相似度高于该值的关系会被认为是相关关系"""

    qa_paragraph_search_top_k: int = 1000
    """段落检索TopK（不能过小，可能影响搜索结果）"""

    qa_paragraph_node_weight: float = 0.05
    """段落节点权重（在图搜索&PPR计算中的权重，当搜索仅使用DPR时，此参数不起作用）"""

    qa_ent_filter_top_k: int = 10
    """实体过滤TopK"""

    qa_ppr_damping: float = 0.8
    """PPR阻尼系数"""

    qa_res_top_k: int = 10
    """最终提供段落TopK"""

    embedding_dimension: int = 1024
    """嵌入向量维度,输出维度"""

    max_embedding_workers: int = 3
    """嵌入/抽取并发线程数"""

    embedding_chunk_size: int = 4
    """每批嵌入的条数"""

    max_synonym_entities: int = 2000
    """同义边参与的实体数上限，超限则跳过"""

    enable_ppr: bool = True
    """是否启用PPR，低配机器可关闭"""


class DreamConfig(ConfigBase):
    """Dream配置类"""

    interval_minutes: int = 30
    """做梦时间间隔（分钟），默认30分钟"""

    max_iterations: int = 20
    """做梦最大轮次，默认20轮"""

    first_delay_seconds: int = 1800
    """程序启动后首次做梦前的延迟时间（秒），默认1800秒"""

    dream_send: str = ""
    """做梦结果推送目标，格式为 "platform:user_id，为空则不发送"""

    dream_time_ranges: list[str] = Field(default_factory=lambda: ["23:00-10:00"])
    """_wrap_做梦时间段配置列表"""

    dream_visible: bool = False
    """做梦结果发送后是否存储到上下文"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        if self.interval_minutes < 1:
            raise ValueError(f"interval_minutes 必须至少为1，当前值: {self.interval_minutes}")
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations 必须至少为1，当前值: {self.max_iterations}")
        if self.first_delay_seconds < 0:
            raise ValueError(f"first_delay_seconds 不能为负数，当前值: {self.first_delay_seconds}")
        return super().model_post_init(context)

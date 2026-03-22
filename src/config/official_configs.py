import re

from dataclasses import dataclass, field
from typing import Literal, Optional
import time

from src.config.config_base import ConfigBase

"""
须知：
1. 本文件中记录了所有的配置项
2. 所有新增的class都需要继承自ConfigBase
3. 所有新增的class都应在config.py中的Config类中添加字段
4. 对于新增的字段，若为可选项，则应在其后添加field()并设置default_factory或default
"""


@dataclass
class BotConfig(ConfigBase):
    """QQ机器人配置类"""

    platform: str
    """平台"""

    qq_account: str
    """QQ账号"""

    nickname: str
    """昵称"""

    platforms: list[str] = field(default_factory=lambda: [])
    """其他平台列表"""

    alias_names: list[str] = field(default_factory=lambda: [])
    """别名列表"""


@dataclass
class PersonalityConfig(ConfigBase):
    """人格配置类"""

    personality: str
    """人格"""

    reply_style: str = ""
    """默认表达风格"""

    multiple_reply_style: list[str] = field(default_factory=lambda: [])
    """可选的多种表达风格列表，当配置不为空时可按概率随机替换 reply_style"""

    multiple_probability: float = 0.0
    """每次构建回复时，从 multiple_reply_style 中随机替换 reply_style 的概率（0.0-1.0）"""

    plan_style: str = ""
    """说话规则，行为风格"""

    visual_style: str = ""
    """图片提示词"""

    states: list[str] = field(default_factory=lambda: [])
    """状态列表，用于随机替换personality"""

    state_probability: float = 0.0
    """状态概率，每次构建人格时替换personality的概率"""


@dataclass
class RelationshipConfig(ConfigBase):
    """关系配置类"""

    enable_relationship: bool = True
    """是否启用关系系统"""


@dataclass
class ChatConfig(ConfigBase):
    """聊天配置类"""

    max_context_size: int = 18
    """上下文长度"""

    mentioned_bot_reply: bool = True
    """是否启用提及必回复"""

    at_bot_inevitable_reply: float = 1
    """@bot 必然回复，1为100%回复，0为不额外增幅"""

    planner_smooth: float = 3
    """规划器平滑，增大数值会减小planner负荷，略微降低反应速度，推荐2-5，0为关闭，必须大于等于0"""

    talk_value: float = 1
    """思考频率"""

    enable_talk_value_rules: bool = True
    """是否启用动态发言频率规则"""

    talk_value_rules: list[dict] = field(default_factory=lambda: [])
    """
    思考频率规则列表，支持按聊天流/按日内时段配置。
    规则格式：{ target="platform:id:type" 或 "", time="HH:MM-HH:MM", value=0.5 }

    示例:
    [
        ["", "00:00-08:59", 0.2],                 # 全局规则：凌晨到早上更安静
        ["", "09:00-22:59", 1.0],                 # 全局规则：白天正常
        ["qq:1919810:group", "20:00-23:59", 0.6], # 指定群在晚高峰降低发言
        ["qq:114514:private", "00:00-23:59", 0.3],# 指定私聊全时段较安静
    ]

    匹配优先级: 先匹配指定 chat 流规则，再匹配全局规则(\"\"). 
    时间区间支持跨夜，例如 "23:00-02:00"。
    """

    think_mode: Literal["classic", "deep", "dynamic"] = "classic"
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

    enable_multi_turn: bool = False
    """是否启用多轮对话模式（将聊天历史拆分为 user/assistant 消息而非单一 prompt）"""

    self_sign: bool = True
    """是否在多轮对话模式下显示 bot 自己发言时的"bot_name(你):"前缀"""

    def _parse_stream_config_to_chat_id(self, stream_config_str: str) -> Optional[str]:
        """与 ChatStream.get_stream_id 一致地从 "platform:id:type" 生成 chat_id。"""
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None

            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]

            is_group = stream_type == "group"

            from src.chat.message_receive.chat_stream import get_chat_manager

            return get_chat_manager().get_stream_id(platform, str(id_str), is_group=is_group)

        except (ValueError, IndexError):
            return None

    def _now_minutes(self) -> int:
        """返回本地时间的分钟数(0-1439)。"""
        lt = time.localtime()
        return lt.tm_hour * 60 + lt.tm_min

    def _parse_range(self, range_str: str) -> Optional[tuple[int, int]]:
        """解析 "HH:MM-HH:MM" 到 (start_min, end_min)。"""
        try:
            start_str, end_str = [s.strip() for s in range_str.split("-")]
            sh, sm = [int(x) for x in start_str.split(":")]
            eh, em = [int(x) for x in end_str.split(":")]
            return sh * 60 + sm, eh * 60 + em
        except Exception:
            return None

    def _in_range(self, now_min: int, start_min: int, end_min: int) -> bool:
        """
        判断 now_min 是否在 [start_min, end_min] 区间内。
        支持跨夜：如果 start > end，则表示跨越午夜。
        """
        if start_min <= end_min:
            return start_min <= now_min <= end_min
        # 跨夜：例如 23:00-02:00
        return now_min >= start_min or now_min <= end_min

    def get_talk_value(self, chat_id: Optional[str]) -> float:
        """根据规则返回当前 chat 的动态 talk_value，未匹配则回退到基础值。"""
        if not self.enable_talk_value_rules or not self.talk_value_rules:
            result = self.talk_value
            # 防止返回0值，自动转换为0.0001
            if result == 0:
                return 0.0000001
            return result

        now_min = self._now_minutes()

        # 1) 先尝试匹配指定 chat 的规则
        if chat_id:
            for rule in self.talk_value_rules:
                if not isinstance(rule, dict):
                    continue
                target = rule.get("target", "")
                time_range = rule.get("time", "")
                value = rule.get("value", None)
                if not isinstance(time_range, str):
                    continue
                # 跳过全局
                if target == "":
                    continue
                config_chat_id = self._parse_stream_config_to_chat_id(str(target))
                if config_chat_id is None or config_chat_id != chat_id:
                    continue
                parsed = self._parse_range(time_range)
                if not parsed:
                    continue
                start_min, end_min = parsed
                if self._in_range(now_min, start_min, end_min):
                    try:
                        result = float(value)
                        # 防止返回0值，自动转换为0.0001
                        if result == 0:
                            return 0.0000001
                        return result
                    except Exception:
                        continue

        # 2) 再匹配全局规则("")
        for rule in self.talk_value_rules:
            if not isinstance(rule, dict):
                continue
            target = rule.get("target", None)
            time_range = rule.get("time", "")
            value = rule.get("value", None)
            if target != "" or not isinstance(time_range, str):
                continue
            parsed = self._parse_range(time_range)
            if not parsed:
                continue
            start_min, end_min = parsed
            if self._in_range(now_min, start_min, end_min):
                try:
                    result = float(value)
                    # 防止返回0值，自动转换为0.0001
                    if result == 0:
                        return 0.0000001
                    return result
                except Exception:
                    continue

        # 3) 未命中规则返回基础值
        result = self.talk_value
        # 防止返回0值，自动转换为0.0001
        if result == 0:
            return 0.0000001
        return result


@dataclass
class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    ban_words: set[str] = field(default_factory=lambda: set())
    """过滤词列表"""

    ban_msgs_regex: set[str] = field(default_factory=lambda: set())
    """过滤正则表达式列表"""


@dataclass
class MemoryConfig(ConfigBase):
    """记忆配置类"""

    enable_lpmm: bool = False
    """是否启用LPMM知识库记忆。开启后，聊天历史总结会导入到LPMM知识库，记忆检索Agent可通过search_knowledge工具查询知识库"""

    max_agent_iterations: int = 5
    """Agent最多迭代轮数（最低为1）"""

    agent_timeout_seconds: float = 120.0
    """Agent超时时间（秒）"""

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

    def __post_init__(self):
        """验证配置值"""
        if self.max_agent_iterations < 1:
            raise ValueError(f"max_agent_iterations 必须至少为1，当前值: {self.max_agent_iterations}")
        if self.agent_timeout_seconds <= 0:
            raise ValueError(f"agent_timeout_seconds 必须大于0，当前值: {self.agent_timeout_seconds}")
        if self.chat_history_topic_check_message_threshold < 1:
            raise ValueError(f"chat_history_topic_check_message_threshold 必须至少为1，当前值: {self.chat_history_topic_check_message_threshold}")
        if self.chat_history_topic_check_time_hours <= 0:
            raise ValueError(f"chat_history_topic_check_time_hours 必须大于0，当前值: {self.chat_history_topic_check_time_hours}")
        if self.chat_history_topic_check_min_messages < 1:
            raise ValueError(f"chat_history_topic_check_min_messages 必须至少为1，当前值: {self.chat_history_topic_check_min_messages}")
        if self.chat_history_finalize_no_update_checks < 1:
            raise ValueError(f"chat_history_finalize_no_update_checks 必须至少为1，当前值: {self.chat_history_finalize_no_update_checks}")
        if self.chat_history_finalize_message_count < 1:
            raise ValueError(f"chat_history_finalize_message_count 必须至少为1，当前值: {self.chat_history_finalize_message_count}")


@dataclass
class ExpressionConfig(ConfigBase):
    """表达配置类"""

    learning_list: list[list] = field(default_factory=lambda: [])
    """
    表达学习配置列表，支持按聊天流配置
    格式: [["chat_stream_id", "use_expression", "enable_learning", "enable_jargon_learning"], ...]
    
    示例:
    [
        ["", "enable", "enable", "enable"],  # 全局配置：使用表达，启用学习，启用jargon学习
        ["qq:1919810:private", "enable", "enable", "enable"],  # 特定私聊配置：使用表达，启用学习，启用jargon学习
        ["qq:114514:private", "enable", "disable", "disable"],  # 特定私聊配置：使用表达，禁用学习，禁用jargon学习
    ]
    
    说明:
    - 第一位: chat_stream_id，空字符串表示全局配置
    - 第二位: 是否使用学到的表达 ("enable"/"disable")
    - 第三位: 是否学习表达 ("enable"/"disable") 
    - 第四位: 是否启用jargon学习 ("enable"/"disable")
    """

    expression_groups: list[list[str]] = field(default_factory=list)
    """
    表达学习互通组
    格式: [["qq:12345:group", "qq:67890:private"]]
    """

    expression_self_reflect: bool = False
    """是否启用自动表达优化"""

    all_global_jargon: bool = False
    """是否将所有新增的jargon项目默认为全局（is_global=True），chat_id记录第一次存储时的id。注意，此功能关闭后，已经记录的全局黑话不会改变，需要手动删除"""

    enable_jargon_explanation: bool = True
    """是否在回复前尝试对上下文中的黑话进行解释（关闭可减少一次LLM调用，仅影响回复前的黑话匹配与解释，不影响黑话学习）"""

    jargon_mode: Literal["context", "planner"] = "context"
    """
    黑话解释来源模式：
    - "context": 使用上下文自动匹配黑话并解释（原有模式）
    - "planner": 仅使用 Planner 在 reply 动作中给出的 unknown_words 列表进行黑话检索
    """

    expression_checked_only: bool = False
    """
    是否仅选择已检查且未拒绝的表达方式
    当设置为 true 时，只有 checked=True 且 rejected=False 的表达方式才会被选择
    当设置为 false 时，保留旧的筛选原则（仅排除 rejected=True 的表达方式）
    """


    expression_auto_check_interval: int = 3600
    """
    表达方式自动检查的间隔时间（单位：秒）
    默认值：3600秒（1小时）
    """

    expression_auto_check_count: int = 10
    """
    每次自动检查时随机选取的表达方式数量
    默认值：10条
    """

    expression_auto_check_custom_criteria: list[str] = field(default_factory=list)
    """
    表达方式自动检查的额外自定义评估标准
    格式: ["标准1", "标准2", "标准3", ...]
    这些标准会被添加到评估提示词中，作为额外的评估要求
    默认值：空列表
    """

    def _parse_stream_config_to_chat_id(self, stream_config_str: str) -> Optional[str]:
        """
        解析流配置字符串并生成对应的 chat_id

        Args:
            stream_config_str: 格式为 "platform:id:type" 的字符串

        Returns:
            str: 生成的 chat_id，如果解析失败则返回 None
        """
        try:
            parts = stream_config_str.split(":")
            if len(parts) != 3:
                return None

            platform = parts[0]
            id_str = parts[1]
            stream_type = parts[2]

            # 判断是否为群聊
            is_group = stream_type == "group"

            # 使用 ChatManager 提供的接口生成 chat_id，避免在此重复实现逻辑
            from src.chat.message_receive.chat_stream import get_chat_manager

            return get_chat_manager().get_stream_id(platform, str(id_str), is_group=is_group)

        except (ValueError, IndexError):
            return None

    def get_expression_config_for_chat(self, chat_stream_id: Optional[str] = None) -> tuple[bool, bool, bool]:
        """
        根据聊天流ID获取表达配置

        Args:
            chat_stream_id: 聊天流ID，格式为哈希值

        Returns:
            tuple: (是否使用表达, 是否学习表达, 是否启用jargon学习)
        """
        if not self.learning_list:
            # 如果没有配置，使用默认值：启用表达，启用学习，启用jargon学习
            return True, True, True

        # 优先检查聊天流特定的配置
        if chat_stream_id:
            specific_expression_config = self._get_stream_specific_config(chat_stream_id)
            if specific_expression_config is not None:
                return specific_expression_config

        # 检查全局配置（第一个元素为空字符串的配置）
        global_expression_config = self._get_global_config()
        if global_expression_config is not None:
            return global_expression_config

        # 如果都没有匹配，返回默认值：启用表达，启用学习，启用jargon学习
        return True, True, True

    def _get_stream_specific_config(self, chat_stream_id: str) -> Optional[tuple[bool, bool, bool]]:
        """
        获取特定聊天流的表达配置

        Args:
            chat_stream_id: 聊天流ID（哈希值）

        Returns:
            tuple: (是否使用表达, 是否学习表达, 是否启用jargon学习)，如果没有配置则返回 None
        """
        for config_item in self.learning_list:
            if not config_item or len(config_item) < 4:
                continue

            stream_config_str = config_item[0]  # 例如 "qq:1026294844:group"

            # 如果是空字符串，跳过（这是全局配置）
            if stream_config_str == "":
                continue

            # 解析配置字符串并生成对应的 chat_id
            config_chat_id = self._parse_stream_config_to_chat_id(stream_config_str)
            if config_chat_id is None:
                continue

            # 比较生成的 chat_id
            if config_chat_id != chat_stream_id:
                continue

            # 解析配置
            try:
                use_expression: bool = config_item[1].lower() == "enable"
                enable_learning: bool = config_item[2].lower() == "enable"
                enable_jargon_learning: bool = config_item[3].lower() == "enable"
                return use_expression, enable_learning, enable_jargon_learning  # type: ignore
            except (ValueError, IndexError):
                continue

        return None

    def _get_global_config(self) -> Optional[tuple[bool, bool, bool]]:
        """
        获取全局表达配置

        Returns:
            tuple: (是否使用表达, 是否学习表达, 是否启用jargon学习)，如果没有配置则返回 None
        """
        for config_item in self.learning_list:
            if not config_item or len(config_item) < 4:
                continue

            # 检查是否为全局配置（第一个元素为空字符串）
            if config_item[0] == "":
                try:
                    use_expression: bool = config_item[1].lower() == "enable"
                    enable_learning: bool = config_item[2].lower() == "enable"
                    enable_jargon_learning: bool = config_item[3].lower() == "enable"
                    return use_expression, enable_learning, enable_jargon_learning  # type: ignore
                except (ValueError, IndexError):
                    continue

        return None


@dataclass
class ToolConfig(ConfigBase):
    """工具配置类"""

    enable_tool: bool = False
    """是否在聊天中启用工具"""


@dataclass
class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    enable_asr: bool = False
    """是否启用语音识别"""


@dataclass
class EmojiConfig(ConfigBase):
    """表情包配置类"""

    emoji_chance: float = 0.6
    """发送表情包的基础概率"""

    max_reg_num: int = 200
    """表情包最大注册数量"""

    do_replace: bool = True
    """达到最大注册数量时替换旧表情包"""

    check_interval: int = 120
    """表情包检查间隔（分钟）"""

    steal_emoji: bool = True
    """是否偷取表情包，让麦麦可以发送她保存的这些表情包"""

    content_filtration: bool = False
    """是否开启表情包过滤"""

    filtration_prompt: str = "符合公序良俗"
    """表情包过滤要求"""


@dataclass
class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    keywords: list[str] = field(default_factory=lambda: [])
    """关键词列表"""

    regex: list[str] = field(default_factory=lambda: [])
    """正则表达式列表"""

    reaction: str = ""
    """关键词触发的反应"""

    def __post_init__(self):
        """验证配置"""
        if not self.keywords and not self.regex:
            raise ValueError("关键词规则必须至少包含keywords或regex中的一个")

        if not self.reaction:
            raise ValueError("关键词规则必须包含reaction")

        # 验证正则表达式
        for pattern in self.regex:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"无效的正则表达式 '{pattern}': {str(e)}") from e


@dataclass
class KeywordReactionConfig(ConfigBase):
    """关键词配置类"""

    keyword_rules: list[KeywordRuleConfig] = field(default_factory=lambda: [])
    """关键词规则列表"""

    regex_rules: list[KeywordRuleConfig] = field(default_factory=lambda: [])
    """正则表达式规则列表"""

    def __post_init__(self):
        """验证配置"""
        # 验证所有规则
        for rule in self.keyword_rules + self.regex_rules:
            if not isinstance(rule, KeywordRuleConfig):
                raise ValueError(f"规则必须是KeywordRuleConfig类型，而不是{type(rule).__name__}")


@dataclass
class ResponsePostProcessConfig(ConfigBase):
    """回复后处理配置类"""

    enable_response_post_process: bool = True
    """是否启用回复后处理，包括错别字生成器，回复分割器"""


@dataclass
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


@dataclass
class ResponseSplitterConfig(ConfigBase):
    """回复分割器配置类"""

    enable: bool = True
    """是否启用回复分割器"""

    max_length: int = 256
    """回复允许的最大长度"""

    max_sentence_num: int = 3
    """回复允许的最大句子数"""

    enable_kaomoji_protection: bool = False
    """是否启用颜文字保护"""

    enable_overflow_return_all: bool = False
    """是否在超出句子数量限制时合并后一次性返回"""


@dataclass
class TelemetryConfig(ConfigBase):
    """遥测配置类"""

    enable: bool = True
    """是否启用遥测"""


@dataclass
class WebUIConfig(ConfigBase):
    """WebUI配置类
    
    注意: host 和 port 配置已移至环境变量 WEBUI_HOST 和 WEBUI_PORT
    """

    enabled: bool = True
    """是否启用WebUI"""

    mode: Literal["development", "production"] = "production"
    """运行模式：development(开发) 或 production(生产)"""

    anti_crawler_mode: Literal["false", "strict", "loose", "basic"] = "basic"
    """防爬虫模式：false(禁用) / strict(严格) / loose(宽松) / basic(基础-只记录不阻止)"""

    allowed_ips: str = "127.0.0.1"
    """IP白名单（逗号分隔，支持精确IP、CIDR格式和通配符）"""

    trusted_proxies: str = ""
    """信任的代理IP列表（逗号分隔），只有来自这些IP的X-Forwarded-For才被信任"""

    trust_xff: bool = False
    """是否启用X-Forwarded-For代理解析（默认false）"""

    secure_cookie: bool = False
    """是否启用安全Cookie（仅通过HTTPS传输，默认false）"""

    enable_paragraph_content: bool = False
    """是否在知识图谱中加载段落完整内容（需要加载embedding store，会占用额外内存）"""


@dataclass
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
    """是否显示planner相关提示词"""

    show_lpmm_paragraph: bool = False
    """是否显示lpmm找到的相关文段日志"""


@dataclass
class ExperimentalConfig(ConfigBase):
    """实验功能配置类"""

    private_plan_style: str = ""
    """私聊说话规则，行为风格（实验性功能）"""

    chat_prompts: list[str] = field(default_factory=lambda: [])
    """
    为指定聊天添加额外的prompt配置列表
    格式: ["platform:id:type:prompt内容", ...]
    
    示例:
    [
        "qq:114514:group:这是一个摄影群，你精通摄影知识",
        "qq:19198:group:这是一个二次元交流群",
        "qq:114514:private:这是你与好朋友的私聊"
    ]
    
    说明:
    - platform: 平台名称，如 "qq"
    - id: 群ID或用户ID
    - type: "group" 或 "private"
    - prompt内容: 要添加的额外prompt文本
    """



@dataclass
class MaimMessageConfig(ConfigBase):
    """maim_message配置类"""

    auth_token: list[str] = field(default_factory=lambda: [])
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

    api_server_allowed_api_keys: list[str] = field(default_factory=lambda: [])
    """新版API Server允许的API Key列表，为空则允许所有连接"""


@dataclass
class LPMMKnowledgeConfig(ConfigBase):
    """LPMM知识库配置类"""

    rag_synonym_search_top_k: int = 10
    """RAG同义词搜索的Top K数量"""

    rag_synonym_threshold: float = 0.8
    """RAG同义词搜索的相似度阈值"""

    info_extraction_workers: int = 3
    """信息提取工作线程数"""

    qa_relation_search_top_k: int = 10
    """QA关系搜索的Top K数量"""

    qa_relation_threshold: float = 0.75
    """QA关系搜索的相似度阈值"""

    qa_paragraph_search_top_k: int = 1000
    """QA段落搜索的Top K数量"""

    qa_paragraph_node_weight: float = 0.05
    """QA段落节点权重"""

    qa_ent_filter_top_k: int = 10
    """QA实体过滤的Top K数量"""

    qa_ppr_damping: float = 0.8
    """QA PageRank阻尼系数"""

    qa_res_top_k: int = 10
    """QA最终结果的Top K数量"""

    embedding_dimension: int = 1024
    """嵌入向量维度，应该与模型的输出维度一致"""

    max_embedding_workers: int = 3
    """嵌入/抽取并发线程数"""

    embedding_chunk_size: int = 4
    """每批嵌入的条数"""

    max_synonym_entities: int = 2000
    """同义边参与的实体数上限，超限则跳过"""

    enable_ppr: bool = True
    """是否启用PPR，低配机器可关闭"""

    max_cache_size: int = 5000
    """嵌入库最大内存缓存条目数，超过此数量将使用按需加载模式
    
    每个条目约占用6KB内存（1024维向量），默认值5000约占用30MB内存。
    如果知识库很大（>10万条），建议设置为5000-10000。
    设置为0表示全部加载到内存（仅适合小数据集）。
    """



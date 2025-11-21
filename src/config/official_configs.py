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
    """表达风格"""

    interest: str = ""
    """兴趣"""

    plan_style: str = ""
    """说话规则，行为风格"""

    visual_style: str = ""
    """图片提示词"""

    private_plan_style: str = ""
    """私聊说话规则，行为风格"""

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

    interest_rate_mode: Literal["fast", "accurate"] = "fast"
    """兴趣值计算模式，fast为快速计算，accurate为精确计算"""

    planner_size: float = 1.5
    """副规划器大小，越小，麦麦的动作执行能力越精细，但是消耗更多token，调大可以缓解429类错误"""

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

    include_planner_reasoning: bool = False
    """是否将planner推理加入replyer，默认关闭（不加入）"""

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

            import hashlib

            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()

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
            return self.talk_value

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
                        return float(value)
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
                    return float(value)
                except Exception:
                    continue

        # 3) 未命中规则返回基础值
        return self.talk_value


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

    max_agent_iterations: int = 5
    """Agent最多迭代轮数（最低为1）"""

    def __post_init__(self):
        """验证配置值"""
        if self.max_agent_iterations < 1:
            raise ValueError(f"max_agent_iterations 必须至少为1，当前值: {self.max_agent_iterations}")


@dataclass
class ExpressionConfig(ConfigBase):
    """表达配置类"""

    learning_list: list[list] = field(default_factory=lambda: [])
    """
    表达学习配置列表，支持按聊天流配置
    格式: [["chat_stream_id", "use_expression", "enable_learning", learning_intensity], ...]
    
    示例:
    [
        ["", "enable", "enable", 1.0],  # 全局配置：使用表达，启用学习，学习强度1.0
        ["qq:1919810:private", "enable", "enable", 1.5],  # 特定私聊配置：使用表达，启用学习，学习强度1.5
        ["qq:114514:private", "enable", "disable", 0.5],  # 特定私聊配置：使用表达，禁用学习，学习强度0.5
    ]
    
    说明:
    - 第一位: chat_stream_id，空字符串表示全局配置
    - 第二位: 是否使用学到的表达 ("enable"/"disable")
    - 第三位: 是否学习表达 ("enable"/"disable") 
    - 第四位: 学习强度（浮点数），影响学习频率，最短学习时间间隔 = 300/学习强度（秒）
    """

    expression_groups: list[list[str]] = field(default_factory=list)
    """
    表达学习互通组
    格式: [["qq:12345:group", "qq:67890:private"]]
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

            # 使用与 ChatStream.get_stream_id 相同的逻辑生成 chat_id
            import hashlib

            if is_group:
                components = [platform, str(id_str)]
            else:
                components = [platform, str(id_str), "private"]
            key = "_".join(components)
            return hashlib.md5(key.encode()).hexdigest()

        except (ValueError, IndexError):
            return None

    def get_expression_config_for_chat(self, chat_stream_id: Optional[str] = None) -> tuple[bool, bool, int]:
        """
        根据聊天流ID获取表达配置

        Args:
            chat_stream_id: 聊天流ID，格式为哈希值

        Returns:
            tuple: (是否使用表达, 是否学习表达, 学习间隔)
        """
        if not self.learning_list:
            # 如果没有配置，使用默认值：启用表达，启用学习，300秒间隔
            return True, True, 300

        # 优先检查聊天流特定的配置
        if chat_stream_id:
            specific_expression_config = self._get_stream_specific_config(chat_stream_id)
            if specific_expression_config is not None:
                return specific_expression_config

        # 检查全局配置（第一个元素为空字符串的配置）
        global_expression_config = self._get_global_config()
        if global_expression_config is not None:
            return global_expression_config

        # 如果都没有匹配，返回默认值
        return True, True, 300

    def _get_stream_specific_config(self, chat_stream_id: str) -> Optional[tuple[bool, bool, int]]:
        """
        获取特定聊天流的表达配置

        Args:
            chat_stream_id: 聊天流ID（哈希值）

        Returns:
            tuple: (是否使用表达, 是否学习表达, 学习间隔)，如果没有配置则返回 None
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
                learning_intensity: float = float(config_item[3])
                return use_expression, enable_learning, learning_intensity  # type: ignore
            except (ValueError, IndexError):
                continue

        return None

    def _get_global_config(self) -> Optional[tuple[bool, bool, int]]:
        """
        获取全局表达配置

        Returns:
            tuple: (是否使用表达, 是否学习表达, 学习间隔)，如果没有配置则返回 None
        """
        for config_item in self.learning_list:
            if not config_item or len(config_item) < 4:
                continue

            # 检查是否为全局配置（第一个元素为空字符串）
            if config_item[0] == "":
                try:
                    use_expression: bool = config_item[1].lower() == "enable"
                    enable_learning: bool = config_item[2].lower() == "enable"
                    learning_intensity = float(config_item[3])
                    return use_expression, enable_learning, learning_intensity  # type: ignore
                except (ValueError, IndexError):
                    continue

        return None


@dataclass
class ToolConfig(ConfigBase):
    """工具配置类"""

    enable_tool: bool = False
    """是否在聊天中启用工具"""


@dataclass
class MoodConfig(ConfigBase):
    """情绪配置类"""

    enable_mood: bool = True
    """是否启用情绪系统"""

    mood_update_threshold: float = 1
    """情绪更新阈值,越高，更新越慢"""

    emotion_style: str = "情绪较为稳定，但遭遇特定事件的时候起伏较大"
    """情感特征，影响情绪的变化情况"""


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

    enable_friend_chat: bool = False
    """是否启用好友聊天"""

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

    use_custom: bool = False
    """是否使用自定义的maim_message配置"""

    host: str = "127.0.0.1"
    """主机地址"""

    port: int = 8090
    """"端口号"""

    mode: Literal["ws", "tcp"] = "ws"
    """连接模式，支持ws和tcp"""

    use_wss: bool = False
    """是否使用WSS安全连接"""

    cert_file: str = ""
    """SSL证书文件路径，仅在use_wss=True时有效"""

    key_file: str = ""
    """SSL密钥文件路径，仅在use_wss=True时有效"""

    auth_token: list[str] = field(default_factory=lambda: [])
    """认证令牌，用于API验证，为空则不启用验证"""


@dataclass
class LPMMKnowledgeConfig(ConfigBase):
    """LPMM知识库配置类"""

    enable: bool = True
    """是否启用LPMM知识库"""

    lpmm_mode: Literal["classic", "agent"] = "classic"
    """LPMM知识库模式，可选：classic经典模式，agent 模式，结合最新的记忆一同使用"""

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


@dataclass
class JargonConfig(ConfigBase):
    """Jargon配置类"""

    all_global: bool = False
    """是否将所有新增的jargon项目默认为全局（is_global=True），chat_id记录第一次存储时的id"""

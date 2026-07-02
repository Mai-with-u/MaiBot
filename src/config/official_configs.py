from typing import List, Literal, Optional

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

OVERSIZED_IMAGE_HANDLE_METHOD_DESCRIPTIONS = {
    "compress": "压缩图片并继续处理",
    "discard": "丢弃超过最大大小的图片组件",
}

REPLY_TRIGGER_MODE_OPTION_DESCRIPTIONS = {
    "frequency": "按照新消息数量决定思考",
    "reply_necessity": "综合新消息数量、内容、过往发言决定思考",
}

REPLY_TRIGGER_MODE_OPTION_LABELS = {
    "frequency": "频率触发",
    "reply_necessity": "必要性触发",
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
    __ui_order__ = 10

    platform: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "平台",
                "en_US": "Platform",
                "ja_JP": "プラットフォーム",
            },
            "x-widget": "input",
            "x-icon": "wifi",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "bot-platform-account",
        },
    )
    """麦麦主账号所在的平台，例如 qq。"""

    qq_account: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "QQ账号",
                "en_US": "QQ account",
                "ja_JP": "QQアカウント",
            },
            "x-widget": "input",
            "x-icon": "user",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "bot-platform-account",
        },
    )
    """麦麦主账号 ID，用来识别哪些消息是麦麦自己发的。"""

    platforms: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "其他平台",
                "en_US": "Other platforms",
                "ja_JP": "他のプラットフォーム",
            },
            "x-widget": "custom",
            "x-icon": "layers",
        },
    )
    """其他平台账号，格式为 platform:账号；没有多平台时不用填。"""

    nickname: str = Field(
        default="麦麦",
        json_schema_extra={
            "label": {
                "zh_CN": "机器人昵称",
                "en_US": "Bot nickname",
                "ja_JP": "ボットのニックネーム",
            },
            "x-widget": "input",
            "x-icon": "user-circle",
        },
    )
    """麦麦显示和自称时使用的名字。"""

    alias_names: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "别名",
                "en_US": "Aliases",
                "ja_JP": "別名",
            },
            "x-widget": "custom",
            "x-icon": "tags",
            "advanced": True,
        },
    )
    """别人可能用来称呼麦麦的名字，用于辅助识别提及。"""


class PersonalityConfig(ConfigBase):
    """人格配置类"""

    __ui_parent__ = "bot"
    __ui_label__ = "人格"

    personality: str = Field(
        default="你是一个大二女大学生，现在正在上网和群友聊天。",
        json_schema_extra={
            "label": {
                "zh_CN": "人格设定",
                "en_US": "Personality",
                "ja_JP": "人格設定",
            },
            "x-widget": "textarea",
            "x-icon": "user-circle",
            "x-textarea-min-height": 40,
            "x-textarea-rows": 1,
            "x-description-display": "icon",
        },
    )
    """麦麦的人格和身份设定，建议简短描述她是谁、是什么性格。"""

    reply_style: str = Field(
        default="你的风格平淡简短。可以参考贴吧，知乎和微博的回复风格。不浮夸不长篇大论，不要过分修辞和复杂句。尽量回复的简短一些，平淡一些",
        json_schema_extra={
            "label": {
                "zh_CN": "表达风格",
                "en_US": "Reply style",
                "ja_JP": "返信スタイル",
            },
            "x-widget": "textarea",
            "x-icon": "message-square",
            "x-textarea-min-height": 40,
            "x-textarea-rows": 1,
            "x-description-display": "icon",
        },
    )
    """麦麦平时说话的风格，例如简短、温和、吐槽或正式。"""

    multiple_reply_style: list[str] = Field(
        default_factory=lambda: [
            "你的风格平淡但不失讽刺，很简短,很白话。可以参考贴吧，微博的回复风格。",
            "用1-2个字进行回复",
            "用1-2个符号进行回复",
            "言辭凝練古雅，穿插《論語》經句卻不晦澀，以文言短句為基，輔以淺白語意，持長者溫和風範，全用繁體字表達，具先秦儒者談吐韻致。",
            "带点翻译腔，但不要太长",
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "备用表达风格",
                "en_US": "Alternate reply styles",
                "ja_JP": "代替返信スタイル",
            },
            "advanced": True,
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """备用说话风格；触发后只影响本次回复。"""

    multiple_probability: float = Field(
        default=0,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "临时风格注入概率",
                "en_US": "Temporary style injection chance",
                "ja_JP": "一時スタイル注入確率",
            },
            "advanced": True,
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
        },
    )
    """随机启用备用风格的概率；0 表示不随机切换。"""


class ImageCacheCleanupConfig(ConfigBase):
    """图片缓存自动清理配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "trash-2",
            "label": {
                "zh_CN": "启用图片缓存自动清理",
                "en_US": "Enable image cache cleanup",
                "ja_JP": "画像キャッシュ自動クリーンアップを有効化",
            },
        },
    )
    """开启后会自动删除长期不用的图片缓存。"""

    check_interval_hours: float = Field(
        default=6.0,
        ge=1.0 / 60.0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
            "label": {
                "zh_CN": "清理检查间隔（小时）",
                "en_US": "Cleanup check interval (hours)",
                "ja_JP": "クリーンアップ確認間隔（時間）",
            },
        },
    )
    """每隔多少小时检查一次旧图片。"""

    image_file_retention_days: int = Field(
        default=14,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "calendar-days",
            "label": {
                "zh_CN": "图片文件保留天数",
                "en_US": "Image file retention days",
                "ja_JP": "画像ファイル保持日数",
            },
        },
    )
    """图片文件多久没被使用后可以删除。"""

    no_file_result_retention_days: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "database",
            "label": {
                "zh_CN": "图片理解保留天数",
                "en_US": "image recognition retention days",
                "ja_JP": "認識結果保持日数",
            },
        },
    )
    """图片文件删掉后，识别文字还能保留多久。"""


class VisualConfig(ConfigBase):
    """视觉配置类"""

    __ui_label__ = "视觉"
    __ui_order__ = 60

    planner_mode: Literal["text", "multimodal", "auto"] = Field(
        default="auto",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "git-branch",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-option-descriptions": VISUAL_MODE_OPTION_DESCRIPTIONS,
            "x-row": "visual-modes",
            "label": {
                "zh_CN": "规划阶段视觉模式",
                "en_US": "Planner vision mode",
                "ja_JP": "プランナー視覚モード",
            },
        },
    )
    """控制规划阶段是否把图片内容直接发送给 planner 模型。auto 会根据模型是否支持视觉自动选择；text 始终只使用文字和图片识别结果；multimodal 会强制使用多模态输入。"""

    replyer_mode: Literal["text", "multimodal", "auto"] = Field(
        default="auto",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "git-branch",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-option-descriptions": VISUAL_MODE_OPTION_DESCRIPTIONS,
            "x-row": "visual-modes",
            "label": {
                "zh_CN": "回复生成视觉模式",
                "en_US": "Replyer vision mode",
                "ja_JP": "返信生成視覚モード",
            },
        },
    )
    """控制回复生成阶段是否把图片内容直接发送给 replyer 模型。auto 会根据模型是否支持视觉自动选择；text 始终只使用文字和图片识别结果；multimodal 会强制使用多模态输入。"""

    max_image_num: int = Field(
        default=128,
        ge=0,
        json_schema_extra={
            "advanced": True,
            "x-widget": "input",
            "x-icon": "images",
            "label": {
                "zh_CN": "多模态最大图片数",
                "en_US": "Max multimodal images",
                "ja_JP": "マルチモーダル最大画像数",
            },
        },
    )
    """一次多模态请求最多带多少张图，太大可能更慢更贵。"""

    wait_image_recognize_max_time: float = Field(
        default=10,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "timer",
            "x-layout": "inline-right",
            "x-input-width": "7.5rem",
            "label": {
                "zh_CN": "识图最长等待时间",
                "en_US": "Max image recognition wait time",
                "ja_JP": "画像認識の最長待機時間",
            },
        },
    )
    """等图片识别完成的最长秒数；0 表示不等待。"""

    handle_oversized_images: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "image",
            "x-layout": "inline-right",
            "x-row": "visual-image-compression",
            "label": {
                "zh_CN": "是否处理过大图片",
                "en_US": "Handle oversized images",
                "ja_JP": "是否過大画像を処理",
            },
        },
    )
    """收到太大的图片时，是否自动压缩或丢弃。"""

    max_image_size_mb: float = Field(
        default=30.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "image",
            "x-layout": "inline-right",
            "x-input-width": "min(100%, 5.5rem)",
            "x-row": "visual-image-compression",
            "label": {
                "zh_CN": "最大图片大小(MB)",
                "en_US": "Max image size (MB)",
                "ja_JP": "最大画像サイズ(MB)",
            },
        },
    )
    """超过这个大小的图片会按过大图片处理方法处理；0 表示不限。"""

    oversized_image_handle_method: Literal["compress", "discard"] = Field(
        default="compress",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "minimize-2",
            "x-layout": "inline-right",
            "x-input-width": "min(100%, 8.5rem)",
            "x-row": "visual-image-compression",
            "x-option-descriptions": OVERSIZED_IMAGE_HANDLE_METHOD_DESCRIPTIONS,
            "label": {
                "zh_CN": "过大图片处理方法",
                "en_US": "Oversized image handling",
                "ja_JP": "過大画像の処理方法",
            },
        },
    )
    """大图的处理方式：压缩后继续用，或直接丢弃。"""

    image_cache_cleanup: ImageCacheCleanupConfig = Field(default_factory=ImageCacheCleanupConfig)
    """定期清理旧图片缓存，减少磁盘占用。"""


class TalkRulesItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "平台",
                "en_US": "Platform",
                "ja_JP": "プラットフォーム",
            },
        },
    )
    """规则作用的平台；留空表示不限定平台，* 表示任意平台。"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流 ID",
                "en_US": "Chat stream ID",
                "ja_JP": "チャットストリーム ID",
            },
        },
    )
    """规则作用的群号或用户 ID；留空表示不限定聊天，* 表示任意聊天。"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天类型",
                "en_US": "Chat type",
                "ja_JP": "チャット種別",
            },
            "x-widget": "select",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """规则作用于群聊还是私聊。"""

    time: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "时间段",
                "en_US": "Time range",
                "ja_JP": "時間帯",
            },
            "x-widget": "talk-time",
        },
    )
    """规则生效时间；留空为兜底，* 为全天，也可填 23:00-02:00。"""

    value: float = Field(
        default=0.5,
        json_schema_extra={
            "label": {
                "zh_CN": "发言频率",
                "en_US": "Talk frequency",
                "ja_JP": "発言頻度",
            },
        },
    )
    """该规则下的发言频率；0 更安静，1 按正常频率。"""


class ChatReplyTimingConfig(ConfigBase):
    """聊天回复时机与频率配置类"""

    __ui_label__ = "什么时候发言"
    __ui_icon__ = "activity"

    talk_value: float = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "群聊频率",
                "en_US": "Group talk frequency",
                "ja_JP": "グループ発言頻度",
            },
            "x-widget": "slider",
            "x-icon": "message-circle",
            "x-row": "talk-values",
            "step": 0.001,
        },
    )
    """群聊里麦麦主动说话的频率；越小越安静。"""

    private_talk_value: float = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "私聊频率",
                "en_US": "Private talk frequency",
                "ja_JP": "個別チャット発言頻度",
            },
            "x-widget": "slider",
            "x-icon": "message-circle",
            "x-row": "talk-values",
            "step": 0.001,
        },
    )
    """私聊里麦麦主动说话的频率；越小越安静。"""

    mentioned_bot_reply: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "提及必回复",
                "en_US": "Always reply when mentioned",
                "ja_JP": "メンション時に必ず返信",
            },
            "x-widget": "switch",
            "x-icon": "at-sign",
            "x-row": "reply-switches",
        },
    )
    """开启后，只要消息提到麦麦名字就更容易回复。"""

    inevitable_at_reply: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "At 必回复",
                "en_US": "Always reply to @",
                "ja_JP": "@ に必ず返信",
            },
            "x-widget": "switch",
            "x-icon": "at-sign",
            "x-row": "reply-switches",
        },
    )
    """开启后，被 @ 时会尽量回复。"""

    reply_trigger_mode: Literal["frequency", "reply_necessity"] = Field(
        default="frequency",
        json_schema_extra={
            "label": {
                "zh_CN": "回复触发模式",
                "en_US": "Reply trigger mode",
                "ja_JP": "返信トリガーモード",
            },
            "x-widget": "select",
            "x-icon": "activity",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-option-labels": REPLY_TRIGGER_MODE_OPTION_LABELS,
            "x-option-descriptions": REPLY_TRIGGER_MODE_OPTION_DESCRIPTIONS,
        },
    )
    """控制新消息何时进入 Planner。"""

    planner_interrupt_max_consecutive_count: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "规划器连续打断上限",
                "en_US": "Planner consecutive interrupt limit",
                "ja_JP": "プランナー連続中断上限",
            },
            "x-widget": "input",
            "x-icon": "pause-circle",
            "advanced": True,
        },
    )
    """思考时来了新消息，最多重新思考多少次。"""

    max_consecutive_wait_count: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "连续 wait 上限",
                "en_US": "Max consecutive wait",
                "ja_JP": "連続 wait 上限",
            },
            "x-widget": "input",
            "x-icon": "timer-reset",
        },
    )
    """Planner 最多连续调用 wait 多少次；达到上限后 wait 工具会拒绝继续进入等待。"""

    no_action_backoff_base_seconds: float = Field(
        default=15,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "空闲退避基准",
                "en_US": "Idle backoff base",
                "ja_JP": "アイドルバックオフ基準",
            },
            "x-widget": "input",
            "x-icon": "timer",
            "x-layout": "inline-right",
            "x-input-width": "7.5rem",
            "x-description-display": "icon",
            "advanced": False,
        },
    )
    """连续决定不回复后，下一次检查前先等多久。"""

    no_action_backoff_cap_seconds: float = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "空闲退避上限",
                "en_US": "Idle backoff cap",
                "ja_JP": "アイドルバックオフ上限",
            },
            "x-widget": "input",
            "x-icon": "timer-reset",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """不回复退避等待的最长时间。"""

    no_action_backoff_start_count: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "空闲退避起点",
                "en_US": "Idle backoff start",
                "ja_JP": "アイドルバックオフ開始",
            },
            "x-widget": "input",
            "x-icon": "list-start",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """连续几次不回复后开始放慢检查。"""

    no_action_backoff_bypass_pending_count: int = Field(
        default=6,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "空闲退避绕过消息数",
                "en_US": "Idle backoff bypass messages",
                "ja_JP": "アイドルバックオフ迂回メッセージ数",
            },
            "x-widget": "input",
            "x-icon": "message-square-more",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """等待期间新消息达到多少条就立刻重新处理；0 表示不按条数打断等待。"""

    enable_talk_value_rules: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用动态发言频率规则",
                "en_US": "Enable dynamic talk frequency rules",
                "ja_JP": "動的な発言頻度ルールを有効化",
            },
            "x-widget": "switch",
            "x-icon": "settings",
        },
    )
    """开启后，可以按聊天或时间段单独调整发言频率。"""

    talk_value_rules: list[TalkRulesItem] = Field(
        default_factory=lambda: [
            TalkRulesItem(platform="", item_id="", rule_type="group", time="00:00-08:59", value=0.8),
            TalkRulesItem(platform="", item_id="", rule_type="group", time="09:00-18:59", value=1.0),
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "动态发言频率规则",
                "en_US": "Dynamic talk frequency rules",
                "ja_JP": "動的な発言頻度ルール",
            },
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """
    _wrap_动态发言频率规则；可让麦麦在某些群、私聊或时段更活跃或更安静。
    """


class ChatReplyStyleConfig(ConfigBase):
    """聊天回复方式配置类"""

    __ui_label__ = "如何发言"
    __ui_icon__ = "message-square"

    enable_reply_quote: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用引用回复",
                "en_US": "Enable quoted replies",
                "ja_JP": "引用返信を有効化",
            },
            "x-widget": "switch",
            "x-icon": "quote",
            "advanced": True,
        },
    )
    """回复时是否可以引用上一条或相关消息。"""

    group_chat_prompt: str = Field(
        default=(
            "你正在qq群里聊天，下面是群里正在聊的内容，聊天中包含文字，图片和表情包等消息。\n"
            "回复尽量简短一些。最好一次对一个话题进行回复，但必须考虑不同群友发言之间的交互，免得啰嗦或者回复内容太乱。请注意把握聊天内容。\n"
            "不要总是提及自己的身份背景，根据聊天内容自由发挥，但是要日常不浮夸，不要刻意找话题。\n"
            "不用刻意回复其他人发送的表情包，只要关注表情包表达的含义。你可以适当发送表情包表达情绪。控制回复的频率，不要每个人的消息都回复，优先回复你感兴趣的或者主动提及你的，适当回复其他话题。\n"
        ),
        json_schema_extra={
            "label": {
                "zh_CN": "群聊提示词",
                "en_US": "Group chat prompt",
                "ja_JP": "グループチャットプロンプト",
            },
            "x-widget": "textarea",
            "x-icon": "users",
        },
    )
    """_wrap_群聊通用提示词，告诉麦麦群聊中该怎么说话。"""

    private_chat_prompts: str = Field(
        default=(
            "你正在聊天，下面是正在聊的内容，其中包含聊天记录和聊天中的图片。\n"
            "回复尽量简短一些。请注意把握聊天内容。\n"
            "请考虑对方的发言频率，想法，思考自己何时回复以及回复内容。\n"
        ),
        json_schema_extra={
            "label": {
                "zh_CN": "私聊提示词",
                "en_US": "Private chat prompt",
                "ja_JP": "個別チャットプロンプト",
            },
            "x-widget": "textarea",
            "x-icon": "user",
        },
    )
    """_wrap_私聊通用提示词，告诉麦麦私聊中该怎么说话。"""

    chat_prompts: list["ExtraPromptItem"] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "额外 Prompt",
                "en_US": "Extra prompts",
                "ja_JP": "追加プロンプト",
            },
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """给指定群聊或私聊额外补充聊天要求；有特殊群规或语气要求时再加。"""


class ChatConfig(ConfigBase):
    """聊天配置类"""

    __ui_label__ = "聊天"
    __ui_order__ = 20
    __ui_use_subtabs__ = True
    __ui_root_sub_label__ = "基础设置"

    max_context_size: int = Field(
        default=40,
        json_schema_extra={
            "label": {
                "zh_CN": "群聊上下文",
                "en_US": "Group context size",
                "ja_JP": "グループ文脈数",
            },
            "x-widget": "input",
            "x-icon": "layers",
            "x-layout": "inline-right",
            "x-input-width": "6.5rem",
            "x-row": "chat-context-controls",
        },
    )
    """群聊回复时参考的最近消息数量；越大越懂上下文，也更耗模型。"""

    max_private_context_size: int = Field(
        default=60,
        json_schema_extra={
            "label": {
                "zh_CN": "私聊上下文",
                "en_US": "Private context size",
                "ja_JP": "個別チャット文脈数",
            },
            "x-widget": "input",
            "x-icon": "layers",
            "x-layout": "inline-right",
            "x-input-width": "6.5rem",
            "x-row": "chat-context-controls",
        },
    )
    """私聊回复时参考的最近消息数量。"""

    enable_context_optimization: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "优化上下文",
                "en_US": "Optimize context",
                "ja_JP": "コンテキスト最適化",
            },
            "x-widget": "switch",
            "x-icon": "scissors",
            "x-row": "chat-context-controls",
        },
    )
    """压缩部分上下文，减少模型消耗；一般建议开启。"""

    mid_term_memory: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天回想",
                "en_US": "Chat recall",
                "ja_JP": "チャット回想",
            },
            "x-widget": "switch",
            "x-icon": "archive",
            "x-row": "chat-recall-controls",
        },
    )
    """打开后会主动召回最近聊天发生的事情。"""

    mid_term_memory_lenth: int = Field(
        default=10,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天回想保留数",
                "en_US": "Chat recall limit",
                "ja_JP": "チャット回想保持数",
            },
            "x-widget": "input",
            "x-icon": "archive",
            "x-layout": "inline-right",
            "x-input-width": "6.5rem",
            "x-row": "chat-recall-controls",
        },
    )
    """最多保留多少条聊天回想；设为 0 表示不保留。"""

    self_message_special_mark: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自身消息特殊标注",
                "en_US": "Special mark for self messages",
                "ja_JP": "自分のメッセージ特別マーク",
            },
            "x-widget": "switch",
            "x-icon": "badge-check",
            "x-row": "self-message-mark",
        },
    )
    """加强标记麦麦自己的消息，减少把自己当成别人的情况。"""

    reply_timing: ChatReplyTimingConfig = Field(default_factory=ChatReplyTimingConfig)
    """什么时候回复、回复频率与等待退避配置。"""

    reply_style: ChatReplyStyleConfig = Field(default_factory=ChatReplyStyleConfig)
    """如何回复、引用回复与聊天 Prompt 配置。"""


class ExperimentalConfig(ConfigBase):
    """实验性功能配置类"""

    __ui_label__ = "实验性功能"
    __ui_advanced__ = True
    __ui_order__ = 30

    enable_behavior_learning: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用行为学习",
                "en_US": "Enable behavior learning",
                "ja_JP": "行動学習を有効化",
            },
            "x-widget": "switch",
            "x-icon": "brain-circuit",
        },
    )
    """让麦麦从聊天中学习什么时候该怎么回应的经验。"""

    enable_rich_reply: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "丰富回复能力",
                "en_US": "Rich reply ability",
                "ja_JP": "豊かな返信能力",
            },
            "x-widget": "switch",
            "x-icon": "sparkles",
        },
    )
    """开启后，replyer 生成文本后会由检查器决定是否插入图片、表情包或 at。"""

    behavior_learning_list: list["LearningItem"] = Field(
        default_factory=lambda: [
            LearningItem(
                platform="",
                item_id="",
                type="group",
                use=True,
                learn=True,
            )
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "行为学习配置",
                "en_US": "Behavior learning settings",
                "ja_JP": "行動学習設定",
            },
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """配置哪些聊天会学习和使用行为经验；默认规则不够时再单独添加。"""

    behavior_groups: list["ChatStreamGroup"] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "行为互通组",
                "en_US": "Behavior sharing groups",
                "ja_JP": "行動共有グループ",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_让多个群聊或私聊共享学到的行为经验。"""

    focus_mode: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "Focus 模式",
                "en_US": "Focus mode",
                "ja_JP": "Focus モード",
            },
            "x-widget": "switch",
            "x-icon": "target",
        },
    )
    """让麦麦同一时间只专注一个聊天流，适合直播或高强度聊天场景。"""

    focus_on_private: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "私聊启用 Focus",
                "en_US": "Focus private chats",
                "ja_JP": "私聊で Focus を有効化",
            },
            "x-widget": "switch",
            "x-icon": "message-circle",
        },
    )
    """Focus 模式是否也作用于私聊。"""

    focus_chat_whitelist: list["TargetItem"] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "Focus 白名单",
                "en_US": "Focus whitelist",
                "ja_JP": "Focus ホワイトリスト",
            },
            "x-widget": "custom",
            "x-icon": "list-checks",
        },
    )
    """_wrap_Focus 白名单。配置后只有命中的群聊或私聊会进入 Focus；留空表示所有符合聊天类型开关的聊天都可进入 Focus。"""

    focus_groups: list["ChatStreamGroup"] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "Focus 互通组",
                "en_US": "Focus sharing groups",
                "ja_JP": "Focus 共有グループ",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_把聊天流分组后，同组共享 Focus，不同组互不抢占。"""

    focus_cool_time: int = Field(
        default=120,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "Focus 冷却时间",
                "en_US": "Focus cool time",
                "ja_JP": "Focus クールタイム",
            },
            "x-widget": "input",
            "x-icon": "timer",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-row": "focus-cool-time",
        },
    )
    """当前关注的聊天多久没继续处理后，允许被其他聊天唤醒。"""


class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    __ui_label__ = "消息接收"
    __ui_advanced__ = True
    __ui_order__ = 70

    image_parse_threshold: int = Field(
        default=5,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "image",
            "advanced": True,
        },
    )
    """
    单条消息图片数不超过这个值时才识图，避免图片太多拖慢处理。
    """

    ban_words: set[str] = Field(
        default_factory=lambda: set(),
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "ban",
        },
    )
    """包含这些词的消息会被过滤，不进入麦麦处理。"""

    ban_msgs_regex: set[str] = Field(
        default_factory=lambda: set(),
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "regex",
        },
    )
    """用正则过滤消息；适合更复杂的过滤规则。"""

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
            "label": {
                "zh_CN": "平台",
                "en_US": "Platform",
                "ja_JP": "プラットフォーム",
            },
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """要单独配置的平台；和聊天流 ID 都留空表示全局默认，仅平台有值且聊天流 ID 留空表示平台兜底。"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流 ID",
                "en_US": "Chat stream ID",
                "ja_JP": "チャットストリーム ID",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """用户/群 ID；留空时和平台字段共同决定全局默认或平台兜底，* 表示任意聊天流。"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天类型",
                "en_US": "Chat type",
                "ja_JP": "チャット種別",
            },
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """聊天流类型，group（群聊）或private（私聊）"""


class AMemorixIntegrationConfig(ConfigBase):
    """记忆在聊天中的使用"""

    __ui_parent__ = "a_memorix"

    enable_memory_query_tool: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆检索",
                "en_US": "Enable memory search",
                "ja_JP": "記憶検索を有効化",
            },
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """是否允许麦麦在聊天时查询长期记忆"""

    memory_query_default_limit: int = Field(
        default=5,
        ge=1,
        le=20,
        json_schema_extra={
            "label": {
                "zh_CN": "默认检索条数",
                "en_US": "Default memory result count",
                "ja_JP": "既定の記憶検索件数",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """每次默认从长期记忆中取回多少条结果"""

    enable_person_profile_query_tool: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用人物画像查询",
                "en_US": "Enable profile search",
                "ja_JP": "人物プロファイル検索を有効化",
            },
            "x-widget": "switch",
            "x-icon": "user-round-search",
        },
    )
    """是否允许麦麦查询人物画像记忆"""

    enable_person_profile_injection: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动注入人物画像",
                "en_US": "Inject person profiles",
                "ja_JP": "人物プロファイルを自動注入",
            },
            "x-widget": "switch",
            "x-icon": "user-round-check",
        },
    )
    """是否在 Maisaka Planner 调用前自动注入当前对象相关的人物画像"""

    person_profile_injection_max_profiles: int = Field(
        default=3,
        ge=1,
        le=5,
        json_schema_extra={
            "label": {
                "zh_CN": "注入画像上限",
                "en_US": "Injected profile limit",
                "ja_JP": "注入プロファイル上限",
            },
            "x-widget": "input",
            "x-icon": "users",
        },
    )
    """每轮自动注入的人物画像数量上限"""

    heuristic_memory_recall_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启发式拉起记忆",
                "en_US": "Heuristic memory recall",
                "ja_JP": "ヒューリスティック記憶呼び出し",
            },
            "x-widget": "switch",
            "x-icon": "sparkles",
        },
    )
    """是否根据当前聊天印象自然拉起长期记忆"""

    heuristic_memory_cross_chat_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "允许跨聊天流拉起",
                "en_US": "Allow cross-chat recall",
                "ja_JP": "チャット横断呼び出しを許可",
            },
            "x-widget": "switch",
            "x-icon": "shuffle",
        },
    )
    """是否允许启发式记忆从其他聊天流召回候选"""

    heuristic_memory_recall_window_size: int = Field(
        default=20,
        ge=1,
        le=200,
        json_schema_extra={
            "label": {
                "zh_CN": "印象窗口消息数",
                "en_US": "Impression window size",
                "ja_JP": "印象ウィンドウ件数",
            },
            "x-widget": "input",
            "x-icon": "rows-3",
            "advanced": True,
        },
    )
    """生成当前聊天印象时使用的最近消息数量"""

    heuristic_memory_recall_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起记忆条数",
                "en_US": "Heuristic memory limit",
                "ja_JP": "自然呼び出し記憶数",
            },
            "x-widget": "input",
            "x-icon": "list",
            "advanced": True,
        },
    )
    """每轮自然拉起的长期记忆数量上限"""

    heuristic_memory_recall_max_chars: int = Field(
        default=900,
        ge=100,
        le=4000,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起文本上限",
                "en_US": "Heuristic memory text limit",
                "ja_JP": "自然呼び出し文字数上限",
            },
            "x-widget": "input",
            "x-icon": "text-cursor-input",
            "advanced": True,
        },
    )
    """自然拉起记忆注入文本的最大字符数"""

    heuristic_memory_recall_min_interval_seconds: int = Field(
        default=180,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起冷却秒数",
                "en_US": "Heuristic recall cooldown",
                "ja_JP": "自然呼び出しクールダウン",
            },
            "x-widget": "input",
            "x-icon": "timer",
            "advanced": True,
        },
    )
    """同一聊天流两次自然拉起的最小间隔秒数"""

    heuristic_memory_recall_min_new_messages: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起新增消息阈值",
                "en_US": "Heuristic recall message threshold",
                "ja_JP": "自然呼び出し新規メッセージ閾値",
            },
            "x-widget": "input",
            "x-icon": "messages-square",
            "advanced": True,
        },
    )
    """两次自然拉起之间至少需要新增的当前聊天流消息数"""

    heuristic_memory_recall_cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "自然拉起缓存秒数",
                "en_US": "Heuristic recall cache TTL",
                "ja_JP": "自然呼び出しキャッシュ秒数",
            },
            "x-widget": "input",
            "x-icon": "clock-4",
            "advanced": True,
        },
    )
    """同一聊天流自然拉起结果的运行时缓存时间"""

    heuristic_memory_group_to_private_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "私聊拉起群聊记忆",
                "en_US": "Group memories in private chats",
                "ja_JP": "個人チャットでグループ記憶を使う",
            },
            "x-widget": "switch",
            "x-icon": "users-round",
            "advanced": True,
        },
    )
    """私聊中是否允许自然拉起群聊记忆"""

    heuristic_memory_private_to_group_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "群聊拉起私聊记忆",
                "en_US": "Private memories in group chats",
                "ja_JP": "グループチャットで個人記憶を使う",
            },
            "x-widget": "switch",
            "x-icon": "message-circle",
            "advanced": True,
        },
    )
    """群聊中是否允许自然拉起私聊记忆"""

    person_fact_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "写回人物事实",
                "en_US": "Write back person facts",
                "ja_JP": "人物事実を書き戻す",
            },
            "x-widget": "switch",
            "x-icon": "user-round-pen",
        },
    )
    """是否在发送回复后自动提取并写回人物事实到长期记忆"""

    chat_summary_writeback_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "写回聊天摘要",
                "en_US": "Write back chat summaries",
                "ja_JP": "チャット要約を書き戻す",
            },
            "x-widget": "switch",
            "x-icon": "scroll-text",
        },
    )
    """是否在 Maisaka 聊天过程中按消息窗口自动写回聊天摘要到长期记忆"""

    chat_summary_writeback_message_threshold: int = Field(
        default=36,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "messages-square",
            "advanced": True,
        },
    )
    """自动写回聊天摘要的消息窗口阈值"""

    chat_summary_writeback_context_length: int = Field(
        default=36,
        ge=1,
        le=500,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "rows-3",
            "advanced": True,
        },
    )
    """自动写回聊天摘要时，从聊天流中回看的消息条数"""

    fuzzy_modify_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "wand-sparkles",
            "advanced": True,
        },
    )
    """是否启用自然语言记忆修正的后台接口"""

    fuzzy_modify_auto_execute_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "badge-check",
            "advanced": True,
        },
    )
    """是否允许高置信记忆修正跳过人工确认自动执行"""

    fuzzy_modify_confirm_threshold: float = Field(
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
    """记忆修正建议进入自动确认判定时使用的置信度阈值"""

    fuzzy_modify_candidate_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "list-filter",
            "advanced": True,
        },
    )
    """每次记忆修正交给 LLM 的候选记忆上限"""

    fuzzy_modify_max_targets: int = Field(
        default=5,
        ge=1,
        le=20,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "crosshair",
            "advanced": True,
        },
    )
    """单个记忆修正计划允许标记失效的旧记忆上限"""

    fuzzy_modify_allow_global_scope: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "globe-2",
            "advanced": True,
        },
    )
    """未指定聊天流时，是否允许在全局记忆范围内做记忆修正候选检索"""

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
        if not 0 <= self.fuzzy_modify_confirm_threshold <= 1:
            raise ValueError(
                "fuzzy_modify_confirm_threshold 必须在 [0, 1] 之间，"
                f"当前值: {self.fuzzy_modify_confirm_threshold}"
            )
        if self.fuzzy_modify_candidate_limit < 1:
            raise ValueError(
                "fuzzy_modify_candidate_limit 必须至少为1，"
                f"当前值: {self.fuzzy_modify_candidate_limit}"
            )
        if self.fuzzy_modify_max_targets < 1:
            raise ValueError(
                f"fuzzy_modify_max_targets 必须至少为1，当前值: {self.fuzzy_modify_max_targets}"
            )
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
    """记忆系统"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆",
                "en_US": "Enable memory",
                "ja_JP": "記憶を有効化",
            },
        },
    )
    """是否启用长期记忆系统"""


class AMemorixStorageConfig(ConfigBase):
    """A_Memorix 存储位置"""

    data_dir: str = Field(
        default="data/a-memorix",
        json_schema_extra={
            "label": {
                "zh_CN": "数据目录",
                "en_US": "Data directory",
                "ja_JP": "データディレクトリ",
            },
        },
    )
    """数据目录"""


class AMemorixEmbeddingFallbackConfig(ConfigBase):
    """A_Memorix Embedding 回退"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回退",
                "en_US": "Enable fallback",
                "ja_JP": "フォールバックを有効化",
            },
        },
    )
    """是否启用回退机制"""

    probe_interval_seconds: int = Field(
        default=180,
        ge=10,
        json_schema_extra={
            "label": {
                "zh_CN": "探测间隔",
                "en_US": "Probe interval",
                "ja_JP": "プローブ間隔",
            },
        },
    )
    """探测间隔秒数"""

    allow_metadata_only_write: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "允许仅写元数据",
                "en_US": "Allow metadata-only writes",
                "ja_JP": "メタデータのみの書き込みを許可",
            },
        },
    )
    """是否允许仅写入元数据"""


class AMemorixParagraphVectorBackfillConfig(ConfigBase):
    """A_Memorix 段落向量回填"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回填",
                "en_US": "Enable backfill",
                "ja_JP": "バックフィルを有効化",
            },
        },
    )
    """是否启用回填任务"""

    interval_seconds: int = Field(
        default=60,
        ge=5,
        json_schema_extra={
            "label": {
                "zh_CN": "回填间隔",
                "en_US": "Backfill interval",
                "ja_JP": "バックフィル間隔",
            },
        },
    )
    """回填轮询间隔"""

    batch_size: int = Field(
        default=64,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "批量大小",
                "en_US": "Batch size",
                "ja_JP": "バッチサイズ",
            },
        },
    )
    """单批回填数量"""

    max_retry: int = Field(
        default=5,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "最大重试",
                "en_US": "Max retries",
                "ja_JP": "最大リトライ回数",
            },
        },
    )
    """最大重试次数"""


class AMemorixEmbeddingConfig(ConfigBase):
    """记忆向量化配置"""

    model_name: str = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "向量化模型",
                "en_US": "Embedding model",
                "ja_JP": "Embedding モデル",
            },
        },
    )
    """用于把记忆内容转换成向量的模型，auto 表示自动选择"""

    dimension: int = Field(
        default=1024,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "向量维度",
                "en_US": "Vector dimension",
                "ja_JP": "ベクトル次元",
            },
        },
    )
    """记忆向量的维度，需要与向量化模型保持一致"""

    dimension_request_mode: Literal["explicit", "always", "never"] = Field(
        default="explicit",
        json_schema_extra={
            "label": {
                "zh_CN": "维度请求模式",
                "en_US": "Dimension request mode",
                "ja_JP": "次元リクエストモード",
            },
        },
    )
    """是否在 embedding 请求中携带维度参数：explicit 仅显式指定时携带，always 总是携带，never 不携带"""

    batch_size: int = Field(
        default=32,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单批数量",
                "en_US": "Batch size",
                "ja_JP": "バッチサイズ",
            },
        },
    )
    """每次向量化请求处理的记忆条数"""

    max_concurrent: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大并发数",
                "en_US": "Max concurrency",
                "ja_JP": "最大同時実行数",
            },
        },
    )
    """同时进行的向量化请求数量"""

    enable_cache: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用向量缓存",
                "en_US": "Enable cache",
                "ja_JP": "キャッシュを有効化",
            },
        },
    )
    """是否缓存向量化结果"""

    quantization_type: Literal["int8"] = Field(
        default="int8",
        json_schema_extra={
            "label": {
                "zh_CN": "量化方式",
                "en_US": "Quantization type",
                "ja_JP": "量子化方式",
            },
        },
    )
    """向量压缩方式，当前仅支持 int8(SQ8)"""

    fallback: AMemorixEmbeddingFallbackConfig = Field(
        default_factory=AMemorixEmbeddingFallbackConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Embedding 回退",
                "en_US": "Embedding fallback",
                "ja_JP": "Embedding フォールバック",
            },
        },
    )
    """Embedding 回退配置"""

    paragraph_vector_backfill: AMemorixParagraphVectorBackfillConfig = Field(
        default_factory=AMemorixParagraphVectorBackfillConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "段落向量回填",
                "en_US": "Paragraph vector backfill",
                "ja_JP": "段落ベクトルバックフィル",
            },
        },
    )
    """段落向量回填配置"""


class AMemorixSparseRetrievalConfig(ConfigBase):
    """A_Memorix 稀疏检索配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用稀疏检索",
                "en_US": "Enable sparse retrieval",
                "ja_JP": "疎検索を有効化",
            },
        },
    )
    """是否启用稀疏检索"""

    backend: Literal["fts5"] = Field(
        default="fts5",
        json_schema_extra={
            "label": {
                "zh_CN": "检索后端",
                "en_US": "Retrieval backend",
                "ja_JP": "検索バックエンド",
            },
        },
    )
    """稀疏检索后端"""

    mode: Literal["auto", "fallback_only", "hybrid"] = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "检索模式",
                "en_US": "Retrieval mode",
                "ja_JP": "検索モード",
            },
        },
    )
    """稀疏检索模式"""

    tokenizer_mode: Literal["jieba", "mixed", "char_2gram"] = Field(
        default="jieba",
        json_schema_extra={
            "label": {
                "zh_CN": "分词模式",
                "en_US": "Tokenizer mode",
                "ja_JP": "トークナイザーモード",
            },
        },
    )
    """分词模式"""

    candidate_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落候选数",
                "en_US": "Paragraph candidates",
                "ja_JP": "段落候補数",
            },
        },
    )
    """段落候选数"""

    relation_candidate_k: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系候选数",
                "en_US": "Relation candidates",
                "ja_JP": "関係候補数",
            },
        },
    )
    """关系候选数"""


class AMemorixSmartFallbackConfig(ConfigBase):
    """A_Memorix 智能兜底检索配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用智能兜底",
                "en_US": "Enable smart fallback",
                "ja_JP": "スマートフォールバックを有効化",
            },
        },
    )
    """是否启用智能兜底检索"""


class AMemorixRetrievalSearchConfig(ConfigBase):
    """A_Memorix 搜索后处理配置"""

    smart_fallback: AMemorixSmartFallbackConfig = Field(
        default_factory=AMemorixSmartFallbackConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "智能兜底",
                "en_US": "Smart fallback",
                "ja_JP": "スマートフォールバック",
            },
        },
    )
    """智能兜底检索配置"""


class AMemorixFusionRetrievalConfig(ConfigBase):
    """A_Memorix 检索融合配置"""

    method: Literal["weighted_rrf", "alpha_legacy"] = Field(
        default="weighted_rrf",
        json_schema_extra={
            "label": {
                "zh_CN": "融合方法",
                "en_US": "Fusion method",
                "ja_JP": "融合方式",
            },
        },
    )
    """检索融合方法"""

    rrf_k: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "RRF K",
                "en_US": "RRF K",
                "ja_JP": "RRF K",
            },
        },
    )
    """RRF 融合参数"""

    vector_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "向量权重",
                "en_US": "Vector weight",
                "ja_JP": "ベクトル重み",
            },
        },
    )
    """向量检索权重"""

    bm25_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "BM25 权重",
                "en_US": "BM25 weight",
                "ja_JP": "BM25 重み",
            },
        },
    )
    """BM25 稀疏检索权重"""


class AMemorixRelationVectorizationConfig(ConfigBase):
    """A_Memorix 关系向量化配置"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用关系向量",
                "en_US": "Enable relation vectors",
                "ja_JP": "関係ベクトルを有効化",
            },
        },
    )
    """是否启用关系向量化"""

    backfill_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用关系向量回填",
                "en_US": "Enable relation vector backfill",
                "ja_JP": "関係ベクトルバックフィルを有効化",
            },
        },
    )
    """是否启用关系向量回填"""

    write_on_import: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "导入时写入关系向量",
                "en_US": "Write relation vectors on import",
                "ja_JP": "インポート時に関係ベクトルを書き込む",
            },
        },
    )
    """导入时是否写入关系向量"""


class AMemorixRelationIntentVectorPoolConfig(ConfigBase):
    """A_Memorix 关系意图下的双向量池配置"""

    graph_top_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图图谱候选数",
                "en_US": "Relation-intent graph candidates",
                "ja_JP": "関係意図グラフ候補数",
            },
        },
    )
    """关系意图命中时的图谱池候选数"""

    semantic_weight: float = Field(
        default=0.45,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图语义权重",
                "en_US": "Relation-intent semantic weight",
                "ja_JP": "関係意図セマンティック重み",
            },
        },
    )
    """关系意图命中时的段落语义权重"""

    sparse_weight: float = Field(
        default=0.15,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图稀疏权重",
                "en_US": "Relation-intent sparse weight",
                "ja_JP": "関係意図疎検索重み",
            },
        },
    )
    """关系意图命中时的稀疏检索权重"""

    graph_weight: float = Field(
        default=0.40,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图图谱权重",
                "en_US": "Relation-intent graph weight",
                "ja_JP": "関係意図グラフ重み",
            },
        },
    )
    """关系意图命中时的图谱证据权重"""

    return_relation_items: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "返回独立关系项",
                "en_US": "Return relation items",
                "ja_JP": "独立した関係項目を返す",
            },
        },
    )
    """关系意图命中时是否返回独立关系结果"""


class AMemorixVectorPoolsConfig(ConfigBase):
    """A_Memorix 双向量池检索配置"""

    mode: Literal["single", "dual"] = Field(
        default="dual",
        json_schema_extra={
            "label": {
                "zh_CN": "向量池模式",
                "en_US": "Vector pool mode",
                "ja_JP": "ベクトルプールモード",
            },
        },
    )
    """向量池模式"""

    paragraph_top_k: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落池候选数",
                "en_US": "Paragraph pool candidates",
                "ja_JP": "段落プール候補数",
            },
        },
    )
    """段落向量池候选数"""

    graph_top_k: int = Field(
        default=40,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱池候选数",
                "en_US": "Graph pool candidates",
                "ja_JP": "グラフプール候補数",
            },
        },
    )
    """图谱向量池候选数"""

    graph_expand_paragraph_k: int = Field(
        default=80,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱展开段落上限",
                "en_US": "Graph expanded paragraph limit",
                "ja_JP": "グラフ展開段落上限",
            },
        },
    )
    """图谱证据展开段落上限"""

    relation_expand_per_hit: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "每个关系展开段落数",
                "en_US": "Paragraphs per relation hit",
                "ja_JP": "関係ヒットごとの段落数",
            },
        },
    )
    """每个关系命中最多展开的段落数"""

    entity_expand_per_hit: int = Field(
        default=8,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "每个实体展开段落数",
                "en_US": "Paragraphs per entity hit",
                "ja_JP": "エンティティヒットごとの段落数",
            },
        },
    )
    """每个实体命中最多展开的段落数"""

    relation_evidence_weight: float = Field(
        default=1.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系证据权重",
                "en_US": "Relation evidence weight",
                "ja_JP": "関係証拠重み",
            },
        },
    )
    """关系证据分权重"""

    entity_evidence_weight: float = Field(
        default=0.55,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "实体证据权重",
                "en_US": "Entity evidence weight",
                "ja_JP": "エンティティ証拠重み",
            },
        },
    )
    """实体证据分权重"""

    semantic_weight: float = Field(
        default=0.65,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "段落语义权重",
                "en_US": "Paragraph semantic weight",
                "ja_JP": "段落セマンティック重み",
            },
        },
    )
    """段落语义分权重"""

    sparse_weight: float = Field(
        default=0.20,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "稀疏检索权重",
                "en_US": "Sparse retrieval weight",
                "ja_JP": "疎検索重み",
            },
        },
    )
    """稀疏检索分权重"""

    graph_weight: float = Field(
        default=0.15,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "图谱证据权重",
                "en_US": "Graph evidence weight",
                "ja_JP": "グラフ証拠重み",
            },
        },
    )
    """图谱证据分权重"""

    relation_intent: AMemorixRelationIntentVectorPoolConfig = Field(
        default_factory=AMemorixRelationIntentVectorPoolConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "关系意图权重",
                "en_US": "Relation intent weights",
                "ja_JP": "関係意図重み",
            },
        },
    )
    """关系意图命中时的双向量池配置"""


class AMemorixRetrievalConfig(ConfigBase):
    """A_Memorix 检索配置"""

    top_k_paragraphs: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "段落 Top-K",
                "en_US": "Paragraph Top-K",
                "ja_JP": "段落 Top-K",
            },
        },
    )
    """段落候选数"""

    top_k_relations: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "关系 Top-K",
                "en_US": "Relation Top-K",
                "ja_JP": "関係 Top-K",
            },
        },
    )
    """关系候选数"""

    top_k_final: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最终 Top-K",
                "en_US": "Final Top-K",
                "ja_JP": "最終 Top-K",
            },
        },
    )
    """最终返回条数"""

    alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "关系融合权重",
                "en_US": "Relation fusion weight",
                "ja_JP": "関係融合重み",
            },
        },
    )
    """关系融合权重"""

    enable_ppr: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用 PPR",
                "en_US": "Enable PPR",
                "ja_JP": "PPR を有効化",
            },
        },
    )
    """是否启用 PPR"""

    ppr_alpha: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR Alpha",
                "en_US": "PPR alpha",
                "ja_JP": "PPR Alpha",
            },
        },
    )
    """PPR alpha"""

    ppr_timeout_seconds: float = Field(
        default=1.5,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR 超时",
                "en_US": "PPR timeout",
                "ja_JP": "PPR タイムアウト",
            },
        },
    )
    """PPR 超时秒数"""

    ppr_concurrency_limit: int = Field(
        default=4,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "PPR 并发限制",
                "en_US": "PPR concurrency limit",
                "ja_JP": "PPR 同時実行制限",
            },
        },
    )
    """PPR 并发限制"""

    enable_parallel: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "并行检索",
                "en_US": "Parallel retrieval",
                "ja_JP": "並列検索",
            },
        },
    )
    """是否启用并行检索"""

    search: AMemorixRetrievalSearchConfig = Field(
        default_factory=AMemorixRetrievalSearchConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "搜索后处理",
                "en_US": "Search post-processing",
                "ja_JP": "検索後処理",
            },
        },
    )
    """搜索后处理配置"""

    fusion: AMemorixFusionRetrievalConfig = Field(
        default_factory=AMemorixFusionRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "检索融合",
                "en_US": "Retrieval fusion",
                "ja_JP": "検索融合",
            },
        },
    )
    """检索融合配置"""

    relation_vectorization: AMemorixRelationVectorizationConfig = Field(
        default_factory=AMemorixRelationVectorizationConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "关系向量化",
                "en_US": "Relation vectorization",
                "ja_JP": "関係ベクトル化",
            },
        },
    )
    """关系向量化配置"""

    vector_pools: AMemorixVectorPoolsConfig = Field(
        default_factory=AMemorixVectorPoolsConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "向量池检索",
                "en_US": "Vector pool retrieval",
                "ja_JP": "ベクトルプール検索",
            },
        },
    )
    """双向量池检索配置"""

    sparse: AMemorixSparseRetrievalConfig = Field(
        default_factory=AMemorixSparseRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "稀疏检索",
                "en_US": "Sparse retrieval",
                "ja_JP": "疎検索",
            },
        },
    )
    """稀疏检索配置"""


class AMemorixThresholdConfig(ConfigBase):
    """A_Memorix 阈值过滤配置"""

    min_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "最小阈值",
                "en_US": "Minimum threshold",
                "ja_JP": "最小しきい値",
            },
        },
    )
    """最小阈值"""

    max_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "最大阈值",
                "en_US": "Maximum threshold",
                "ja_JP": "最大しきい値",
            },
        },
    )
    """最大阈值"""

    percentile: int = Field(
        default=75,
        ge=0,
        le=100,
        json_schema_extra={
            "label": {
                "zh_CN": "动态百分位",
                "en_US": "Dynamic percentile",
                "ja_JP": "動的パーセンタイル",
            },
        },
    )
    """动态阈值百分位"""

    min_results: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最小保留数",
                "en_US": "Minimum results",
                "ja_JP": "最小保持件数",
            },
        },
    )
    """最小保留条数"""

    enable_auto_adjust: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动调整阈值",
                "en_US": "Auto-adjust threshold",
                "ja_JP": "しきい値を自動調整",
            },
        },
    )
    """是否启用自动阈值调整"""


class AMemorixRetrievalSubtypeFilterConfig(ConfigBase):
    """A_Memorix 跨聊天流检索结果分类型过滤配置"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用跨聊天流过滤",
                "en_US": "Enable cross-chat filter",
                "ja_JP": "チャット横断フィルターを有効化",
            },
        },
    )
    """是否启用当前检索结果类型的跨聊天流过滤"""

    mode: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        json_schema_extra={
            "label": {
                "zh_CN": "过滤模式",
                "en_US": "Filter mode",
                "ja_JP": "フィルターモード",
            },
        },
    )
    """过滤模式"""

    chats: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流列表",
                "en_US": "Chat stream list",
                "ja_JP": "チャットストリーム一覧",
            },
        },
    )
    """聊天流列表"""


class AMemorixRetrievalFilterConfig(ConfigBase):
    """A_Memorix 跨聊天流检索结果后置过滤配置"""

    chat_stream: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "普通聊天流记忆",
                "en_US": "Chat stream memory",
                "ja_JP": "通常チャット記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """普通 paragraph/relation 命中的跨聊天流检索后置过滤"""

    chat_summary: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天总结记忆",
                "en_US": "Chat summary memory",
                "ja_JP": "チャット要約記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """聊天总结命中的跨聊天流检索后置过滤"""

    episode: AMemorixRetrievalSubtypeFilterConfig = Field(
        default_factory=AMemorixRetrievalSubtypeFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Episode 记忆",
                "en_US": "Episode memory",
                "ja_JP": "Episode 記憶",
            },
            "x-collapsed-by-default": True,
        },
    )
    """Episode 命中的跨聊天流检索后置过滤"""


class AMemorixFilterConfig(ConfigBase):
    """A_Memorix 聊天过滤配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用聊天过滤",
                "en_US": "Enable chat filter",
                "ja_JP": "チャットフィルターを有効化",
            },
        },
    )
    """是否启用聊天过滤"""

    mode: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        json_schema_extra={
            "label": {
                "zh_CN": "过滤模式",
                "en_US": "Filter mode",
                "ja_JP": "フィルターモード",
            },
        },
    )
    """过滤模式"""

    chats: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流列表",
                "en_US": "Chat stream list",
                "ja_JP": "チャットストリーム一覧",
            },
        },
    )
    """聊天流列表"""

    retrieval: AMemorixRetrievalFilterConfig = Field(
        default_factory=AMemorixRetrievalFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "跨聊天流检索结果过滤",
                "en_US": "Cross-chat retrieval result filter",
                "ja_JP": "チャット横断検索結果フィルター",
            },
            "x-collapsed-by-default": True,
        },
    )
    """仅对跨聊天流检索结果生效的分类型过滤，不影响本聊天流读取自身记忆、写入和后台生成"""


class AMemorixSharedMemoryGroupConfig(ConfigBase):
    """A_Memorix 共享记忆聊天流组配置"""

    targets: list[TargetItem] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "共享聊天流",
                "en_US": "Shared chat streams",
                "ja_JP": "共有チャットストリーム",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """同组聊天会在回忆长期记忆时互相参考，新内容仍记在原来的聊天中"""


class AMemorixEpisodeConfig(ConfigBase):
    """A_Memorix Episode 配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用 Episode",
                "en_US": "Enable episodes",
                "ja_JP": "Episode を有効化",
            },
        },
    )
    """是否启用 Episode"""

    generation_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动生成 Episode",
                "en_US": "Generate episodes",
                "ja_JP": "Episode を自動生成",
            },
        },
    )
    """是否启用自动生成"""

    pending_batch_size: int = Field(
        default=50,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "待处理批量",
                "en_US": "Pending batch size",
                "ja_JP": "保留中バッチサイズ",
            },
        },
    )
    """待处理批大小"""

    pending_max_retry: int = Field(
        default=3,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "待处理重试",
                "en_US": "Pending max retries",
                "ja_JP": "保留中最大リトライ",
            },
        },
    )
    """待处理最大重试次数"""

    max_paragraphs_per_call: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单次段落上限",
                "en_US": "Paragraphs per call",
                "ja_JP": "1回あたりの段落上限",
            },
        },
    )
    """单次最大段落数"""

    max_chars_per_call: int = Field(
        default=6000,
        ge=100,
        json_schema_extra={
            "label": {
                "zh_CN": "单次字符上限",
                "en_US": "Characters per call",
                "ja_JP": "1回あたりの文字上限",
            },
        },
    )
    """单次最大字符数"""

    source_time_window_hours: float = Field(
        default=24.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "来源时间窗口",
                "en_US": "Source time window",
                "ja_JP": "ソース時間窓",
            },
        },
    )
    """时间窗口小时数"""

    segmentation_model: str = Field(
        default="auto",
        json_schema_extra={
            "label": {
                "zh_CN": "分段模型",
                "en_US": "Segmentation model",
                "ja_JP": "分割モデル",
            },
        },
    )
    """分段模型选择"""

    disabled_source_types: List[str] = Field(
        default_factory=lambda: ["person_fact"],
        json_schema_extra={
            "label": {
                "zh_CN": "跳过来源类型",
                "en_US": "Disabled source types",
                "ja_JP": "スキップするソース種別",
            },
            "advanced": True,
        },
    )
    """自动生成 Episode 时跳过的来源类型"""


class AMemorixPersonProfileConfig(ConfigBase):
    """A_Memorix 人物画像配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用人物画像",
                "en_US": "Enable person profiles",
                "ja_JP": "人物プロファイルを有効化",
            },
        },
    )
    """是否启用画像"""

    refresh_interval_minutes: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新间隔",
                "en_US": "Refresh interval",
                "ja_JP": "更新間隔",
            },
        },
    )
    """刷新间隔分钟数"""

    active_window_hours: float = Field(
        default=72.0,
        ge=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "活跃窗口",
                "en_US": "Active window",
                "ja_JP": "アクティブ期間",
            },
        },
    )
    """活跃窗口小时数"""

    max_refresh_per_cycle: int = Field(
        default=50,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单轮刷新上限",
                "en_US": "Refreshes per cycle",
                "ja_JP": "1サイクル更新上限",
            },
        },
    )
    """单轮最大刷新数"""

    refresh_debounce_seconds: int = Field(
        default=120,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新静默期",
                "en_US": "Refresh debounce",
                "ja_JP": "更新デバウンス",
            },
            "advanced": True,
        },
    )
    """写入触发画像刷新前等待的静默秒数"""

    refresh_queue_interval_seconds: int = Field(
        default=60,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新队列间隔",
                "en_US": "Refresh queue interval",
                "ja_JP": "更新キュー間隔",
            },
            "advanced": True,
        },
    )
    """画像刷新队列扫描间隔秒数"""

    refresh_queue_batch_size: int = Field(
        default=10,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新队列批量",
                "en_US": "Refresh queue batch size",
                "ja_JP": "更新キューバッチサイズ",
            },
            "advanced": True,
        },
    )
    """画像刷新队列单轮处理人数"""

    refresh_retry_backoff_seconds: int = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新重试等待",
                "en_US": "Refresh retry backoff",
                "ja_JP": "更新リトライ待機",
            },
            "advanced": True,
        },
    )
    """画像刷新失败后再次重试前等待的秒数"""

    max_retry: int = Field(
        default=3,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "刷新最大重试",
                "en_US": "Refresh max retries",
                "ja_JP": "更新最大リトライ",
            },
            "advanced": True,
        },
    )
    """画像刷新队列最大重试次数"""

    top_k_evidence: int = Field(
        default=12,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "证据条数",
                "en_US": "Evidence count",
                "ja_JP": "証拠件数",
            },
        },
    )
    """证据条数"""

    evidence_classification_max_tokens: int = Field(
        default=1200,
        ge=128,
        json_schema_extra={
            "label": {
                "zh_CN": "证据分类输出上限",
                "en_US": "Evidence classification max tokens",
                "ja_JP": "証拠分類の最大トークン数",
            },
        },
    )
    """人物画像证据分类最大输出 token 数"""

    evidence_classification_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        json_schema_extra={
            "label": {
                "zh_CN": "证据分类温度",
                "en_US": "Evidence classification temperature",
                "ja_JP": "証拠分類の温度",
            },
        },
    )
    """人物画像证据分类模型温度"""


class AMemorixMemoryEvolutionConfig(ConfigBase):
    """A_Memorix 记忆演化配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用记忆演化",
                "en_US": "Enable memory evolution",
                "ja_JP": "記憶進化を有効化",
            },
        },
    )
    """是否启用记忆演化"""

    half_life_hours: float = Field(
        default=24.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "半衰期",
                "en_US": "Half-life",
                "ja_JP": "半減期",
            },
        },
    )
    """半衰期小时数"""

    prune_threshold: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        json_schema_extra={
            "label": {
                "zh_CN": "裁剪阈值",
                "en_US": "Prune threshold",
                "ja_JP": "剪定しきい値",
            },
        },
    )
    """裁剪阈值"""

    freeze_duration_hours: float = Field(
        default=24.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "冻结时长",
                "en_US": "Freeze duration",
                "ja_JP": "凍結時間",
            },
        },
    )
    """冻结时长小时数"""


class AMemorixAdvancedConfig(ConfigBase):
    """A_Memorix 高级运行时配置"""

    enable_auto_save: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动保存",
                "en_US": "Auto save",
                "ja_JP": "自動保存",
            },
        },
    )
    """是否启用自动保存"""

    auto_save_interval_minutes: int = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "自动保存间隔",
                "en_US": "Auto-save interval",
                "ja_JP": "自動保存間隔",
            },
        },
    )
    """自动保存间隔"""

    debug: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "调试模式",
                "en_US": "Debug mode",
                "ja_JP": "デバッグモード",
            },
        },
    )
    """是否启用调试"""


class AMemorixWebImportTimeoutConfig(ConfigBase):
    """A_Memorix 导入中心超时配置"""

    llm_call_seconds: float = Field(
        default=240.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "LLM 单次调用超时",
                "en_US": "LLM call timeout",
                "ja_JP": "LLM 呼び出しタイムアウト",
            },
        },
    )
    """Web 导入中单次 LLM 抽取调用的超时时间，0 表示不额外限制"""

    process_poll_seconds: float = Field(
        default=1.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "子进程轮询等待",
                "en_US": "Process poll wait",
                "ja_JP": "子プロセス待機ポーリング",
            },
        },
    )
    """迁移或转换子进程状态轮询等待时间"""

    process_terminate_seconds: float = Field(
        default=5.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "子进程终止等待",
                "en_US": "Process terminate wait",
                "ja_JP": "子プロセス終了待機",
            },
        },
    )
    """取消任务时等待子进程正常终止的时间"""

    process_kill_seconds: float = Field(
        default=3.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "子进程强杀等待",
                "en_US": "Process kill wait",
                "ja_JP": "子プロセス強制終了待機",
            },
        },
    )
    """取消任务时强制结束子进程后的等待时间"""

    convert_preflight_seconds: float = Field(
        default=20.0,
        ge=0.1,
        json_schema_extra={
            "label": {
                "zh_CN": "转换预检超时",
                "en_US": "Convert preflight timeout",
                "ja_JP": "変換事前チェックタイムアウト",
            },
        },
    )
    """LPMM 转换依赖预检的超时时间"""


class AMemorixWebImportConfig(ConfigBase):
    """A_Memorix 导入中心配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用导入中心",
                "en_US": "Enable import center",
                "ja_JP": "インポートセンターを有効化",
            },
        },
    )
    """是否启用导入中心"""

    max_queue_size: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大队列",
                "en_US": "Max queue size",
                "ja_JP": "最大キューサイズ",
            },
        },
    )
    """最大队列长度"""

    max_files_per_task: int = Field(
        default=200,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单任务文件上限",
                "en_US": "Files per task",
                "ja_JP": "タスクあたりのファイル上限",
            },
        },
    )
    """单任务最大文件数"""

    max_file_size_mb: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单文件大小上限",
                "en_US": "File size limit",
                "ja_JP": "ファイルサイズ上限",
            },
        },
    )
    """单文件大小上限 MB"""

    max_paste_chars: int = Field(
        default=200000,
        ge=100,
        json_schema_extra={
            "label": {
                "zh_CN": "粘贴字符上限",
                "en_US": "Paste character limit",
                "ja_JP": "貼り付け文字数上限",
            },
        },
    )
    """粘贴字符数上限"""

    default_file_concurrency: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认文件并发",
                "en_US": "Default file concurrency",
                "ja_JP": "既定ファイル並列数",
            },
        },
    )
    """默认文件并发"""

    default_chunk_concurrency: int = Field(
        default=4,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认分块并发",
                "en_US": "Default chunk concurrency",
                "ja_JP": "既定チャンク並列数",
            },
        },
    )
    """默认分块并发"""

    default_narrative_window_size: int = Field(
        default=1600,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "默认叙事抽取窗口",
                "en_US": "Default narrative extraction window",
                "ja_JP": "既定ナラティブ抽出ウィンドウ",
            },
            "advanced": True,
        },
    )
    """默认叙事抽取窗口字符数"""

    default_narrative_overlap: int = Field(
        default=400,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "默认叙事重叠字符",
                "en_US": "Default narrative overlap",
                "ja_JP": "既定ナラティブ重複文字数",
            },
            "advanced": True,
        },
    )
    """默认叙事窗口重叠字符数"""

    default_factual_target_size: int = Field(
        default=1200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "默认事实分块目标",
                "en_US": "Default factual chunk target",
                "ja_JP": "既定ファクトチャンク目標",
            },
            "advanced": True,
        },
    )
    """默认事实分块目标字符数"""

    max_chunk_chars: int = Field(
        default=3200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "分块字符上限",
                "en_US": "Chunk character limit",
                "ja_JP": "チャンク文字数上限",
            },
            "advanced": True,
        },
    )
    """单个抽取分块的字符上限"""

    timeout: AMemorixWebImportTimeoutConfig = Field(
        default_factory=AMemorixWebImportTimeoutConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "导入超时",
                "en_US": "Import timeouts",
                "ja_JP": "インポートタイムアウト",
            },
        },
    )
    """导入中心超时配置"""


class AMemorixWebTuningConfig(ConfigBase):
    """A_Memorix 调优中心配置"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用调优中心",
                "en_US": "Enable tuning center",
                "ja_JP": "チューニングセンターを有効化",
            },
        },
    )
    """是否启用调优中心"""

    max_queue_size: int = Field(
        default=8,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最大队列",
                "en_US": "Max queue size",
                "ja_JP": "最大キューサイズ",
            },
        },
    )
    """最大队列长度"""

    poll_interval_ms: int = Field(
        default=1200,
        ge=200,
        json_schema_extra={
            "label": {
                "zh_CN": "轮询间隔",
                "en_US": "Poll interval",
                "ja_JP": "ポーリング間隔",
            },
        },
    )
    """轮询间隔毫秒数"""

    default_intensity: Literal["quick", "standard", "deep"] = Field(
        default="standard",
        json_schema_extra={
            "label": {
                "zh_CN": "默认强度",
                "en_US": "Default intensity",
                "ja_JP": "既定の強度",
            },
        },
    )
    """默认调优强度"""

    default_objective: Literal["precision_priority", "balanced", "recall_priority"] = Field(
        default="precision_priority",
        json_schema_extra={
            "label": {
                "zh_CN": "默认目标",
                "en_US": "Default objective",
                "ja_JP": "既定の目標",
            },
        },
    )
    """默认调优目标"""

    default_top_k_eval: int = Field(
        default=20,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认评估 Top-K",
                "en_US": "Default eval Top-K",
                "ja_JP": "既定評価 Top-K",
            },
        },
    )
    """默认评估 Top-K"""

    default_sample_size: int = Field(
        default=24,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "默认样本数",
                "en_US": "Default sample size",
                "ja_JP": "既定サンプル数",
            },
        },
    )
    """默认样本数"""


class AMemorixWebConfig(ConfigBase):
    """A_Memorix Web 运维配置"""

    import_config: AMemorixWebImportConfig = Field(
        default_factory=AMemorixWebImportConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "导入中心",
                "en_US": "Import center",
                "ja_JP": "インポートセンター",
            },
        },
    )
    """导入中心配置"""

    tuning: AMemorixWebTuningConfig = Field(
        default_factory=AMemorixWebTuningConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "调优中心",
                "en_US": "Tuning center",
                "ja_JP": "チューニングセンター",
            },
        },
    )
    """调优中心配置"""


class AMemorixConfig(ConfigBase):
    """长期记忆配置"""

    __ui_label__ = "记忆"
    __ui_order__ = 50

    plugin: AMemorixPluginConfig = Field(
        default_factory=AMemorixPluginConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆系统",
                "en_US": "Memory system",
                "ja_JP": "記憶システム",
            },
        },
    )
    """长期记忆系统的总开关"""

    integration: AMemorixIntegrationConfig = Field(
        default_factory=AMemorixIntegrationConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天中使用记忆",
                "en_US": "Use memory in chat",
                "ja_JP": "MaiSaka 連携",
            },
        },
    )
    """控制麦麦在聊天中如何使用长期记忆"""

    storage: AMemorixStorageConfig = Field(
        default_factory=AMemorixStorageConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "存储",
                "en_US": "Storage",
                "ja_JP": "ストレージ",
            },
        },
    )
    """存储位置"""

    embedding: AMemorixEmbeddingConfig = Field(
        default_factory=AMemorixEmbeddingConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆向量化",
                "en_US": "Memory embedding",
                "ja_JP": "記憶ベクトル化",
            },
            "x-collapsed-by-default": True,
        },
    )
    """把记忆内容转换为向量时使用的基础设置"""

    retrieval: AMemorixRetrievalConfig = Field(
        default_factory=AMemorixRetrievalConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "检索",
                "en_US": "Retrieval",
                "ja_JP": "検索",
            },
        },
    )
    """检索配置"""

    threshold: AMemorixThresholdConfig = Field(
        default_factory=AMemorixThresholdConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "阈值",
                "en_US": "Thresholds",
                "ja_JP": "しきい値",
            },
        },
    )
    """阈值过滤配置"""

    filter: AMemorixFilterConfig = Field(
        default_factory=AMemorixFilterConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "过滤",
                "en_US": "Filter",
                "ja_JP": "フィルター",
            },
        },
    )
    """聊天过滤配置"""

    global_memory_sharing_enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "全局共享记忆",
                "en_US": "Global memory sharing",
                "ja_JP": "記憶のグローバル共有",
            },
            "x-widget": "switch",
            "x-icon": "globe-2",
        },
    )
    """是否让普通记忆查询在所有聊天流范围内检索"""

    shared_memory_groups: list[AMemorixSharedMemoryGroupConfig] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "共享记忆组",
                "en_US": "Shared memory groups",
                "ja_JP": "共有記憶グループ",
            },
            "x-widget": "custom",
            "x-icon": "users-round",
            "x-display-as-section": True,
        },
    )
    """把需要互相参考长期记忆的群聊或私聊放到同一组"""

    episode: AMemorixEpisodeConfig = Field(
        default_factory=AMemorixEpisodeConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Episode",
                "en_US": "Episodes",
                "ja_JP": "Episode",
            },
        },
    )
    """Episode 配置"""

    person_profile: AMemorixPersonProfileConfig = Field(
        default_factory=AMemorixPersonProfileConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "人物画像",
                "en_US": "Person profiles",
                "ja_JP": "人物プロファイル",
            },
        },
    )
    """人物画像配置"""

    memory: AMemorixMemoryEvolutionConfig = Field(
        default_factory=AMemorixMemoryEvolutionConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "记忆演化",
                "en_US": "Memory evolution",
                "ja_JP": "記憶進化",
            },
        },
    )
    """记忆演化配置"""

    advanced: AMemorixAdvancedConfig = Field(
        default_factory=AMemorixAdvancedConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "高级",
                "en_US": "Advanced",
                "ja_JP": "詳細設定",
            },
        },
    )
    """高级运行时配置"""

    web: AMemorixWebConfig = Field(
        default_factory=AMemorixWebConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "Web 运维",
                "en_US": "Web operations",
                "ja_JP": "Web 運用",
            },
        },
    )
    """Web 运维配置"""


class LearningItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "平台",
                "en_US": "Platform",
                "ja_JP": "プラットフォーム",
            },
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """平台，与ID一起留空表示全局"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天流 ID",
                "en_US": "Chat stream ID",
                "ja_JP": "チャットストリーム ID",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """要单独配置的群号或用户 ID；留空表示默认规则。"""

    type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "label": {
                "zh_CN": "聊天类型",
                "en_US": "Chat type",
                "ja_JP": "チャット種別",
            },
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """这条规则作用于群聊还是私聊。"""

    use: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "使用",
                "en_US": "Use",
                "ja_JP": "使用",
            },
            "x-widget": "switch",
            "x-icon": "message-square",
        },
    )
    """是否在这个聊天里使用已学到的内容。"""

    learn: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "学习",
                "en_US": "Learn",
                "ja_JP": "学習",
            },
            "x-widget": "switch",
            "x-icon": "graduation-cap",
        },
    )
    """是否从这个聊天里继续学习新内容。"""


class ChatStreamGroup(ConfigBase):
    """聊天流互通组配置类"""

    targets: list[TargetItem] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "互通聊天流",
                "en_US": "Shared chat streams",
                "ja_JP": "共有チャットストリーム",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_这个组里的聊天流会共享对应的学习内容。"""


ExperimentalConfig.model_rebuild()


class ExpressionConfig(ConfigBase):
    """表达配置类"""

    __ui_label__ = "学习"
    __ui_order__ = 40
    __ui_use_subtabs__ = True
    __ui_sub_label__ = "表达"

    expression_checked_only: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "仅用人工检查表达",
                "en_US": "Use human-reviewed expressions only",
                "ja_JP": "人間が確認した表現のみ使用",
            },
            "x-widget": "switch",
            "x-icon": "check",
            "x-row": "expression-learning-switches",
        },
    )
    """只使用人工确认过的表达方式，更稳但学习效果会慢一些。"""

    expression_self_reflect: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "优化表达方式学习",
                "en_US": "Optimize expression learning",
                "ja_JP": "表現学習を最適化",
            },
            "x-widget": "switch",
            "x-icon": "sparkles",
            "x-row": "expression-learning-switches",
        },
    )
    """写入表达方式前先让 AI 检查，减少学到奇怪内容。"""

    expression_selection_mode: Literal["legacy", "vector", "vector_intent"] = Field(
        default="legacy",
        json_schema_extra={
            "label": {
                "zh_CN": "表达使用方式",
                "en_US": "Expression usage mode",
                "ja_JP": "表現の使用方法",
            },
            "x-widget": "select",
            "x-icon": "route",
            "advanced": False,
            "options": ["legacy", "vector", "vector_intent"],
            "x-option-labels": {
                "legacy": "随手",
                "vector": "精细",
                "vector_intent": "超级精细",
            },
            "x-option-descriptions": {
                "legacy": "使用 LLM 进行选择，效果一般",
                "vector": "使用嵌入模型进行选择，效果较好（需要配置嵌入模型）",
                "vector_intent": "使用特殊构建的回复方式加上嵌入模型进行选择，效果非常好（需要配置嵌入模型）",
            },
        },
    )
    """表达方式的使用策略：legacy 使用 LLM 选择，vector 使用嵌入召回，vector_intent 会额外使用表达选择意图。"""

    expression_vector_index_path: str = Field(
        default="data/expression_selection/expression_vector_index.json",
        json_schema_extra={
            "label": {
                "zh_CN": "表达向量索引路径",
                "en_US": "Expression vector index path",
                "ja_JP": "表現ベクトル索引パス",
            },
            "x-widget": "input",
            "x-icon": "file-search",
            "advanced": True,
        },
    )
    """向量召回使用的表达索引 JSON；相对路径按项目根目录解析。"""

    expression_vector_candidate_pool_size: int = Field(
        default=50,
        ge=1,
        le=50,
        json_schema_extra={
            "label": {
                "zh_CN": "向量候选上限",
                "en_US": "Vector candidate limit",
                "ja_JP": "ベクトル候補上限",
            },
            "x-widget": "input",
            "x-icon": "list-filter",
            "advanced": True,
        },
    )
    """向量召回后最多交给表达方式 LLM 选择的候选数；硬上限为 50。"""

    max_expression_learner: int = Field(
        default=3,
        json_schema_extra={
            "label": {
                "zh_CN": "表达学习最大并发",
                "en_US": "Max expression learners",
                "ja_JP": "表現学習の最大同時実行数",
            },
            "x-widget": "input",
            "x-icon": "layers",
            "advanced": True,
        },
    )
    """同时运行的表达学习任务数量；太高可能占用更多资源。"""

    learning_list: list[LearningItem] = Field(
        default_factory=lambda: [
            LearningItem(
                platform="",
                item_id="",
                type="group",
                use=True,
                learn=True,
            )
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "学习配置",
                "en_US": "Learning settings",
                "ja_JP": "学習設定",
            },
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """配置哪些聊天会学习和使用表达方式；默认规则不够时再单独添加。"""

    expression_groups: list[ChatStreamGroup] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "表达互通组",
                "en_US": "Expression sharing groups",
                "ja_JP": "表現共有グループ",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_让多个群聊或私聊共享学到的表达方式。"""


class JargonConfig(ConfigBase):
    """黑话配置类"""

    __ui_parent__ = "expression"
    __ui_label__ = "黑话"
    __ui_sub_label__ = "黑话"

    learning_list: list[LearningItem] = Field(
        default_factory=lambda: [
            LearningItem(
                platform="",
                item_id="",
                type="group",
                use=True,
                learn=True,
            )
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "学习配置",
                "en_US": "Learning settings",
                "ja_JP": "学習設定",
            },
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """_wrap_配置哪些聊天会学习和使用黑话；默认规则不够时再单独添加。"""

    jargon_groups: list[ChatStreamGroup] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "黑话互通组",
                "en_US": "Jargon sharing groups",
                "ja_JP": "隠語共有グループ",
            },
            "x-widget": "custom",
            "x-icon": "users",
        },
    )
    """_wrap_让多个群聊或私聊共享学到的黑话。"""


class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    __ui_label__ = "语音"
    __ui_advanced__ = True
    __ui_order__ = 90

    enable_asr: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "mic",
        },
    )
    """开启后麦麦可以把语音消息识别成文字再处理。"""


class EmojiCacheCleanupConfig(ConfigBase):
    """表情包缓存自动清理配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "trash-2",
            "label": {
                "zh_CN": "启用表情包缓存自动清理",
                "en_US": "Enable emoji cache cleanup",
                "ja_JP": "絵文字キャッシュ自動クリーンアップを有効化",
            },
        },
    )
    """开启后会自动删除长期未注册、未使用的表情包缓存。"""

    check_interval_hours: float = Field(
        default=6.0,
        ge=1.0 / 60.0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "clock",
            "label": {
                "zh_CN": "表情包清理检查间隔（小时）",
                "en_US": "Emoji cleanup check interval (hours)",
                "ja_JP": "絵文字クリーンアップ確認間隔（時間）",
            },
        },
    )
    """每隔多少小时检查一次旧表情包缓存。"""

    emoji_file_retention_days: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "calendar-days",
            "label": {
                "zh_CN": "未注册表情包文件保留天数",
                "en_US": "Unregistered emoji file retention days",
                "ja_JP": "未登録絵文字ファイル保持日数",
            },
        },
    )
    """未注册表情包文件多久没被使用后可以删除；已注册表情包永远不会由该任务删除。"""

    no_file_record_retention_days: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "database",
            "label": {
                "zh_CN": "未注册表情包无文件记录保留天数",
                "en_US": "Unregistered emoji no-file record retention days",
                "ja_JP": "未登録絵文字のファイルなし記録保持日数",
            },
        },
    )
    """未注册表情包文件删掉后，描述缓存记录还能保留多久。"""


class EmojiConfig(ConfigBase):
    """表情包配置类"""

    __ui_label__ = "表情"
    __ui_advanced__ = True
    __ui_order__ = 80

    emoji_send_num: int = Field(
        default=25,
        ge=1,
        le=64,
        json_schema_extra={
            "label": {
                "zh_CN": "单次发送候选数",
                "en_US": "Emoji send candidate count",
                "ja_JP": "送信候補の絵文字数",
            },
            "x-widget": "input",
            "x-icon": "grid",
            "advanced": True,
        },
    )
    """每次从多少个候选表情里挑一个发送；不是一次发送这么多。"""

    max_reg_num: int = Field(
        default=64,
        json_schema_extra={
            "label": {
                "zh_CN": "最大注册数量",
                "en_US": "Max registered emojis",
                "ja_JP": "最大登録数",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """最多保存多少个可用表情。"""

    do_replace: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "满额后替换旧表情",
                "en_US": "Replace old emojis when full",
                "ja_JP": "上限到達時に古い絵文字を置換",
            },
            "x-widget": "switch",
            "x-icon": "refresh-cw",
            "advanced": True,
        },
    )
    """表情满了以后是否用新表情替换旧表情。"""

    check_interval: int = Field(
        default=10,
        json_schema_extra={
            "label": {
                "zh_CN": "检查间隔",
                "en_US": "Check interval",
                "ja_JP": "チェック間隔",
            },
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """每隔多少分钟检查一次表情库状态。"""

    steal_emoji: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "收集聊天表情",
                "en_US": "Collect chat emojis",
                "ja_JP": "チャット絵文字を収集",
            },
            "x-widget": "switch",
            "x-icon": "copy",
        },
    )
    """是否从聊天中自动收集别人发的表情。"""

    max_emoji_size_mb: float = Field(
        default=5.0,
        ge=0.0,
        json_schema_extra={
            "label": {
                "zh_CN": "收集表情大小上限（MB）",
                "en_US": "Collected emoji size limit (MB)",
                "ja_JP": "収集する絵文字サイズ上限（MB）",
            },
            "x-widget": "input",
            "x-icon": "file-warning",
            "advanced": True,
        },
    )
    """收集表情时允许的最大文件大小；0 表示不限。"""

    content_filtration: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用内容过滤",
                "en_US": "Enable content filtering",
                "ja_JP": "内容フィルタリングを有効化",
            },
            "advanced": True,
            "x-widget": "switch",
            "x-icon": "filter",
        },
    )
    """开启后只保存内容合适的表情。"""

    cache_cleanup: EmojiCacheCleanupConfig = Field(default_factory=EmojiCacheCleanupConfig)
    """定期清理未注册表情包缓存，减少磁盘占用；已注册表情包不会被清理。"""


class KeywordRuleConfig(ConfigBase):
    """关键词规则配置类"""

    keywords: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "tag",
        },
    )
    """要匹配的关键词；命中任意一个即可触发。"""

    regex: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "regex",
        },
    )
    """要匹配的正则表达式；适合复杂文本规则。"""

    reaction: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "message-circle",
        },
    )
    """命中后给麦麦看的提示内容，不会直接当作消息发送。"""

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
    """命中关键词后，给麦麦追加一段固定反应提示。"""

    regex_rules: list[KeywordRuleConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """命中正则规则后，给麦麦追加一段固定反应提示。"""

    def model_post_init(self, context: Optional[dict] = None) -> None:
        """验证配置"""
        for rule in self.keyword_rules + self.regex_rules:
            if not isinstance(rule, KeywordRuleConfig):
                raise ValueError(f"规则必须是KeywordRuleConfig类型，而不是{type(rule).__name__}")
        return super().model_post_init(context)


class ResponsePostProcessConfig(ConfigBase):
    """回复后处理配置类"""

    __ui_parent__ = "chat"
    __ui_label__ = "后处理"
    __ui_advanced__ = True
    __ui_order__ = 100

    enable_response_post_process: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回复后处理",
                "en_US": "Enable response post-process",
                "ja_JP": "返信後処理を有効化",
            },
            "x-widget": "switch",
            "x-icon": "settings",
        },
    )
    """开启后会对回复做错别字、分段等后处理。"""

    typing_speed: float = Field(
        default=1.0,
        ge=0,
        le=2,
        json_schema_extra={
            "label": {
                "zh_CN": "打字速度",
                "en_US": "Typing speed",
                "ja_JP": "タイピング速度",
            },
            "x-widget": "slider",
            "x-icon": "keyboard",
            "x-row": "reply-speed",
            "step": 0.1,
            "advanced": True,
        },
    )
    """模拟打字等待时间；0 最快，1 默认，2 更慢。"""


class ChineseTypoConfig(ConfigBase):
    """中文错别字配置类"""

    __ui_parent__ = "response_post_process"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用错别字",
                "en_US": "Enable typos",
                "ja_JP": "誤字生成を有効化",
            },
            "x-widget": "switch",
            "x-icon": "type",
        },
    )
    """让麦麦偶尔打错字，更像真人聊天。"""

    error_rate: float = Field(
        default=0.01,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "单字错字概率",
                "en_US": "Single-character typo chance",
                "ja_JP": "単字誤字確率",
            },
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.01,
            "advanced": True,
        },
    )
    """单个字被替换成错字的概率。"""

    min_freq: int = Field(
        default=9,
        json_schema_extra={
            "label": {
                "zh_CN": "最小字频",
                "en_US": "Minimum character frequency",
                "ja_JP": "最小文字頻度",
            },
            "x-widget": "input",
            "x-icon": "hash",
            "advanced": True,
        },
    )
    """只对常见程度达到该值的字尝试制造错字。"""

    tone_error_rate: float = Field(
        default=0.1,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "声调错字概率",
                "en_US": "Tone typo chance",
                "ja_JP": "声調誤字確率",
            },
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.1,
            "advanced": True,
        },
    )
    """按相近声调制造错字的概率。"""

    word_replace_rate: float = Field(
        default=0.006,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "整词替换概率",
                "en_US": "Word replacement chance",
                "ja_JP": "単語置換確率",
            },
            "x-widget": "slider",
            "x-icon": "percent",
            "step": 0.001,
            "advanced": True,
        },
    )
    """整词被替换成错词的概率。"""


class ResponseSplitterConfig(ConfigBase):
    """回复分割器配置类"""

    __ui_parent__ = "response_post_process"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用回复分割",
                "en_US": "Enable response splitting",
                "ja_JP": "返信分割を有効化",
            },
            "x-widget": "switch",
            "x-icon": "scissors",
        },
    )
    """把过长回复拆成多条发送。"""

    max_length: int = Field(
        default=512,
        json_schema_extra={
            "label": {
                "zh_CN": "单条最大长度",
                "en_US": "Max message length",
                "ja_JP": "1通の最大長",
            },
            "x-widget": "input",
            "x-icon": "ruler",
        },
    )
    """单条回复允许的最大长度。"""

    max_sentence_num: int = Field(
        default=8,
        json_schema_extra={
            "label": {
                "zh_CN": "单条最大句数",
                "en_US": "Max sentences per message",
                "ja_JP": "1通の最大文数",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """单条回复最多包含多少个句子。"""

    max_split_num: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "最多分割条数",
                "en_US": "Max split messages",
                "ja_JP": "最大分割数",
            },
            "x-widget": "input",
            "x-icon": "list",
        },
    )
    """一次回复最多拆成几条消息。"""

    enable_kaomoji_protection: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "保护颜文字",
                "en_US": "Protect kaomoji",
                "ja_JP": "顔文字を保護",
            },
            "x-widget": "switch",
            "x-icon": "smile",
            "advanced": True,
        },
    )
    """尽量避免把颜文字从中间拆开。"""

    enable_overflow_return_all: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "超限保留全文",
                "en_US": "Keep full text on overflow",
                "ja_JP": "超過時に全文保持",
            },
            "x-widget": "switch",
            "x-icon": "maximize",
            "advanced": True,
        },
    )
    """句子太多时是否直接保留完整回复，不再强行截断。"""


class LogConfig(ConfigBase):
    """日志配置类"""

    __ui_label__ = "调试"
    __ui_advanced__ = True
    __ui_order__ = 130

    date_style: str = Field(
        default="m-d H:i:s",
        json_schema_extra={
            "label": {
                "zh_CN": "日期格式",
                "en_US": "Date format",
                "ja_JP": "日付形式",
            },
            "x-widget": "input",
            "x-icon": "clock",
        },
    )
    """日志时间的显示格式。"""

    log_level_style: Literal["lite", "compact", "full"] = Field(
        default="lite",
        json_schema_extra={
            "label": {
                "zh_CN": "日志等级样式",
                "en_US": "Log level style",
                "ja_JP": "ログレベル表示",
            },
            "x-widget": "select",
            "x-icon": "list",
        },
    )
    """日志等级的显示样式，只影响日志外观。"""

    color_text: Literal["none", "title", "full"] = Field(
        default="full",
        json_schema_extra={
            "label": {
                "zh_CN": "控制台颜色",
                "en_US": "Console color",
                "ja_JP": "コンソール色",
            },
            "x-widget": "select",
            "x-icon": "palette",
        },
    )
    """控制台日志颜色范围。"""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        json_schema_extra={
            "label": {
                "zh_CN": "全局日志级别",
                "en_US": "Global log level",
                "ja_JP": "全体ログレベル",
            },
            "x-widget": "select",
            "x-icon": "list-filter",
        },
    )
    """全局最低日志等级；DEBUG 最详细，ERROR 最安静。"""

    console_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        json_schema_extra={
            "label": {
                "zh_CN": "控制台日志级别",
                "en_US": "Console log level",
                "ja_JP": "コンソールログレベル",
            },
            "x-widget": "select",
            "x-icon": "terminal",
        },
    )
    """控制台输出的最低日志等级。"""

    file_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="DEBUG",
        json_schema_extra={
            "label": {
                "zh_CN": "文件日志级别",
                "en_US": "File log level",
                "ja_JP": "ファイルログレベル",
            },
            "x-widget": "select",
            "x-icon": "file-json",
        },
    )
    """写入日志文件的最低日志等级。"""

    log_file_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        json_schema_extra={
            "label": {
                "zh_CN": "单个日志大小",
                "en_US": "Single log file size",
                "ja_JP": "単一ログサイズ",
            },
            "x-widget": "input",
            "x-icon": "hard-drive",
        },
    )
    """单个日志文件超过这个大小后会轮转。"""

    max_log_files: int = Field(
        default=30,
        json_schema_extra={
            "label": {
                "zh_CN": "日志文件保留数",
                "en_US": "Retained log files",
                "ja_JP": "保持ログ数",
            },
            "x-widget": "input",
            "x-icon": "files",
        },
    )
    """最多保留多少个主日志文件。"""

    log_cleanup_days: int = Field(
        default=30,
        json_schema_extra={
            "label": {
                "zh_CN": "日志保留天数",
                "en_US": "Log retention days",
                "ja_JP": "ログ保持日数",
            },
            "x-widget": "input",
            "x-icon": "calendar-days",
        },
    )
    """主日志文件超过多少天后清理。"""

    llm_request_snapshot_limit: int = Field(
        default=128,
        json_schema_extra={
            "label": {
                "zh_CN": "请求快照保留数",
                "en_US": "Request snapshot limit",
                "ja_JP": "リクエストスナップショット数",
            },
            "x-widget": "input",
            "x-icon": "archive",
        },
    )
    """失败模型请求快照最多保留多少份。"""

    maisaka_prompt_preview_limit: int = Field(
        default=256,
        json_schema_extra={
            "label": {
                "zh_CN": "Prompt 预览保留数",
                "en_US": "Prompt preview limit",
                "ja_JP": "Prompt プレビュー保持数",
            },
            "x-widget": "input",
            "x-icon": "panel-top",
        },
    )
    """每个聊天最多保留多少组 Prompt 预览。"""

    maisaka_reply_effect_limit: int = Field(
        default=256,
        json_schema_extra={
            "label": {
                "zh_CN": "回复效果记录数",
                "en_US": "Reply effect record limit",
                "ja_JP": "返信効果記録数",
            },
            "x-widget": "input",
            "x-icon": "clipboard-check",
        },
    )
    """每个聊天最多保留多少条回复效果记录。"""

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
            "label": {
                "zh_CN": "屏蔽库日志",
                "en_US": "Suppressed library logs",
                "ja_JP": "抑制ライブラリログ",
            },
            "x-widget": "custom",
            "x-icon": "volume-x",
            "advanced": True,
        },
    )
    """完全不显示日志的第三方库名称列表。"""

    library_log_levels: dict[str, str] = Field(
        default_factory=lambda: {"aiohttp": "WARNING", "PIL": "WARNING"},
        json_schema_extra={
            "label": {
                "zh_CN": "库日志级别",
                "en_US": "Library log levels",
                "ja_JP": "ライブラリログレベル",
            },
            "x-widget": "custom",
            "x-icon": "sliders-horizontal",
            "advanced": True,
        },
    )
    """单独设置某些第三方库的日志等级。"""


class TelemetryConfig(ConfigBase):
    """遥测配置类"""

    __ui_parent__ = "debug"

    enable: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用遥测",
                "en_US": "Enable telemetry",
                "ja_JP": "テレメトリを有効化",
            },
            "x-widget": "switch",
            "x-icon": "activity",
        },
    )
    """是否发送匿名运行统计；关闭不影响正常使用。"""


class DebugConfig(ConfigBase):
    """调试配置类"""

    __ui_parent__ = "log"
    __ui_label__ = "其他"

    show_maisaka_thinking: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "显示思考过程",
                "en_US": "Show thinking process",
                "ja_JP": "思考過程を表示",
            },
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """在日志或界面中显示麦麦的思考过程。"""

    show_jargon_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "显示黑话 Prompt",
                "en_US": "Show jargon prompt",
                "ja_JP": "隠語 Prompt を表示",
            },
            "x-widget": "switch",
            "x-icon": "book",
        },
    )
    """调试黑话学习时显示相关 Prompt。"""

    show_memory_prompt: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "显示记忆 Prompt",
                "en_US": "Show memory prompt",
                "ja_JP": "記憶 Prompt を表示",
            },
            "x-widget": "switch",
            "x-icon": "database",
        },
    )
    """调试记忆检索时显示相关 Prompt。"""

    enable_reply_effect_tracking: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "记录回复效果",
                "en_US": "Track reply effects",
                "ja_JP": "返信効果を記録",
            },
            "x-widget": "switch",
            "x-icon": "activity",
        },
    )
    """记录回复效果评分，方便观察回复质量。"""

    keep_prompt_preview_json_base64: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "保留预览图片数据",
                "en_US": "Keep preview image data",
                "ja_JP": "プレビュー画像データを保持",
            },
            "x-widget": "switch",
            "x-icon": "image",
        },
    )
    """Prompt 预览里保留图片 base64，便于复现但会占空间。"""

    record_tool_structured_content: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "记录工具结构化内容",
                "en_US": "Record tool structured content",
                "ja_JP": "ツール構造化内容を記録",
            },
            "x-widget": "switch",
            "x-icon": "braces",
        },
    )
    """保存工具返回的结构化内容，便于调试但会增加数据库体积。"""

    enable_llm_cache_stats: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "记录模型缓存统计",
                "en_US": "Record model cache stats",
                "ja_JP": "モデルキャッシュ統計を記録",
            },
            "x-widget": "switch",
            "x-icon": "chart-no-axes-column",
        },
    )
    """记录模型 prompt cache 统计，用于性能调试。"""


class ExtraPromptItem(ConfigBase):
    platform: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "wifi",
        },
    )
    """额外提示作用的平台，和聊天流 ID、提示内容需要一起填写。"""

    item_id: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """额外提示作用的群号或用户 ID。"""

    rule_type: Literal["group", "private"] = Field(
        default="group",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "users",
            "x-option-descriptions": RULE_TYPE_OPTION_DESCRIPTIONS,
        },
    )
    """额外提示作用于群聊还是私聊。"""

    prompt: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "textarea",
            "x-icon": "file-text",
        },
    )
    """给这个聊天额外补充的要求。"""

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
            "label": {
                "zh_CN": "旧版 WS 主机",
                "en_US": "Legacy WS host",
                "ja_JP": "旧 WS ホスト",
            },
            "x-widget": "input",
            "x-icon": "server",
        },
    )
    """旧版 WebSocket 服务监听地址；不清楚就保持默认。"""

    ws_server_port: int = Field(
        default=8000,
        json_schema_extra={
            "label": {
                "zh_CN": "旧版 WS 端口",
                "en_US": "Legacy WS port",
                "ja_JP": "旧 WS ポート",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """旧版 WebSocket 服务端口。"""

    auth_token: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "旧版认证令牌",
                "en_US": "Legacy auth tokens",
                "ja_JP": "旧認証トークン",
            },
            "x-widget": "custom",
            "x-icon": "key",
        },
    )
    """旧版 API 的认证令牌；为空表示不验证。"""

    enable_api_server: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用新版 API",
                "en_US": "Enable new API",
                "ja_JP": "新 API を有効化",
            },
            "x-widget": "switch",
            "x-icon": "server",
        },
    )
    """是否开启新版 API Server，供外部程序调用麦麦。"""

    api_server_host: str = Field(
        default="0.0.0.0",
        json_schema_extra={
            "label": {
                "zh_CN": "新版 API 主机",
                "en_US": "New API host",
                "ja_JP": "新 API ホスト",
            },
            "x-widget": "input",
            "x-icon": "globe",
        },
    )
    """新版 API Server 监听地址；0.0.0.0 表示允许外部访问。"""

    api_server_port: int = Field(
        default=8090,
        json_schema_extra={
            "label": {
                "zh_CN": "新版 API 端口",
                "en_US": "New API port",
                "ja_JP": "新 API ポート",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """新版 API Server 监听端口。"""

    api_server_use_wss: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "新版 API 使用 WSS",
                "en_US": "Use WSS for new API",
                "ja_JP": "新 API で WSS を使用",
            },
            "x-widget": "switch",
            "x-icon": "lock",
        },
    )
    """新版 API Server 是否使用加密 WebSocket。"""

    api_server_cert_file: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "WSS 证书文件",
                "en_US": "WSS certificate file",
                "ja_JP": "WSS 証明書ファイル",
            },
            "x-widget": "input",
            "x-icon": "file",
        },
    )
    """WSS 使用的证书文件路径。"""

    api_server_key_file: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "WSS 私钥文件",
                "en_US": "WSS key file",
                "ja_JP": "WSS 秘密鍵ファイル",
            },
            "x-widget": "input",
            "x-icon": "key",
        },
    )
    """WSS 使用的私钥文件路径。"""

    api_server_allowed_api_keys: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "label": {
                "zh_CN": "新版 API Key 白名单",
                "en_US": "New API key allowlist",
                "ja_JP": "新 API Key 許可リスト",
            },
            "x-widget": "custom",
            "x-icon": "shield",
        },
    )
    """允许访问新版 API 的 Key 列表；为空表示不限制。"""


class WebUIConfig(ConfigBase):
    """WebUI配置类"""

    __ui_label__ = "WebUI"
    __ui_advanced__ = True
    __ui_order__ = 110

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用 WebUI",
                "en_US": "Enable WebUI",
                "ja_JP": "WebUI を有効化",
            },
            "x-widget": "switch",
            "x-icon": "monitor",
        },
    )
    """是否启动 WebUI 管理界面。"""

    host: list[str] = Field(
        default=["127.0.0.1", "::1"],
        json_schema_extra={
            "label": {
                "zh_CN": "WebUI 主机",
                "en_US": "WebUI host",
                "ja_JP": "WebUI ホスト",
            },
            "x-widget": "tags",
            "x-icon": "globe",
            "x-placeholder": "127.0.0.1",
        },
    )
    """WebUI 监听地址列表；可同时绑定 IPv4 和 IPv6，例如 ["0.0.0.0", "::"]。"""

    port: int = Field(
        default=8001,
        json_schema_extra={
            "label": {
                "zh_CN": "WebUI 端口",
                "en_US": "WebUI port",
                "ja_JP": "WebUI ポート",
            },
            "x-widget": "input",
            "x-icon": "hash",
        },
    )
    """WebUI 访问端口。"""

    mode: Literal["development", "production"] = Field(
        default="production",
        json_schema_extra={
            "label": {
                "zh_CN": "运行模式",
                "en_US": "Run mode",
                "ja_JP": "実行モード",
            },
            "x-widget": "select",
            "x-icon": "settings",
        },
    )
    """WebUI 运行模式；普通使用保持 production。"""

    webui_style: int = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "label": {
                "zh_CN": "界面风格",
                "en_US": "Interface style",
                "ja_JP": "画面スタイル",
            },
            "x-widget": "number",
            "x-icon": "palette",
            "x-layout": "inline-right",
            "x-input-width": "8rem",
        },
    )
    """界面风格编号；0 为旧风格，1 为未来复古风格。"""

    anti_crawler_mode: Literal["false", "strict", "loose", "basic"] = Field(
        default="basic",
        json_schema_extra={
            "label": {
                "zh_CN": "防爬虫模式",
                "en_US": "Anti-crawler mode",
                "ja_JP": "クローラー対策モード",
            },
            "x-widget": "select",
            "x-icon": "shield",
        },
    )
    """防爬虫策略；basic 只记录，strict/loose 会拦截更多请求。"""

    allowed_ips: str = Field(
        default="127.0.0.1",
        json_schema_extra={
            "label": {
                "zh_CN": "允许访问 IP",
                "en_US": "Allowed IPs",
                "ja_JP": "許可 IP",
            },
            "x-widget": "comma-list",
            "x-icon": "network",
            "x-placeholder": "127.0.0.1",
        },
    )
    """允许访问 WebUI 的 IP，多个用逗号分隔。"""

    trusted_proxies: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "可信代理 IP",
                "en_US": "Trusted proxy IPs",
                "ja_JP": "信頼プロキシ IP",
            },
            "x-widget": "comma-list",
            "x-icon": "server",
            "x-placeholder": "127.0.0.1",
        },
    )
    """可信反向代理 IP；只有这些代理传来的真实 IP 会被信任。"""

    trust_xff: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "信任 XFF",
                "en_US": "Trust XFF",
                "ja_JP": "XFF を信頼",
            },
            "x-widget": "switch",
            "x-icon": "shield-check",
        },
    )
    """是否信任 X-Forwarded-For 里的真实访客 IP。"""

    secure_cookie: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "安全 Cookie",
                "en_US": "Secure cookie",
                "ja_JP": "セキュア Cookie",
            },
            "x-widget": "switch",
            "x-icon": "cookie",
        },
    )
    """只在 HTTPS 下发送登录 Cookie；没有 HTTPS 时不要开启。"""

    enforce_public_outbound_url: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "强制公网出站 URL 校验",
                "en_US": "Enforce public outbound URL check",
                "ja_JP": "公開ネットワーク URL チェックを強制",
            },
            "x-widget": "switch",
            "x-icon": "shield-alert",
            "advanced": False,
        },
    )
    """限制 WebUI 访问外部 URL，降低访问内网地址的风险。"""

    enable_paragraph_content: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "加载段落全文",
                "en_US": "Load full paragraph content",
                "ja_JP": "段落全文を読み込む",
            },
            "x-widget": "switch",
            "x-icon": "file-text",
        },
    )
    """知识图谱里是否加载段落全文；更完整但更占内存。"""


class DatabaseConfig(ConfigBase):
    """数据库配置类"""

    __ui_parent__ = "debug"

    save_binary_data: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "保存二进制原文件",
                "en_US": "Save binary source files",
                "ja_JP": "バイナリ原本を保存",
            },
            "x-widget": "switch",
            "x-icon": "save",
            "advanced": True,
        },
    )
    """
    是否保存语音等二进制原文件；更占空间，但方便以后重新识别。
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
    """MCP HTTP 认证方式；none 表示不认证。"""

    bearer_token: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "password",
            "x-icon": "key",
        },
    )
    """Bearer 认证令牌，只在 mode 为 bearer 时使用。"""

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
    """是否启用这个 Root。"""

    uri: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "folder",
        },
    )
    """Root 的 URI，文件夹一般写 file:/// 开头的路径。"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """这个 Root 在 MCP 里的显示名称。"""

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
    """是否向 MCP 服务器暴露 Roots 能力。"""

    items: list[MCPRootItemConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "folder",
        },
    )
    """允许 MCP 服务器看到的目录或资源列表。"""


class MCPSamplingConfig(ConfigBase):
    """MCP Sampling 能力配置。"""

    enable: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "brain",
        },
    )
    """是否声明支持 MCP Sampling。"""

    task_name: str = Field(
        default="planner",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "sparkles",
        },
    )
    """MCP Sampling 调用模型时使用的任务名。"""

    include_context_support: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "layers",
        },
    )
    """是否允许 Sampling 请求带上下文。"""

    tool_support: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "wrench",
        },
    )
    """Sampling 过程中是否允许继续使用工具。"""


class MCPElicitationConfig(ConfigBase):
    """MCP Elicitation 能力配置。"""

    enable: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "message-circle-question",
        },
    )
    """是否声明支持 MCP Elicitation。"""

    allow_form: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "form-input",
        },
    )
    """是否允许 MCP 服务器请求填写表单。"""

    allow_url: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "link",
        },
    )
    """是否允许 MCP 服务器请求打开 URL。"""

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
    """对 MCP 服务器展示的客户端名称。"""

    client_version: str = Field(
        default="1.0.0",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "info",
        },
    )
    """对 MCP 服务器展示的客户端版本。"""

    roots: MCPRootsConfig = Field(default_factory=MCPRootsConfig)
    """是否向 MCP 服务器提供可访问的文件根目录。"""

    sampling: MCPSamplingConfig = Field(default_factory=MCPSamplingConfig)
    """是否允许 MCP 服务器请求麦麦调用模型。"""

    elicitation: MCPElicitationConfig = Field(default_factory=MCPElicitationConfig)
    """是否允许 MCP 服务器向麦麦请求补充信息。"""


class MCPServerItemConfig(ConfigBase):
    """单个 MCP 服务器配置。"""

    name: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "tag",
        },
    )
    """MCP 服务器名称，必须唯一。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "power",
        },
    )
    """是否启用这个 MCP 服务器。"""

    transport: Literal["stdio", "streamable_http", "sse"] = Field(
        default="stdio",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shuffle",
        },
    )
    """连接方式；本地命令通常用 stdio，远程服务用 HTTP/SSE。"""

    command: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "terminal",
        },
    )
    """stdio 模式下启动服务器的命令。"""

    args: list[str] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "list",
        },
    )
    """stdio 模式下传给命令的参数。"""

    env: dict[str, str] = Field(
        default_factory=lambda: {},
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "variable",
        },
    )
    """stdio 模式下额外传入的环境变量。"""

    url: str = Field(
        default="",
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """HTTP 或 SSE 模式下的服务器地址。"""

    headers: dict[str, str] = Field(
        default_factory=lambda: {},
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "file-json",
        },
    )
    """HTTP/SSE 请求时附加的请求头。"""

    http_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "clock-3",
        },
    )
    """HTTP 请求多久没响应就算超时。"""

    read_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """连接建立后，等服务器消息的最长时间。"""

    authorization: MCPAuthorizationConfig = Field(default_factory=MCPAuthorizationConfig)
    """HTTP/SSE 连接的认证设置。"""

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

        if self.transport == "sse" and not self.url.strip():
            raise ValueError(f"MCP 服务器 {self.name} 使用 sse 时必须填写 url")

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
    """是否启用 MCP 工具接入能力。"""

    client: MCPClientConfig = Field(default_factory=MCPClientConfig)
    """麦麦作为 MCP 客户端时声明的能力。"""

    servers: list[MCPServerItemConfig] = Field(
        default_factory=lambda: [],
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "server",
        },
    )
    """_wrap_要连接的 MCP 服务器列表。"""

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


class PluginConfig(ConfigBase):
    """插件管理配置类"""

    __ui_label__ = "插件"
    __ui_advanced__ = True
    __ui_order__ = 120

    permission: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "label": {
                "zh_CN": "插件管理权限",
                "en_US": "Plugin management permissions",
                "ja_JP": "プラグイン管理権限",
            },
            "x-widget": "tags",
            "x-icon": "shield-check",
            "x-placeholder": "qq:123456789",
        },
    )
    """允许用聊天命令管理插件的用户，格式如 qq:123456789。"""


class PluginRuntimeRenderConfig(ConfigBase):
    """插件运行时浏览器渲染配置。"""

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用浏览器渲染",
                "en_US": "Enable browser rendering",
                "ja_JP": "ブラウザ描画を有効化",
            },
            "x-widget": "switch",
            "x-icon": "image",
        },
    )
    """是否允许插件使用浏览器渲染能力。"""

    browser_ws_endpoint: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "浏览器调试地址",
                "en_US": "Browser debug endpoint",
                "ja_JP": "ブラウザデバッグアドレス",
            },
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """已有 Chrome/Chromium 的调试地址；留空则自动启动。"""

    executable_path: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "浏览器程序路径",
                "en_US": "Browser executable path",
                "ja_JP": "ブラウザ実行ファイルパス",
            },
            "x-widget": "input",
            "x-icon": "folder",
        },
    )
    """浏览器程序路径；留空自动查找。"""

    browser_install_root: str = Field(
        default="data/playwright-browsers",
        json_schema_extra={
            "label": {
                "zh_CN": "浏览器安装目录",
                "en_US": "Browser install directory",
                "ja_JP": "ブラウザインストール先",
            },
            "x-widget": "input",
            "x-icon": "hard-drive",
        },
    )
    """自动下载浏览器时保存的位置。"""

    headless: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "无界面运行",
                "en_US": "Run headless",
                "ja_JP": "画面なしで実行",
            },
            "x-widget": "switch",
            "x-icon": "monitor",
        },
    )
    """是否隐藏浏览器窗口运行。"""

    launch_args: list[str] = Field(
        default_factory=lambda: [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            "--no-sandbox",
            "--no-zygote",
        ],
        json_schema_extra={
            "label": {
                "zh_CN": "启动参数",
                "en_US": "Launch arguments",
                "ja_JP": "起動引数",
            },
            "x-widget": "custom",
            "x-icon": "terminal",
        },
    )
    """启动浏览器时附加的命令参数。"""

    concurrency_limit: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "并发渲染数",
                "en_US": "Concurrent renders",
                "ja_JP": "同時描画数",
            },
            "x-widget": "number",
            "x-icon": "layers",
        },
    )
    """同时最多运行多少个渲染任务。"""

    startup_timeout_sec: float = Field(
        default=20.0,
        gt=0,
        json_schema_extra={
            "label": {
                "zh_CN": "启动超时秒数",
                "en_US": "Startup timeout seconds",
                "ja_JP": "起動タイムアウト秒数",
            },
            "x-widget": "number",
            "x-icon": "clock",
        },
    )
    """浏览器启动或连接的最长等待时间。"""

    render_timeout_sec: float = Field(
        default=15.0,
        gt=0,
        json_schema_extra={
            "label": {
                "zh_CN": "渲染超时秒数",
                "en_US": "Render timeout seconds",
                "ja_JP": "描画タイムアウト秒数",
            },
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """单次渲染任务的最长等待时间。"""

    auto_download_chromium: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "自动下载 Chromium",
                "en_US": "Auto download Chromium",
                "ja_JP": "Chromium を自動ダウンロード",
            },
            "x-widget": "switch",
            "x-icon": "download",
        },
    )
    """找不到浏览器时是否自动下载 Chromium。"""

    download_connection_timeout_sec: float = Field(
        default=120.0,
        gt=0,
        json_schema_extra={
            "label": {
                "zh_CN": "下载连接超时秒数",
                "en_US": "Download connection timeout seconds",
                "ja_JP": "ダウンロード接続タイムアウト秒数",
            },
            "x-widget": "number",
            "x-icon": "cloud-lightning",
        },
    )
    """下载 Chromium 时的连接超时时间。"""

    restart_after_render_count: int = Field(
        default=200,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "渲染后重启次数",
                "en_US": "Restart after render count",
                "ja_JP": "描画後の再起動回数",
            },
            "x-widget": "number",
            "x-icon": "refresh-cw",
        },
    )
    """渲染多少次后重启浏览器；0 表示不自动重启。"""


class PluginRuntimeConfig(ConfigBase):
    """插件运行时配置类"""

    __ui_parent__ = "plugin"
    __ui_label__ = "运行时"

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "启用插件运行时",
                "en_US": "Enable plugin runtime",
                "ja_JP": "プラグインランタイムを有効化",
            },
            "x-widget": "switch",
            "x-icon": "power",
        },
    )
    """是否启用新版插件运行时。"""

    health_check_interval_sec: float = Field(
        default=30.0,
        json_schema_extra={
            "label": {
                "zh_CN": "健康检查间隔秒数",
                "en_US": "Health check interval seconds",
                "ja_JP": "ヘルスチェック間隔秒数",
            },
            "x-widget": "number",
            "x-icon": "activity",
        },
    )
    """每隔多少秒检查一次插件运行状态。"""

    max_restart_attempts: int = Field(
        default=3,
        json_schema_extra={
            "label": {
                "zh_CN": "最大重启次数",
                "en_US": "Maximum restart attempts",
                "ja_JP": "最大再起動回数",
            },
            "x-widget": "number",
            "x-icon": "refresh-cw",
        },
    )
    """插件 Runner 崩溃后最多自动重启几次。"""

    runner_spawn_timeout_sec: float = Field(
        default=30.0,
        json_schema_extra={
            "label": {
                "zh_CN": "Runner 启动超时秒数",
                "en_US": "Runner startup timeout seconds",
                "ja_JP": "Runner 起動タイムアウト秒数",
            },
            "x-widget": "number",
            "x-icon": "clock",
        },
    )
    """等待插件 Runner 启动完成的最长时间。"""

    hook_blocking_timeout_sec: float = Field(
        default=60,
        json_schema_extra={
            "label": {
                "zh_CN": "阻塞 Hook 超时秒数",
                "en_US": "Blocking hook timeout seconds",
                "ja_JP": "ブロッキング Hook タイムアウト秒数",
            },
            "x-widget": "number",
            "x-icon": "timer",
        },
    )
    """单个阻塞 Hook 最多允许运行多久。"""

    ipc_socket_path: str = Field(
        default="",
        json_schema_extra={
            "label": {
                "zh_CN": "通信 Socket 路径",
                "en_US": "IPC socket path",
                "ja_JP": "IPC ソケットパス",
            },
            "x-widget": "input",
            "x-icon": "link",
        },
    )
    """
    自定义插件通信 Socket 路径；留空自动生成。
    """

    render: PluginRuntimeRenderConfig = Field(
        default_factory=PluginRuntimeRenderConfig,
        json_schema_extra={
            "label": {
                "zh_CN": "浏览器渲染",
                "en_US": "Browser rendering",
                "ja_JP": "ブラウザ描画",
            },
        },
    )
    """插件需要网页截图或渲染时使用的浏览器配置。"""

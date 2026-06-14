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

OVERSIZED_IMAGE_HANDLE_METHOD_DESCRIPTIONS = {
    "compress": "压缩图片并继续处理",
    "discard": "丢弃超过最大大小的图片组件",
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
    """平台"""

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
    """QQ账号"""

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
    """其他平台"""

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
    """"""

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
    """别名列表"""


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
    """人格，建议200字以内，描述人格特质和身份特征；可以写完整设定。要求第二人称"""

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
    """可选的多种表达风格列表，当配置不为空时可按概率作为本次回复的一次性提示注入"""

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
    """每次构建回复时，从 multiple_reply_style 中随机注入一次性表达风格提示的概率（0.0-1.0）"""


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
    """是否启用图片缓存自动清理。"""

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
    """图片缓存自动清理任务的轮询间隔，单位为小时。"""

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
    """图片文件超过该天数未使用后会被删除，但识别结果会继续保留。"""

    no_file_result_retention_days: int = Field(
        default=30,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "database",
            "label": {
                "zh_CN": "无文件识别结果保留天数",
                "en_US": "No-file recognition retention days",
                "ja_JP": "ファイルなし認識結果保持日数",
            },
        },
    )
    """图片文件被清理或缺失后，识别结果继续保留的天数。"""


class VisualConfig(ConfigBase):
    """视觉配置类"""

    __ui_label__ = "视觉"

    planner_mode: Literal["text", "multimodal", "auto"] = Field(
        default="auto",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "git-branch",
            "x-layout": "inline-right",
            "x-input-width": "12rem",
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
            "x-layout": "inline-right",
            "x-input-width": "12rem",
            "x-option-descriptions": VISUAL_MODE_OPTION_DESCRIPTIONS,
            "x-row": "visual-modes",
        },
    )
    """回复器模式，auto根据模型信息自动选择，text为纯文本模式，multimodal为多模态模式"""

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
    """多模态请求中最多保留的图片数量；只保留最新图片，超出数量的旧图片会显示为 [图片]。"""

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
    """非视觉 planner 请求前等待图片识别完成的最长秒数；为 0 时不等待，保持占位请求。"""

    handle_oversized_images: bool = Field(
        default=True,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "image",
            "x-layout": "inline-right",
            "x-row": "visual-image-compression",
            "label": {
                "zh_CN": "处理过大图片",
                "en_US": "Handle oversized images",
                "ja_JP": "過大画像を処理",
            },
        },
    )
    """开启后，接收图片会检查大小并按配置处理过大图片；关闭后跳过检查和处理。"""

    max_image_size_mb: float = Field(
        default=30.0,
        ge=0,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "image",
            "x-layout": "inline-right",
            "x-input-width": "8rem",
            "x-row": "visual-image-compression",
            "label": {
                "zh_CN": "最大图片大小(MB)",
                "en_US": "Max image size (MB)",
                "ja_JP": "最大画像サイズ(MB)",
            },
        },
    )
    """接收图片超过该大小时视为过大图片；设为 0 时不限制图片大小。"""

    oversized_image_handle_method: Literal["compress", "discard"] = Field(
        default="compress",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "minimize-2",
            "x-layout": "inline-right",
            "x-row": "visual-image-compression",
            "x-option-descriptions": OVERSIZED_IMAGE_HANDLE_METHOD_DESCRIPTIONS,
            "label": {
                "zh_CN": "过大图片处理方法",
                "en_US": "Oversized image handling",
                "ja_JP": "過大画像の処理方法",
            },
        },
    )
    """接收图片超过最大图片大小时的处理方法：compress 为压缩，discard 为丢弃。"""

    image_cache_cleanup: ImageCacheCleanupConfig = Field(default_factory=ImageCacheCleanupConfig)
    """图片缓存自动清理配置。"""


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
    """平台，与 ID 一起留空表示全局；单独填写时表示该平台下所有聊天流的默认值，"*" 表示平台通配覆盖。"""

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
    """聊天流 ID，与平台一起留空表示全局；单独填写时表示该聊天流 ID 在所有平台下的默认值，"*" 表示聊天流通配覆盖。"""

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
    """聊天流类型，group（群聊）或private（私聊）"""

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
    """留空表示兜底，"HH:MM-HH:MM" 表示指定时间段，"*" 表示强制全天覆盖。支持跨夜区间。"""

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
    """聊天频率值，范围0-1"""


class ChatConfig(ConfigBase):
    """聊天配置类"""

    __ui_label__ = "聊天"

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
    """聊天频率，越小越沉默，范围0-1"""

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
    """私聊聊天频率，越小越沉默，范围0-1"""

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
    """是否启用提及必回复"""

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
    """是否启用at必回复"""

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
            "x-row": "reply-switches",
        },
    )
    """在原有基础上进一步强调哪些消息是自己发送的，减少bot认错的情况，不是高级设置"""

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
            "x-row": "context-sizes",
        },
    )
    """上下文长度"""
    
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
            "x-row": "context-sizes",
        },
    )
    """私聊上下文长度"""

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
            "x-row": "context-sizes",
        },
    )
    """优化50%左右的Planner上下文消耗，但是可能影响缓存，轻微影响性能表现"""

    mid_term_memory: bool = Field(
        default=True,
        json_schema_extra={
            "label": {
                "zh_CN": "中期聊天摘要",
                "en_US": "Mid-term chat summaries",
                "ja_JP": "中期チャット要約",
            },
            "x-widget": "switch",
            "x-icon": "archive",
            "x-row": "context-sizes",
        },
    )
    """上下文裁切时是否使用 utils 模型生成中期聊天摘要，并以可展开复杂消息保留在历史中"""

    mid_term_memory_lenth: int = Field(
        default=10,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "中期摘要保留数",
                "en_US": "Mid-term summary limit",
                "ja_JP": "中期要約保持数",
            },
            "x-widget": "input",
            "x-icon": "archive",
            "x-layout": "inline-right",
            "x-input-width": "6.5rem",
            "x-row": "context-sizes",
        },
    )
    """最多保留多少条中期聊天摘要消息，超出后移除最早的摘要"""

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
    """是否启用回复时附带引用回复"""

    typing_speed: float = Field(
        default=1.0,
        ge=0,
        le=2,
        json_schema_extra={
            "label": {
                "zh_CN": "聊天速度",
                "en_US": "Typing speed",
                "ja_JP": "チャット速度",
            },
            "x-widget": "slider",
            "x-icon": "keyboard",
            "x-row": "reply-speed",
            "step": 0.1,
            "advanced": True,
        },
    )
    """模拟打字时间倍乘，0 表示不等待，1 保持默认等待时间，2 表示等待时间变为默认的两倍"""

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
    """planner如果遇到新消息，重新开始思考的次数"""

    no_action_backoff_base_seconds: float = Field(
        default=15,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "no_action 退避基准",
                "en_US": "no_action backoff base",
                "ja_JP": "no_action バックオフ基準",
            },
            "x-widget": "input",
            "x-icon": "timer",
            "x-description-display": "icon",
            "advanced": False,
        },
    )
    """连续 no_action 后的退避基准秒数，0 表示不启用退避"""

    no_action_backoff_cap_seconds: float = Field(
        default=300,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "no_action 退避上限",
                "en_US": "no_action backoff cap",
                "ja_JP": "no_action バックオフ上限",
            },
            "x-widget": "input",
            "x-icon": "timer-reset",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """连续 no_action 退避秒数上限"""

    no_action_backoff_start_count: int = Field(
        default=2,
        ge=1,
        json_schema_extra={
            "label": {
                "zh_CN": "no_action 退避起点",
                "en_US": "no_action backoff start",
                "ja_JP": "no_action バックオフ開始",
            },
            "x-widget": "input",
            "x-icon": "list-start",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """连续第几次 no_action 后开始退避"""

    no_action_backoff_bypass_pending_count: int = Field(
        default=6,
        ge=0,
        json_schema_extra={
            "label": {
                "zh_CN": "no_action 退避绕过消息数",
                "en_US": "no_action backoff bypass messages",
                "ja_JP": "no_action バックオフ迂回メッセージ数",
            },
            "x-widget": "input",
            "x-icon": "message-square-more",
            "x-description-display": "icon",
            "advanced": True,
        },
    )
    """退避期间待处理消息达到该数量时直接绕过退避，0 表示不按消息数绕过"""

    group_chat_prompt: str = Field(
        default=(
            "你正在qq群里聊天，下面是群里正在聊的内容，其中包含聊天记录和聊天中的图片和表情包。\n"
            "回复尽量简短一些。最好一次对一个话题进行回复，但必须考虑不同群友发言之间的交互，免得啰嗦或者回复内容太乱。请注意把握聊天内容。\n"
            "不要总是提及自己的身份背景，根据聊天内容自由发挥，但是要日常不浮夸，不要刻意找话题，。\n"
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
    """_wrap_群聊通用注意事项"""

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
    """_wrap_私聊通用注意事项"""

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
    """是否启用动态发言频率规则"""

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
    _wrap_思考频率规则列表，支持按聊天流/按日内时段配置。
    """


class ExperimentalConfig(ConfigBase):
    """实验性功能配置类"""

    __ui_label__ = "实验性功能"
    __ui_advanced__ = True

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
    """是否启用行为学习；关闭后不再从裁切历史中抽取和写入行为经验。"""

    enable_replyer_format_output: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "Replyer 格式化输出",
                "en_US": "Replyer formatted output",
                "ja_JP": "Replyer フォーマット出力",
            },
            "x-widget": "switch",
            "x-icon": "braces",
            "advanced": True,
        },
    )
    """
    是否允许 replyer 输出 <text>、<at>、<emoji>、<image> 等格式化片段，
    并在发送前解析为真实消息组件，可能会影响回复表现
    """

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
            "advanced": True,
        },
    )
    """开启后仍正常创建聊天流，但同一时间只有一个 Maisaka 处于活跃关注状态，且忽略聊天频率控制"""

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
            "advanced": True,
        },
    )
    """关闭时，Focus 模式只作用于群聊；开启后，群聊和私聊都会进入 Focus。"""

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
            "advanced": True,
        },
    )
    """_wrap_Focus 互通组；不配置时所有启用 Focus 的聊天共享一个 Focus；配置后只有同组聊天互通，不同组可同时 Focus。"""

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
            "advanced": True,
        },
    )
    """Focus 模式下关注聊天超过该秒数没有进入循环时，会被其他聊天的新消息唤醒一次"""


class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    __ui_label__ = "消息接收"
    __ui_advanced__ = True

    image_parse_threshold: int = Field(
        default=5,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "image",
            "advanced": True,
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
    """用户/群ID，与平台一起留空表示全局"""

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
    """A_Memorix 检索结果分类型聊天过滤配置"""

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用结果过滤",
                "en_US": "Enable result filter",
                "ja_JP": "結果フィルターを有効化",
            },
        },
    )
    """是否启用当前检索结果类型的聊天过滤"""

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
    """A_Memorix 检索结果后置聊天过滤配置"""

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
    """普通 paragraph/relation 命中的检索后置过滤"""

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
    """聊天总结命中的检索后置过滤"""

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
    """Episode 命中的检索后置过滤"""


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
                "zh_CN": "检索结果过滤",
                "en_US": "Retrieval result filter",
                "ja_JP": "検索結果フィルター",
            },
            "x-collapsed-by-default": True,
        },
    )
    """仅对检索结果生效的分类型聊天过滤，不影响写入和后台生成"""


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
    """用户ID，与平台一起留空表示全局"""

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
    """聊天流类型，group（群聊）或private（私聊）"""

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
    """是否使用"""

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
    """是否学习"""


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
    """_wrap_互通聊天流"""


ExperimentalConfig.model_rebuild()


class ExpressionConfig(ConfigBase):
    """表达配置类"""

    __ui_label__ = "学习"

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
        },
    )
    """是否仅选择已由用户人工检查的表达方式"""

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
        },
    )
    """是否在表达学习写入前进行 AI 审核；开启后只有审核通过的表达方式会被写入。"""

    enable_precise_expression_selection: bool = Field(
        default=False,
        json_schema_extra={
            "label": {
                "zh_CN": "启用精细表达选择",
                "en_US": "Enable precise expression selection",
                "ja_JP": "精密な表現選択を有効化",
            },
            "x-widget": "switch",
            "x-icon": "target",
            "advanced": False,
        },
    )
    """是否启用精细表达选择；开启后 replyer 会使用子代理从候选表达中挑选更贴合当前语境的表达方式。"""

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
    """所有聊天流合计允许同时运行的表达学习批次数；同一聊天流始终只允许一个批次。"""

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
    """_wrap_表达学习互通组"""


class JargonConfig(ConfigBase):
    """黑话配置类"""

    __ui_parent__ = "expression"
    __ui_label__ = "黑话"

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
    """_wrap_黑话学习配置列表，支持按聊天流配置，platform 或 item_id 可使用 * 通配"""

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
    """_wrap_黑话学习互通组，默认不互通；platform 或 item_id 可使用 * 通配"""


class VoiceConfig(ConfigBase):
    """语音识别配置类"""

    __ui_label__ = "语音"
    __ui_advanced__ = True

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

    __ui_label__ = "表情"
    __ui_advanced__ = True

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
    """一次从多少个表情包中选择发送，最大为 64"""

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
    """表情包最大注册数量"""

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
    """达到最大注册数量时替换旧表情包，关闭则达到最大数量时不会继续收集表情包"""

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
    """表情包检查间隔（分钟）"""

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
    """是否偷取表情包，让麦麦可以将一些表情包据为己有"""

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
    """偷取/收集聊天表情包时允许保存的最大文件大小，0 表示不限制"""

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
    """是否启用表情包过滤，只有符合该要求的表情包才会被保存"""


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
    __ui_advanced__ = True

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

    max_split_num: int = Field(
        default=3,
        ge=1,
        json_schema_extra={
            "x-widget": "input",
            "x-icon": "list",
        },
    )
    """回复最多分割条数"""

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

    __ui_label__ = "调试"
    __ui_advanced__ = True

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
        default_factory=lambda: {"aiohttp": "WARNING", "PIL": "WARNING"},
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

    show_maisaka_thinking: bool = Field(
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

    record_planner_request: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "file-json",
        },
    )
    """是否记录 Planner 完整请求体和完整回复体，默认关闭"""

    keep_prompt_preview_json_base64: bool = Field(
        default=False,
        json_schema_extra={
            "x-widget": "switch",
            "x-icon": "image",
        },
    )
    """是否在 Prompt 预览 JSON 中保留内联 base64 图片，默认关闭以减少日志体积"""

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
    __ui_advanced__ = True

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

    webui_style: int = Field(
        default=1,
        ge=0,
        le=1,
        json_schema_extra={
            "x-widget": "number",
            "x-icon": "palette",
            "x-layout": "inline-right",
            "x-input-width": "8rem",
        },
    )
    """WebUI界面风格：0为当前风格，1为未来复古"""

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
    """是否要求 WebUI 出站 URL 解析到公网地址；关闭后允许内网、本机或 TUN 代理地址，用于内网 LLM、反向代理等场景。"""

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

    transport: Literal["stdio", "streamable_http", "sse"] = Field(
        default="stdio",
        json_schema_extra={
            "x-widget": "select",
            "x-icon": "shuffle",
        },
    )
    """传输方式，可选 `stdio`、`streamable_http` 或 `sse`"""

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


class PluginConfig(ConfigBase):
    """插件管理配置类"""

    __ui_label__ = "插件"
    __ui_advanced__ = True

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
        },
    )
    """允许使用内置插件管理命令的用户 ID 列表，格式为 platform:id，例如 qq:123456789"""


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

    __ui_parent__ = "plugin"
    __ui_label__ = "运行时"

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
        default=60,
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

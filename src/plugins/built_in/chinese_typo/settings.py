from maibot_sdk import Field, PluginConfigBase
from typing import ClassVar, Literal


class TypoSettings(PluginConfigBase):
    """设置错别字生成方式、概率和纠正消息。"""

    __ui_label__: ClassVar[str] = "错别字生成"

    mode: Literal["rule", "dictionary"] = Field(
        default="rule",
        description="规则模式按拼音和字频生成错字，词库模式只使用内置的易混词对。",
        json_schema_extra={
            "label": "错别字生成模式",
            "i18n": {
                "zh_CN": {"label": "错别字生成模式", "choice:rule": "规则模式", "choice:dictionary": "词库模式"},
                "en_US": {"label": "Typo generation mode", "choice:rule": "Rule mode", "choice:dictionary": "Dictionary mode"},
                "ja_JP": {"label": "誤字生成モード", "choice:rule": "ルールモード", "choice:dictionary": "辞書モード"},
            },
            "x-widget": "select",
        },
    )
    """规则模式按拼音和字频生成错字，词库模式只使用内置的易混词对。"""

    dictionary_probability: float = Field(
        default=0.1,
        description="仅词库模式使用。命中词库时，每次回复最多替换一处。",
        ge=0,
        le=1,
        json_schema_extra={
            "label": "词库模式每次回复的错字概率",
            "i18n": {
                "zh_CN": {"label": "词库模式每次回复的错字概率"},
                "en_US": {"label": "Dictionary typo chance per reply"},
                "ja_JP": {"label": "辞書モードの返信ごとの誤字確率"},
            },
            "x-widget": "slider",
            "step": 0.01,
            "advanced": True,
        },
    )
    """仅词库模式使用。命中词库时，每次回复最多替换一处。"""

    enable_correction_quote: bool = Field(
        default=True,
        json_schema_extra={
            "label": "错别字纠正时引用原消息",
            "i18n": {
                "zh_CN": {"label": "错别字纠正时引用原消息"},
                "en_US": {"label": "Quote typo message when correcting"},
                "ja_JP": {"label": "誤字訂正時に元メッセージを引用"},
            },
            "x-widget": "switch",
            "x-row": "typo-correction-quote",
        },
    )
    """纠正错别字时，是否引用上一条包含错别字的消息。"""

    correction_quote_probability: float = Field(
        default=1.0,
        ge=0,
        le=1,
        json_schema_extra={
            "label": "错别字纠正引用概率",
            "i18n": {
                "zh_CN": {"label": "错别字纠正引用概率"},
                "en_US": {"label": "Typo correction quote chance"},
                "ja_JP": {"label": "誤字訂正の引用確率"},
            },
            "x-widget": "slider",
            "x-row": "typo-correction-quote",
            "step": 0.01,
            "advanced": True,
        },
    )
    """生成纠正消息时，引用上一条错别字消息的概率。"""

    error_rate: float = Field(
        default=0.01,
        ge=0,
        le=1,
        json_schema_extra={
            "label": "单字错字概率",
            "i18n": {
                "zh_CN": {"label": "单字错字概率"},
                "en_US": {"label": "Single-character typo chance"},
                "ja_JP": {"label": "単字誤字確率"},
            },
            "x-widget": "slider",
            "step": 0.01,
            "advanced": True,
        },
    )
    """仅规则模式使用。单个字被替换成错字的概率。"""

    min_freq: int = Field(
        default=9,
        json_schema_extra={
            "label": "最小字频",
            "i18n": {
                "zh_CN": {"label": "最小字频"},
                "en_US": {"label": "Minimum character frequency"},
                "ja_JP": {"label": "最小文字頻度"},
            },
            "x-widget": "input",
            "advanced": True,
        },
    )
    """仅规则模式使用。只对常见程度达到该值的字尝试制造错字。"""

    tone_error_rate: float = Field(
        default=0.1,
        ge=0,
        le=1,
        json_schema_extra={
            "label": "声调错字概率",
            "i18n": {
                "zh_CN": {"label": "声调错字概率"},
                "en_US": {"label": "Tone typo chance"},
                "ja_JP": {"label": "声調誤字確率"},
            },
            "x-widget": "slider",
            "step": 0.1,
            "advanced": True,
        },
    )
    """仅规则模式使用。按相近声调制造错字的概率。"""

    word_replace_rate: float = Field(
        default=0.006,
        ge=0,
        le=1,
        json_schema_extra={
            "label": "整词替换概率",
            "i18n": {
                "zh_CN": {"label": "整词替换概率"},
                "en_US": {"label": "Word replacement chance"},
                "ja_JP": {"label": "単語置換確率"},
            },
            "x-widget": "slider",
            "step": 0.001,
            "advanced": True,
        },
    )
    """仅规则模式使用。整词被替换成错词的概率。"""


class PluginSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件"

    enabled: bool = Field(default=True, description="启用麦麦小工具")
    config_version: str = Field(default="1.0.0", description="插件配置版本")


class ChineseTypoPluginConfig(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    typo: TypoSettings = Field(default_factory=TypoSettings)

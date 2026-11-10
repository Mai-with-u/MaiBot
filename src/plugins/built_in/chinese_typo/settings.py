from maibot_sdk import Field, PluginConfigBase
from typing import ClassVar, List, Literal


class TypoSettings(PluginConfigBase):
    """设置错别字生成方式、概率和纠正消息。"""

    __ui_label__: ClassVar[str] = "错别字生成"

    enabled: bool = Field(
        default=True,
        description="关闭后回复不会再生成错别字。",
        json_schema_extra={
            "label": "启用错别字生成",
            "i18n": {
                "zh_CN": {"label": "启用错别字生成"},
                "en_US": {"label": "Enable typo generation"},
                "ja_JP": {"label": "誤字生成を有効化"},
            },
            "x-widget": "switch",
        },
    )
    """关闭后回复不会再生成错别字。"""

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


class KeywordRule(PluginConfigBase):
    """一条关键词反应规则：命中关键词或正则时，给麦麦追加一段反应提示。"""

    keywords: List[str] = Field(
        default_factory=list,
        description="命中任意一个关键词即触发。",
        json_schema_extra={"label": "关键词"},
    )
    regex: List[str] = Field(
        default_factory=list,
        description="Python 正则；命中任意一个即触发。反应提示里的 [名称] 会替换为同名捕获组的内容。",
        json_schema_extra={"label": "正则表达式"},
    )
    reaction: str = Field(
        default="",
        description="命中后给麦麦看的提示，不会直接当作消息发送。",
        json_schema_extra={"label": "反应提示", "x-widget": "textarea"},
    )


class KeywordReactionSettings(PluginConfigBase):
    """最新消息命中规则时，给麦麦追加固定的反应提示。"""

    __ui_label__: ClassVar[str] = "关键词反应"

    enabled: bool = Field(
        default=True,
        description="关闭后不再匹配关键词规则。",
        json_schema_extra={
            "label": "启用关键词反应",
            "i18n": {
                "zh_CN": {"label": "启用关键词反应"},
                "en_US": {"label": "Enable keyword reactions"},
                "ja_JP": {"label": "キーワード反応を有効化"},
            },
            "x-widget": "switch",
        },
    )
    """关闭后不再匹配关键词规则。"""

    rules: List[KeywordRule] = Field(
        default_factory=list,
        description="命中后追加到回复要求里的提示规则，按顺序匹配，可同时命中多条。",
        json_schema_extra={
            "label": "规则",
            "i18n": {
                "zh_CN": {"label": "规则"},
                "en_US": {"label": "Rules"},
                "ja_JP": {"label": "ルール"},
            },
            "x-widget": "list",
        },
    )
    """命中后追加到回复要求里的提示规则，按顺序匹配，可同时命中多条。"""


class PluginSettings(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件"

    enabled: bool = Field(default=True, description="启用麦麦小工具")
    config_version: str = Field(default="1.0.0", description="插件配置版本")


class ChineseTypoPluginConfig(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    typo: TypoSettings = Field(default_factory=TypoSettings)
    keyword_reaction: KeywordReactionSettings = Field(default_factory=KeywordReactionSettings)

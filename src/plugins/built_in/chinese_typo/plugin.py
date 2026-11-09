"""麦麦小工具：断句后注入错字，以及按关键词追加反应提示。纠正消息仍由主程序编排。"""

from maibot_sdk import HookHandler, MaiBotPlugin
from typing import Any, ClassVar, Dict, List, TypedDict, cast

import asyncio
import random

from .dictionary_typo import create_dictionary_typo
from .keyword_reaction import build_reaction_prompt
from .settings import ChineseTypoPluginConfig, TypoSettings
from .typo_generator import ChineseTypoGenerator


class ReplySegment(TypedDict):
    text: str
    separator: str
    quote_previous: bool
    is_correction: bool


def apply_typos(segments: List[ReplySegment], config: TypoSettings) -> List[ReplySegment]:
    rule_generator = None
    dictionary_edit = None
    if config.mode == "dictionary":
        dictionary_edit = create_dictionary_typo(
            [(segment["text"], segment["separator"]) for segment in segments],
            config.dictionary_probability,
        )
    else:
        rule_generator = ChineseTypoGenerator(
            error_rate=config.error_rate,
            min_freq=config.min_freq,
            tone_error_rate=config.tone_error_rate,
            word_replace_rate=config.word_replace_rate,
        )
    output: List[ReplySegment] = []
    for index, segment in enumerate(segments):
        original = segment["text"]
        text, correction = original, None
        if rule_generator is not None:
            text, correction = rule_generator.create_typo_sentence(original)
            if correction and random.random() >= 0.5:
                text, correction = original, None
        elif dictionary_edit is not None and dictionary_edit[0] == index:
            _, text, original_word = dictionary_edit
            if random.random() < 1 / 3:
                correction = original_word
        output.append({**segment, "text": text})
        if correction:
            output.append({
                "text": correction,
                "separator": "",
                "quote_previous": config.enable_correction_quote
                and random.random() < config.correction_quote_probability,
                "is_correction": True,
            })
    return output


class ChineseTypoPlugin(MaiBotPlugin):
    config_model: ClassVar[type[ChineseTypoPluginConfig]] = ChineseTypoPluginConfig

    async def on_load(self) -> None:
        pass

    async def on_unload(self) -> None:
        pass

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        pass

    @HookHandler("maisaka.reply.after_split", name="inject_chinese_typos")
    async def inject_chinese_typos(
        self, segments: List[ReplySegment], enable_chinese_typo: bool, **kwargs: Any
    ) -> Dict[str, Any]:
        config = cast(ChineseTypoPluginConfig, self.config)
        if not config.plugin.enabled or not config.typo.enabled or not enable_chinese_typo:
            return {"action": "continue"}
        edited = await asyncio.to_thread(apply_typos, segments, config.typo)
        return {
            "action": "continue",
            "modified_kwargs": {**kwargs, "segments": edited, "enable_chinese_typo": enable_chinese_typo},
        }

    @HookHandler(
        "maisaka.replyer.before_request",
        name="inject_keyword_reactions",
        description="最新消息命中关键词或正则规则时，把预设反应提示追加到回复要求",
        timeout_ms=3000,
    )
    async def inject_keyword_reactions(
        self, match_text: str = "", extra_prompt: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        config = cast(ChineseTypoPluginConfig, self.config)
        settings = config.keyword_reaction
        if not config.plugin.enabled or not settings.enabled or not match_text.strip():
            return {"action": "continue"}
        prompt = build_reaction_prompt(match_text, settings.rules)
        if not prompt:
            return {"action": "continue"}
        merged = f"{extra_prompt}\n{prompt}" if extra_prompt.strip() else prompt
        return {
            "action": "continue",
            "modified_kwargs": {**kwargs, "match_text": match_text, "extra_prompt": merged},
        }


def create_plugin() -> ChineseTypoPlugin:
    return ChineseTypoPlugin()

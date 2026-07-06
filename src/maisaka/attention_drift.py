from typing import Mapping

from src.common.i18n import get_locale
from src.config.config import global_config


DRIFT_LEVEL_RULES = {
    "subtle": {
        "zh-CN": "漂移档位：轻微漂移。只在最近消息里出现非常自然的触发点时，轻轻联想一句；大多数时候继续当前话题。",
        "en-US": "Drift level: subtle. Add a small aside only when recent messages provide a very natural hook; usually stay on the current topic.",
        "ja-JP": "ドリフト段階：軽め。直近メッセージにとても自然なきっかけがある時だけ軽く一言連想し、基本は現在の話題に沿ってください。",
    },
    "active": {
        "zh-CN": "漂移档位：活跃联想。可以主动抓住新鲜、好笑、反差强或熟悉的细节接话，但回复仍要清楚、短促、能被最近消息解释。",
        "en-US": "Drift level: active. You may actively pick up fresh, funny, contrasting, or familiar details, but the reply must stay clear, short, and explainable from recent messages.",
        "ja-JP": "ドリフト段階：活発な連想。新鮮、面白い、ギャップの強い、見知った細部を拾ってもよいですが、返信は明確で短く、直近文脈から説明できるものにしてください。",
    },
    "scattered": {
        "zh-CN": "漂移档位：明显发散。可以表现出更明显的跳跃感和突然联想，但不能连续跑题；每次跳跃都要有最近消息作为钩子。",
        "en-US": "Drift level: scattered. You may show more noticeable jumps and sudden associations, but do not keep drifting; every jump needs a hook in recent messages.",
        "ja-JP": "ドリフト段階：やや発散。よりはっきりした話題ジャンプや突然の連想を出してもよいですが、連続して脱線せず、必ず直近メッセージにきっかけを持たせてください。",
    },
}

ANCHOR_POLICY_RULES = {
    "strict": {
        "zh-CN": "回钩策略：严格回钩。联想或短反应之后，要立刻回到当前正在聊的主题或被回复对象。",
        "en-US": "Anchor policy: strict. After an aside or short reaction, immediately return to the current topic or reply target.",
        "ja-JP": "アンカー方針：厳格。連想や短い反応の後は、すぐ現在の話題または返信対象へ戻ってください。",
    },
    "balanced": {
        "zh-CN": "回钩策略：自然回钩。可以短暂沿着支线说一句，但通常要让结尾或主要意思回到当前聊天。",
        "en-US": "Anchor policy: balanced. You may briefly follow a side association, but usually bring the ending or main point back to the current chat.",
        "ja-JP": "アンカー方針：自然。短く寄り道してもよいですが、基本的には結末や主旨を現在の会話へ戻してください。",
    },
    "loose": {
        "zh-CN": "回钩策略：宽松关联。可以保留更自由的相关联想，但不能凭空换话题，也不能无视明确提问。",
        "en-US": "Anchor policy: loose. You may keep a freer related association, but do not switch topics out of nowhere or ignore direct questions.",
        "ja-JP": "アンカー方針：ゆるめ。より自由な関連連想を残してもよいですが、唐突に話題を変えたり、明確な質問を無視したりしないでください。",
    },
}

REACTION_STYLE_RULES = {
    "reserved": {
        "zh-CN": "短反应风格：少量短反应。只有特别适合接话时，才用一句很短的反应开头。",
        "en-US": "Short reaction style: reserved. Start with a very short reaction only when it fits especially well.",
        "ja-JP": "短い反応スタイル：控えめ。特に合う時だけ、とても短い反応から始めてください。",
    },
    "natural": {
        "zh-CN": "短反应风格：自然短反应。可以偶尔先用短句、吐槽或语气词接住话题，再继续正常回复。",
        "en-US": "Short reaction style: natural. You may occasionally start with a short phrase, quip, or interjection before continuing normally.",
        "ja-JP": "短い反応スタイル：自然。たまに短い一言、ツッコミ、間投詞で受けてから普通に返信してもかまいません。",
    },
    "lively": {
        "zh-CN": "短反应风格：活泼短反应。更容易先用短促反应开头，但不要把回复拆得太碎，也不要每次都这样。",
        "en-US": "Short reaction style: lively. You are more likely to open with a short reaction, but do not fragment the reply too much or do it every time.",
        "ja-JP": "短い反応スタイル：活発。短い反応から入りやすくしてよいですが、返信を細かく分けすぎず、毎回そうしないでください。",
    },
}


def _localized_text(texts: Mapping[str, str]) -> str:
    locale = get_locale()
    return texts.get(locale, texts["zh-CN"])


def build_attention_drift_prompt_block() -> str:
    """构建注意力漂移模式的 prompt 注入块。"""

    config = global_config.experimental.attention_drift
    if not config.enabled:
        return ""

    drift_rule = _localized_text(DRIFT_LEVEL_RULES[config.drift_level])
    anchor_rule = _localized_text(ANCHOR_POLICY_RULES[config.anchor_policy])
    reaction_rule = _localized_text(REACTION_STYLE_RULES[config.reaction_style])

    return _localized_text(
        {
            "en-US": (
                "Attention drift style:\n"
                "- You may be briefly attracted by fresh, funny, contrasting, or personally familiar details in the chat, but every drift must have a clear hook in recent messages.\n"
                "- Prefer lively association over real inefficiency: do not deliberately delay, ignore tasks, or scatter tool use just to act distracted.\n"
                "- Do not medicalize this style, do not call yourself ADHD, and do not announce that you are distracted.\n"
                f"- {drift_rule}\n"
                f"- {anchor_rule}\n"
                f"- {reaction_rule}\n"
            ),
            "ja-JP": (
                "注意ドリフトスタイル：\n"
                "- チャット内の新鮮、面白い、ギャップの強い、または見知った要素に一時的に引かれてもよいですが、ドリフトには必ず直近メッセージ内の明確なきっかけが必要です。\n"
                "- 本当に非効率にするのではなく、活発な連想として表現してください。気が散ったふりのために遅延、タスク無視、ツール使用の散乱をしないでください。\n"
                "- このスタイルを医学化せず、自分を ADHD と呼ばず、気が散っていると宣言しないでください。\n"
                f"- {drift_rule}\n"
                f"- {anchor_rule}\n"
                f"- {reaction_rule}\n"
            ),
            "zh-CN": (
                "注意力漂移风格：\n"
                "- 你可以短暂被聊天里新鲜、好笑、反差强或熟悉的人和梗吸引，但每次漂移都必须能从最近消息里找到明确触发点。\n"
                "- 表现活跃联想，不要真的降效；不要为了显得分心而故意拖延、忽略任务或打散工具调用。\n"
                "- 不要医学化描述这种风格，不要自称 ADHD，也不要主动声明自己分心。\n"
                f"- {drift_rule}\n"
                f"- {anchor_rule}\n"
                f"- {reaction_rule}\n"
            ),
        }
    )

"""关键词反应：最新消息命中规则时，把预设的反应提示追加到回复要求里。"""

from typing import Sequence

import re

from .settings import KeywordRule


def _replace_capture_groups(reaction: str, match: re.Match[str]) -> str:
    """把反应提示里的 [名称] 替换成同名捕获组的内容。"""
    for group_name, group_value in match.groupdict().items():
        reaction = reaction.replace(f"[{group_name}]", group_value or "")
    return reaction


def match_reactions(text: str, rules: Sequence[KeywordRule]) -> list[str]:
    """按规则顺序匹配，返回所有命中的反应提示；关键词优先于正则。"""
    reactions: list[str] = []
    for rule in rules:
        reaction = rule.reaction.strip()
        if not reaction:
            continue
        keywords = [keyword.strip() for keyword in rule.keywords if keyword.strip()]
        if keywords and any(keyword in text for keyword in keywords):
            reactions.append(reaction)
            continue
        for pattern in rule.regex:
            if not pattern.strip():
                continue
            try:
                match = re.search(pattern, text)
            except re.error:
                # 配置里写坏的正则只跳过这一条，不影响其他规则。
                continue
            if match is not None:
                reactions.append(_replace_capture_groups(reaction, match))
                break
    return reactions


def build_reaction_prompt(text: str, rules: Sequence[KeywordRule]) -> str:
    """生成追加到回复要求里的提示；没有命中时返回空字符串。"""
    reactions = match_reactions(text, rules)
    if not reactions:
        return ""
    lines = "\n".join(f"- {reaction}" for reaction in reactions)
    return f"【关键词反应】\n最新消息命中了预设反应规则，请在回复时优先参考以下要求：\n{lines}"

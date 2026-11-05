"""从随程序分发的词表挑选一处错字。"""

from functools import cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import jieba
import random
import re


_PROTECTED_PATTERNS = (
    re.compile(r"(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)"),
    re.compile(r"(?<!~)(~{3,})(?!~)[\s\S]*?(?<!~)\1(?!~)"),
    re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|《[^》]*》|"(?:\\.|[^"\\])*"'),
    re.compile(r"(?<!\w)'[^'\n]+'(?!\w)"),
    re.compile(r"(?:https?://|www\.)[^\s<>\"'`，。！？；、）】]+", re.IGNORECASE),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|@[\w.-]+"),
    re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/)[^\s<>\"'`，。！？；、）】]+"),
    re.compile(r"(?:[\w.-]+[\\/])+[\w.-]+"),
)


@cache
def _load_words() -> Dict[str, Dict[str, List[str]]]:
    """按首字建立索引，避免对每句遍历整张词表。"""
    words: Dict[str, Dict[str, List[str]]] = {}
    path = Path(__file__).with_name("typo_words.tsv")
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            original, replacement = line.rstrip("\n").split("\t")
            words.setdefault(original[0], {}).setdefault(original, []).append(replacement)
    return words


def create_dictionary_typo(
    segments: Sequence[Tuple[str, str]], probability: float
) -> Optional[Tuple[int, str, str]]:
    """按整次回复抽签，返回修改段的序号、新正文和用于纠正的原词。"""
    if not segments or probability <= 0 or random.random() >= probability:
        return None

    # 断句可能把同一代码块或引文拆到不同段，保护范围要在整次回复上计算。
    text = "".join(sentence + separator for sentence, separator in segments)
    protected = [match.span() for pattern in _PROTECTED_PATTERNS for match in pattern.finditer(text)]
    words = _load_words()
    candidates: List[Tuple[int, int, int, str, List[str]]] = []
    offset = 0
    for index, (sentence, separator) in enumerate(segments):
        tokens = list(jieba.tokenize(sentence))
        ends = {end for _, _, end in tokens}
        for _, start, _ in tokens:
            for original, variants in words.get(sentence[start], {}).items():
                end = start + len(original)
                if end not in ends or not sentence.startswith(original, start):
                    continue
                if any(offset + start < right and offset + end > left for left, right in protected):
                    continue
                before = text[offset + start - 1] if offset + start else ""
                after = text[offset + end:offset + end + 1]
                # 保留原句已有的叠字，只跳过替换新造出的叠字，如“现再再”。
                allowed = [
                    variant for variant in variants
                    if not (before == variant[0] != original[0] or after == variant[-1] != original[-1])
                ]
                if allowed:
                    candidates.append((index, start, end, original, allowed))
        offset += len(sentence) + len(separator)

    if not candidates:
        return None
    # 先选位置再选错法，变体数量不影响该位置的入选机会。
    index, start, end, original, variants = random.choice(candidates)
    sentence = segments[index][0]
    return index, sentence[:start] + random.choice(variants) + sentence[end:], original

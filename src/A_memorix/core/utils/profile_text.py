"""人物画像结构化文本助手。"""

from __future__ import annotations

from typing import Dict, Iterable, List


PROFILE_SECTION_TITLES = (
    "身份设定",
    "关系设定",
    "稳定了解",
    "相处偏好",
    "近期互动",
    "不确定信息",
    "维护备注",
)

PROFILE_INJECTION_SECTION_TITLES = (
    "身份设定",
    "关系设定",
    "稳定了解",
    "相处偏好",
    "近期互动",
)

PROFILE_REQUIRED_PREFIX = "# 人物画像"
PROFILE_EMPTY_ITEM = "- 暂无"
PROFILE_MAINTENANCE_NOTE = "- 自动画像仅供内部参考；若与当前对话冲突，以当前对话为准。"


def clean_profile_bullet(value: object) -> str:
    """规范化单条画像要点，同时保留文本可编辑性。"""

    text = str(value or "").strip()
    if not text:
        return ""
    while text.startswith("-"):
        text = text[1:].strip()
    if not text:
        return ""
    return f"- {text}"


def dedupe_profile_bullets(values: Iterable[object], *, limit: int) -> List[str]:
    """去重并裁剪画像要点列表。"""

    bullets: List[str] = []
    seen = set()
    for value in values:
        bullet = clean_profile_bullet(value)
        if not bullet or bullet == PROFILE_EMPTY_ITEM:
            continue
        key = bullet.lower()
        if key in seen:
            continue
        seen.add(key)
        bullets.append(bullet)
        if len(bullets) >= max(1, int(limit)):
            break
    return bullets


def section_is_empty(lines: Iterable[str]) -> bool:
    """判断段落是否没有有效内容。"""

    items = [str(line or "").strip() for line in lines]
    return not items or all(not item or item == PROFILE_EMPTY_ITEM for item in items)


def build_structured_profile_text(
    *,
    person_id: str,
    primary_name: str = "",
    aliases: Iterable[object] = (),
    identity_settings: Iterable[object] = (),
    relationship_settings: Iterable[object] = (),
    stable_facts: Iterable[object] = (),
    interaction_preferences: Iterable[object] = (),
    recent_interactions: Iterable[object] = (),
    uncertain_notes: Iterable[object] = (),
) -> str:
    """构建固定格式的人物画像文本。"""

    alias_text = "、".join(str(item or "").strip() for item in aliases if str(item or "").strip())
    sections = {
        "身份设定": dedupe_profile_bullets(identity_settings, limit=4),
        "关系设定": dedupe_profile_bullets(relationship_settings, limit=4),
        "稳定了解": dedupe_profile_bullets(stable_facts, limit=6),
        "相处偏好": dedupe_profile_bullets(interaction_preferences, limit=5),
        "近期互动": dedupe_profile_bullets(recent_interactions, limit=3),
        "不确定信息": dedupe_profile_bullets(uncertain_notes, limit=3),
        "维护备注": [PROFILE_MAINTENANCE_NOTE],
    }

    lines = [
        PROFILE_REQUIRED_PREFIX,
        f"人物ID: {str(person_id or '').strip()}",
        f"主称呼: {str(primary_name or '').strip()}",
        f"别名: {alias_text}",
    ]
    for title in PROFILE_SECTION_TITLES:
        lines.extend(["", f"## {title}"])
        section_lines = sections.get(title) or []
        lines.extend(section_lines if section_lines else [PROFILE_EMPTY_ITEM])
    return "\n".join(lines).strip()


def parse_profile_sections(profile_text: str) -> Dict[str, List[str]]:
    """从可编辑画像文本中解析固定段落。"""

    sections: Dict[str, List[str]] = {}
    current_title = ""
    for raw_line in str(profile_text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            current_title = title if title in PROFILE_SECTION_TITLES else ""
            if current_title:
                sections.setdefault(current_title, [])
            continue
        if current_title:
            sections.setdefault(current_title, []).append(stripped)
    return sections


def is_structured_profile_text(profile_text: str) -> bool:
    """判断文本是否符合结构化画像协议。"""

    text = str(profile_text or "").strip()
    if not text.startswith(PROFILE_REQUIRED_PREFIX):
        return False
    sections = parse_profile_sections(text)
    return all(title in sections for title in PROFILE_SECTION_TITLES)


def replace_profile_section(profile_text: str, title: str, new_lines: Iterable[object]) -> str:
    """替换单个结构化画像段落，同时保留其它文本。"""

    clean_title = str(title or "").strip()
    if clean_title not in PROFILE_SECTION_TITLES:
        raise ValueError(f"不支持的人物画像段落: {clean_title}")

    lines = str(profile_text or "").splitlines()
    out: List[str] = []
    index = 0
    replaced = False
    replacement = dedupe_profile_bullets(new_lines, limit=20) or [PROFILE_EMPTY_ITEM]
    while index < len(lines):
        line = lines[index]
        if line.strip() == f"## {clean_title}":
            out.append(line)
            out.extend(replacement)
            replaced = True
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("## "):
                index += 1
            continue
        out.append(line)
        index += 1

    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"## {clean_title}")
        out.extend(replacement)
    return "\n".join(out).strip()


def build_profile_injection_text(
    profile_text: str,
    *,
    recent_limit: int = 2,
    uncertain_fallback_limit: int = 1,
    uncertain_candidates: Iterable[object] = (),
    include_uncertain_fallback: bool = True,
) -> str:
    """从结构化画像段落构建紧凑注入文本。"""

    text = str(profile_text or "").strip()
    if not is_structured_profile_text(text):
        return text

    sections = parse_profile_sections(text)
    selected: List[str] = []
    meaningful_found = False
    for title in PROFILE_INJECTION_SECTION_TITLES:
        lines = sections.get(title, [])
        if title == "近期互动":
            lines = lines[: max(0, int(recent_limit))]
        if section_is_empty(lines):
            continue
        meaningful_found = True
        selected.extend([f"## {title}", *lines, ""])

    selected_uncertain = dedupe_profile_bullets(uncertain_candidates, limit=2)
    if not section_is_empty(selected_uncertain):
        selected.extend(["## 不确定信息（未确认，不可当作确定事实）", *selected_uncertain, ""])
    elif not meaningful_found and include_uncertain_fallback:
        uncertain = sections.get("不确定信息", [])[: max(0, int(uncertain_fallback_limit))]
        if not section_is_empty(uncertain):
            selected.extend(["## 不确定信息（未确认）", *uncertain, ""])

    return "\n".join(selected).strip()

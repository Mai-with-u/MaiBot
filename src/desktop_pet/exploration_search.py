"""普通主人聊天按需检索探索产物，不依赖桌宠窗口或新探索任务。"""

from typing import Any, Dict, List
import json
import unicodedata

from src.common.prompt_i18n import load_prompt
from src.config.config import global_config

from .chat_tools import is_owner_chat
from .exploration import experience_ref, exploration_tasks, reuse_summary


def build_exploration_reuse_context(session_id: str) -> str:
    """只加入简短使用规则，不把整个产物库塞进每轮上下文。"""
    if not global_config.experimental.desktop_pet_exploration_enabled:
        return ""
    return load_prompt("desktop_pet_exploration_reuse") if is_owner_chat(session_id) else ""


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def search_exploration_products(
    session_id: str, keywords: List[str], kind: str = "all", offset: int = 0, limit: int = 5,
) -> Dict[str, Any]:
    """由工具工作线程调用；按匹配词数、标题相关度和更新时间排序，返回有界分页。"""
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 6:
        raise ValueError("请提供 1 至 6 个具体关键词")
    if any(not isinstance(word, str) or not 1 <= len(word.strip()) <= 80 for word in keywords):
        raise ValueError("每个关键词必须为 1 至 80 字")
    if kind not in {"all", "resource", "experience"}:
        raise ValueError("未知产物分类")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("offset 必须为非负整数，limit 必须为 1 至 10")
    terms = list(dict.fromkeys(_normalized(word.strip()) for word in keywords))
    matches = []
    for task in exploration_tasks(session_id):
        for category, key in (("resource", "resources"), ("experience", "experiences")):
            if kind not in {"all", category}:
                continue
            for index, entry in enumerate(task.get(key, [])):
                title = _normalized(entry["title"])
                reference = experience_ref(entry) if category == "experience" else ""
                reuse = reuse_summary(task, reference) if reference else None
                # 只检索产物内容与目标，不让无关的进度日志制造命中。
                content = _normalized(task["goal"] + "\n" + json.dumps(entry, ensure_ascii=False))
                if reuse is not None:
                    content += _normalized(json.dumps(reuse, ensure_ascii=False))
                hit_terms = [term for term in terms if term in content]
                if not hit_terms:
                    continue
                score = (len(hit_terms), sum(term in title for term in terms), task["updated_at"])
                matches.append((score, {"kind": category, "task_id": task["id"], "product_index": index,
                    "goal": task["goal"], "task_status": task["status"], "updated_at": task["updated_at"],
                    "matched_keywords": hit_terms, "product": entry,
                    **({"experience_ref": reference, "reuse": reuse} if reference else {})}))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    items = [entry for _, entry in matches[offset:offset + limit]]
    next_offset = offset + len(items)
    return {"items": items, "total": len(matches),
            "next_offset": next_offset if next_offset < len(matches) else None}

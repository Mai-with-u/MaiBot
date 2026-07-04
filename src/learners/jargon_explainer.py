from typing import Dict, List, Optional
from sqlmodel import col, func as fn, select

import json

from src.common.database.database import get_db_session
from src.common.database.database_model import Jargon
from src.common.logger import get_logger
from src.common.utils.utils_config import JargonConfigUtils

logger = get_logger("jargon_explainer")


def search_jargon(
    keyword: str,
    chat_id: Optional[str] = None,
    limit: int = 10,
    case_sensitive: bool = False,
    fuzzy: bool = True,
) -> List[Dict[str, str]]:
    """
    搜索 jargon，支持大小写不敏感和模糊搜索

    Args:
        keyword: 搜索关键词
        chat_id: 可选的聊天 ID（session_id）
            - 如果当前聊天命中通配共享组：查询所有 is_global=True 的记录
            - 否则如果提供则优先搜索该聊天、同共享组或 global 的 jargon
        limit: 返回结果数量限制，默认 10
        case_sensitive: 是否大小写敏感，默认 False（不敏感）
        fuzzy: 是否模糊搜索，默认 True（使用 LIKE 匹配）

    Returns:
        List[Dict[str, str]]: 包含 content, meaning 的字典列表
    """
    if not keyword or not keyword.strip():
        return []

    keyword = keyword.strip()

    # 构建搜索条件
    if case_sensitive:  # 大小写敏感
        search_condition = Jargon.content.contains(keyword) if fuzzy else Jargon.content == keyword  # type: ignore
    else:
        keyword_lower = keyword.lower()
        search_condition = (
            fn.LOWER(Jargon.content).contains(keyword_lower) if fuzzy else fn.LOWER(Jargon.content) == keyword_lower
        )

    related_session_ids, _ = JargonConfigUtils.resolve_jargon_group_scope(chat_id)

    # 根据黑话共享组配置在 Python 层面过滤，同时限制结果数量（先多取一些，因为后面可能过滤）
    query = (
        select(Jargon)
        .where(search_condition)
        .where(col(Jargon.is_jargon).is_(True))
        .where(fn.LENGTH(fn.TRIM(Jargon.meaning)) > 0)
        .order_by(Jargon.created_by.desc(), Jargon.count.desc())  # type: ignore
        .limit(limit * 2)
    )

    # 执行查询并返回结果
    results: List[Dict[str, str]] = []
    with get_db_session() as session:
        jargons = session.exec(query).all()

        for jargon in jargons:
            # 如果提供了 chat_id，需要检查 session_id_dict 是否属于当前聊天流或共享组范围
            if chat_id and not jargon.is_global:
                try:  # 解析 session_id_dict
                    session_id_dict = json.loads(jargon.session_id_dict) if jargon.session_id_dict else {}
                except (json.JSONDecodeError, TypeError):
                    session_id_dict = {}
                    logger.warning(
                        f"解析 session_id_dict 失败，jargon_id={jargon.id}，原始数据：{jargon.session_id_dict}"
                    )

                # 检查是否属于目标 chat_id 或同共享组
                if not related_session_ids.intersection(session_id_dict):
                    continue
            # 只返回有 meaning 的记录
            if not jargon.meaning.strip():
                continue

            results.append({"content": jargon.content or "", "meaning": jargon.meaning or ""})
            # 达到限制数量后停止
            if len(results) >= limit:
                break

    return results

"""测试黑话按内容正查、按含义反查的效果。"""

# ruff: noqa: E402

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SearchField = Literal["content", "meaning"]


@dataclass(frozen=True)
class JargonQueryResult:
    """一次黑话查询命中的记录。"""

    jargon_id: int
    content: str
    meaning: str
    count: int
    is_global: bool
    created_by: str
    matched_field: SearchField


def parse_args() -> Namespace:
    parser = ArgumentParser(description="测试黑话正向查询与按含义反向查询。")
    parser.add_argument("keyword", help="查询关键词，例如：认可、调侃、上头、yyds")
    parser.add_argument(
        "--field",
        choices=["content", "meaning", "both"],
        default="both",
        help="查询字段：content 为黑话内容正查，meaning 为含义反查，both 为两者都查。",
    )
    parser.add_argument("--session-id", default="", help="可选：按聊天流及其黑话共享组限制查询范围。")
    parser.add_argument("--limit", type=int, default=10, help="最多输出多少条结果。")
    parser.add_argument("--exact", action="store_true", help="使用精确匹配，不使用包含匹配。")
    parser.add_argument("--case-sensitive", action="store_true", help="大小写敏感匹配。")
    parser.add_argument("--show-scope", action="store_true", help="打印解析出的黑话共享作用域。")
    return parser.parse_args()


def build_text_condition(column: Any, keyword: str, *, case_sensitive: bool, fuzzy: bool) -> Any:
    """构建 content 或 meaning 的文本查询条件。"""

    from sqlmodel import func as fn

    if case_sensitive:
        return column.contains(keyword) if fuzzy else column == keyword  # type: ignore[union-attr]

    keyword_lower = keyword.lower()
    return fn.LOWER(column).contains(keyword_lower) if fuzzy else fn.LOWER(column) == keyword_lower


def jargon_in_scope(jargon: Any, related_session_ids: set[str], session_id: str) -> bool:
    """按现有黑话查询规则检查记录是否属于当前聊天流或共享组。"""

    if not session_id or jargon.is_global:
        return True

    session_id_dict = json.loads(jargon.session_id_dict) if jargon.session_id_dict else {}
    if not isinstance(session_id_dict, dict):
        raise TypeError(f"jargon_id={jargon.id} 的 session_id_dict 不是对象: {jargon.session_id_dict!r}")

    return bool(related_session_ids.intersection(str(item_session_id) for item_session_id in session_id_dict))


def search_jargons(
    keyword: str,
    *,
    field: SearchField,
    session_id: str,
    limit: int,
    case_sensitive: bool,
    fuzzy: bool,
) -> list[JargonQueryResult]:
    """查询确认过且有含义的黑话记录。"""

    from sqlmodel import col, func as fn, select

    from src.common.database.database import get_db_session
    from src.common.database.database_model import Jargon
    from src.common.utils.utils_config import JargonConfigUtils

    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        return []

    related_session_ids, _ = JargonConfigUtils.resolve_jargon_group_scope(session_id or None)
    column = Jargon.content if field == "content" else Jargon.meaning
    search_condition = build_text_condition(
        column,
        normalized_keyword,
        case_sensitive=case_sensitive,
        fuzzy=fuzzy,
    )
    query_limit = max(limit * 3, limit)

    statement = (
        select(Jargon)
        .where(search_condition)
        .where(col(Jargon.is_jargon).is_(True))
        .where(fn.LENGTH(fn.TRIM(Jargon.meaning)) > 0)
        .order_by(col(Jargon.created_by).desc(), col(Jargon.count).desc(), col(Jargon.id).desc())
        .limit(query_limit)
    )

    results: list[JargonQueryResult] = []
    with get_db_session(auto_commit=False) as session:
        for jargon in session.exec(statement).all():
            if not jargon_in_scope(jargon, related_session_ids, session_id):
                continue

            content = str(jargon.content or "").strip()
            meaning = str(jargon.meaning or "").strip()
            if not content or not meaning:
                continue

            results.append(
                JargonQueryResult(
                    jargon_id=int(jargon.id or 0),
                    content=content,
                    meaning=meaning,
                    count=int(jargon.count or 0),
                    is_global=bool(jargon.is_global),
                    created_by=str(jargon.created_by or ""),
                    matched_field=field,
                )
            )
            if len(results) >= limit:
                break

    return results


def deduplicate_results(results: list[JargonQueryResult], limit: int) -> list[JargonQueryResult]:
    """合并 content 与 meaning 双字段查询结果，保留靠前匹配。"""

    deduplicated: list[JargonQueryResult] = []
    seen_ids: set[int] = set()
    for result in results:
        if result.jargon_id in seen_ids:
            continue
        seen_ids.add(result.jargon_id)
        deduplicated.append(result)
        if len(deduplicated) >= limit:
            break
    return deduplicated


def print_scope(session_id: str) -> None:
    from src.common.utils.utils_config import JargonConfigUtils

    related_session_ids, has_global_share = JargonConfigUtils.resolve_jargon_group_scope(session_id or None)
    if not session_id:
        print("作用域：未指定 session_id，将查询所有可见记录。")
        return

    print(f"作用域：session_id={session_id}")
    print(f"共享组 session 数：{len(related_session_ids)}")
    print(f"是否命中全局通配共享组：{has_global_share}")
    for related_session_id in sorted(related_session_ids)[:20]:
        print(f"  - {related_session_id}")
    if len(related_session_ids) > 20:
        print(f"  ... 还有 {len(related_session_ids) - 20} 个")


def print_results(results: list[JargonQueryResult]) -> None:
    if not results:
        print("没有命中黑话。")
        return

    for index, result in enumerate(results, start=1):
        scope = "全局" if result.is_global else "聊天/共享组"
        print(
            f"{index}. [{result.matched_field}] #{result.jargon_id} {result.content} -> {result.meaning} "
            f"(count={result.count}, scope={scope}, created_by={result.created_by})"
        )


def main() -> None:
    args = parse_args()
    keyword = str(args.keyword).strip()
    session_id = str(args.session_id or "").strip()
    limit = max(1, int(args.limit))
    fuzzy = not bool(args.exact)

    if args.show_scope:
        print_scope(session_id)
        print()

    fields: list[SearchField]
    if args.field == "both":
        fields = ["content", "meaning"]
    else:
        fields = [args.field]

    all_results: list[JargonQueryResult] = []
    for field in fields:
        all_results.extend(
            search_jargons(
                keyword,
                field=field,
                session_id=session_id,
                limit=limit,
                case_sensitive=bool(args.case_sensitive),
                fuzzy=fuzzy,
            )
        )

    print_results(deduplicate_results(all_results, limit))


if __name__ == "__main__":
    main()

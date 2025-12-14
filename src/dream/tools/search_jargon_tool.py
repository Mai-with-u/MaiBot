from typing import List

from src.common.logger import get_logger
from src.common.database.database_model import Jargon
from src.config.config import global_config
from src.chat.utils.utils import parse_keywords_string
from src.bw_learner.learner_utils import parse_chat_id_list, chat_id_list_contains

logger = get_logger("dream_agent")


def make_search_jargon(chat_id: str):
    async def search_jargon(keyword: str) -> str:
        """根据一个或多个关键词搜索当前 chat_id 相关的 Jargon 记录概览（只包含 is_jargon=True，是否跨 chat_id 由 all_global 决定）"""
        try:
            if not keyword or not keyword.strip():
                return "未指定查询关键词（参数 keyword 为必填，且不能为空）"

            logger.info(f"[dream][tool] 调用 search_jargon(keyword={keyword}) (作用域 chat_id={chat_id})")

            # 基础条件：只查 is_jargon=True 的记录
            query = Jargon.select().where(Jargon.is_jargon)

            # 根据 all_global 配置决定 chat_id 作用域
            if global_config.expression.all_global_jargon:
                # 开启全局黑话：只看 is_global=True 的记录，不区分 chat_id
                query = query.where(Jargon.is_global)
            else:
                # 关闭全局黑话：后续在 Python 层按 chat_id 列表过滤（包含 is_global=True）
                pass

            # 先按使用次数排序取一批候选，做一个安全上限
            query = query.order_by(Jargon.count.desc()).limit(200)
            candidates = list(query)

            if not candidates:
                msg = "未找到符合条件的 Jargon 记录。"
                logger.info(f"[dream][tool] search_jargon 无记录: {msg}")
                return msg

            # 关键词为必填，因此此处必然执行关键词过滤（支持多个关键词，大小写不敏感）
            keywords_list = parse_keywords_string(keyword) or []
            if not keywords_list and keyword.strip():
                keywords_list = [keyword.strip()]
            keywords_lower = [kw.lower() for kw in keywords_list if kw.strip()]

            # 先按关键词过滤（仅对 content 字段进行匹配）
            filtered_keyword: List[Jargon] = []
            for r in candidates:
                content = (r.content or "").lower()

                # 只要命中任意一个关键词即可视为匹配（OR 逻辑）
                any_matched = False
                for kw in keywords_lower:
                    if not kw:
                        continue
                    if kw in content:
                        any_matched = True
                        break

                if any_matched:
                    filtered_keyword.append(r)

            if global_config.expression.all_global_jargon:
                # 全局黑话模式：不再做 chat_id 过滤，直接使用关键词过滤结果
                records = filtered_keyword
            else:
                # 非全局模式：仅保留全局黑话或 chat_id 列表中包含当前 chat_id 的记录
                records = []
                for r in filtered_keyword:
                    if r.is_global:
                        records.append(r)
                        continue
                    chat_id_list = parse_chat_id_list(r.chat_id)
                    if chat_id_list_contains(chat_id_list, chat_id):
                        records.append(r)

            if not records:
                scope_note = (
                    "（当前为全局黑话模式，仅统计 is_global=True 的条目）"
                    if global_config.expression.all_global_jargon
                    else "（当前为按 chat_id 作用域模式，仅统计全局黑话或与当前 chat_id 相关的条目）"
                )
                return f"未找到包含关键词'{keyword}'的 Jargon 记录{scope_note}"

            lines: List[str] = []
            for r in records:
                is_jargon_str = "是" if r.is_jargon else "否" if r.is_jargon is False else "未判定"
                is_global_str = "全局" if r.is_global else "非全局"
                lines.append(
                    f"ID={r.id} | 内容={r.content} | 含义={r.meaning or '无'} | "
                    f"chat_id={r.chat_id} | {is_global_str} | 是否黑话={is_jargon_str}"
                )

            result = "\n".join(lines)
            logger.info(f"[dream][tool] search_jargon 返回 {len(records)} 条记录")
            return result
        except Exception as e:
            logger.error(f"search_jargon 失败: {e}")
            return f"search_jargon 执行失败: {e}"

    return search_jargon

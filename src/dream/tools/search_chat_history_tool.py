import json
from typing import List, Optional

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.chat.utils.utils import parse_keywords_string

logger = get_logger("dream_agent")


def make_search_chat_history(chat_id: str):
    async def search_chat_history(
        keyword: Optional[str] = None,
        participant: Optional[str] = None,
    ) -> str:
        """根据关键词或参与人查询记忆，返回匹配的记忆id、记忆标题theme和关键词keywords（dream 维护专用版本）"""
        try:
            # 检查参数
            if not keyword and not participant:
                return "未指定查询参数（需要提供keyword或participant之一）"

            logger.info(
                f"[dream][tool] 调用 search_chat_history(keyword={keyword}, participant={participant}) "
                f"(作用域 chat_id={chat_id})"
            )

            # 构建查询条件
            query = ChatHistory.select().where(ChatHistory.chat_id == chat_id)

            # 执行查询（按时间倒序，最近的在前）
            records = list(query.order_by(ChatHistory.start_time.desc()).limit(50))

            filtered_records: List[ChatHistory] = []

            for record in records:
                participant_matched = True  # 如果没有participant条件，默认为True
                keyword_matched = True  # 如果没有keyword条件，默认为True

                # 检查参与人匹配
                if participant:
                    participant_matched = False
                    participants_list: List[str] = []
                    if record.participants:
                        try:
                            participants_data = (
                                json.loads(record.participants)
                                if isinstance(record.participants, str)
                                else record.participants
                            )
                            if isinstance(participants_data, list):
                                participants_list = [str(p).lower() for p in participants_data]
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass

                    participant_lower = participant.lower().strip()
                    if participant_lower and any(participant_lower in p for p in participants_list):
                        participant_matched = True

                # 检查关键词匹配
                if keyword:
                    keyword_matched = False
                    # 解析多个关键词（支持空格、逗号等分隔符）
                    keywords_list = parse_keywords_string(keyword)
                    if not keywords_list:
                        keywords_list = [keyword.strip()] if keyword.strip() else []

                    # 转换为小写以便匹配
                    keywords_lower = [kw.lower() for kw in keywords_list if kw.strip()]

                    if keywords_lower:
                        # 在theme、keywords、summary、original_text中搜索
                        theme = (record.theme or "").lower()
                        summary = (record.summary or "").lower()
                        original_text = (record.original_text or "").lower()

                        # 解析record中的keywords JSON
                        record_keywords_list: List[str] = []
                        if record.keywords:
                            try:
                                keywords_data = (
                                    json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
                                )
                                if isinstance(keywords_data, list):
                                    record_keywords_list = [str(k).lower() for k in keywords_data]
                            except (json.JSONDecodeError, TypeError, ValueError):
                                pass

                        # 有容错的全匹配：如果关键词数量>2，允许n-1个关键词匹配；否则必须全部匹配
                        matched_count = 0
                        for kw in keywords_lower:
                            kw_matched = (
                                kw in theme
                                or kw in summary
                                or kw in original_text
                                or any(kw in k for k in record_keywords_list)
                            )
                            if kw_matched:
                                matched_count += 1

                        # 计算需要匹配的关键词数量
                        total_keywords = len(keywords_lower)
                        if total_keywords > 2:
                            # 关键词数量>2，允许n-1个关键词匹配
                            required_matches = total_keywords - 1
                        else:
                            # 关键词数量<=2，必须全部匹配
                            required_matches = total_keywords

                        keyword_matched = matched_count >= required_matches

                # 两者都匹配（如果同时有participant和keyword，需要两者都匹配；如果只有一个条件，只需要该条件匹配）
                matched = participant_matched and keyword_matched

                if matched:
                    filtered_records.append(record)

            if not filtered_records:
                if keyword and participant:
                    keywords_str = "、".join(parse_keywords_string(keyword) if keyword else [])
                    return f"未找到包含关键词'{keywords_str}'且参与人包含'{participant}'的聊天记录"
                elif keyword:
                    keywords_list = parse_keywords_string(keyword)
                    keywords_str = "、".join(keywords_list)
                    if len(keywords_list) > 2:
                        required_count = len(keywords_list) - 1
                        return f"未找到包含至少{required_count}个关键词（共{len(keywords_list)}个）'{keywords_str}'的聊天记录"
                    else:
                        return f"未找到包含所有关键词'{keywords_str}'的聊天记录"
                elif participant:
                    return f"未找到参与人包含'{participant}'的聊天记录"
                else:
                    return "未找到相关聊天记录"

            # 如果匹配结果超过20条，不返回具体记录，只返回提示和所有相关关键词
            if len(filtered_records) > 20:
                all_keywords_set = set()
                for record in filtered_records:
                    if record.keywords:
                        try:
                            keywords_data = (
                                json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
                            )
                            if isinstance(keywords_data, list):
                                for k in keywords_data:
                                    k_str = str(k).strip()
                                    if k_str:
                                        all_keywords_set.add(k_str)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue

                search_label = keyword or participant or "当前条件"

                if all_keywords_set:
                    keywords_str = "、".join(sorted(all_keywords_set))
                    response_text = (
                        f"包含“{search_label}”的结果过多，请尝试更多关键词精确查找\n\n"
                        f'有关"{search_label}"的关键词：\n'
                        f"{keywords_str}"
                    )
                else:
                    response_text = (
                        f"包含“{search_label}”的结果过多，请尝试更多关键词精确查找\n\n"
                        f'有关"{search_label}"的关键词信息为空'
                    )

                logger.info(
                    f"[dream][tool] search_chat_history 匹配结果超过20条，返回关键词汇总提示，总数={len(filtered_records)}"
                )
                return response_text

            # 构建结果文本，返回id、theme和keywords（最多20条）
            results: List[str] = []
            for record in filtered_records[:20]:
                result_parts: List[str] = []

                # 记忆ID
                result_parts.append(f"记忆ID：{record.id}")

                # 主题
                if record.theme:
                    result_parts.append(f"主题：{record.theme}")
                else:
                    result_parts.append("主题：（无）")

                # 关键词
                if record.keywords:
                    try:
                        keywords_data = (
                            json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
                        )
                        if isinstance(keywords_data, list) and keywords_data:
                            keywords_str = "、".join([str(k) for k in keywords_data])
                            result_parts.append(f"关键词：{keywords_str}")
                        else:
                            result_parts.append("关键词：（无）")
                    except (json.JSONDecodeError, TypeError, ValueError):
                        result_parts.append("关键词：（无）")
                else:
                    result_parts.append("关键词：（无）")

                results.append("\n".join(result_parts))

            if not results:
                return "未找到相关聊天记录"

            response_text = "\n\n---\n\n".join(results)

            logger.info(f"[dream][tool] search_chat_history 返回 {len(filtered_records)} 条匹配记录")
            return response_text
        except Exception as e:
            logger.error(f"search_chat_history 失败: {e}")
            return f"search_chat_history 执行失败: {e}"

    return search_chat_history

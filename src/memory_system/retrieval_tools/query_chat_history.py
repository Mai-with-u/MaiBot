"""
根据时间或关键词在chat_history中查询 - 工具实现
从ChatHistory表的聊天记录概述库中查询
"""

import json
from typing import Optional
from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.chat.utils.utils import parse_keywords_string
from .tool_registry import register_memory_retrieval_tool
from ..memory_utils import parse_datetime_to_timestamp, parse_time_range

logger = get_logger("memory_retrieval_tools")


async def query_chat_history(
    chat_id: str, keyword: Optional[str] = None, time_range: Optional[str] = None, fuzzy: bool = True
) -> str:
    """根据时间或关键词在chat_history表中查询聊天记录概述

    Args:
        chat_id: 聊天ID
        keyword: 关键词（可选，支持多个关键词，可用空格、逗号等分隔）
        time_range: 时间范围或时间点，格式：
            - 时间范围："YYYY-MM-DD HH:MM:SS - YYYY-MM-DD HH:MM:SS"
            - 时间点："YYYY-MM-DD HH:MM:SS"（查询包含该时间点的记录）
        fuzzy: 是否使用模糊匹配模式（默认True）
            - True: 模糊匹配，只要包含任意一个关键词即匹配（OR关系）
            - False: 全匹配，必须包含所有关键词才匹配（AND关系）

    Returns:
        str: 查询结果
    """
    try:
        # 检查参数
        if not keyword and not time_range:
            return "未指定查询参数（需要提供keyword或time_range之一）"

        # 构建查询条件
        query = ChatHistory.select().where(ChatHistory.chat_id == chat_id)

        # 时间过滤条件
        if time_range:
            # 判断是时间点还是时间范围
            if " - " in time_range:
                # 时间范围：查询与时间范围有交集的记录
                start_timestamp, end_timestamp = parse_time_range(time_range)
                # 交集条件：start_time < end_timestamp AND end_time > start_timestamp
                time_filter = (ChatHistory.start_time < end_timestamp) & (ChatHistory.end_time > start_timestamp)
            else:
                # 时间点：查询包含该时间点的记录（start_time <= time_point <= end_time）
                target_timestamp = parse_datetime_to_timestamp(time_range)
                time_filter = (ChatHistory.start_time <= target_timestamp) & (ChatHistory.end_time >= target_timestamp)
            query = query.where(time_filter)

        # 执行查询
        records = list(query.order_by(ChatHistory.start_time.desc()).limit(50))

        # 如果有关键词，进一步过滤
        if keyword:
            # 解析多个关键词（支持空格、逗号等分隔符）
            keywords_list = parse_keywords_string(keyword)
            if not keywords_list:
                keywords_list = [keyword.strip()] if keyword.strip() else []

            # 转换为小写以便匹配
            keywords_lower = [kw.lower() for kw in keywords_list if kw.strip()]

            if not keywords_lower:
                return "关键词为空"

            filtered_records = []

            for record in records:
                # 在theme、keywords、summary、original_text中搜索
                theme = (record.theme or "").lower()
                summary = (record.summary or "").lower()
                original_text = (record.original_text or "").lower()

                # 解析record中的keywords JSON
                record_keywords_list = []
                if record.keywords:
                    try:
                        keywords_data = (
                            json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
                        )
                        if isinstance(keywords_data, list):
                            record_keywords_list = [str(k).lower() for k in keywords_data]
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass

                # 根据匹配模式检查关键词
                matched = False
                if fuzzy:
                    # 模糊匹配：只要包含任意一个关键词即匹配（OR关系）
                    for kw in keywords_lower:
                        if (
                            kw in theme
                            or kw in summary
                            or kw in original_text
                            or any(kw in k for k in record_keywords_list)
                        ):
                            matched = True
                            break
                else:
                    # 全匹配：必须包含所有关键词才匹配（AND关系）
                    matched = True
                    for kw in keywords_lower:
                        kw_matched = (
                            kw in theme
                            or kw in summary
                            or kw in original_text
                            or any(kw in k for k in record_keywords_list)
                        )
                        if not kw_matched:
                            matched = False
                            break

                if matched:
                    filtered_records.append(record)

            if not filtered_records:
                keywords_str = "、".join(keywords_list)
                match_mode = "包含任意一个关键词" if fuzzy else "包含所有关键词"
                if time_range:
                    return f"未找到{match_mode}'{keywords_str}'且在指定时间范围内的聊天记录概述"
                else:
                    return f"未找到{match_mode}'{keywords_str}'的聊天记录概述"

            records = filtered_records

        # 如果没有记录（可能是时间范围查询但没有匹配的记录）
        if not records:
            if time_range:
                return "未找到指定时间范围内的聊天记录概述"
            else:
                return "未找到相关聊天记录概述"

        # 对即将返回的记录增加使用计数
        records_to_use = records[:3]
        for record in records_to_use:
            try:
                ChatHistory.update(count=ChatHistory.count + 1).where(ChatHistory.id == record.id).execute()
                record.count = (record.count or 0) + 1
            except Exception as update_error:
                logger.error(f"更新聊天记录概述计数失败: {update_error}")

        # 构建结果文本
        results = []
        for record in records_to_use:  # 最多返回3条记录
            result_parts = []

            # 添加主题
            if record.theme:
                result_parts.append(f"主题：{record.theme}")

            # 添加时间范围
            from datetime import datetime

            start_str = datetime.fromtimestamp(record.start_time).strftime("%Y-%m-%d %H:%M:%S")
            end_str = datetime.fromtimestamp(record.end_time).strftime("%Y-%m-%d %H:%M:%S")
            result_parts.append(f"时间：{start_str} - {end_str}")

            # 添加概括（优先使用summary，如果没有则使用original_text的前200字符）
            if record.summary:
                result_parts.append(f"概括：{record.summary}")
            elif record.original_text:
                text_preview = record.original_text[:200]
                if len(record.original_text) > 200:
                    text_preview += "..."
                result_parts.append(f"内容：{text_preview}")

            results.append("\n".join(result_parts))

        if not results:
            return "未找到相关聊天记录概述"

        response_text = "\n\n---\n\n".join(results)
        if len(records) > len(records_to_use):
            omitted_count = len(records) - len(records_to_use)
            response_text += f"\n\n(还有{omitted_count}条历史记录已省略)"
        return response_text

    except Exception as e:
        logger.error(f"查询聊天历史概述失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    register_memory_retrieval_tool(
        name="query_chat_history",
        description="根据时间或关键词在聊天记录中查询。可以查询某个时间点发生了什么、某个时间范围内的事件，或根据关键词搜索消息概述。支持两种匹配模式：模糊匹配（默认，只要包含任意一个关键词即匹配）和全匹配（必须包含所有关键词才匹配）",
        parameters=[
            {
                "name": "keyword",
                "type": "string",
                "description": "关键词（可选，支持多个关键词，可用空格、逗号、斜杠等分隔，如：'麦麦 百度网盘' 或 '麦麦,百度网盘'。用于在主题、关键词、概括、原文中搜索）",
                "required": False,
            },
            {
                "name": "time_range",
                "type": "string",
                "description": "时间范围或时间点（可选）。格式：'YYYY-MM-DD HH:MM:SS - YYYY-MM-DD HH:MM:SS'（时间范围，查询与时间范围有交集的记录）或 'YYYY-MM-DD HH:MM:SS'（时间点，查询包含该时间点的记录）",
                "required": False,
            },
            {
                "name": "fuzzy",
                "type": "boolean",
                "description": "是否使用模糊匹配模式（默认True）。True表示模糊匹配（只要包含任意一个关键词即匹配，OR关系），False表示全匹配（必须包含所有关键词才匹配，AND关系）",
                "required": False,
            },
        ],
        execute_func=query_chat_history,
    )

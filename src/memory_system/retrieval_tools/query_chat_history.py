"""
根据关键词或参与人在chat_history中查询记忆 - 工具实现
从ChatHistory表的聊天记录概述库中查询
"""

import json
from typing import Optional
from datetime import datetime

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.chat.utils.utils import parse_keywords_string
from src.config.config import global_config
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


async def search_chat_history(chat_id: str, keyword: Optional[str] = None, participant: Optional[str] = None) -> str:
    """根据关键词或参与人查询记忆，返回匹配的记忆id、记忆标题theme和关键词keywords

    Args:
        chat_id: 聊天ID
        keyword: 关键词（可选，支持多个关键词，可用空格、逗号等分隔。匹配规则：如果关键词数量<=2，必须全部匹配；如果关键词数量>2，允许n-1个关键词匹配）
        participant: 参与人昵称（可选）

    Returns:
        str: 查询结果，包含记忆id、theme和keywords
    """
    try:
        # 检查参数
        if not keyword and not participant:
            return "未指定查询参数（需要提供keyword或participant之一）"

        # 构建查询条件
        # 根据配置决定是否限制在当前 chat_id 内查询
        use_global_search = global_config.memory.global_memory

        if use_global_search:
            # 全局查询所有聊天记录
            query = ChatHistory.select()
            logger.debug(
                f"search_chat_history 启用全局查询模式，忽略 chat_id 过滤，keyword={keyword}, participant={participant}"
            )
        else:
            # 仅在当前聊天流内查询
            query = ChatHistory.select().where(ChatHistory.chat_id == chat_id)

        # 执行查询
        records = list(query.order_by(ChatHistory.start_time.desc()).limit(50))

        filtered_records = []

        for record in records:
            participant_matched = True  # 如果没有participant条件，默认为True
            keyword_matched = True  # 如果没有keyword条件，默认为True

            # 检查参与人匹配
            if participant:
                participant_matched = False
                participants_list = []
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
                keywords_str = "、".join(parse_keywords_string(keyword))
                keywords_list = parse_keywords_string(keyword)
                if len(keywords_list) > 2:
                    required_count = len(keywords_list) - 1
                    return (
                        f"未找到包含至少{required_count}个关键词（共{len(keywords_list)}个）'{keywords_str}'的聊天记录"
                    )
                else:
                    return f"未找到包含所有关键词'{keywords_str}'的聊天记录"
            elif participant:
                return f"未找到参与人包含'{participant}'的聊天记录"
            else:
                return "未找到相关聊天记录"

        # 如果匹配结果超过20条，不返回具体记录，只返回提示和所有相关关键词
        if len(filtered_records) > 15:
            # 统计所有记录上的关键词并去重
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

            # xxx 使用用户原始查询词，优先 keyword，其次 participant，最后退化成“当前条件”
            search_label = keyword or participant or "当前条件"

            if all_keywords_set:
                keywords_str = "、".join(sorted(all_keywords_set))
                return (
                    f"包含“{search_label}”的结果过多，请尝试更多关键词精确查找\n\n"
                    f'有关"{search_label}"的关键词：\n'
                    f"{keywords_str}"
                )
            else:
                return (
                    f'包含“{search_label}”的结果过多，请尝试更多关键词精确查找\n\n有关"{search_label}"的关键词信息为空'
                )

        # 构建结果文本，返回id、theme和keywords（最多20条）
        results = []
        for record in filtered_records[:20]:
            result_parts = []

            # 添加记忆ID
            result_parts.append(f"记忆ID：{record.id}")

            # 添加主题
            if record.theme:
                result_parts.append(f"主题：{record.theme}")
            else:
                result_parts.append("主题：（无）")

            # 添加关键词
            if record.keywords:
                try:
                    keywords_data = json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
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
        return response_text

    except Exception as e:
        logger.error(f"查询聊天历史概述失败: {e}")
        return f"查询失败: {str(e)}"


async def get_chat_history_detail(chat_id: str, memory_ids: str) -> str:
    """根据记忆ID，展示某条或某几条记忆的具体内容

    Args:
        chat_id: 聊天ID
        memory_ids: 记忆ID，可以是单个ID（如"123"）或多个ID（用逗号分隔，如"1,2,3"）

    Returns:
        str: 记忆的详细内容
    """
    try:
        # 解析memory_ids
        id_list = []
        # 尝试解析为逗号分隔的ID列表
        try:
            id_list = [int(id_str.strip()) for id_str in memory_ids.split(",") if id_str.strip()]
        except ValueError:
            return f"无效的记忆ID格式: {memory_ids}，请使用数字ID，多个ID用逗号分隔（如：'123' 或 '123,456'）"

        if not id_list:
            return "未提供有效的记忆ID"

        # 查询记录
        query = ChatHistory.select().where((ChatHistory.chat_id == chat_id) & (ChatHistory.id.in_(id_list)))
        records = list(query.order_by(ChatHistory.start_time.desc()))

        if not records:
            return f"未找到ID为{id_list}的记忆记录（可能ID不存在或不属于当前聊天）"

        # 对即将返回的记录增加使用计数
        for record in records:
            try:
                ChatHistory.update(count=ChatHistory.count + 1).where(ChatHistory.id == record.id).execute()
                record.count = (record.count or 0) + 1
            except Exception as update_error:
                logger.error(f"更新聊天记录概述计数失败: {update_error}")

        # 构建详细结果
        results = []
        for record in records:
            result_parts = []

            # 添加记忆ID
            result_parts.append(f"记忆ID：{record.id}")

            # 添加主题
            if record.theme:
                result_parts.append(f"主题：{record.theme}")

            # 添加时间范围
            start_str = datetime.fromtimestamp(record.start_time).strftime("%Y-%m-%d %H:%M:%S")
            end_str = datetime.fromtimestamp(record.end_time).strftime("%Y-%m-%d %H:%M:%S")
            result_parts.append(f"时间：{start_str} - {end_str}")

            # 添加参与人
            if record.participants:
                try:
                    participants_data = (
                        json.loads(record.participants) if isinstance(record.participants, str) else record.participants
                    )
                    if isinstance(participants_data, list) and participants_data:
                        participants_str = "、".join([str(p) for p in participants_data])
                        result_parts.append(f"参与人：{participants_str}")
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            # 添加关键词
            if record.keywords:
                try:
                    keywords_data = json.loads(record.keywords) if isinstance(record.keywords, str) else record.keywords
                    if isinstance(keywords_data, list) and keywords_data:
                        keywords_str = "、".join([str(k) for k in keywords_data])
                        result_parts.append(f"关键词：{keywords_str}")
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            # 添加概括
            if record.summary:
                result_parts.append(f"概括：{record.summary}")

            # 添加关键信息点
            if record.key_point:
                try:
                    key_point_data = (
                        json.loads(record.key_point) if isinstance(record.key_point, str) else record.key_point
                    )
                    if isinstance(key_point_data, list) and key_point_data:
                        key_point_str = "\n".join([f"  - {str(kp)}" for kp in key_point_data])
                        result_parts.append(f"关键信息点：\n{key_point_str}")
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            results.append("\n".join(result_parts))

        if not results:
            return "未找到相关记忆记录"

        response_text = "\n\n" + "=" * 50 + "\n\n".join(results)
        return response_text

    except Exception as e:
        logger.error(f"获取记忆详情失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    # 注册工具1：搜索记忆
    register_memory_retrieval_tool(
        name="search_chat_history",
        description="根据关键词或参与人查询记忆，返回匹配的记忆id、记忆标题theme和关键词keywords。用于快速搜索和定位相关记忆。匹配规则：如果关键词数量<=2，必须全部匹配；如果关键词数量>2，允许n-1个关键词匹配（容错匹配）。",
        parameters=[
            {
                "name": "keyword",
                "type": "string",
                "description": "关键词（可选，支持多个关键词，可用空格、逗号、斜杠等分隔，如：'麦麦 百度网盘' 或 '麦麦,百度网盘'。用于在主题、关键词、概括、原文中搜索。匹配规则：如果关键词数量<=2，必须全部匹配；如果关键词数量>2，允许n-1个关键词匹配）",
                "required": False,
            },
            {
                "name": "participant",
                "type": "string",
                "description": "参与人昵称（可选），用于查询包含该参与人的记忆",
                "required": False,
            },
        ],
        execute_func=search_chat_history,
    )

    # 注册工具2：获取记忆详情
    register_memory_retrieval_tool(
        name="get_chat_history_detail",
        description="根据记忆ID，展示某条或某几条记忆的具体内容。包括主题、时间、参与人、关键词、概括和关键信息点等详细信息。需要先使用search_chat_history工具获取记忆ID。",
        parameters=[
            {
                "name": "memory_ids",
                "type": "string",
                "description": "记忆ID，可以是单个ID（如'123'）或多个ID（用逗号分隔，如'123,456,789'）",
                "required": True,
            },
        ],
        execute_func=get_chat_history_detail,
    )

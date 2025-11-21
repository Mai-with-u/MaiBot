"""
根据person_name查询用户信息 - 工具实现
支持模糊查询，可以查询某个用户的所有信息
"""

import json
from datetime import datetime
from src.common.logger import get_logger
from src.common.database.database_model import PersonInfo
from .tool_registry import register_memory_retrieval_tool

logger = get_logger("memory_retrieval_tools")


def _format_group_nick_names(group_nick_name_field) -> str:
    """格式化群昵称信息

    Args:
        group_nick_name_field: 群昵称字段（可能是字符串JSON或None）

    Returns:
        str: 格式化后的群昵称信息字符串
    """
    if not group_nick_name_field:
        return ""

    try:
        # 解析JSON格式的群昵称列表
        group_nick_names_data = (
            json.loads(group_nick_name_field) if isinstance(group_nick_name_field, str) else group_nick_name_field
        )

        if not isinstance(group_nick_names_data, list) or not group_nick_names_data:
            return ""

        # 格式化群昵称列表
        group_nick_list = []
        for item in group_nick_names_data:
            if isinstance(item, dict):
                group_id = item.get("group_id", "未知群号")
                group_nick_name = item.get("group_nick_name", "未知群昵称")
                group_nick_list.append(f"  - 群号 {group_id}：{group_nick_name}")
            elif isinstance(item, str):
                # 兼容旧格式（如果存在）
                group_nick_list.append(f"  - {item}")

        if group_nick_list:
            return "群昵称：\n" + "\n".join(group_nick_list)
        return ""
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"解析群昵称信息失败: {e}")
        # 如果解析失败，尝试显示原始内容（截断）
        if isinstance(group_nick_name_field, str):
            preview = group_nick_name_field[:200]
            if len(group_nick_name_field) > 200:
                preview += "..."
            return f"群昵称（原始数据）：{preview}"
        return ""


async def query_person_info(person_name: str) -> str:
    """根据person_name查询用户信息，使用模糊查询

    Args:
        person_name: 用户名称（person_name字段）

    Returns:
        str: 查询结果，包含用户的所有信息
    """
    try:
        person_name = str(person_name).strip()
        if not person_name:
            return "用户名称为空"

        # 构建查询条件（使用模糊查询）
        query = PersonInfo.select().where(PersonInfo.person_name.contains(person_name))

        # 执行查询
        records = list(query.limit(20))  # 最多返回20条记录

        if not records:
            return f"未找到模糊匹配'{person_name}'的用户信息"

        # 区分精确匹配和模糊匹配的结果
        exact_matches = []
        fuzzy_matches = []

        for record in records:
            # 检查是否是精确匹配
            if record.person_name and record.person_name.strip() == person_name:
                exact_matches.append(record)
            else:
                fuzzy_matches.append(record)

        # 构建结果文本
        results = []

        # 先处理精确匹配的结果
        for record in exact_matches:
            result_parts = []
            result_parts.append("【精确匹配】")  # 标注为精确匹配

            # 基本信息
            if record.person_name:
                result_parts.append(f"用户名称：{record.person_name}")
            if record.nickname:
                result_parts.append(f"昵称：{record.nickname}")
            if record.person_id:
                result_parts.append(f"用户ID：{record.person_id}")
            if record.platform:
                result_parts.append(f"平台：{record.platform}")
            if record.user_id:
                result_parts.append(f"平台用户ID：{record.user_id}")

            # 群昵称信息
            group_nick_name_str = _format_group_nick_names(getattr(record, "group_nick_name", None))
            if group_nick_name_str:
                result_parts.append(group_nick_name_str)

            # 名称设定原因
            if record.name_reason:
                result_parts.append(f"名称设定原因：{record.name_reason}")

            # 认识状态
            result_parts.append(f"是否已认识：{'是' if record.is_known else '否'}")

            # 时间信息
            if record.know_since:
                know_since_str = datetime.fromtimestamp(record.know_since).strftime("%Y-%m-%d %H:%M:%S")
                result_parts.append(f"首次认识时间：{know_since_str}")
            if record.last_know:
                last_know_str = datetime.fromtimestamp(record.last_know).strftime("%Y-%m-%d %H:%M:%S")
                result_parts.append(f"最后认识时间：{last_know_str}")
            if record.know_times:
                result_parts.append(f"认识次数：{int(record.know_times)}")

            # 记忆点（memory_points）
            if record.memory_points:
                try:
                    memory_points_data = (
                        json.loads(record.memory_points)
                        if isinstance(record.memory_points, str)
                        else record.memory_points
                    )
                    if isinstance(memory_points_data, list) and memory_points_data:
                        # 解析记忆点格式：category:content:weight
                        memory_list = []
                        for memory_point in memory_points_data:
                            if memory_point and isinstance(memory_point, str):
                                parts = memory_point.split(":", 2)
                                if len(parts) >= 3:
                                    category = parts[0].strip()
                                    content = parts[1].strip()
                                    weight = parts[2].strip()
                                    memory_list.append(f"  - [{category}] {content} (权重: {weight})")
                                else:
                                    memory_list.append(f"  - {memory_point}")

                        if memory_list:
                            result_parts.append("记忆点：\n" + "\n".join(memory_list))
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.warning(f"解析用户 {record.person_id} 的memory_points失败: {e}")
                    # 如果解析失败，直接显示原始内容（截断）
                    memory_preview = str(record.memory_points)[:200]
                    if len(str(record.memory_points)) > 200:
                        memory_preview += "..."
                    result_parts.append(f"记忆点（原始数据）：{memory_preview}")

            results.append("\n".join(result_parts))

        # 再处理模糊匹配的结果
        for record in fuzzy_matches:
            result_parts = []
            result_parts.append("【模糊匹配】")  # 标注为模糊匹配

            # 基本信息
            if record.person_name:
                result_parts.append(f"用户名称：{record.person_name}")
            if record.nickname:
                result_parts.append(f"昵称：{record.nickname}")
            if record.person_id:
                result_parts.append(f"用户ID：{record.person_id}")
            if record.platform:
                result_parts.append(f"平台：{record.platform}")
            if record.user_id:
                result_parts.append(f"平台用户ID：{record.user_id}")

            # 群昵称信息
            group_nick_name_str = _format_group_nick_names(getattr(record, "group_nick_name", None))
            if group_nick_name_str:
                result_parts.append(group_nick_name_str)

            # 名称设定原因
            if record.name_reason:
                result_parts.append(f"名称设定原因：{record.name_reason}")

            # 认识状态
            result_parts.append(f"是否已认识：{'是' if record.is_known else '否'}")

            # 时间信息
            if record.know_since:
                know_since_str = datetime.fromtimestamp(record.know_since).strftime("%Y-%m-%d %H:%M:%S")
                result_parts.append(f"首次认识时间：{know_since_str}")
            if record.last_know:
                last_know_str = datetime.fromtimestamp(record.last_know).strftime("%Y-%m-%d %H:%M:%S")
                result_parts.append(f"最后认识时间：{last_know_str}")
            if record.know_times:
                result_parts.append(f"认识次数：{int(record.know_times)}")

            # 记忆点（memory_points）
            if record.memory_points:
                try:
                    memory_points_data = (
                        json.loads(record.memory_points)
                        if isinstance(record.memory_points, str)
                        else record.memory_points
                    )
                    if isinstance(memory_points_data, list) and memory_points_data:
                        # 解析记忆点格式：category:content:weight
                        memory_list = []
                        for memory_point in memory_points_data:
                            if memory_point and isinstance(memory_point, str):
                                parts = memory_point.split(":", 2)
                                if len(parts) >= 3:
                                    category = parts[0].strip()
                                    content = parts[1].strip()
                                    weight = parts[2].strip()
                                    memory_list.append(f"  - [{category}] {content} (权重: {weight})")
                                else:
                                    memory_list.append(f"  - {memory_point}")

                        if memory_list:
                            result_parts.append("记忆点：\n" + "\n".join(memory_list))
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.warning(f"解析用户 {record.person_id} 的memory_points失败: {e}")
                    # 如果解析失败，直接显示原始内容（截断）
                    memory_preview = str(record.memory_points)[:200]
                    if len(str(record.memory_points)) > 200:
                        memory_preview += "..."
                    result_parts.append(f"记忆点（原始数据）：{memory_preview}")

            results.append("\n".join(result_parts))

        # 组合所有结果
        if not results:
            return f"未找到匹配'{person_name}'的用户信息"

        response_text = "\n\n---\n\n".join(results)

        # 添加统计信息
        total_count = len(records)
        exact_count = len(exact_matches)
        fuzzy_count = len(fuzzy_matches)

        # 显示精确匹配和模糊匹配的统计
        if exact_count > 0 or fuzzy_count > 0:
            stats_parts = []
            if exact_count > 0:
                stats_parts.append(f"精确匹配：{exact_count} 条")
            if fuzzy_count > 0:
                stats_parts.append(f"模糊匹配：{fuzzy_count} 条")
            stats_text = "，".join(stats_parts)
            response_text = f"找到 {total_count} 条匹配的用户信息（{stats_text}）：\n\n{response_text}"
        elif total_count > 1:
            response_text = f"找到 {total_count} 条匹配的用户信息：\n\n{response_text}"
        else:
            response_text = f"找到用户信息：\n\n{response_text}"

        # 如果结果数量达到限制，添加提示
        if total_count >= 20:
            response_text += "\n\n(已显示前20条结果，可能还有更多匹配记录)"

        return response_text

    except Exception as e:
        logger.error(f"查询用户信息失败: {e}")
        return f"查询失败: {str(e)}"


def register_tool():
    """注册工具"""
    register_memory_retrieval_tool(
        name="query_person_info",
        description="根据查询某个用户的所有信息。名称、昵称、平台、用户ID、qq号、群昵称等",
        parameters=[
            {"name": "person_name", "type": "string", "description": "用户名称，用于查询用户信息", "required": True}
        ],
        execute_func=query_person_info,
    )

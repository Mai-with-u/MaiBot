"""
统计和展示 replyer 动作选择记录

用法:
    python scripts/replyer_action_stats.py
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from src.common.database.database_model import ChatStreams
    from src.chat.message_receive.chat_manager import chat_manager as _script_chat_manager
except ImportError:
    ChatStreams = None
    _script_chat_manager = None


def get_chat_name(chat_id: str) -> str:
    """根据 chat_id 获取聊天名称"""
    try:
        if ChatStreams:
            chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == chat_id)
            if chat_stream:
                if chat_stream.group_name:
                    return f"{chat_stream.group_name}"
                elif chat_stream.user_nickname:
                    return f"{chat_stream.user_nickname}的私聊"

        if get_chat_manager:
            chat_manager = get_chat_manager()
            stream_name = chat_manager.get_stream_name(chat_id)
            if stream_name:
                return stream_name

        return f"未知聊天 ({chat_id[:8]}...)"
    except Exception:
        return f"查询失败 ({chat_id[:8]}...)"


def load_records(temp_dir: str = "data/temp") -> List[Dict[str, Any]]:
    """加载所有 replyer 动作记录"""
    records = []
    temp_path = Path(temp_dir)

    if not temp_path.exists():
        print(f"目录不存在: {temp_dir}")
        return records

    # 查找所有 replyer_action_*.json 文件
    pattern = "replyer_action_*.json"
    for file_path in temp_path.glob(pattern):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                records.append(data)
        except Exception as e:
            print(f"读取文件失败 {file_path}: {e}")

    # 按时间戳排序
    records.sort(key=lambda x: x.get("timestamp", ""))
    return records


def format_timestamp(ts: str) -> str:
    """格式化时间戳"""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def calculate_time_distribution(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """计算时间分布"""
    now = datetime.now()
    distribution = {
        "今天": 0,
        "昨天": 0,
        "3天内": 0,
        "7天内": 0,
        "30天内": 0,
        "更早": 0,
    }

    for record in records:
        try:
            ts = record.get("timestamp", "")
            if not ts:
                continue
            dt = datetime.fromisoformat(ts)
            diff = (now - dt).days

            if diff == 0:
                distribution["今天"] += 1
            elif diff == 1:
                distribution["昨天"] += 1
            elif diff < 3:
                distribution["3天内"] += 1
            elif diff < 7:
                distribution["7天内"] += 1
            elif diff < 30:
                distribution["30天内"] += 1
            else:
                distribution["更早"] += 1
        except Exception:
            pass

    return distribution


def print_statistics(records: List[Dict[str, Any]]):
    """打印统计信息"""
    if not records:
        print("没有找到任何记录")
        return

    print("=" * 80)
    print("Replyer 动作选择记录统计")
    print("=" * 80)
    print()

    # 总记录数
    total_count = len(records)
    print(f"📊 总记录数: {total_count}")
    print()

    # 时间范围
    timestamps = [r.get("timestamp", "") for r in records if r.get("timestamp")]
    if timestamps:
        first_time = format_timestamp(min(timestamps))
        last_time = format_timestamp(max(timestamps))
        print(f"📅 时间范围: {first_time} ~ {last_time}")
        print()

    # 按 think_level 统计
    think_levels = [r.get("think_level", 0) for r in records]
    think_level_counter = Counter(think_levels)
    print("🧠 思考深度分布:")
    for level in sorted(think_level_counter.keys()):
        count = think_level_counter[level]
        percentage = (count / total_count) * 100
        level_name = {0: "不需要思考", 1: "简单思考", 2: "深度思考"}.get(level, f"未知({level})")
        print(f"  Level {level} ({level_name}): {count} 次 ({percentage:.1f}%)")
    print()

    # 按 chat_id 统计（总体）
    chat_counter = Counter([r.get("chat_id", "未知") for r in records])
    print(f"💬 聊天分布 (共 {len(chat_counter)} 个聊天):")
    # 只显示前10个
    for chat_id, count in chat_counter.most_common(10):
        chat_name = get_chat_name(chat_id)
        percentage = (count / total_count) * 100
        print(f"  {chat_name}: {count} 次 ({percentage:.1f}%)")
    if len(chat_counter) > 10:
        print(f"  ... 还有 {len(chat_counter) - 10} 个聊天")
    print()

    # 每个 chat_id 的详细统计
    print("=" * 80)
    print("每个聊天的详细统计")
    print("=" * 80)
    print()

    # 按 chat_id 分组记录
    records_by_chat = defaultdict(list)
    for record in records:
        chat_id = record.get("chat_id", "未知")
        records_by_chat[chat_id].append(record)

    # 按记录数排序
    sorted_chats = sorted(records_by_chat.items(), key=lambda x: len(x[1]), reverse=True)

    for chat_id, chat_records in sorted_chats:
        chat_name = get_chat_name(chat_id)
        chat_count = len(chat_records)
        chat_percentage = (chat_count / total_count) * 100

        print(f"📱 {chat_name} ({chat_id[:8]}...)")
        print(f"   总记录数: {chat_count} ({chat_percentage:.1f}%)")

        # 该聊天的 think_level 分布
        chat_think_levels = [r.get("think_level", 0) for r in chat_records]
        chat_think_counter = Counter(chat_think_levels)
        print("   思考深度分布:")
        for level in sorted(chat_think_counter.keys()):
            level_count = chat_think_counter[level]
            level_percentage = (level_count / chat_count) * 100
            level_name = {0: "不需要思考", 1: "简单思考", 2: "深度思考"}.get(level, f"未知({level})")
            print(f"     Level {level} ({level_name}): {level_count} 次 ({level_percentage:.1f}%)")

        # 该聊天的时间范围
        chat_timestamps = [r.get("timestamp", "") for r in chat_records if r.get("timestamp")]
        if chat_timestamps:
            first_time = format_timestamp(min(chat_timestamps))
            last_time = format_timestamp(max(chat_timestamps))
            print(f"   时间范围: {first_time} ~ {last_time}")

        # 该聊天的时间分布
        chat_time_dist = calculate_time_distribution(chat_records)
        print("   时间分布:")
        for period, count in chat_time_dist.items():
            if count > 0:
                period_percentage = (count / chat_count) * 100
                print(f"     {period}: {count} 次 ({period_percentage:.1f}%)")

        # 显示该聊天最近的一条理由示例
        if chat_records:
            latest_record = chat_records[-1]
            reason = latest_record.get("reason", "无理由")
            if len(reason) > 120:
                reason = reason[:120] + "..."
            timestamp = format_timestamp(latest_record.get("timestamp", ""))
            think_level = latest_record.get("think_level", 0)
            print(f"   最新记录 [{timestamp}] (Level {think_level}): {reason}")

        print()

    # 时间分布
    time_dist = calculate_time_distribution(records)
    print("⏰ 时间分布:")
    for period, count in time_dist.items():
        if count > 0:
            percentage = (count / total_count) * 100
            print(f"  {period}: {count} 次 ({percentage:.1f}%)")
    print()

    # 显示一些示例理由
    print("📝 示例理由 (最近5条):")
    recent_records = records[-5:]
    for i, record in enumerate(recent_records, 1):
        reason = record.get("reason", "无理由")
        think_level = record.get("think_level", 0)
        timestamp = format_timestamp(record.get("timestamp", ""))
        chat_id = record.get("chat_id", "未知")
        chat_name = get_chat_name(chat_id)

        # 截断过长的理由
        if len(reason) > 100:
            reason = reason[:100] + "..."

        print(f"  {i}. [{timestamp}] {chat_name} (Level {think_level})")
        print(f"     {reason}")
        print()

    # 按 think_level 分组显示理由示例
    print("=" * 80)
    print("按思考深度分类的示例理由")
    print("=" * 80)
    print()

    for level in [0, 1, 2]:
        level_records = [r for r in records if r.get("think_level") == level]
        if not level_records:
            continue

        level_name = {0: "不需要思考", 1: "简单思考", 2: "深度思考"}.get(level, f"未知({level})")
        print(f"Level {level} ({level_name}) - 共 {len(level_records)} 条:")

        # 显示3个示例（选择最近的）
        examples = level_records[-3:] if len(level_records) >= 3 else level_records
        for i, record in enumerate(examples, 1):
            reason = record.get("reason", "无理由")
            if len(reason) > 150:
                reason = reason[:150] + "..."
            timestamp = format_timestamp(record.get("timestamp", ""))
            chat_id = record.get("chat_id", "未知")
            chat_name = get_chat_name(chat_id)
            print(f"  {i}. [{timestamp}] {chat_name}")
            print(f"     {reason}")
        print()

    # 统计信息汇总
    print("=" * 80)
    print("统计汇总")
    print("=" * 80)
    print(f"总记录数: {total_count}")
    print(f"涉及聊天数: {len(chat_counter)}")
    if chat_counter:
        avg_count = total_count / len(chat_counter)
        print(f"平均每个聊天记录数: {avg_count:.1f}")
    else:
        print("平均每个聊天记录数: N/A")
    print()


def main():
    """主函数"""
    records = load_records()
    print_statistics(records)


if __name__ == "__main__":
    main()

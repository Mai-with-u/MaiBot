import argparse
import json
import random
import re
import sys
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.message_repository import find_messages
from src.chat.utils.chat_message_builder import build_readable_messages

# 确保可从任意工作目录运行：将项目根目录加入 sys.path（scripts 的上一级）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


SECONDS_5_MINUTES = 5 * 60


def clean_output_text(text: str) -> str:
    """
    清理输出文本，移除表情包和回复内容
    - 移除 [表情包：...] 格式的内容
    - 移除 [回复...] 格式的内容
    """
    if not text:
        return text

    # 移除表情包内容：[表情包：...]
    text = re.sub(r"\[表情包：[^\]]*\]", "", text)

    # 移除回复内容：[回复...]，说：... 的完整模式
    text = re.sub(r"\[回复[^\]]*\]，说：[^@]*@[^:]*:", "", text)

    # 清理多余的空格和换行
    text = re.sub(r"\s+", " ", text).strip()

    return text


def parse_datetime_to_timestamp(value: str) -> float:
    """
    接受多种常见格式并转换为时间戳（秒）
    支持示例：
    - 2025-09-29
    - 2025-09-29 00:00:00
    - 2025/09/29 00:00
    - 2025-09-29T00:00:00
    """
    value = value.strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]
    last_err: Optional[Exception] = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.timestamp()
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise ValueError(f"无法解析时间: {value} ({last_err})")


def fetch_messages_between(
    start_ts: float,
    end_ts: float,
    platform: Optional[str] = None,
) -> List[DatabaseMessages]:
    """使用 find_messages 获取指定区间的消息，可选按 chat_info_platform 过滤。按时间升序返回。"""
    filter_query: Dict[str, object] = {"time": {"$gt": start_ts, "$lt": end_ts}}
    if platform:
        filter_query["chat_info_platform"] = platform
    # 当 limit==0 时，sort 生效，这里按时间升序
    return find_messages(message_filter=filter_query, sort=[("time", 1)], limit=0)


def group_by_chat(messages: Iterable[DatabaseMessages]) -> Dict[str, List[DatabaseMessages]]:
    groups: Dict[str, List[DatabaseMessages]] = {}
    for msg in messages:
        groups.setdefault(msg.chat_id, []).append(msg)
    # 保证每个分组内按时间升序
    for _chat_id, msgs in groups.items():
        msgs.sort(key=lambda m: m.time or 0)
    return groups


def _merge_bucket_to_message(bucket: List[DatabaseMessages]) -> DatabaseMessages:
    """
    将相邻、同一 user_id 且 5 分钟内的消息 bucket 合并为一条。
    processed_plain_text 合并（以换行连接），其余字段取最新一条（时间最大）。
    """
    if not bucket:
        raise ValueError("bucket 为空，无法合并")

    latest = bucket[-1]
    merged_texts: List[str] = []
    for m in bucket:
        text = m.processed_plain_text or ""
        if text:
            merged_texts.append(text)

    merged = DatabaseMessages(
        # 其他信息采用最新消息
        message_id=latest.message_id,
        time=latest.time,
        chat_id=latest.chat_id,
        reply_to=latest.reply_to,
        interest_value=latest.interest_value,
        key_words=latest.key_words,
        key_words_lite=latest.key_words_lite,
        is_mentioned=latest.is_mentioned,
        is_at=latest.is_at,
        reply_probability_boost=latest.reply_probability_boost,
        processed_plain_text="\n".join(merged_texts) if merged_texts else latest.processed_plain_text,
        display_message=latest.display_message,
        priority_mode=latest.priority_mode,
        priority_info=latest.priority_info,
        additional_config=latest.additional_config,
        is_emoji=latest.is_emoji,
        is_picid=latest.is_picid,
        is_command=latest.is_command,
        is_notify=latest.is_notify,
        selected_expressions=latest.selected_expressions,
        user_id=latest.user_info.user_id,
        user_nickname=latest.user_info.user_nickname,
        user_cardname=latest.user_info.user_cardname,
        user_platform=latest.user_info.platform,
        chat_info_group_id=(latest.group_info.group_id if latest.group_info else None),
        chat_info_group_name=(latest.group_info.group_name if latest.group_info else None),
        chat_info_group_platform=(latest.group_info.group_platform if latest.group_info else None),
        chat_info_user_id=latest.chat_info.user_info.user_id,
        chat_info_user_nickname=latest.chat_info.user_info.user_nickname,
        chat_info_user_cardname=latest.chat_info.user_info.user_cardname,
        chat_info_user_platform=latest.chat_info.user_info.platform,
        chat_info_stream_id=latest.chat_info.stream_id,
        chat_info_platform=latest.chat_info.platform,
        chat_info_create_time=latest.chat_info.create_time,
        chat_info_last_active_time=latest.chat_info.last_active_time,
    )
    return merged


def merge_adjacent_same_user(messages: List[DatabaseMessages]) -> List[DatabaseMessages]:
    """按 5 分钟窗口合并相邻同 user_id 的消息。输入需按时间升序。"""
    if not messages:
        return []

    merged: List[DatabaseMessages] = []
    bucket: List[DatabaseMessages] = []

    def flush_bucket() -> None:
        nonlocal bucket
        if bucket:
            merged.append(_merge_bucket_to_message(bucket))
            bucket = []

    for msg in messages:
        if not bucket:
            bucket = [msg]
            continue

        last = bucket[-1]
        same_user = msg.user_info.user_id == last.user_info.user_id
        close_enough = (msg.time or 0) - (last.time or 0) <= SECONDS_5_MINUTES

        if same_user and close_enough:
            bucket.append(msg)
        else:
            flush_bucket()
            bucket = [msg]

    flush_bucket()
    return merged


def build_pairs_for_chat(
    original_messages: List[DatabaseMessages],
    merged_messages: List[DatabaseMessages],
    min_ctx: int,
    max_ctx: int,
    target_user_id: Optional[str] = None,
) -> List[Tuple[str, str, str]]:
    """
    对每条合并后的消息作为 output，从其前面取 20-30 条（可配置）的原始消息作为 input。
    input 使用原始未合并的消息构建上下文。
    output 使用合并后消息的 processed_plain_text。
    如果指定了 target_user_id，则只处理该用户的消息作为 output。
    """
    pairs: List[Tuple[str, str, str]] = []
    n_merged = len(merged_messages)
    n_original = len(original_messages)

    if n_merged == 0 or n_original == 0:
        return pairs

    # 为每个合并后的消息找到对应的原始消息位置
    merged_to_original_map = {}
    original_idx = 0

    for merged_idx, merged_msg in enumerate(merged_messages):
        # 找到这个合并消息对应的第一个原始消息
        while original_idx < n_original and original_messages[original_idx].time < merged_msg.time:
            original_idx += 1

        # 如果找到了时间匹配的原始消息，建立映射
        if original_idx < n_original and original_messages[original_idx].time == merged_msg.time:
            merged_to_original_map[merged_idx] = original_idx

    for merged_idx in range(n_merged):
        merged_msg = merged_messages[merged_idx]

        # 如果指定了 target_user_id，只处理该用户的消息作为 output
        if target_user_id and merged_msg.user_info.user_id != target_user_id:
            continue

        # 找到对应的原始消息位置
        if merged_idx not in merged_to_original_map:
            continue

        original_idx = merged_to_original_map[merged_idx]

        # 选择上下文窗口大小
        window = random.randint(min_ctx, max_ctx) if max_ctx > min_ctx else min_ctx
        start = max(0, original_idx - window)
        context_msgs = original_messages[start:original_idx]

        # 使用原始未合并消息构建 input
        input_str = build_readable_messages(
            messages=context_msgs,
            timestamp_mode="normal_no_YMD",
            show_actions=False,
            show_pic=True,
        )

        # 输出取合并后消息的 processed_plain_text 并清理表情包和回复内容
        output_text = merged_msg.processed_plain_text or ""
        output_text = clean_output_text(output_text)
        output_id = merged_msg.message_id or ""
        pairs.append((input_str, output_text, output_id))

    return pairs


def build_pairs(
    start_ts: float,
    end_ts: float,
    platform: Optional[str],
    user_id: Optional[str],
    min_ctx: int,
    max_ctx: int,
) -> List[Tuple[str, str, str]]:
    # 获取所有消息（不按user_id过滤），这样input上下文可以包含所有用户的消息
    messages = fetch_messages_between(start_ts, end_ts, platform)
    groups = group_by_chat(messages)

    all_pairs: List[Tuple[str, str, str]] = []
    for _chat_id, msgs in groups.items():  # noqa: F841 - chat_id 未直接使用
        # 对消息进行合并，用于output
        merged = merge_adjacent_same_user(msgs)
        # 传递原始消息和合并后消息，input使用原始消息，output使用合并后消息
        pairs = build_pairs_for_chat(msgs, merged, min_ctx, max_ctx, user_id)
        all_pairs.extend(pairs)

    return all_pairs


def main(argv: Optional[List[str]] = None) -> int:
    # 若未提供参数，则进入交互模式
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) == 0:
        return run_interactive()

    parser = argparse.ArgumentParser(description="构建 (input_str, output_str, message_id) 列表，支持按用户ID筛选消息")
    parser.add_argument("start", help="起始时间，如 2025-09-28 00:00:00")
    parser.add_argument("end", help="结束时间，如 2025-09-29 00:00:00")
    parser.add_argument("--platform", default=None, help="仅选择 chat_info_platform 为该值的消息")
    parser.add_argument("--user_id", default=None, help="仅选择指定 user_id 的消息")
    parser.add_argument("--min_ctx", type=int, default=20, help="输入上下文的最少条数，默认20")
    parser.add_argument("--max_ctx", type=int, default=30, help="输入上下文的最多条数，默认30")
    parser.add_argument(
        "--output",
        default=None,
        help="输出保存路径，支持 .jsonl（每行 {input, output}），若不指定则打印到stdout",
    )

    args = parser.parse_args(argv)

    start_ts = parse_datetime_to_timestamp(args.start)
    end_ts = parse_datetime_to_timestamp(args.end)
    if end_ts <= start_ts:
        raise ValueError("结束时间必须大于起始时间")

    if args.max_ctx < args.min_ctx:
        raise ValueError("max_ctx 不能小于 min_ctx")

    pairs = build_pairs(start_ts, end_ts, args.platform, args.user_id, args.min_ctx, args.max_ctx)

    if args.output:
        # 保存为 JSONL，每行一个 {input, output, message_id}
        with open(args.output, "w", encoding="utf-8") as f:
            for input_str, output_str, message_id in pairs:
                obj = {"input": input_str, "output": output_str, "message_id": message_id}
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        print(f"已保存 {len(pairs)} 条到 {args.output}")
    else:
        # 打印到 stdout
        for input_str, output_str, message_id in pairs:
            print(json.dumps({"input": input_str, "output": output_str, "message_id": message_id}, ensure_ascii=False))

    return 0


def _prompt_with_default(prompt_text: str, default: Optional[str]) -> str:
    suffix = f"[{default}]" if default not in (None, "") else ""
    value = input(f"{prompt_text}{' ' + suffix if suffix else ''}: ").strip()
    if value == "" and default is not None:
        return default
    return value


def run_interactive() -> int:
    print("进入交互模式（直接回车采用默认值）。时间格式例如：2025-09-28 00:00:00 或 2025-09-28")
    start_str = _prompt_with_default("请输入起始时间", None)
    end_str = _prompt_with_default("请输入结束时间", None)
    platform = _prompt_with_default("平台（可留空表示不限）", "")
    user_id = _prompt_with_default("用户ID（可留空表示不限）", "")
    try:
        min_ctx = int(_prompt_with_default("输入上下文最少条数", "20"))
        max_ctx = int(_prompt_with_default("输入上下文最多条数", "30"))
    except Exception:
        print("上下文条数输入有误，使用默认 20/30")
        min_ctx, max_ctx = 20, 30
    output_path = _prompt_with_default("输出路径（.jsonl，可留空打印到控制台）", "")

    if not start_str or not end_str:
        print("必须提供起始与结束时间。")
        return 2

    try:
        start_ts = parse_datetime_to_timestamp(start_str)
        end_ts = parse_datetime_to_timestamp(end_str)
    except Exception as e:  # noqa: BLE001
        print(f"时间解析失败：{e}")
        return 2

    if end_ts <= start_ts:
        print("结束时间必须大于起始时间。")
        return 2

    if max_ctx < min_ctx:
        print("最多条数不能小于最少条数。")
        return 2

    platform_val = platform if platform != "" else None
    user_id_val = user_id if user_id != "" else None
    pairs = build_pairs(start_ts, end_ts, platform_val, user_id_val, min_ctx, max_ctx)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            for input_str, output_str, message_id in pairs:
                obj = {"input": input_str, "output": output_str, "message_id": message_id}
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        print(f"已保存 {len(pairs)} 条到 {output_path}")
    else:
        for input_str, output_str, message_id in pairs:
            print(json.dumps({"input": input_str, "output": output_str, "message_id": message_id}, ensure_ascii=False))
        print(f"总计 {len(pairs)} 条。")

    return 0


if __name__ == "__main__":
    sys.exit(main())

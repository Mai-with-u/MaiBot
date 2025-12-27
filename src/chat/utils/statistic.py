import asyncio
import concurrent.futures
import json

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple, List

from src.common.logger import get_logger
from src.common.database.database import db
from src.common.database.database_model import OnlineTime, LLMUsage, Messages, ActionRecords
from src.manager.async_task_manager import AsyncTask
from src.manager.local_store_manager import local_storage
from src.config.config import global_config

logger = get_logger("maibot_statistic")

# 统计数据的键
TOTAL_REQ_CNT = "total_requests"
TOTAL_COST = "total_cost"
REQ_CNT_BY_TYPE = "requests_by_type"
REQ_CNT_BY_USER = "requests_by_user"
REQ_CNT_BY_MODEL = "requests_by_model"
REQ_CNT_BY_MODULE = "requests_by_module"
IN_TOK_BY_TYPE = "in_tokens_by_type"
IN_TOK_BY_USER = "in_tokens_by_user"
IN_TOK_BY_MODEL = "in_tokens_by_model"
IN_TOK_BY_MODULE = "in_tokens_by_module"
OUT_TOK_BY_TYPE = "out_tokens_by_type"
OUT_TOK_BY_USER = "out_tokens_by_user"
OUT_TOK_BY_MODEL = "out_tokens_by_model"
OUT_TOK_BY_MODULE = "out_tokens_by_module"
TOTAL_TOK_BY_TYPE = "tokens_by_type"
TOTAL_TOK_BY_USER = "tokens_by_user"
TOTAL_TOK_BY_MODEL = "tokens_by_model"
TOTAL_TOK_BY_MODULE = "tokens_by_module"
COST_BY_TYPE = "costs_by_type"
COST_BY_USER = "costs_by_user"
COST_BY_MODEL = "costs_by_model"
COST_BY_MODULE = "costs_by_module"
TIME_COST_BY_TYPE = "time_costs_by_type"
TIME_COST_BY_USER = "time_costs_by_user"
TIME_COST_BY_MODEL = "time_costs_by_model"
TIME_COST_BY_MODULE = "time_costs_by_module"
AVG_TIME_COST_BY_TYPE = "avg_time_costs_by_type"
AVG_TIME_COST_BY_USER = "avg_time_costs_by_user"
AVG_TIME_COST_BY_MODEL = "avg_time_costs_by_model"
AVG_TIME_COST_BY_MODULE = "avg_time_costs_by_module"
STD_TIME_COST_BY_TYPE = "std_time_costs_by_type"
STD_TIME_COST_BY_USER = "std_time_costs_by_user"
STD_TIME_COST_BY_MODEL = "std_time_costs_by_model"
STD_TIME_COST_BY_MODULE = "std_time_costs_by_module"
ONLINE_TIME = "online_time"
TOTAL_MSG_CNT = "total_messages"
MSG_CNT_BY_CHAT = "messages_by_chat"
TOTAL_REPLY_CNT = "total_replies"


class OnlineTimeRecordTask(AsyncTask):
    """在线时间记录任务"""

    def __init__(self):
        super().__init__(task_name="Online Time Record Task", run_interval=60)

        self.record_id: int | None = None  # Changed to int for Peewee's default ID
        """记录ID"""

        self._init_database()  # 初始化数据库

    @staticmethod
    def _init_database():
        """初始化数据库"""
        with db.atomic():  # Use atomic operations for schema changes
            OnlineTime.create_table(safe=True)  # Creates table if it doesn't exist, Peewee handles indexes from model

    async def run(self):  # sourcery skip: use-named-expression
        try:
            current_time = datetime.now()
            extended_end_time = current_time + timedelta(minutes=1)

            if self.record_id:
                # 如果有记录，则更新结束时间
                query = OnlineTime.update(end_timestamp=extended_end_time).where(OnlineTime.id == self.record_id)  # type: ignore
                updated_rows = query.execute()
                if updated_rows == 0:
                    # Record might have been deleted or ID is stale, try to find/create
                    self.record_id = None  # Reset record_id to trigger find/create logic below

            if not self.record_id:  # Check again if record_id was reset or initially None
                # 如果没有记录，检查一分钟以内是否已有记录
                # Look for a record whose end_timestamp is recent enough to be considered ongoing
                recent_record = (
                    OnlineTime.select()
                    .where(OnlineTime.end_timestamp >= (current_time - timedelta(minutes=1)))  # type: ignore
                    .order_by(OnlineTime.end_timestamp.desc())
                    .first()
                )

                if recent_record:
                    # 如果有记录，则更新结束时间
                    self.record_id = recent_record.id
                    recent_record.end_timestamp = extended_end_time
                    recent_record.save()
                else:
                    # 若没有记录，则插入新的在线时间记录
                    new_record = OnlineTime.create(
                        timestamp=current_time.timestamp(),  # 添加此行
                        start_timestamp=current_time,
                        end_timestamp=extended_end_time,
                        duration=5,  # 初始时长为5分钟
                    )
                    self.record_id = new_record.id
        except Exception as e:
            logger.error(f"在线时间记录失败，错误信息：{e}")


def _format_online_time(online_seconds: int) -> str:
    """
    格式化在线时间
    :param online_seconds: 在线时间（秒）
    :return: 格式化后的在线时间字符串
    """
    total_online_time = timedelta(seconds=online_seconds)

    days = total_online_time.days
    hours = total_online_time.seconds // 3600
    minutes = (total_online_time.seconds // 60) % 60
    seconds = total_online_time.seconds % 60
    if days > 0:
        # 如果在线时间超过1天，则格式化为"X天X小时X分钟"
        return f"{total_online_time.days}天{hours}小时{minutes}分钟{seconds}秒"
    elif hours > 0:
        # 如果在线时间超过1小时，则格式化为"X小时X分钟X秒"
        return f"{hours}小时{minutes}分钟{seconds}秒"
    else:
        # 其他情况格式化为"X分钟X秒"
        return f"{minutes}分钟{seconds}秒"


def _format_large_number(num: float | int, html: bool = False) -> str:
    """
    格式化大数字，使用K后缀节省空间（大于9999时）
    :param num: 要格式化的数字
    :param html: 是否用于HTML输出（如果是，K会着色）
    :return: 格式化后的字符串，如 12K, 1.3K, 120K
    """
    if num >= 10000:
        # 大于等于10000，使用K后缀
        value = num / 1000.0
        if value >= 10:
            number_part = str(int(value))
            k_suffix = "K"
        else:
            number_part = f"{value:.1f}"
            k_suffix = "K"

        if html:
            # HTML输出：K着色为主题色并加粗大写
            return f"{number_part}<span style='color: #8b5cf6; font-weight: bold;'>K</span>"
        else:
            # 控制台输出：纯文本，K大写
            return f"{number_part}{k_suffix}"
    else:
        # 小于10000，直接显示
        if isinstance(num, float):
            return f"{num:.1f}" if num != int(num) else str(int(num))
        else:
            return str(num)


class StatisticOutputTask(AsyncTask):
    """统计输出任务"""

    SEP_LINE = "-" * 84

    def __init__(self, record_file_path: str = "maibot_statistics.html"):
        # 延迟300秒启动，运行间隔300秒
        super().__init__(task_name="Statistics Data Output Task", wait_before_start=0, run_interval=300)

        self.name_mapping: Dict[str, Tuple[str, float]] = {}
        """
            联系人/群聊名称映射 {聊天ID: (联系人/群聊名称, 记录时间（timestamp）)}
            注：设计记录时间的目的是方便更新名称，使联系人/群聊名称保持最新
        """

        self.record_file_path: str = record_file_path
        """
        记录文件路径
        """

        now = datetime.now()
        if "deploy_time" in local_storage:
            # 如果存在部署时间，则使用该时间作为全量统计的起始时间
            deploy_time = datetime.fromtimestamp(local_storage["deploy_time"])  # type: ignore
        else:
            # 否则，使用最大时间范围，并记录部署时间为当前时间
            deploy_time = datetime(2000, 1, 1)
            local_storage["deploy_time"] = now.timestamp()

        self.stat_period: List[Tuple[str, timedelta, str]] = [
            ("all_time", now - deploy_time, "自部署以来"),  # 必须保留"all_time"
            ("last_30_days", timedelta(days=30), "近30天"),
            ("last_7_days", timedelta(days=7), "近7天"),
            ("last_3_days", timedelta(days=3), "近3天"),
            ("last_24_hours", timedelta(days=1), "近1天"),
            ("last_3_hours", timedelta(hours=3), "近3小时"),
            ("last_hour", timedelta(hours=1), "近1小时"),
            ("last_15_minutes", timedelta(minutes=15), "近15分钟"),
        ]
        """
        统计时间段 [(统计名称, 统计时间段, 统计描述), ...]
        """

    def _statistic_console_output(self, stats: Dict[str, Any], now: datetime):
        """
        输出统计数据到控制台
        :param stats: 统计数据
        :param now: 基准当前时间
        """
        # 输出最近一小时的统计数据

        output = [
            self.SEP_LINE,
            f"  最近1小时的统计数据  (自{now.strftime('%Y-%m-%d %H:%M:%S')}开始，详细信息见文件：{self.record_file_path})",
            self.SEP_LINE,
            self._format_total_stat(stats["last_hour"]),
            "",
            self._format_model_classified_stat(stats["last_hour"]),
            "",
            self._format_module_classified_stat(stats["last_hour"]),
            "",
            self._format_chat_stat(stats["last_hour"]),
            self.SEP_LINE,
            "",
        ]

        logger.info("\n" + "\n".join(output))

    async def run(self):
        try:
            now = datetime.now()

            # 使用线程池并行执行耗时操作
            loop = asyncio.get_event_loop()

            # 在线程池中并行执行数据收集和之前的HTML生成（如果存在）
            with concurrent.futures.ThreadPoolExecutor() as executor:
                logger.info("正在收集统计数据...")

                # 数据收集任务
                collect_task = loop.run_in_executor(executor, self._collect_all_statistics, now)

                # 等待数据收集完成
                stats = await collect_task
                logger.info("统计数据收集完成")

                # 并行执行控制台输出和HTML报告生成
                console_task = loop.run_in_executor(executor, self._statistic_console_output, stats, now)
                html_task = loop.run_in_executor(executor, self._generate_html_report, stats, now)

                # 等待两个输出任务完成
                await asyncio.gather(console_task, html_task)

            logger.info("统计数据输出完成")
        except Exception as e:
            logger.exception(f"输出统计数据过程中发生异常，错误信息：{e}")

    async def run_async_background(self):
        """
        备选方案：完全异步后台运行统计输出
        使用此方法可以让统计任务完全非阻塞
        """

        async def _async_collect_and_output():
            try:
                import concurrent.futures

                now = datetime.now()
                loop = asyncio.get_event_loop()

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    logger.info("正在后台收集统计数据...")

                    # 创建后台任务，不等待完成
                    collect_task = asyncio.create_task(
                        loop.run_in_executor(executor, self._collect_all_statistics, now)  # type: ignore
                    )

                    stats = await collect_task
                    logger.info("统计数据收集完成")

                    # 创建并发的输出任务
                    output_tasks = [
                        asyncio.create_task(loop.run_in_executor(executor, self._statistic_console_output, stats, now)),  # type: ignore
                        asyncio.create_task(loop.run_in_executor(executor, self._generate_html_report, stats, now)),  # type: ignore
                    ]

                    # 等待所有输出任务完成
                    await asyncio.gather(*output_tasks)

                logger.info("统计数据后台输出完成")
            except Exception as e:
                logger.exception(f"后台统计数据输出过程中发生异常：{e}")

        # 创建后台任务，立即返回
        asyncio.create_task(_async_collect_and_output())

    # -- 以下为统计数据收集方法 --

    @staticmethod
    def _collect_model_request_for_period(collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        """
        收集指定时间段的LLM请求统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        # 排序-按照时间段开始时间降序排列（最晚的时间段在前）
        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                TOTAL_REQ_CNT: 0,
                REQ_CNT_BY_TYPE: defaultdict(int),
                REQ_CNT_BY_USER: defaultdict(int),
                REQ_CNT_BY_MODEL: defaultdict(int),
                REQ_CNT_BY_MODULE: defaultdict(int),
                IN_TOK_BY_TYPE: defaultdict(int),
                IN_TOK_BY_USER: defaultdict(int),
                IN_TOK_BY_MODEL: defaultdict(int),
                IN_TOK_BY_MODULE: defaultdict(int),
                OUT_TOK_BY_TYPE: defaultdict(int),
                OUT_TOK_BY_USER: defaultdict(int),
                OUT_TOK_BY_MODEL: defaultdict(int),
                OUT_TOK_BY_MODULE: defaultdict(int),
                TOTAL_TOK_BY_TYPE: defaultdict(int),
                TOTAL_TOK_BY_USER: defaultdict(int),
                TOTAL_TOK_BY_MODEL: defaultdict(int),
                TOTAL_TOK_BY_MODULE: defaultdict(int),
                TOTAL_COST: 0.0,
                COST_BY_TYPE: defaultdict(float),
                COST_BY_USER: defaultdict(float),
                COST_BY_MODEL: defaultdict(float),
                COST_BY_MODULE: defaultdict(float),
                TIME_COST_BY_TYPE: defaultdict(list),
                TIME_COST_BY_USER: defaultdict(list),
                TIME_COST_BY_MODEL: defaultdict(list),
                TIME_COST_BY_MODULE: defaultdict(list),
                AVG_TIME_COST_BY_TYPE: defaultdict(float),
                AVG_TIME_COST_BY_USER: defaultdict(float),
                AVG_TIME_COST_BY_MODEL: defaultdict(float),
                AVG_TIME_COST_BY_MODULE: defaultdict(float),
                STD_TIME_COST_BY_TYPE: defaultdict(float),
                STD_TIME_COST_BY_USER: defaultdict(float),
                STD_TIME_COST_BY_MODEL: defaultdict(float),
                STD_TIME_COST_BY_MODULE: defaultdict(float),
            }
            for period_key, _ in collect_period
        }

        # 以最早的时间戳为起始时间获取记录
        # Assuming LLMUsage.timestamp is a DateTimeField
        query_start_time = collect_period[-1][1]
        for record in LLMUsage.select().where(LLMUsage.timestamp >= query_start_time):  # type: ignore
            record_timestamp = record.timestamp  # This is already a datetime object
            for idx, (_, period_start) in enumerate(collect_period):
                if record_timestamp >= period_start:
                    for period_key, _ in collect_period[idx:]:
                        stats[period_key][TOTAL_REQ_CNT] += 1

                        request_type = record.request_type or "unknown"
                        user_id = record.user_id or "unknown"  # user_id is TextField, already string
                        model_name = record.model_assign_name or record.model_name or "unknown"

                        # 提取模块名：如果请求类型包含"."，取第一个"."之前的部分
                        module_name = request_type.split(".")[0] if "." in request_type else request_type

                        stats[period_key][REQ_CNT_BY_TYPE][request_type] += 1
                        stats[period_key][REQ_CNT_BY_USER][user_id] += 1
                        stats[period_key][REQ_CNT_BY_MODEL][model_name] += 1
                        stats[period_key][REQ_CNT_BY_MODULE][module_name] += 1

                        prompt_tokens = record.prompt_tokens or 0
                        completion_tokens = record.completion_tokens or 0
                        total_tokens = prompt_tokens + completion_tokens

                        stats[period_key][IN_TOK_BY_TYPE][request_type] += prompt_tokens
                        stats[period_key][IN_TOK_BY_USER][user_id] += prompt_tokens
                        stats[period_key][IN_TOK_BY_MODEL][model_name] += prompt_tokens
                        stats[period_key][IN_TOK_BY_MODULE][module_name] += prompt_tokens

                        stats[period_key][OUT_TOK_BY_TYPE][request_type] += completion_tokens
                        stats[period_key][OUT_TOK_BY_USER][user_id] += completion_tokens
                        stats[period_key][OUT_TOK_BY_MODEL][model_name] += completion_tokens
                        stats[period_key][OUT_TOK_BY_MODULE][module_name] += completion_tokens

                        stats[period_key][TOTAL_TOK_BY_TYPE][request_type] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_USER][user_id] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_MODEL][model_name] += total_tokens
                        stats[period_key][TOTAL_TOK_BY_MODULE][module_name] += total_tokens

                        cost = record.cost or 0.0
                        stats[period_key][TOTAL_COST] += cost
                        stats[period_key][COST_BY_TYPE][request_type] += cost
                        stats[period_key][COST_BY_USER][user_id] += cost
                        stats[period_key][COST_BY_MODEL][model_name] += cost
                        stats[period_key][COST_BY_MODULE][module_name] += cost

                        # 收集time_cost数据
                        time_cost = record.time_cost or 0.0
                        if time_cost > 0:  # 只记录有效的time_cost
                            stats[period_key][TIME_COST_BY_TYPE][request_type].append(time_cost)
                            stats[period_key][TIME_COST_BY_USER][user_id].append(time_cost)
                            stats[period_key][TIME_COST_BY_MODEL][model_name].append(time_cost)
                            stats[period_key][TIME_COST_BY_MODULE][module_name].append(time_cost)
                    break

        # 计算平均耗时和标准差
        for period_key in stats:
            for category in [REQ_CNT_BY_TYPE, REQ_CNT_BY_USER, REQ_CNT_BY_MODEL, REQ_CNT_BY_MODULE]:
                time_cost_key = f"time_costs_by_{category.split('_')[-1]}"
                avg_key = f"avg_time_costs_by_{category.split('_')[-1]}"
                std_key = f"std_time_costs_by_{category.split('_')[-1]}"

                for item_name in stats[period_key][category]:
                    time_costs = stats[period_key][time_cost_key].get(item_name, [])
                    if time_costs:
                        # 计算平均耗时
                        avg_time_cost = sum(time_costs) / len(time_costs)
                        stats[period_key][avg_key][item_name] = round(avg_time_cost, 3)

                        # 计算标准差
                        if len(time_costs) > 1:
                            variance = sum((x - avg_time_cost) ** 2 for x in time_costs) / len(time_costs)
                            std_time_cost = variance**0.5
                            stats[period_key][std_key][item_name] = round(std_time_cost, 3)
                        else:
                            stats[period_key][std_key][item_name] = 0.0
                    else:
                        stats[period_key][avg_key][item_name] = 0.0
                        stats[period_key][std_key][item_name] = 0.0

        return stats

    @staticmethod
    def _collect_online_time_for_period(collect_period: List[Tuple[str, datetime]], now: datetime) -> Dict[str, Any]:
        """
        收集指定时间段的在线时间统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                ONLINE_TIME: 0.0,
            }
            for period_key, _ in collect_period
        }

        query_start_time = collect_period[-1][1]
        # Assuming OnlineTime.end_timestamp is a DateTimeField
        for record in OnlineTime.select().where(OnlineTime.end_timestamp >= query_start_time):  # type: ignore
            # record.end_timestamp and record.start_timestamp are datetime objects
            record_end_timestamp = record.end_timestamp
            record_start_timestamp = record.start_timestamp

            for idx, (_, period_boundary_start) in enumerate(collect_period):
                if record_end_timestamp >= period_boundary_start:
                    # Calculate effective end time for this record in relation to 'now'
                    effective_end_time = min(record_end_timestamp, now)

                    for period_key, current_period_start_time in collect_period[idx:]:
                        # Determine the portion of the record that falls within this specific statistical period
                        overlap_start = max(record_start_timestamp, current_period_start_time)
                        overlap_end = effective_end_time  # Already capped by 'now' and record's own end

                        if overlap_end > overlap_start:
                            stats[period_key][ONLINE_TIME] += (overlap_end - overlap_start).total_seconds()
                    break
        return stats

    def _collect_message_count_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        """
        收集指定时间段的消息统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats = {
            period_key: {
                TOTAL_MSG_CNT: 0,
                MSG_CNT_BY_CHAT: defaultdict(int),
                TOTAL_REPLY_CNT: 0,
            }
            for period_key, _ in collect_period
        }

        query_start_timestamp = collect_period[-1][1].timestamp()  # Messages.time is a DoubleField (timestamp)
        for message in Messages.select().where(Messages.time >= query_start_timestamp):  # type: ignore
            message_time_ts = message.time  # This is a float timestamp

            chat_id = None
            chat_name = None

            # Logic based on Peewee model structure, aiming to replicate original intent
            if message.chat_info_group_id:
                chat_id = f"g{message.chat_info_group_id}"
                chat_name = message.chat_info_group_name or f"群{message.chat_info_group_id}"
            elif message.user_id:  # Fallback to sender's info for chat_id if not a group_info based chat
                # This uses the message SENDER's ID as per original logic's fallback
                chat_id = f"u{message.user_id}"  # SENDER's user_id
                chat_name = message.user_nickname  # SENDER's nickname
            else:
                # If neither group_id nor sender_id is available for chat identification
                logger.warning(
                    f"Message (PK: {message.id if hasattr(message, 'id') else 'N/A'}) lacks group_id and user_id for chat stats."
                )
                continue

            if not chat_id:  # Should not happen if above logic is correct
                continue

            # Update name_mapping（仅用于展示聊天名称）
            try:
                if chat_id in self.name_mapping:
                    if chat_name != self.name_mapping[chat_id][0] and message_time_ts > self.name_mapping[chat_id][1]:
                        self.name_mapping[chat_id] = (chat_name, message_time_ts)
                else:
                    self.name_mapping[chat_id] = (chat_name, message_time_ts)
            except (IndexError, TypeError) as e:
                logger.warning(f"更新 name_mapping 时发生错误，chat_id: {chat_id}, 错误: {e}")
                # 重置为正确的格式
                self.name_mapping[chat_id] = (chat_name, message_time_ts)

            for idx, (_, period_start_dt) in enumerate(collect_period):
                if message_time_ts >= period_start_dt.timestamp():
                    for period_key, _ in collect_period[idx:]:
                        stats[period_key][TOTAL_MSG_CNT] += 1
                        stats[period_key][MSG_CNT_BY_CHAT][chat_id] += 1
                    break

        # 使用 ActionRecords 中的 reply 动作次数作为回复数基准
        try:
            action_query_start_timestamp = collect_period[-1][1].timestamp()
            for action in ActionRecords.select().where(ActionRecords.time >= action_query_start_timestamp):  # type: ignore
                # 仅统计已完成的 reply 动作
                if action.action_name != "reply" or not action.action_done:
                    continue

                action_time_ts = action.time
                for idx, (_, period_start_dt) in enumerate(collect_period):
                    if action_time_ts >= period_start_dt.timestamp():
                        for period_key, _ in collect_period[idx:]:
                            stats[period_key][TOTAL_REPLY_CNT] += 1
                        break
        except Exception as e:
            logger.warning(f"统计 reply 动作次数失败，将回复数视为 0，错误信息：{e}")

        return stats

    def _collect_all_statistics(self, now: datetime) -> Dict[str, Dict[str, Any]]:
        """
        收集各时间段的统计数据
        :param now: 基准当前时间
        """

        last_all_time_stat = None

        try:
            if "last_full_statistics" in local_storage:
                # 如果存在上次完整统计数据，则使用该数据进行增量统计
                last_stat: Dict[str, Any] = local_storage["last_full_statistics"]  # 上次完整统计数据 # type: ignore

                # 修复 name_mapping 数据类型不匹配问题
                # JSON 中存储为列表，但代码期望为元组
                raw_name_mapping = last_stat["name_mapping"]
                self.name_mapping = {}
                for chat_id, value in raw_name_mapping.items():
                    if isinstance(value, list) and len(value) == 2:
                        # 将列表转换为元组
                        self.name_mapping[chat_id] = (value[0], value[1])
                    elif isinstance(value, tuple) and len(value) == 2:
                        # 已经是元组，直接使用
                        self.name_mapping[chat_id] = value
                    else:
                        # 数据格式不正确，跳过或使用默认值
                        logger.warning(f"name_mapping 中 chat_id {chat_id} 的数据格式不正确: {value}")
                        continue
                last_all_time_stat = last_stat["stat_data"]  # 上次完整统计的统计数据
                last_stat_timestamp = datetime.fromtimestamp(last_stat["timestamp"])  # 上次完整统计数据的时间戳
                self.stat_period = [
                    item for item in self.stat_period if item[0] != "all_time"
                ]  # 删除"所有时间"的统计时段
                self.stat_period.append(("all_time", now - last_stat_timestamp, "自部署以来的"))
        except Exception as e:
            logger.warning(f"加载上次完整统计数据失败，进行全量统计，错误信息：{e}")

        stat_start_timestamp = [(period[0], now - period[1]) for period in self.stat_period]

        stat = {item[0]: {} for item in self.stat_period}

        model_req_stat = self._collect_model_request_for_period(stat_start_timestamp)
        online_time_stat = self._collect_online_time_for_period(stat_start_timestamp, now)
        message_count_stat = self._collect_message_count_for_period(stat_start_timestamp)

        # 统计数据合并
        # 合并三类统计数据
        for period_key, _ in stat_start_timestamp:
            stat[period_key].update(model_req_stat[period_key])
            stat[period_key].update(online_time_stat[period_key])
            stat[period_key].update(message_count_stat[period_key])

        if last_all_time_stat:
            # 若存在上次完整统计数据，则将其与当前统计数据合并
            for key, val in last_all_time_stat.items():
                # 确保当前统计数据中存在该key
                if key not in stat["all_time"]:
                    continue

                if isinstance(val, dict):
                    # 是字典类型，则进行合并
                    for sub_key, sub_val in val.items():
                        # 普通的数值或字典合并
                        if sub_key in stat["all_time"][key]:
                            # 检查是否为嵌套的字典类型（如版本统计）
                            if isinstance(sub_val, dict) and isinstance(stat["all_time"][key][sub_key], dict):
                                # 合并嵌套字典
                                for nested_key, nested_val in sub_val.items():
                                    if nested_key in stat["all_time"][key][sub_key]:
                                        stat["all_time"][key][sub_key][nested_key] += nested_val
                                    else:
                                        stat["all_time"][key][sub_key][nested_key] = nested_val
                            else:
                                # 普通数值累加
                                stat["all_time"][key][sub_key] += sub_val
                        else:
                            stat["all_time"][key][sub_key] = sub_val
                else:
                    # 直接合并
                    stat["all_time"][key] += val

        # 更新上次完整统计数据的时间戳
        # 将所有defaultdict转换为普通dict以避免类型冲突
        clean_stat_data = self._convert_defaultdict_to_dict(stat["all_time"])

        # 将 name_mapping 中的元组转换为列表，因为JSON不支持元组
        json_safe_name_mapping = {}
        for chat_id, (chat_name, timestamp) in self.name_mapping.items():
            json_safe_name_mapping[chat_id] = [chat_name, timestamp]

        local_storage["last_full_statistics"] = {
            "name_mapping": json_safe_name_mapping,
            "stat_data": clean_stat_data,
            "timestamp": now.timestamp(),
        }

        return stat

    def _convert_defaultdict_to_dict(self, data):
        # sourcery skip: dict-comprehension, extract-duplicate-method, inline-immediately-returned-variable, merge-duplicate-blocks
        """递归转换defaultdict为普通dict"""
        if isinstance(data, defaultdict):
            # 转换defaultdict为普通dict
            result = {}
            for key, value in data.items():
                result[key] = self._convert_defaultdict_to_dict(value)
            return result
        elif isinstance(data, dict):
            # 递归处理普通dict
            result = {}
            for key, value in data.items():
                result[key] = self._convert_defaultdict_to_dict(value)
            return result
        else:
            # 其他类型直接返回
            return data

    # -- 以下为统计数据格式化方法 --

    @staticmethod
    def _format_total_stat(stats: Dict[str, Any]) -> str:
        """
        格式化总统计数据
        """
        # 计算总token数（从所有模型的token数中累加）
        total_tokens = sum(stats[TOTAL_TOK_BY_MODEL].values()) if stats[TOTAL_TOK_BY_MODEL] else 0

        # 计算花费/消息数量指标（每100条）
        cost_per_100_messages = (stats[TOTAL_COST] / stats[TOTAL_MSG_CNT] * 100) if stats[TOTAL_MSG_CNT] > 0 else 0.0

        # 计算花费/时间指标（花费/小时）
        online_hours = stats[ONLINE_TIME] / 3600.0 if stats[ONLINE_TIME] > 0 else 0.0
        cost_per_hour = stats[TOTAL_COST] / online_hours if online_hours > 0 else 0.0

        # 计算token/时间指标（token/小时）
        tokens_per_hour = (total_tokens / online_hours) if online_hours > 0 else 0.0

        # 计算花费/回复数量指标（每100条）
        total_replies = stats.get(TOTAL_REPLY_CNT, 0)
        cost_per_100_replies = (stats[TOTAL_COST] / total_replies * 100) if total_replies > 0 else 0.0

        # 计算花费/消息数量（排除自己回复）指标（每100条）
        total_messages_excluding_replies = stats[TOTAL_MSG_CNT] - total_replies
        cost_per_100_messages_excluding_replies = (
            (stats[TOTAL_COST] / total_messages_excluding_replies * 100)
            if total_messages_excluding_replies > 0
            else 0.0
        )

        output = [
            f"总在线时间: {_format_online_time(stats[ONLINE_TIME])}",
            f"总消息数: {_format_large_number(stats[TOTAL_MSG_CNT])}",
            f"总回复数: {_format_large_number(total_replies)}",
            f"总请求数: {_format_large_number(stats[TOTAL_REQ_CNT])}",
            f"总Token数: {_format_large_number(total_tokens)}",
            f"总花费: {stats[TOTAL_COST]:.2f}¥",
            f"花费/消息数量: {cost_per_100_messages:.4f}¥/100条" if stats[TOTAL_MSG_CNT] > 0 else "花费/消息数量: N/A",
            f"花费/接受消息数量: {cost_per_100_messages_excluding_replies:.4f}¥/100条"
            if total_messages_excluding_replies > 0
            else "花费/消息数量(排除回复): N/A",
            f"花费/回复消息数量: {cost_per_100_replies:.4f}¥/100条" if total_replies > 0 else "花费/回复数量: N/A",
            f"花费/时间: {cost_per_hour:.2f}¥/小时" if online_hours > 0 else "花费/时间: N/A",
            f"Token/时间: {_format_large_number(tokens_per_hour)}/小时" if online_hours > 0 else "Token/时间: N/A",
            "",
        ]

        return "\n".join(output)

    @staticmethod
    def _format_model_classified_stat(stats: Dict[str, Any]) -> str:
        """
        格式化按模型分类的统计数据
        """
        if stats[TOTAL_REQ_CNT] <= 0:
            return ""
        data_fmt = "{:<32}  {:>10}  {:>12}  {:>12}  {:>12}  {:>9.2f}¥  {:>10.1f}  {:>10.1f}  {:>12}  {:>12}  {:>12}"

        total_replies = stats.get(TOTAL_REPLY_CNT, 0)

        output = [
            "按模型分类统计:",
            " 模型名称                          调用次数    输入Token     输出Token     Token总量     累计花费    平均耗时(秒)  标准差(秒)  每次回复平均调用次数  每次回复平均Token数  每次调用平均Token",
        ]
        for model_name, count in sorted(stats[REQ_CNT_BY_MODEL].items()):
            name = f"{model_name[:29]}..." if len(model_name) > 32 else model_name
            in_tokens = stats[IN_TOK_BY_MODEL][model_name]
            out_tokens = stats[OUT_TOK_BY_MODEL][model_name]
            tokens = stats[TOTAL_TOK_BY_MODEL][model_name]
            cost = stats[COST_BY_MODEL][model_name]
            avg_time_cost = stats[AVG_TIME_COST_BY_MODEL][model_name]
            std_time_cost = stats[STD_TIME_COST_BY_MODEL][model_name]

            # 计算每次回复平均值
            avg_count_per_reply = count / total_replies if total_replies > 0 else 0.0
            avg_tokens_per_reply = tokens / total_replies if total_replies > 0 else 0.0

            # 计算每次调用平均token
            avg_tokens_per_call = tokens / count if count > 0 else 0.0

            # 格式化大数字
            formatted_count = _format_large_number(count)
            formatted_in_tokens = _format_large_number(in_tokens)
            formatted_out_tokens = _format_large_number(out_tokens)
            formatted_tokens = _format_large_number(tokens)
            formatted_avg_count = _format_large_number(avg_count_per_reply) if total_replies > 0 else "N/A"
            formatted_avg_tokens = _format_large_number(avg_tokens_per_reply) if total_replies > 0 else "N/A"
            formatted_avg_tokens_per_call = _format_large_number(avg_tokens_per_call) if count > 0 else "N/A"

            output.append(
                data_fmt.format(
                    name,
                    formatted_count,
                    formatted_in_tokens,
                    formatted_out_tokens,
                    formatted_tokens,
                    cost,
                    avg_time_cost,
                    std_time_cost,
                    formatted_avg_count,
                    formatted_avg_tokens,
                    formatted_avg_tokens_per_call,
                )
            )

        output.append("")
        return "\n".join(output)

    @staticmethod
    def _format_module_classified_stat(stats: Dict[str, Any]) -> str:
        """
        格式化按模块分类的统计数据
        """
        if stats[TOTAL_REQ_CNT] <= 0:
            return ""
        data_fmt = "{:<32}  {:>10}  {:>12}  {:>12}  {:>12}  {:>9.2f}¥  {:>10.1f}  {:>10.1f}  {:>12}  {:>12}  {:>12}"

        total_replies = stats.get(TOTAL_REPLY_CNT, 0)

        output = [
            "按模块分类统计:",
            " 模块名称                          调用次数    输入Token     输出Token     Token总量     累计花费    平均耗时(秒)  标准差(秒)  每次回复平均调用次数  每次回复平均Token数  每次调用平均Token",
        ]
        for module_name, count in sorted(stats[REQ_CNT_BY_MODULE].items()):
            name = f"{module_name[:29]}..." if len(module_name) > 32 else module_name
            in_tokens = stats[IN_TOK_BY_MODULE][module_name]
            out_tokens = stats[OUT_TOK_BY_MODULE][module_name]
            tokens = stats[TOTAL_TOK_BY_MODULE][module_name]
            cost = stats[COST_BY_MODULE][module_name]
            avg_time_cost = stats[AVG_TIME_COST_BY_MODULE][module_name]
            std_time_cost = stats[STD_TIME_COST_BY_MODULE][module_name]

            # 计算每次回复平均值
            avg_count_per_reply = count / total_replies if total_replies > 0 else 0.0
            avg_tokens_per_reply = tokens / total_replies if total_replies > 0 else 0.0

            # 计算每次调用平均token
            avg_tokens_per_call = tokens / count if count > 0 else 0.0

            # 格式化大数字
            formatted_count = _format_large_number(count)
            formatted_in_tokens = _format_large_number(in_tokens)
            formatted_out_tokens = _format_large_number(out_tokens)
            formatted_tokens = _format_large_number(tokens)
            formatted_avg_count = _format_large_number(avg_count_per_reply) if total_replies > 0 else "N/A"
            formatted_avg_tokens = _format_large_number(avg_tokens_per_reply) if total_replies > 0 else "N/A"
            formatted_avg_tokens_per_call = _format_large_number(avg_tokens_per_call) if count > 0 else "N/A"

            output.append(
                data_fmt.format(
                    name,
                    formatted_count,
                    formatted_in_tokens,
                    formatted_out_tokens,
                    formatted_tokens,
                    cost,
                    avg_time_cost,
                    std_time_cost,
                    formatted_avg_count,
                    formatted_avg_tokens,
                    formatted_avg_tokens_per_call,
                )
            )

        output.append("")
        return "\n".join(output)

    def _format_chat_stat(self, stats: Dict[str, Any]) -> str:
        """
        格式化聊天统计数据
        """
        if stats[TOTAL_MSG_CNT] <= 0:
            return ""
        output = ["聊天消息统计:", " 联系人/群组名称                  消息数量"]
        for chat_id, count in sorted(stats[MSG_CNT_BY_CHAT].items()):
            try:
                chat_name = self.name_mapping.get(chat_id, ("未知聊天", 0))[0]
                formatted_count = _format_large_number(count)
                output.append(f"{chat_name[:32]:<32}  {formatted_count:>10}")
            except (IndexError, TypeError) as e:
                logger.warning(f"格式化聊天统计时发生错误，chat_id: {chat_id}, 错误: {e}")
                formatted_count = _format_large_number(count)
                output.append(f"{'未知聊天':<32}  {formatted_count:>10}")
        output.append("")
        return "\n".join(output)

    def _get_chat_display_name_from_id(self, chat_id: str) -> str:
        """从chat_id获取显示名称"""
        try:
            # 首先尝试从chat_stream获取真实群组名称
            from src.chat.message_receive.chat_stream import get_chat_manager

            chat_manager = get_chat_manager()

            if chat_id in chat_manager.streams:
                stream = chat_manager.streams[chat_id]
                if stream.group_info and hasattr(stream.group_info, "group_name"):
                    group_name = stream.group_info.group_name
                    if group_name and group_name.strip():
                        return group_name.strip()
                elif stream.user_info and hasattr(stream.user_info, "user_nickname"):
                    user_name = stream.user_info.user_nickname
                    if user_name and user_name.strip():
                        return user_name.strip()

            # 如果从chat_stream获取失败，尝试解析chat_id格式
            if chat_id.startswith("g"):
                return f"群聊{chat_id[1:]}"
            elif chat_id.startswith("u"):
                return f"用户{chat_id[1:]}"
            else:
                return chat_id
        except Exception as e:
            logger.warning(f"获取聊天显示名称失败: {e}")
            return chat_id

    # 移除_generate_versions_tab方法

    def _generate_html_report(self, stat: dict[str, Any], now: datetime):
        """
        生成HTML格式的统计报告
        :param stat: 统计数据
        :param now: 基准当前时间
        :return: HTML格式的统计报告
        """

        # 移除版本对比内容相关tab和内容
        tab_list = [
            f'<button class="tab-link" onclick="showTab(event, \'{period[0]}\')">{period[2]}</button>'
            for period in self.stat_period
        ]
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'charts\')">数据图表</button>')
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'metrics\')">指标趋势</button>')

        def _format_stat_data(stat_data: dict[str, Any], div_id: str, start_time: datetime) -> str:
            """
            格式化一个时间段的统计数据到html div块
            :param stat_data: 统计数据
            :param div_id: div的ID
            :param start_time: 统计时间段开始时间
            """
            # format总在线时间

            # 按模型分类统计
            total_replies = stat_data.get(TOTAL_REPLY_CNT, 0)
            model_rows = "\n".join(
                [
                    f"<tr>"
                    f"<td>{model_name}</td>"
                    f"<td>{_format_large_number(count, html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[IN_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[OUT_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{stat_data[COST_BY_MODEL][model_name]:.2f} ¥</td>"
                    f"<td>{stat_data[AVG_TIME_COST_BY_MODEL][model_name]:.1f} 秒</td>"
                    f"<td>{stat_data[STD_TIME_COST_BY_MODEL][model_name]:.1f} 秒</td>"
                    f"<td>{_format_large_number(count / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODEL][model_name] / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODEL][model_name] / count, html=True) if count > 0 else 'N/A'}</td>"
                    f"</tr>"
                    for model_name, count in sorted(stat_data[REQ_CNT_BY_MODEL].items())
                ]
                if stat_data[REQ_CNT_BY_MODEL]
                else ["<tr><td colspan='11' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
            )
            # 按请求类型分类统计
            type_rows = "\n".join(
                [
                    f"<tr>"
                    f"<td>{req_type}</td>"
                    f"<td>{_format_large_number(count, html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[IN_TOK_BY_TYPE][req_type], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[OUT_TOK_BY_TYPE][req_type], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_TYPE][req_type], html=True)}</td>"
                    f"<td>{stat_data[COST_BY_TYPE][req_type]:.2f} ¥</td>"
                    f"<td>{stat_data[AVG_TIME_COST_BY_TYPE][req_type]:.1f} 秒</td>"
                    f"<td>{stat_data[STD_TIME_COST_BY_TYPE][req_type]:.1f} 秒</td>"
                    f"<td>{_format_large_number(count / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_TYPE][req_type] / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_TYPE][req_type] / count, html=True) if count > 0 else 'N/A'}</td>"
                    f"</tr>"
                    for req_type, count in sorted(stat_data[REQ_CNT_BY_TYPE].items())
                ]
                if stat_data[REQ_CNT_BY_TYPE]
                else ["<tr><td colspan='11' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
            )
            # 按模块分类统计
            module_rows = "\n".join(
                [
                    f"<tr>"
                    f"<td>{module_name}</td>"
                    f"<td>{_format_large_number(count, html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[IN_TOK_BY_MODULE][module_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[OUT_TOK_BY_MODULE][module_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODULE][module_name], html=True)}</td>"
                    f"<td>{stat_data[COST_BY_MODULE][module_name]:.2f} ¥</td>"
                    f"<td>{stat_data[AVG_TIME_COST_BY_MODULE][module_name]:.1f} 秒</td>"
                    f"<td>{stat_data[STD_TIME_COST_BY_MODULE][module_name]:.1f} 秒</td>"
                    f"<td>{_format_large_number(count / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODULE][module_name] / total_replies, html=True) if total_replies > 0 else 'N/A'}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODULE][module_name] / count, html=True) if count > 0 else 'N/A'}</td>"
                    f"</tr>"
                    for module_name, count in sorted(stat_data[REQ_CNT_BY_MODULE].items())
                ]
                if stat_data[REQ_CNT_BY_MODULE]
                else ["<tr><td colspan='11' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
            )

            # 聊天消息统计
            chat_rows = []
            for chat_id, count in sorted(stat_data[MSG_CNT_BY_CHAT].items()):
                try:
                    chat_name = self.name_mapping.get(chat_id, ("未知聊天", 0))[0]
                    chat_rows.append(f"<tr><td>{chat_name}</td><td>{_format_large_number(count, html=True)}</td></tr>")
                except (IndexError, TypeError) as e:
                    logger.warning(f"生成HTML聊天统计时发生错误，chat_id: {chat_id}, 错误: {e}")
                    chat_rows.append(f"<tr><td>未知聊天</td><td>{_format_large_number(count, html=True)}</td></tr>")

            chat_rows_html = (
                "\n".join(chat_rows)
                if chat_rows
                else "<tr><td colspan='2' style='text-align: center; color: #999;'>暂无数据</td></tr>"
            )
            # 生成HTML
            return f"""
            <div id=\"{div_id}\" class=\"tab-content\">
                <p class=\"info-item\">
                    <strong>统计时段: </strong>
                    {start_time.strftime("%Y-%m-%d %H:%M:%S")} ~ {now.strftime("%Y-%m-%d %H:%M:%S")}
                </p>
                <div class=\"kpi-cards\">
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总在线时间</div>
                        <div class=\"kpi-value\">{_format_online_time(stat_data[ONLINE_TIME])}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总消息数</div>
                        <div class=\"kpi-value\">{_format_large_number(stat_data[TOTAL_MSG_CNT], html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总回复数</div>
                        <div class=\"kpi-value\">{_format_large_number(stat_data.get(TOTAL_REPLY_CNT, 0), html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总请求数</div>
                        <div class=\"kpi-value\">{_format_large_number(stat_data[TOTAL_REQ_CNT], html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总Token数</div>
                        <div class=\"kpi-value\">{_format_large_number(sum(stat_data[TOTAL_TOK_BY_MODEL].values()) if stat_data[TOTAL_TOK_BY_MODEL] else 0, html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总花费</div>
                        <div class=\"kpi-value\">{stat_data[TOTAL_COST]:.2f} ¥</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">花费/消息数量</div>
                        <div class=\"kpi-value\">{(stat_data[TOTAL_COST] / stat_data[TOTAL_MSG_CNT] * 100 if stat_data[TOTAL_MSG_CNT] > 0 else 0.0):.4f} ¥/100条</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">花费/消息数量(排除回复)</div>
                        <div class=\"kpi-value\">{(stat_data[TOTAL_COST] / (stat_data[TOTAL_MSG_CNT] - stat_data.get(TOTAL_REPLY_CNT, 0)) * 100 if (stat_data[TOTAL_MSG_CNT] - stat_data.get(TOTAL_REPLY_CNT, 0)) > 0 else 0.0):.4f} ¥/100条</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">花费/回复数量</div>
                        <div class=\"kpi-value\">{(stat_data[TOTAL_COST] / stat_data.get(TOTAL_REPLY_CNT, 0) * 100 if stat_data.get(TOTAL_REPLY_CNT, 0) > 0 else 0.0):.4f} ¥/100条</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">花费/时间</div>
                        <div class=\"kpi-value\">{(stat_data[TOTAL_COST] / (stat_data[ONLINE_TIME] / 3600.0) if stat_data[ONLINE_TIME] > 0 else 0.0):.2f} ¥/小时</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">Token/时间</div>
                        <div class=\"kpi-value\">{_format_large_number(sum(stat_data[TOTAL_TOK_BY_MODEL].values()) / (stat_data[ONLINE_TIME] / 3600.0) if stat_data[ONLINE_TIME] > 0 and stat_data[TOTAL_TOK_BY_MODEL] else 0.0, html=True)}/小时</div>
                    </div>
                </div>
                
                <h2>按模型分类统计</h2>
                <div class=\"table-wrap\">
                    <table>
                        <thead><tr><th>模型名称</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr></thead>
                        <tbody>
                            {model_rows}
                        </tbody>
                    </table>
                </div>
                
                <h2>按模块分类统计</h2>
                <div class=\"table-wrap\">
                    <table>
                        <thead>
                            <tr><th>模块名称</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr>
                        </thead>
                        <tbody>
                        {module_rows}
                        </tbody>
                    </table>
                </div>
    
                <h2>按请求类型分类统计</h2>
                <div class=\"table-wrap\">
                    <table>
                        <thead>
                            <tr><th>请求类型</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr>
                        </thead>
                        <tbody>
                        {type_rows}
                        </tbody>
                    </table>
                </div>
    
                <h2>聊天消息统计</h2>
                <div class=\"table-wrap\">
                    <table>
                        <thead>
                            <tr><th>联系人/群组名称</th><th>消息数量</th></tr>
                        </thead>
                        <tbody>
                        {chat_rows_html}
                        </tbody>
                    </table>
                </div>
                
                <h2>数据分布图表</h2>
                <div style="display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px;">
                    <div style="flex: 1; min-width: 300px;">
                        <h3>模型花费分布</h3>
                        <canvas id="modelPieChart_{div_id}" width="300" height="300"></canvas>
                    </div>
                    <div style="flex: 1; min-width: 300px;">
                        <h3>模块花费分布</h3>
                        <canvas id="modulePieChart_{div_id}" width="300" height="300"></canvas>
                    </div>
                    <div style="flex: 1; min-width: 300px;">
                        <h3>请求类型花费分布</h3>
                        <canvas id="typePieChart_{div_id}" width="300" height="300"></canvas>
                    </div>
                    <div style="flex: 1; min-width: 300px;">
                        <h3>聊天消息分布</h3>
                        <canvas id="chatPieChart_{div_id}" width="300" height="300"></canvas>
                    </div>
                </div>
                
                <script>
                    // 为当前统计卡片创建饼图
                    document.addEventListener('DOMContentLoaded', function() {{
                        createPieCharts_{div_id}();
                    }});
                    
                    function createPieCharts_{div_id}() {{
                        const colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#34495e', '#e67e22', '#95a5a6', '#f1c40f'];
                        
                        // 模型花费分布饼图
                        const modelLabels = {list(sorted(stat_data[COST_BY_MODEL].keys())) if stat_data[COST_BY_MODEL] else []};
                        if (modelLabels.length > 0) {{
                            const modelData = {{
                                labels: modelLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_MODEL][model_name] for model_name in sorted(stat_data[COST_BY_MODEL].keys())] if stat_data[COST_BY_MODEL] else []},
                                    backgroundColor: colors.slice(0, {len(stat_data[COST_BY_MODEL]) if stat_data[COST_BY_MODEL] else 0}),
                                    borderColor: colors.slice(0, {len(stat_data[COST_BY_MODEL]) if stat_data[COST_BY_MODEL] else 0}),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            new Chart(document.getElementById('modelPieChart_{div_id}'), {{
                                type: 'pie',
                                data: modelData,
                                options: {{
                                    responsive: true,
                                    plugins: {{
                                        legend: {{
                                            position: 'bottom'
                                        }},
                                        tooltip: {{
                                            callbacks: {{
                                                label: function(context) {{
                                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                                    return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} else {{
                            document.getElementById('modelPieChart_{div_id}').style.display = 'none';
                            document.querySelector('#modelPieChart_{div_id}').parentElement.querySelector('h3').textContent = '模型花费分布 (无数据)';
                        }}
                        
                        // 模块花费分布饼图
                        const moduleLabels = {list(sorted(stat_data[COST_BY_MODULE].keys())) if stat_data[COST_BY_MODULE] else []};
                        if (moduleLabels.length > 0) {{
                            const moduleData = {{
                                labels: moduleLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_MODULE][module_name] for module_name in sorted(stat_data[COST_BY_MODULE].keys())] if stat_data[COST_BY_MODULE] else []},
                                    backgroundColor: colors.slice(0, {len(stat_data[COST_BY_MODULE]) if stat_data[COST_BY_MODULE] else 0}),
                                    borderColor: colors.slice(0, {len(stat_data[COST_BY_MODULE]) if stat_data[COST_BY_MODULE] else 0}),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            new Chart(document.getElementById('modulePieChart_{div_id}'), {{
                                type: 'pie',
                                data: moduleData,
                                options: {{
                                    responsive: true,
                                    plugins: {{
                                        legend: {{
                                            position: 'bottom'
                                        }},
                                        tooltip: {{
                                            callbacks: {{
                                                label: function(context) {{
                                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                                    return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} else {{
                            document.getElementById('modulePieChart_{div_id}').style.display = 'none';
                            document.querySelector('#modulePieChart_{div_id}').parentElement.querySelector('h3').textContent = '模块花费分布 (无数据)';
                        }}
                        
                        // 请求类型花费分布饼图
                        const typeLabels = {list(sorted(stat_data[COST_BY_TYPE].keys())) if stat_data[COST_BY_TYPE] else []};
                        if (typeLabels.length > 0) {{
                            const typeData = {{
                                labels: typeLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_TYPE][req_type] for req_type in sorted(stat_data[COST_BY_TYPE].keys())] if stat_data[COST_BY_TYPE] else []},
                                    backgroundColor: colors.slice(0, {len(stat_data[COST_BY_TYPE]) if stat_data[COST_BY_TYPE] else 0}),
                                    borderColor: colors.slice(0, {len(stat_data[COST_BY_TYPE]) if stat_data[COST_BY_TYPE] else 0}),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            new Chart(document.getElementById('typePieChart_{div_id}'), {{
                                type: 'pie',
                                data: typeData,
                                options: {{
                                    responsive: true,
                                    plugins: {{
                                        legend: {{
                                            position: 'bottom'
                                        }},
                                        tooltip: {{
                                            callbacks: {{
                                                label: function(context) {{
                                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                                    return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} else {{
                            document.getElementById('typePieChart_{div_id}').style.display = 'none';
                            document.querySelector('#typePieChart_{div_id}').parentElement.querySelector('h3').textContent = '请求类型花费分布 (无数据)';
                        }}
                        
                        // 聊天消息分布饼图
                        const chatLabels = {[self.name_mapping.get(chat_id, ("未知聊天", 0))[0] for chat_id in sorted(stat_data[MSG_CNT_BY_CHAT].keys())] if stat_data[MSG_CNT_BY_CHAT] else []};
                        if (chatLabels.length > 0) {{
                            const chatData = {{
                                labels: chatLabels,
                                datasets: [{{
                                    data: {[stat_data[MSG_CNT_BY_CHAT][chat_id] for chat_id in sorted(stat_data[MSG_CNT_BY_CHAT].keys())] if stat_data[MSG_CNT_BY_CHAT] else []},
                                    backgroundColor: colors.slice(0, {len(stat_data[MSG_CNT_BY_CHAT]) if stat_data[MSG_CNT_BY_CHAT] else 0}),
                                    borderColor: colors.slice(0, {len(stat_data[MSG_CNT_BY_CHAT]) if stat_data[MSG_CNT_BY_CHAT] else 0}),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            new Chart(document.getElementById('chatPieChart_{div_id}'), {{
                                type: 'pie',
                                data: chatData,
                                options: {{
                                    responsive: true,
                                    plugins: {{
                                        legend: {{
                                            position: 'bottom'
                                        }},
                                        tooltip: {{
                                            callbacks: {{
                                                label: function(context) {{
                                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                                    const percentage = ((context.parsed / total) * 100).toFixed(1);
                                                    return context.label + ': ' + context.parsed + ' (' + percentage + '%)';
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} else {{
                            document.getElementById('chatPieChart_{div_id}').style.display = 'none';
                            document.querySelector('#chatPieChart_{div_id}').parentElement.querySelector('h3').textContent = '聊天消息分布 (无数据)';
                        }}
                    }}
                </script>

            </div>
            """

        tab_content_list = [
            _format_stat_data(stat[period[0]], period[0], now - period[1])
            for period in self.stat_period
            if period[0] != "all_time"
        ]

        tab_content_list.append(
            _format_stat_data(stat["all_time"], "all_time", datetime.fromtimestamp(local_storage["deploy_time"]))  # type: ignore
        )

        # 不再添加版本对比内容
        # 添加图表内容
        chart_data = self._generate_chart_data(stat)
        tab_content_list.append(self._generate_chart_tab(chart_data))

        # 添加指标趋势图表
        metrics_data = self._generate_metrics_data(now)
        tab_content_list.append(self._generate_metrics_tab(metrics_data))

        joined_tab_list = "\n".join(tab_list)
        joined_tab_content = "\n".join(tab_content_list)

        html_template = (
            """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MaiBot运行统计报告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #faf7ff;
            color: #3a2f57;
            line-height: 1.6;
        }
        .container {
            max-width: 900px;
            margin: 20px auto;
            background-color: #ffffff;
            padding: 25px;
            border-radius: 10px;
            box-shadow: 0 10px 28px rgba(122, 98, 182, 0.12);
            border: 1px solid #e5dcff;
        }
        h1, h2 {
            color: #473673;
            border-bottom: 2px solid #9f8efb;
            padding-bottom: 10px;
            margin-top: 0;
        }
        h1 {
            text-align: center;
            font-size: 2em;
        }
        h2 {
            font-size: 1.5em;
            margin-top: 30px;
        }
        p {
            margin-bottom: 10px;
        }
        .info-item {
            background-color: #f3eeff;
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 8px;
            font-size: 0.95em;
        }
        .info-item strong {
            color: #7162bf;
        }
        /* 新增：顶部工具条与按钮 */
        .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
        .toolbar .right { display: flex; gap: 8px; align-items: center; }
        .btn {
            border: 1px solid #e3daff;
            background-color: #fbf9ff;
            color: #4a3c75;
            padding: 8px 12px;
            border-radius: 6px;
            cursor: pointer;
            transition: all .2s ease;
        }
        .btn:hover { border-color: #9f8efb; color: #7c6bcf; background-color: #f1ecff; }
        /* 新增：KPI 卡片 */
        .kpi-cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 12px 0 6px; }
        .kpi-card {
            background: linear-gradient(145deg, #ffffff 0%, #f6f2ff 100%);
            border: 1px solid #e3dbff;
            border-radius: 10px;
            padding: 14px 16px;
            box-shadow: 0 6px 16px rgba(113, 98, 191, 0.1);
        }
        .kpi-title { font-size: 12px; color: #8579a6; letter-spacing: .3px; margin-bottom: 6px; }
        .kpi-value { font-size: 20px; font-weight: 700; letter-spacing: .2px; color: #8b5cf6; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 0.9em;
        }
        /* 新增：表格包裹容器，支持横向滚动 */
        .table-wrap { width: 100%; overflow-x: auto; border-radius: 6px; }
        th, td {
            border: 1px solid #e6ddff;
            padding: 10px;
            text-align: left;
        }
        th {
            background-color: #9f8efb;
            color: white;
            font-weight: bold;
            position: sticky;
            top: 0;
            z-index: 1;
        }
        tr:nth-child(even) {
            background-color: #f6f1ff;
        }
        .footer {
            text-align: center;
            margin-top: 30px;
            font-size: 0.8em;
            color: #7f8c8d;
        }
        .tabs {
            overflow: hidden;
            background: #f9f6ff;
            display: flex;
            border: 1px solid #e4dcff;
            border-radius: 10px;
            box-shadow: 0 8px 18px rgba(120, 101, 179, 0.08);
        }
        .tabs button {
            background: inherit; border: none; outline: none;
            padding: 12px 14px; cursor: pointer;
            transition: 0.2s; font-size: 15px;
            color: #52467a;
        }
        .tabs button:hover {
            background-color: #efe9ff;
        }
        .tabs button.active {
            background-color: rgba(159, 142, 251, 0.25);
            color: #6253a9;
        }
        .tab-content {
            display: none;
            padding: 20px;
            background-color: #fefcff;
            border: 1px solid #e4dcff;
            border-top: none;
            border-radius: 0 0 10px 10px;
        }
        .tab-content.active {
            display: block;
        }
    </style>
</head>
<body>
"""
            + f"""
    <div class="container">
        <div class="toolbar">
            <h1 style="margin: 0;">MaiBot运行统计报告</h1>
            <div class="right">
                <span class="info-item" style="margin: 0;"><strong>统计截止时间:</strong> {now.strftime("%Y-%m-%d %H:%M:%S")}</span>
            </div>
        </div>

        <div class="tabs">
            {joined_tab_list}
        </div>

        {joined_tab_content}
        <div class="footer">Made with ❤️ by MaiBot • 本页会定期自动覆盖生成</div>
    </div>
"""
            + """
<script>
    let i, tab_content, tab_links;
    tab_content = document.getElementsByClassName("tab-content");
    tab_links = document.getElementsByClassName("tab-link");
    
    tab_content[0].classList.add("active");
    tab_links[0].classList.add("active");

    function showTab(evt, tabName) {{
        for (i = 0; i < tab_content.length; i++) tab_content[i].classList.remove("active");
        for (i = 0; i < tab_links.length; i++) tab_links[i].classList.remove("active");
        document.getElementById(tabName).classList.add("active");
        evt.currentTarget.classList.add("active");
    }}
</script>
</body>
</html>
        """
        )

        with open(self.record_file_path, "w", encoding="utf-8") as f:
            f.write(html_template)

    def _generate_chart_data(self, stat: dict[str, Any]) -> dict:
        """生成图表数据"""
        now = datetime.now()
        chart_data = {}

        # 支持多个时间范围
        time_ranges = [
            ("6h", 6, 10),  # 6小时，10分钟间隔
            ("12h", 12, 15),  # 12小时，15分钟间隔
            ("24h", 24, 15),  # 24小时，15分钟间隔
            ("48h", 48, 30),  # 48小时，30分钟间隔
        ]

        for range_key, hours, interval_minutes in time_ranges:
            range_data = self._collect_interval_data(now, hours, interval_minutes)
            chart_data[range_key] = range_data

        return chart_data

    def _collect_interval_data(self, now: datetime, hours: int, interval_minutes: int) -> dict:
        """收集指定时间范围内每个间隔的数据"""
        # 生成时间点
        start_time = now - timedelta(hours=hours)
        time_points = []
        current_time = start_time

        while current_time <= now:
            time_points.append(current_time)
            current_time += timedelta(minutes=interval_minutes)

        # 初始化数据结构
        total_cost_data = [0] * len(time_points)
        cost_by_model = {}
        cost_by_module = {}
        message_by_chat = {}
        time_labels = [t.strftime("%H:%M") for t in time_points]

        interval_seconds = interval_minutes * 60

        # 查询LLM使用记录
        query_start_time = start_time
        for record in LLMUsage.select().where(LLMUsage.timestamp >= query_start_time):  # type: ignore
            record_time = record.timestamp

            # 找到对应的时间间隔索引
            time_diff = (record_time - start_time).total_seconds()
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                # 累加总花费数据
                cost = record.cost or 0.0
                total_cost_data[interval_index] += cost  # type: ignore

                # 累加按模型分类的花费
                model_name = record.model_assign_name or record.model_name or "unknown"
                if model_name not in cost_by_model:
                    cost_by_model[model_name] = [0] * len(time_points)
                cost_by_model[model_name][interval_index] += cost

                # 累加按模块分类的花费
                request_type = record.request_type or "unknown"
                module_name = request_type.split(".")[0] if "." in request_type else request_type
                if module_name not in cost_by_module:
                    cost_by_module[module_name] = [0] * len(time_points)
                cost_by_module[module_name][interval_index] += cost

        # 查询消息记录
        query_start_timestamp = start_time.timestamp()
        for message in Messages.select().where(Messages.time >= query_start_timestamp):  # type: ignore
            message_time_ts = message.time

            # 找到对应的时间间隔索引
            time_diff = message_time_ts - query_start_timestamp
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                # 确定聊天流名称
                chat_name = None
                if message.chat_info_group_id:
                    chat_name = message.chat_info_group_name or f"群{message.chat_info_group_id}"
                elif message.user_id:
                    chat_name = message.user_nickname or f"用户{message.user_id}"
                else:
                    continue

                if not chat_name:
                    continue

                # 累加消息数
                if chat_name not in message_by_chat:
                    message_by_chat[chat_name] = [0] * len(time_points)
                message_by_chat[chat_name][interval_index] += 1

        return {
            "time_labels": time_labels,
            "total_cost_data": total_cost_data,
            "cost_by_model": cost_by_model,
            "cost_by_module": cost_by_module,
            "message_by_chat": message_by_chat,
        }

    def _generate_chart_tab(self, chart_data: dict) -> str:
        # sourcery skip: extract-duplicate-method, move-assign-in-block
        """生成图表选项卡HTML内容"""

        # 生成不同颜色的调色板
        colors = [
            "#8b5cf6",
            "#9f8efb",
            "#b5a6ff",
            "#c7bbff",
            "#d9ceff",
            "#a78bfa",
            "#9073d8",
            "#bfaefc",
            "#cabdfd",
            "#e6e0ff",
        ]

        # 默认使用24小时数据生成数据集
        default_data = chart_data["24h"]

        # 为每个模型生成数据集
        model_datasets = []
        for i, (model_name, cost_data) in enumerate(default_data["cost_by_model"].items()):
            color = colors[i % len(colors)]
            model_datasets.append(f"""{{
                label: '{model_name}',
                data: {cost_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(model_datasets)

        # 为每个模块生成数据集
        module_datasets = []
        for i, (module_name, cost_data) in enumerate(default_data["cost_by_module"].items()):
            color = colors[i % len(colors)]
            module_datasets.append(f"""{{
                label: '{module_name}',
                data: {cost_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(module_datasets)

        # 为每个聊天流生成消息数据集
        message_datasets = []
        for i, (chat_name, message_data) in enumerate(default_data["message_by_chat"].items()):
            color = colors[i % len(colors)]
            message_datasets.append(f"""{{
                label: '{chat_name}',
                data: {message_data},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(message_datasets)

        return f"""
        <div id="charts" class="tab-content">
            <h2>数据图表</h2>
            
            <!-- 时间范围选择按钮 -->
            <div style="margin: 20px 0; text-align: center;">
                <label style="margin-right: 10px; font-weight: bold;">时间范围:</label>
                <button class="time-range-btn" onclick="switchTimeRange('6h')">6小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('12h')">12小时</button>
                <button class="time-range-btn active" onclick="switchTimeRange('24h')">24小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('48h')">48小时</button>
            </div>
            
            <div style="margin-top: 20px;">
                <div style="margin-bottom: 40px;">
                    <canvas id="totalCostChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="costByModuleChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="costByModelChart" width="800" height="400"></canvas>
                </div>
                <div>
                    <canvas id="messageByChatChart" width="800" height="400"></canvas>
                </div>
            </div>
            
            <style>
                .time-range-btn {{
                    background-color: #ecf0f1;
                    border: 1px solid #bdc3c7;
                    color: #2c3e50;
                    padding: 8px 16px;
                    margin: 0 5px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                
                .time-range-btn:hover {{
                    background-color: #d5dbdb;
                }}
                
                .time-range-btn.active {{
                    background-color: #3498db;
                    color: white;
                    border-color: #2980b9;
                }}
            </style>
            
            <script>
                const allChartData = {chart_data};
                let currentCharts = {{}};
                
                // 图表配置模板
                const chartConfigs = {{
                    totalCost: {{
                        id: 'totalCostChart',
                        title: '总花费',
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'total_cost_data',
                        fill: true
                    }},
                    costByModule: {{
                        id: 'costByModuleChart', 
                        title: '各模块花费',
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'cost_by_module',
                        fill: false
                    }},
                    costByModel: {{
                        id: 'costByModelChart',
                        title: '各模型花费', 
                        yAxisLabel: '花费 (¥)',
                        dataKey: 'cost_by_model',
                        fill: false
                    }},
                    messageByChat: {{
                        id: 'messageByChatChart',
                        title: '各聊天流消息数',
                        yAxisLabel: '消息数',
                        dataKey: 'message_by_chat',
                        fill: false
                    }},
                    focusCyclesByAction: {{
                        id: 'focusCyclesByActionChart',
                        title: 'Focus循环按Action类型',
                        yAxisLabel: '循环数',
                        dataKey: 'focus_cycles_by_action',
                        fill: false
                    }},
                    focusTimeByStage: {{
                        id: 'focusTimeByStageChart',
                        title: 'Focus各阶段累计时间',
                        yAxisLabel: '时间 (秒)',
                        dataKey: 'focus_time_by_stage',
                        fill: false
                    }}
                }};
                
                function switchTimeRange(timeRange) {{
                    // 更新按钮状态
                    document.querySelectorAll('.time-range-btn').forEach(btn => {{
                        btn.classList.remove('active');
                    }});
                    event.target.classList.add('active');
                    
                    // 更新图表数据
                    const data = allChartData[timeRange];
                    updateAllCharts(data, timeRange);
                }}
                
                function updateAllCharts(data, timeRange) {{
                    // 销毁现有图表
                    Object.values(currentCharts).forEach(chart => {{
                        if (chart) chart.destroy();
                    }});
                    
                    currentCharts = {{}};
                    
                    // 重新创建图表
                    createChart('totalCost', data, timeRange);
                    createChart('costByModule', data, timeRange);
                    createChart('costByModel', data, timeRange);
                    createChart('messageByChat', data, timeRange);
                }}
                
                function createChart(chartType, data, timeRange) {{
                    const config = chartConfigs[chartType];
                    const colors = ['#8b5cf6', '#9f8efb', '#b5a6ff', '#c7bbff', '#d9ceff', '#a78bfa', '#9073d8', '#bfaefc', '#cabdfd', '#e6e0ff'];
                    
                    let datasets = [];
                    
                    if (chartType === 'totalCost') {{
                        datasets = [{{
                            label: config.title,
                            data: data[config.dataKey],
                            borderColor: colors[0],
                            backgroundColor: 'rgba(52, 152, 219, 0.1)',
                            tension: 0.4,
                            fill: config.fill
                        }}];
                    }} else {{
                        let i = 0;
                        Object.entries(data[config.dataKey]).forEach(([name, chartData]) => {{
                            datasets.push({{
                                label: name,
                                data: chartData,
                                borderColor: colors[i % colors.length],
                                backgroundColor: colors[i % colors.length] + '20',
                                tension: 0.4,
                                fill: config.fill
                            }});
                            i++;
                        }});
                    }}
                    
                    currentCharts[chartType] = new Chart(document.getElementById(config.id), {{
                        type: 'line',
                        data: {{
                            labels: data.time_labels,
                            datasets: datasets
                        }},
                        options: {{
                            responsive: true,
                            plugins: {{
                                title: {{
                                    display: true,
                                    text: timeRange + '内' + config.title + '趋势',
                                    font: {{ size: 16 }}
                                }},
                                legend: {{
                                    display: chartType !== 'totalCost',
                                    position: 'top'
                                }}
                            }},
                            scales: {{
                                x: {{
                                    title: {{
                                        display: true,
                                        text: '时间'
                                    }},
                                    ticks: {{
                                        maxTicksLimit: 12
                                    }}
                                }},
                                y: {{
                                    title: {{
                                        display: true,
                                        text: config.yAxisLabel
                                    }},
                                    beginAtZero: true
                                }}
                            }},
                            interaction: {{
                                intersect: false,
                                mode: 'index'
                            }}
                        }}
                    }});
                }}
                
                // 初始化图表（默认24小时）
                document.addEventListener('DOMContentLoaded', function() {{
                    updateAllCharts(allChartData['24h'], '24h');
                }});
            </script>
        </div>
        """

    def _generate_metrics_data(self, now: datetime) -> dict:
        """生成指标趋势数据"""
        metrics_data = {}

        # 24小时尺度：1小时为单位
        metrics_data["24h"] = self._collect_metrics_interval_data(now, hours=24, interval_hours=1)

        # 7天尺度：1天为单位
        metrics_data["7d"] = self._collect_metrics_interval_data(now, hours=24 * 7, interval_hours=24)

        # 30天尺度：1天为单位
        metrics_data["30d"] = self._collect_metrics_interval_data(now, hours=24 * 30, interval_hours=24)

        return metrics_data

    def _collect_metrics_interval_data(self, now: datetime, hours: int, interval_hours: int) -> dict:
        """收集指定时间范围内每个间隔的指标数据"""
        start_time = now - timedelta(hours=hours)
        time_points = []
        current_time = start_time

        # 生成时间点
        while current_time <= now:
            time_points.append(current_time)
            current_time += timedelta(hours=interval_hours)

        # 初始化数据结构
        cost_per_100_messages = [0.0] * len(time_points)  # 花费/消息数量（每100条）
        cost_per_hour = [0.0] * len(time_points)  # 花费/时间（每小时）
        tokens_per_hour = [0.0] * len(time_points)  # Token/时间（每小时）
        cost_per_100_replies = [0.0] * len(time_points)  # 花费/回复数量（每100条）

        # 每个时间点的累计数据
        total_costs = [0.0] * len(time_points)
        total_tokens = [0] * len(time_points)
        total_messages = [0] * len(time_points)
        total_replies = [0] * len(time_points)
        total_online_hours = [0.0] * len(time_points)

        # 获取bot的QQ账号
        bot_qq_account = (
            str(global_config.bot.qq_account)
            if hasattr(global_config, "bot") and hasattr(global_config.bot, "qq_account")
            else ""
        )

        interval_seconds = interval_hours * 3600

        # 查询LLM使用记录
        query_start_time = start_time
        for record in LLMUsage.select().where(LLMUsage.timestamp >= query_start_time):  # type: ignore
            record_time = record.timestamp

            # 找到对应的时间间隔索引
            time_diff = (record_time - start_time).total_seconds()
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                cost = record.cost or 0.0
                prompt_tokens = record.prompt_tokens or 0
                completion_tokens = record.completion_tokens or 0
                total_token = prompt_tokens + completion_tokens

                total_costs[interval_index] += cost
                total_tokens[interval_index] += total_token

        # 查询消息记录
        query_start_timestamp = start_time.timestamp()
        for message in Messages.select().where(Messages.time >= query_start_timestamp):  # type: ignore
            message_time_ts = message.time

            time_diff = message_time_ts - query_start_timestamp
            interval_index = int(time_diff // interval_seconds)

            if 0 <= interval_index < len(time_points):
                total_messages[interval_index] += 1
                # 检查是否是bot发送的消息（回复）
                if bot_qq_account and message.user_id == bot_qq_account:
                    total_replies[interval_index] += 1

        # 查询在线时间记录
        for record in OnlineTime.select().where(OnlineTime.end_timestamp >= start_time):  # type: ignore
            record_start = record.start_timestamp
            record_end = record.end_timestamp

            # 找到记录覆盖的所有时间间隔
            for idx, time_point in enumerate(time_points):
                interval_start = time_point
                interval_end = time_point + timedelta(hours=interval_hours)

                # 计算重叠部分
                overlap_start = max(record_start, interval_start)
                overlap_end = min(record_end, interval_end)

                if overlap_end > overlap_start:
                    overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600.0
                    total_online_hours[idx] += overlap_hours

        # 计算指标
        for idx in range(len(time_points)):
            # 花费/消息数量（每100条）
            if total_messages[idx] > 0:
                cost_per_100_messages[idx] = total_costs[idx] / total_messages[idx] * 100

            # 花费/时间（每小时）
            if total_online_hours[idx] > 0:
                cost_per_hour[idx] = total_costs[idx] / total_online_hours[idx]

            # Token/时间（每小时）
            if total_online_hours[idx] > 0:
                tokens_per_hour[idx] = total_tokens[idx] / total_online_hours[idx]

            # 花费/回复数量（每100条）
            if total_replies[idx] > 0:
                cost_per_100_replies[idx] = total_costs[idx] / total_replies[idx] * 100

        # 生成时间标签
        if interval_hours == 1:
            time_labels = [t.strftime("%H:%M") for t in time_points]
        else:
            time_labels = [t.strftime("%m-%d") for t in time_points]

        return {
            "time_labels": time_labels,
            "cost_per_100_messages": cost_per_100_messages,
            "cost_per_hour": cost_per_hour,
            "tokens_per_hour": tokens_per_hour,
            "cost_per_100_replies": cost_per_100_replies,
        }

    def _generate_metrics_tab(self, metrics_data: dict) -> str:
        """生成指标趋势图表选项卡HTML内容"""
        colors = {
            "cost_per_100_messages": "#8b5cf6",
            "cost_per_hour": "#9f8efb",
            "tokens_per_hour": "#c7bbff",
            "cost_per_100_replies": "#d9ceff",
        }

        return f"""
        <div id="metrics" class="tab-content">
            <h2>指标趋势图表</h2>
            
            <!-- 时间尺度选择按钮 -->
            <div style="margin: 20px 0; text-align: center;">
                <label style="margin-right: 10px; font-weight: bold;">时间尺度:</label>
                <button class="time-scale-btn" onclick="switchMetricsTimeScale('24h')">24小时</button>
                <button class="time-scale-btn active" onclick="switchMetricsTimeScale('7d')">7天</button>
                <button class="time-scale-btn" onclick="switchMetricsTimeScale('30d')">30天</button>
            </div>
            
            <div style="margin-top: 20px;">
                <div style="margin-bottom: 40px;">
                    <canvas id="costPer100MessagesChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="costPerHourChart" width="800" height="400"></canvas>
                </div>
                <div style="margin-bottom: 40px;">
                    <canvas id="tokensPerHourChart" width="800" height="400"></canvas>
                </div>
                <div>
                    <canvas id="costPer100RepliesChart" width="800" height="400"></canvas>
                </div>
            </div>
            
            <style>
                .time-scale-btn {{
                    background-color: #ecf0f1;
                    border: 1px solid #bdc3c7;
                    color: #2c3e50;
                    padding: 8px 16px;
                    margin: 0 5px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                
                .time-scale-btn:hover {{
                    background-color: #d5dbdb;
                }}
                
                .time-scale-btn.active {{
                    background-color: #8b5cf6;
                    color: white;
                    border-color: #7c6bcf;
                }}
            </style>
            
            <script>
                const allMetricsData = {json.dumps(metrics_data)};
                let currentMetricsCharts = {{}};
                
                const metricsConfigs = {{
                    costPer100Messages: {{
                        id: 'costPer100MessagesChart',
                        title: '花费/消息数量',
                        yAxisLabel: '花费 (¥/100条)',
                        dataKey: 'cost_per_100_messages',
                        color: '{colors["cost_per_100_messages"]}'
                    }},
                    costPerHour: {{
                        id: 'costPerHourChart',
                        title: '花费/时间',
                        yAxisLabel: '花费 (¥/小时)',
                        dataKey: 'cost_per_hour',
                        color: '{colors["cost_per_hour"]}'
                    }},
                    tokensPerHour: {{
                        id: 'tokensPerHourChart',
                        title: 'Token/时间',
                        yAxisLabel: 'Token (/小时)',
                        dataKey: 'tokens_per_hour',
                        color: '{colors["tokens_per_hour"]}'
                    }},
                    costPer100Replies: {{
                        id: 'costPer100RepliesChart',
                        title: '花费/回复数量',
                        yAxisLabel: '花费 (¥/100条)',
                        dataKey: 'cost_per_100_replies',
                        color: '{colors["cost_per_100_replies"]}'
                    }}
                }};
                
                function switchMetricsTimeScale(timeScale) {{
                    // 更新按钮状态
                    document.querySelectorAll('.time-scale-btn').forEach(btn => {{
                        btn.classList.remove('active');
                    }});
                    event.target.classList.add('active');
                    
                    // 更新图表数据
                    const data = allMetricsData[timeScale];
                    updateAllMetricsCharts(data, timeScale);
                }}
                
                function updateAllMetricsCharts(data, timeScale) {{
                    // 销毁现有图表
                    Object.values(currentMetricsCharts).forEach(chart => {{
                        if (chart) chart.destroy();
                    }});
                    
                    currentMetricsCharts = {{}};
                    
                    // 重新创建图表
                    createMetricsChart('costPer100Messages', data, timeScale);
                    createMetricsChart('costPerHour', data, timeScale);
                    createMetricsChart('tokensPerHour', data, timeScale);
                    createMetricsChart('costPer100Replies', data, timeScale);
                }}
                
                function createMetricsChart(chartType, data, timeScale) {{
                    const config = metricsConfigs[chartType];
                    
                    currentMetricsCharts[chartType] = new Chart(document.getElementById(config.id), {{
                        type: 'line',
                        data: {{
                            labels: data.time_labels,
                            datasets: [{{
                                label: config.title,
                                data: data[config.dataKey],
                                borderColor: config.color,
                                backgroundColor: config.color + '20',
                                tension: 0.4,
                                fill: false
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            plugins: {{
                                title: {{
                                    display: true,
                                    text: timeScale + '内' + config.title + '趋势',
                                    font: {{ size: 16 }}
                                }},
                                legend: {{
                                    display: false
                                }}
                            }},
                            scales: {{
                                x: {{
                                    title: {{
                                        display: true,
                                        text: '时间'
                                    }},
                                    ticks: {{
                                        maxTicksLimit: 12
                                    }}
                                }},
                                y: {{
                                    title: {{
                                        display: true,
                                        text: config.yAxisLabel
                                    }},
                                    beginAtZero: true
                                }}
                            }},
                            interaction: {{
                                intersect: false,
                                mode: 'index'
                            }}
                        }}
                    }});
                }}
                
                // 初始化图表（默认7天）
                document.addEventListener('DOMContentLoaded', function() {{
                    updateAllMetricsCharts(allMetricsData['7d'], '7d');
                }});
            </script>
        </div>
        """


class AsyncStatisticOutputTask(AsyncTask):
    """完全异步的统计输出任务 - 更高性能版本"""

    def __init__(self, record_file_path: str = "maibot_statistics.html"):
        # 延迟0秒启动，运行间隔300秒
        super().__init__(task_name="Async Statistics Data Output Task", wait_before_start=0, run_interval=300)

        # 直接复用 StatisticOutputTask 的初始化逻辑
        temp_stat_task = StatisticOutputTask(record_file_path)
        self.name_mapping = temp_stat_task.name_mapping
        self.record_file_path = temp_stat_task.record_file_path
        self.stat_period = temp_stat_task.stat_period

    async def run(self):
        """完全异步执行统计任务"""

        async def _async_collect_and_output():
            try:
                now = datetime.now()
                loop = asyncio.get_event_loop()

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    logger.info("正在后台收集统计数据...")

                    # 数据收集任务
                    collect_task = asyncio.create_task(
                        loop.run_in_executor(executor, self._collect_all_statistics, now)  # type: ignore
                    )

                    stats = await collect_task
                    logger.info("统计数据收集完成")

                    # 创建并发的输出任务
                    output_tasks = [
                        asyncio.create_task(loop.run_in_executor(executor, self._statistic_console_output, stats, now)),  # type: ignore
                        asyncio.create_task(loop.run_in_executor(executor, self._generate_html_report, stats, now)),  # type: ignore
                    ]

                    # 等待所有输出任务完成
                    await asyncio.gather(*output_tasks)

                logger.info("统计数据后台输出完成")
            except Exception as e:
                logger.exception(f"后台统计数据输出过程中发生异常：{e}")

        # 创建后台任务，立即返回
        asyncio.create_task(_async_collect_and_output())

    # 复用 StatisticOutputTask 的所有方法
    def _collect_all_statistics(self, now: datetime):
        return StatisticOutputTask._collect_all_statistics(self, now)  # type: ignore

    def _statistic_console_output(self, stats: Dict[str, Any], now: datetime):
        return StatisticOutputTask._statistic_console_output(self, stats, now)  # type: ignore

    def _generate_html_report(self, stats: dict[str, Any], now: datetime):
        return StatisticOutputTask._generate_html_report(self, stats, now)  # type: ignore

    # 其他需要的方法也可以类似复用...
    @staticmethod
    def _collect_model_request_for_period(collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        return StatisticOutputTask._collect_model_request_for_period(collect_period)

    @staticmethod
    def _collect_online_time_for_period(collect_period: List[Tuple[str, datetime]], now: datetime) -> Dict[str, Any]:
        return StatisticOutputTask._collect_online_time_for_period(collect_period, now)

    def _collect_message_count_for_period(self, collect_period: List[Tuple[str, datetime]]) -> Dict[str, Any]:
        return StatisticOutputTask._collect_message_count_for_period(self, collect_period)  # type: ignore

    @staticmethod
    def _format_total_stat(stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_total_stat(stats)

    @staticmethod
    def _format_model_classified_stat(stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_model_classified_stat(stats)

    def _format_chat_stat(self, stats: Dict[str, Any]) -> str:
        return StatisticOutputTask._format_chat_stat(self, stats)  # type: ignore

    def _generate_chart_data(self, stat: dict[str, Any]) -> dict:
        return StatisticOutputTask._generate_chart_data(self, stat)  # type: ignore

    def _collect_interval_data(self, now: datetime, hours: int, interval_minutes: int) -> dict:
        return StatisticOutputTask._collect_interval_data(self, now, hours, interval_minutes)  # type: ignore

    def _generate_chart_tab(self, chart_data: dict) -> str:
        return StatisticOutputTask._generate_chart_tab(self, chart_data)  # type: ignore

    def _generate_metrics_data(self, now: datetime) -> dict:
        return StatisticOutputTask._generate_metrics_data(self, now)  # type: ignore

    def _collect_metrics_interval_data(self, now: datetime, hours: int, interval_hours: int) -> dict:
        return StatisticOutputTask._collect_metrics_interval_data(self, now, hours, interval_hours)  # type: ignore

    def _generate_metrics_tab(self, metrics_data: dict) -> str:
        return StatisticOutputTask._generate_metrics_tab(self, metrics_data)  # type: ignore

    def _get_chat_display_name_from_id(self, chat_id: str) -> str:
        return StatisticOutputTask._get_chat_display_name_from_id(self, chat_id)  # type: ignore

    def _convert_defaultdict_to_dict(self, data):
        return StatisticOutputTask._convert_defaultdict_to_dict(self, data)  # type: ignore

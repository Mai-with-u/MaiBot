from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from os import getenv
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Callable, DefaultDict, Dict, List, Mapping, Tuple, cast

import asyncio
import concurrent.futures
import json

from typing_extensions import TypedDict

from src.common.logger import get_logger
from src.manager.async_task_manager import AsyncTask
from src.services.statistics_cache_files import full_statistics_cache
from src.services.statistics_usage_rules import (
    UsageKey,
    UsageMetrics,
    ceil_hour,
    clamp_hour_range,
    normalize_prompt_cache_tokens,
    normalize_usage_record,
)
from src.services.statistics_usage_rules import bucket_start as usage_bucket_start

from .statistics_timeseries import (
    SOURCE_HOURLY,
    SOURCE_RAW,
    SOURCE_UNAGGREGATED,
    PeriodTrendCollector,
    PeriodTrendWindow,
    UsageEntry,
    build_series_windows,
    collect_time_series,
    hours_needing_detail,
    usage_entry_from_record,
)

if TYPE_CHECKING:
    from src.services.statistics_usage_hourly import UsageHourlyState
    from src.webui.schemas.statistics import (
        DetailedDistributionItem,
        DetailedReliabilityData,
        DetailedStatisticsBreakdown,
        DetailedStatisticsData,
        DetailedStatisticsDistributions,
        DetailedStatisticsSummary,
    )

logger = get_logger("maibot_statistic")

STATISTICS_REPORT_PATH_ENV = "MAIBOT_STATISTICS_REPORT_PATH"
DEFAULT_STATISTICS_REPORT_PATH = "maibot_statistics.html"


class _LocalStorageProxy:
    """延迟访问本地存储，避免导入统计任务时读取完整本地记事本。"""

    @staticmethod
    def _store():
        from src.manager.local_store_manager import local_storage

        return local_storage

    def __contains__(self, key: str) -> bool:
        return key in self._store()

    def __getitem__(self, key: str):
        return self._store()[key]

    def __setitem__(self, key: str, value) -> None:
        self._store()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._store()[key]


local_storage = _LocalStorageProxy()


def fetch_model_usage_since(*args, **kwargs):
    from src.services.statistics_service import fetch_model_usage_since as impl

    return impl(*args, **kwargs)


def refresh_usage_hourly(*args, **kwargs):
    from src.services.statistics_usage_hourly import refresh_usage_hourly as impl

    return impl(*args, **kwargs)


def fetch_usage_hourly(*args, **kwargs):
    from src.services.statistics_usage_hourly import fetch_usage_hourly as impl

    return impl(*args, **kwargs)


def fetch_usage_records_between(*args, **kwargs):
    from src.services.statistics_usage_hourly import fetch_usage_records_between as impl

    return impl(*args, **kwargs)


def fetch_usage_records_after_id(*args, **kwargs):
    from src.services.statistics_usage_hourly import fetch_usage_records_after_id as impl

    return impl(*args, **kwargs)


def fetch_usage_duration_moments(*args, **kwargs):
    from src.services.statistics_usage_hourly import fetch_usage_duration_moments as impl

    return impl(*args, **kwargs)


def fetch_model_duration_aggregates_since(*args, **kwargs):
    from src.services.statistics_service import fetch_model_duration_aggregates_since as impl

    return impl(*args, **kwargs)


def fetch_online_time_since(*args, **kwargs):
    from src.services.statistics_service import fetch_online_time_since as impl

    return impl(*args, **kwargs)


def get_earliest_statistics_time(*args, **kwargs):
    from src.services.statistics_service import get_earliest_statistics_time as impl

    return impl(*args, **kwargs)


async def refresh_dashboard_statistics_cache(*args, **kwargs):
    from src.services.statistics_service import refresh_dashboard_statistics_cache as impl

    return await impl(*args, **kwargs)


def count_tool_records_since(*args, **kwargs):
    from src.services.statistics_aggregation_service import count_tool_records_since as impl

    return impl(*args, **kwargs)


def fetch_message_count_by_chat_since(*args, **kwargs):
    from src.services.statistics_aggregation_service import fetch_message_count_by_chat_since as impl

    return impl(*args, **kwargs)


def refresh_statistics_aggregates(*args, **kwargs):
    from src.services.statistics_aggregation_service import refresh_statistics_aggregates as impl

    return impl(*args, **kwargs)


def count_chat_messages_between(*args, **kwargs):
    from src.services.statistics_aggregation_service import count_chat_messages_between as impl

    return impl(*args, **kwargs)


def count_tool_records_between(*args, **kwargs):
    from src.services.statistics_aggregation_service import count_tool_records_between as impl

    return impl(*args, **kwargs)


def fetch_window_usage_totals(*args, **kwargs):
    from src.services.statistics_service import fetch_window_usage_totals as impl

    return impl(*args, **kwargs)


def fetch_model_task_usage_groups(*args, **kwargs):
    from src.services.model_task_usage_service import fetch_model_task_usage_groups as impl

    return impl(*args, **kwargs)


def fetch_model_task_usage_minutes(*args, **kwargs):
    from src.services.model_task_usage_service import fetch_model_task_usage_minutes as impl

    return impl(*args, **kwargs)


def get_model_task_usage_retention() -> timedelta:
    from src.services.model_task_usage_service import MODEL_TASK_USAGE_RETENTION

    return MODEL_TASK_USAGE_RETENTION


# 详细统计各时段趋势的分桶粒度（分钟）；趋势明细最多覆盖 30 天，更早的时段会被标记为截断。
PERIOD_TREND_BUCKET_MINUTES: Dict[str, int] = {
    "all_time": 1440,
    "last_30_days": 1440,
    "last_7_days": 360,
    "last_3_days": 180,
    "last_24_hours": 60,
    "last_3_hours": 10,
    "last_hour": 5,
    "last_15_minutes": 1,
}
PERIOD_TREND_MAX_RANGE = timedelta(days=30)
EMBEDDING_TASK_NAMES = frozenset({"embedding", "image_embedding"})
_DURATION_CATEGORIES = ("type", "user", "model", "module", "task")


def _resolve_statistics_report_path(record_file_path: str | None = None) -> str:
    if record_file_path:
        return record_file_path

    configured_path = getenv(STATISTICS_REPORT_PATH_ENV, "").strip()
    return configured_path or DEFAULT_STATISTICS_REPORT_PATH


class StatPeriodData(TypedDict):
    classified_requests: Dict[str, Dict[str, float]]
    total_requests: int
    total_cost: float
    requests_by_type: defaultdict[str, int]
    requests_by_user: defaultdict[str, int]
    requests_by_model: defaultdict[str, int]
    requests_by_module: defaultdict[str, int]
    requests_by_task: DefaultDict[str, int]
    in_tokens_by_type: defaultdict[str, int]
    in_tokens_by_user: defaultdict[str, int]
    in_tokens_by_model: defaultdict[str, int]
    in_tokens_by_module: defaultdict[str, int]
    in_tokens_by_task: DefaultDict[str, int]
    out_tokens_by_type: defaultdict[str, int]
    out_tokens_by_user: defaultdict[str, int]
    out_tokens_by_model: defaultdict[str, int]
    out_tokens_by_module: defaultdict[str, int]
    out_tokens_by_task: DefaultDict[str, int]
    tokens_by_type: defaultdict[str, int]
    tokens_by_user: defaultdict[str, int]
    tokens_by_model: defaultdict[str, int]
    tokens_by_module: defaultdict[str, int]
    tokens_by_task: DefaultDict[str, int]
    tokens_by_chat: DefaultDict[str, int]
    costs_by_type: defaultdict[str, float]
    costs_by_user: defaultdict[str, float]
    costs_by_model: defaultdict[str, float]
    costs_by_module: defaultdict[str, float]
    costs_by_task: DefaultDict[str, float]
    costs_by_chat: defaultdict[str, float]
    cache_hit_tokens: int
    cache_miss_tokens: int
    cache_hit_tokens_by_type: defaultdict[str, int]
    cache_hit_tokens_by_user: defaultdict[str, int]
    cache_hit_tokens_by_model: defaultdict[str, int]
    cache_hit_tokens_by_module: defaultdict[str, int]
    cache_hit_tokens_by_task: DefaultDict[str, int]
    cache_miss_tokens_by_type: defaultdict[str, int]
    cache_miss_tokens_by_user: defaultdict[str, int]
    cache_miss_tokens_by_model: defaultdict[str, int]
    cache_miss_tokens_by_module: defaultdict[str, int]
    cache_miss_tokens_by_task: DefaultDict[str, int]
    time_costs_by_type: defaultdict[str, list[float]]
    time_costs_by_user: defaultdict[str, list[float]]
    time_costs_by_model: defaultdict[str, list[float]]
    time_costs_by_module: defaultdict[str, list[float]]
    avg_time_costs_by_type: defaultdict[str, float]
    avg_time_costs_by_user: defaultdict[str, float]
    avg_time_costs_by_model: defaultdict[str, float]
    avg_time_costs_by_module: defaultdict[str, float]
    avg_time_costs_by_task: DefaultDict[str, float]
    std_time_costs_by_type: defaultdict[str, float]
    std_time_costs_by_user: defaultdict[str, float]
    std_time_costs_by_model: defaultdict[str, float]
    std_time_costs_by_module: defaultdict[str, float]
    std_time_costs_by_task: DefaultDict[str, float]
    online_time: float
    total_messages: int
    messages_by_chat: defaultdict[str, int]
    total_replies: int


StatPeriodMapping = dict[str, StatPeriodData]

# 统计数据的键
TOTAL_REQ_CNT = "total_requests"
TOTAL_COST = "total_cost"
REQ_CNT_BY_TYPE = "requests_by_type"
REQ_CNT_BY_USER = "requests_by_user"
REQ_CNT_BY_MODEL = "requests_by_model"
REQ_CNT_BY_MODULE = "requests_by_module"
REQ_CNT_BY_TASK = "requests_by_task"
IN_TOK_BY_TYPE = "in_tokens_by_type"
IN_TOK_BY_USER = "in_tokens_by_user"
IN_TOK_BY_MODEL = "in_tokens_by_model"
IN_TOK_BY_MODULE = "in_tokens_by_module"
IN_TOK_BY_TASK = "in_tokens_by_task"
OUT_TOK_BY_TYPE = "out_tokens_by_type"
OUT_TOK_BY_USER = "out_tokens_by_user"
OUT_TOK_BY_MODEL = "out_tokens_by_model"
OUT_TOK_BY_MODULE = "out_tokens_by_module"
OUT_TOK_BY_TASK = "out_tokens_by_task"
TOTAL_TOK_BY_TYPE = "tokens_by_type"
TOTAL_TOK_BY_USER = "tokens_by_user"
TOTAL_TOK_BY_MODEL = "tokens_by_model"
TOTAL_TOK_BY_MODULE = "tokens_by_module"
TOTAL_TOK_BY_TASK = "tokens_by_task"
TOTAL_TOK_BY_CHAT = "tokens_by_chat"
COST_BY_TYPE = "costs_by_type"
COST_BY_USER = "costs_by_user"
COST_BY_MODEL = "costs_by_model"
COST_BY_MODULE = "costs_by_module"
COST_BY_TASK = "costs_by_task"
COST_BY_CHAT = "costs_by_chat"
GLOBAL_COST_SESSION_KEY = "__global__"
CACHE_HIT_TOK = "cache_hit_tokens"
CACHE_MISS_TOK = "cache_miss_tokens"
CACHE_HIT_TOK_BY_TYPE = "cache_hit_tokens_by_type"
CACHE_HIT_TOK_BY_USER = "cache_hit_tokens_by_user"
CACHE_HIT_TOK_BY_MODEL = "cache_hit_tokens_by_model"
CACHE_HIT_TOK_BY_MODULE = "cache_hit_tokens_by_module"
CACHE_HIT_TOK_BY_TASK = "cache_hit_tokens_by_task"
CACHE_MISS_TOK_BY_TYPE = "cache_miss_tokens_by_type"
CACHE_MISS_TOK_BY_USER = "cache_miss_tokens_by_user"
CACHE_MISS_TOK_BY_MODEL = "cache_miss_tokens_by_model"
CACHE_MISS_TOK_BY_MODULE = "cache_miss_tokens_by_module"
CACHE_MISS_TOK_BY_TASK = "cache_miss_tokens_by_task"
TIME_COST_BY_TYPE = "time_costs_by_type"
TIME_COST_BY_USER = "time_costs_by_user"
TIME_COST_BY_MODEL = "time_costs_by_model"
TIME_COST_BY_MODULE = "time_costs_by_module"
AVG_TIME_COST_BY_TYPE = "avg_time_costs_by_type"
AVG_TIME_COST_BY_USER = "avg_time_costs_by_user"
AVG_TIME_COST_BY_MODEL = "avg_time_costs_by_model"
AVG_TIME_COST_BY_MODULE = "avg_time_costs_by_module"
AVG_TIME_COST_BY_TASK = "avg_time_costs_by_task"
STD_TIME_COST_BY_TYPE = "std_time_costs_by_type"
STD_TIME_COST_BY_USER = "std_time_costs_by_user"
STD_TIME_COST_BY_MODEL = "std_time_costs_by_model"
STD_TIME_COST_BY_MODULE = "std_time_costs_by_module"
STD_TIME_COST_BY_TASK = "std_time_costs_by_task"
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
        from src.common.database.database import get_db_session

        with get_db_session() as _:
            return

    async def run(self):  # sourcery skip: use-named-expression
        try:
            current_time = datetime.now()
            extended_end_time = current_time + timedelta(minutes=1)

            if self.record_id:
                # 如果有记录，则更新结束时间
                from sqlmodel import col, select
                from src.common.database.database import get_db_session
                from src.common.database.database_model import OnlineTime

                with get_db_session() as session:
                    statement = select(OnlineTime).where(col(OnlineTime.id) == self.record_id).limit(1)
                    existing_record = session.exec(statement).first()
                    if existing_record:
                        existing_record.end_timestamp = extended_end_time
                        session.add(existing_record)
                    else:
                        self.record_id = None

            if not self.record_id:  # Check again if record_id was reset or initially None
                # 如果没有记录，检查一分钟以内是否已有记录
                # Look for a record whose end_timestamp is recent enough to be considered ongoing
                from sqlmodel import col, select
                from src.common.database.database import get_db_session
                from src.common.database.database_model import OnlineTime

                with get_db_session() as session:
                    statement = (
                        select(OnlineTime)
                        .where(col(OnlineTime.end_timestamp) >= (current_time - timedelta(minutes=1)))
                        .order_by(col(OnlineTime.end_timestamp).desc())
                        .limit(1)
                    )
                    recent_record = session.exec(statement).first()

                    if recent_record:
                        self.record_id = recent_record.id
                        recent_record.end_timestamp = extended_end_time
                        session.add(recent_record)
                    else:
                        new_record = OnlineTime(
                            timestamp=current_time,
                            start_timestamp=current_time,
                            end_timestamp=extended_end_time,
                            duration_minutes=5,
                        )
                        session.add(new_record)
                        session.flush()
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
            return f"{number_part}<span style='color: var(--statistics-primary); font-weight: bold;'>K</span>"
        else:
            # 控制台输出：纯文本，K大写
            return f"{number_part}{k_suffix}"
    else:
        # 小于10000，直接显示
        if isinstance(num, float):
            return f"{num:.1f}" if num != int(num) else str(int(num))
        else:
            return str(num)


_normalize_prompt_cache_tokens = normalize_prompt_cache_tokens


def _format_cache_hit_rate(hit_tokens: int, miss_tokens: int) -> str:
    total_cache_tokens = hit_tokens + miss_tokens
    if total_cache_tokens <= 0:
        return "N/A"
    return f"{hit_tokens / total_cache_tokens * 100:.2f}%"


def _json_for_html_script(value: object) -> str:
    json_text = json.dumps(value, ensure_ascii=False)
    return (
        json_text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _build_llm_owner_distribution(values_by_type: Mapping[str, float]) -> Dict[str, float]:
    """按相同的调用来源规则聚合花费或 Token，区分本体与各插件。"""
    owner_costs: defaultdict[str, float] = defaultdict(float)
    for request_type, cost in values_by_type.items():
        normalized_request_type = str(request_type or "").strip()
        if normalized_request_type.startswith("plugin."):
            plugin_id = normalized_request_type.removeprefix("plugin.").strip()
            if plugin_id.endswith(".asr"):
                plugin_id = plugin_id.removesuffix(".asr").strip()
            owner_label = f"插件 {plugin_id}" if plugin_id else "插件（未知）"
        else:
            owner_label = "本体"
        owner_costs[owner_label] += float(cost or 0.0)

    return {
        owner_label: owner_costs[owner_label]
        for owner_label in sorted(owner_costs.keys(), key=lambda label: (label != "本体", label))
        if owner_costs[owner_label] > 0
    }


class StatisticOutputTask(AsyncTask):
    """统计输出任务"""

    SEP_LINE = "-" * 84
    RUN_INTERVAL_SECONDS = 15 * 60
    # 报表之后补做回填的时间上限；每 20 秒约 58 万条，足够一次补完百万级的历史
    BACKFILL_AFTER_REPORT_SECONDS = 120.0

    def __init__(self, record_file_path: str | None = None):
        # 启动后立即运行，之后每15分钟输出一次统计数据
        super().__init__(
            task_name="Statistics Data Output Task",
            wait_before_start=0,
            run_interval=self.RUN_INTERVAL_SECONDS,
        )

        self.name_mapping: dict[str, tuple[str, float]] = {}
        """
            联系人/群聊名称映射 {聊天ID: (联系人/群聊名称, 记录时间（timestamp）)}
            注：设计记录时间的目的是方便更新名称，使联系人/群聊名称保持最新
        """

        self.record_file_path: str = _resolve_statistics_report_path(record_file_path)
        """
        记录文件路径
        """

        now = datetime.now()
        if "deploy_time" in local_storage:
            # 如果存在部署时间，则使用该时间作为全量统计的起始时间
            deploy_time = datetime.fromtimestamp(self._to_float_timestamp(local_storage["deploy_time"]))
        else:
            # 否则，使用最大时间范围，并记录部署时间为当前时间
            deploy_time = datetime(2000, 1, 1)
            local_storage["deploy_time"] = now.timestamp()

        self._deploy_time = deploy_time
        self._all_time_start_resolved = False
        self.all_time_start_time = deploy_time

        self.stat_period: list[tuple[str, timedelta, str]] = [
            ("all_time", now - self.all_time_start_time, "自部署以来"),  # 必须保留"all_time"
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


    def _ensure_all_time_start_time(self, now: datetime) -> None:
        """首次输出统计前再查询真实最早统计时间。"""

        if self._all_time_start_resolved:
            return

        self.all_time_start_time = get_earliest_statistics_time(self._deploy_time)
        self.stat_period = [item for item in self.stat_period if item[0] != "all_time"]
        self.stat_period.insert(0, ("all_time", now - self.all_time_start_time, "自部署以来"))
        self._all_time_start_resolved = True


    def _statistic_console_output(self, stats: StatPeriodMapping, now: datetime) -> None:
        """
        输出统计数据到控制台
        :param stats: 统计数据
        :param now: 基准当前时间
        """
        # 输出最近一小时的统计数据

        output = [
            self.SEP_LINE,
            f"最近1小时统计 · {now:%Y-%m-%d %H:%M:%S}",
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
            started_at = perf_counter()
            now = datetime.now()
            # 首次解析统计起点需要查询数据库，也放在线程中，避免占住主事件循环。
            await asyncio.to_thread(self._ensure_all_time_start_time, now)
            logger.info(f"统计阶段耗时: 起点查询={perf_counter() - started_at:.2f}s")

            # 使用线程池并行执行耗时操作
            loop = asyncio.get_event_loop()

            # 在线程池中收集数据，再并行生成控制台输出和 HTML 报告。
            with concurrent.futures.ThreadPoolExecutor() as executor:
                logger.info("正在收集统计数据...")

                stage_started_at = perf_counter()
                await loop.run_in_executor(executor, refresh_statistics_aggregates)
                self._usage_hourly = await loop.run_in_executor(executor, refresh_usage_hourly)
                StatisticOutputTask._usage_hourly_for_comparison = self._usage_hourly
                logger.info(
                    f"统计阶段耗时: 增量汇总={perf_counter() - stage_started_at:.2f}s，"
                    f"小时汇总{'已就绪' if self._usage_hourly.ready else '未就绪，本轮读明细'}"
                )

                # 数据收集任务
                stage_started_at = perf_counter()
                collect_task = loop.run_in_executor(executor, self._collect_all_statistics, now)

                # 等待数据收集完成
                stats = await collect_task
                logger.info(f"统计阶段耗时: 多周期统计={perf_counter() - stage_started_at:.2f}s")
                stage_started_at = perf_counter()
                try:
                    await refresh_dashboard_statistics_cache(usage_hourly=self._usage_hourly)
                except Exception as e:
                    logger.warning(f"刷新 WebUI 统计缓存失败，将继续生成 HTML 报告: {e}")
                logger.info(f"统计数据收集完成，首页缓存刷新耗时={perf_counter() - stage_started_at:.2f}s")

                # 并行执行控制台输出和HTML报告生成
                stage_started_at = perf_counter()
                console_task = loop.run_in_executor(executor, self._statistic_console_output, stats, now)
                html_task = loop.run_in_executor(executor, self._generate_html_report, stats, now)

                # 等待两个输出任务完成
                await asyncio.gather(console_task, html_task)

            logger.info(
                f"统计数据输出完成，报表输出耗时={perf_counter() - stage_started_at:.2f}s，"
                f"总耗时={perf_counter() - started_at:.2f}s"
            )
            await self._finish_usage_hourly_backfill()
        except Exception as e:
            logger.exception(f"输出统计数据过程中发生异常，错误信息：{e}")

    async def _finish_usage_hourly_backfill(self) -> None:
        """本轮已用明细出完报表；汇总还没追上时接着回填，让下一轮直接用上汇总。

        单轮开头的回填受时间预算限制，记录多时一轮回填不完；这里在报表之后补做，不影响本轮耗时。
        失败照常按退避重试，不抛出。
        """
        usage_hourly = getattr(self, "_usage_hourly", None)
        if usage_hourly is None or usage_hourly.ready:
            return
        started_at = perf_counter()
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(
            None, lambda: refresh_usage_hourly(time_budget_seconds=self.BACKFILL_AFTER_REPORT_SECONDS)
        )
        logger.info(
            f"报表之后继续回填小时汇总：耗时={perf_counter() - started_at:.2f}s，"
            f"{'已追上，下一轮改读汇总' if state.ready else '仍未追上，下一轮继续'}"
        )

    # -- 以下为统计数据收集方法 --

    @staticmethod
    def _build_stat_period_data() -> StatPeriodData:
        time_costs_by_type: defaultdict[str, list[float]] = defaultdict(list)
        time_costs_by_user: defaultdict[str, list[float]] = defaultdict(list)
        time_costs_by_model: defaultdict[str, list[float]] = defaultdict(list)
        time_costs_by_module: defaultdict[str, list[float]] = defaultdict(list)
        avg_time_costs_by_type: defaultdict[str, float] = defaultdict(float)
        avg_time_costs_by_user: defaultdict[str, float] = defaultdict(float)
        avg_time_costs_by_model: defaultdict[str, float] = defaultdict(float)
        avg_time_costs_by_module: defaultdict[str, float] = defaultdict(float)
        avg_time_costs_by_task: DefaultDict[str, float] = defaultdict(float)
        std_time_costs_by_type: defaultdict[str, float] = defaultdict(float)
        std_time_costs_by_user: defaultdict[str, float] = defaultdict(float)
        std_time_costs_by_model: defaultdict[str, float] = defaultdict(float)
        std_time_costs_by_module: defaultdict[str, float] = defaultdict(float)
        std_time_costs_by_task: DefaultDict[str, float] = defaultdict(float)

        return {
            "classified_requests": {},
            TOTAL_REQ_CNT: 0,
            REQ_CNT_BY_TYPE: defaultdict(int),
            REQ_CNT_BY_USER: defaultdict(int),
            REQ_CNT_BY_MODEL: defaultdict(int),
            REQ_CNT_BY_MODULE: defaultdict(int),
            REQ_CNT_BY_TASK: defaultdict(int),
            IN_TOK_BY_TYPE: defaultdict(int),
            IN_TOK_BY_USER: defaultdict(int),
            IN_TOK_BY_MODEL: defaultdict(int),
            IN_TOK_BY_MODULE: defaultdict(int),
            IN_TOK_BY_TASK: defaultdict(int),
            OUT_TOK_BY_TYPE: defaultdict(int),
            OUT_TOK_BY_USER: defaultdict(int),
            OUT_TOK_BY_MODEL: defaultdict(int),
            OUT_TOK_BY_MODULE: defaultdict(int),
            OUT_TOK_BY_TASK: defaultdict(int),
            TOTAL_TOK_BY_TYPE: defaultdict(int),
            TOTAL_TOK_BY_USER: defaultdict(int),
            TOTAL_TOK_BY_MODEL: defaultdict(int),
            TOTAL_TOK_BY_MODULE: defaultdict(int),
            TOTAL_TOK_BY_TASK: defaultdict(int),
            TOTAL_TOK_BY_CHAT: defaultdict(int),
            TOTAL_COST: 0.0,
            COST_BY_TYPE: defaultdict(float),
            COST_BY_USER: defaultdict(float),
            COST_BY_MODEL: defaultdict(float),
            COST_BY_MODULE: defaultdict(float),
            COST_BY_TASK: defaultdict(float),
            COST_BY_CHAT: defaultdict(float),
            CACHE_HIT_TOK: 0,
            CACHE_MISS_TOK: 0,
            CACHE_HIT_TOK_BY_TYPE: defaultdict(int),
            CACHE_HIT_TOK_BY_USER: defaultdict(int),
            CACHE_HIT_TOK_BY_MODEL: defaultdict(int),
            CACHE_HIT_TOK_BY_MODULE: defaultdict(int),
            CACHE_HIT_TOK_BY_TASK: defaultdict(int),
            CACHE_MISS_TOK_BY_TYPE: defaultdict(int),
            CACHE_MISS_TOK_BY_USER: defaultdict(int),
            CACHE_MISS_TOK_BY_MODEL: defaultdict(int),
            CACHE_MISS_TOK_BY_MODULE: defaultdict(int),
            CACHE_MISS_TOK_BY_TASK: defaultdict(int),
            TIME_COST_BY_TYPE: time_costs_by_type,
            TIME_COST_BY_USER: time_costs_by_user,
            TIME_COST_BY_MODEL: time_costs_by_model,
            TIME_COST_BY_MODULE: time_costs_by_module,
            AVG_TIME_COST_BY_TYPE: avg_time_costs_by_type,
            AVG_TIME_COST_BY_USER: avg_time_costs_by_user,
            AVG_TIME_COST_BY_MODEL: avg_time_costs_by_model,
            AVG_TIME_COST_BY_MODULE: avg_time_costs_by_module,
            AVG_TIME_COST_BY_TASK: avg_time_costs_by_task,
            STD_TIME_COST_BY_TYPE: std_time_costs_by_type,
            STD_TIME_COST_BY_USER: std_time_costs_by_user,
            STD_TIME_COST_BY_MODEL: std_time_costs_by_model,
            STD_TIME_COST_BY_MODULE: std_time_costs_by_module,
            STD_TIME_COST_BY_TASK: std_time_costs_by_task,
            ONLINE_TIME: 0.0,
            TOTAL_MSG_CNT: 0,
            MSG_CNT_BY_CHAT: defaultdict(int),
            TOTAL_REPLY_CNT: 0,
        }

    @staticmethod
    def _add_int_stat(stats_period: StatPeriodData, key: str, amount: int) -> None:
        stats_period[key] = cast(int, stats_period.get(key, 0)) + amount

    @staticmethod
    def _add_defaultdict_int(stats_period: StatPeriodData, key: str, subkey: str, amount: int) -> None:
        counter = cast(defaultdict[str, int], stats_period[key])
        counter[subkey] += amount

    @staticmethod
    def _collect_model_request_for_period(
        collect_period: List[Tuple[str, datetime]],
        usage_hourly: "UsageHourlyState | None" = None,
        now: datetime | None = None,
    ) -> StatPeriodMapping:
        """
        收集指定时间段的LLM请求统计数据

        小时汇总就绪时（usage_hourly.ready），整点区间读汇总，时间段两端不满一小时的部分、
        以及汇总刷新之后才写入的记录读明细；否则全部读明细。
        两种来源先归一化成同一种（维度, 指标）再累加，所以结果一致。

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        # 排序-按照时间段开始时间降序排列（最晚的时间段在前）
        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats: StatPeriodMapping = {
            period_key: StatisticOutputTask._build_stat_period_data() for period_key, _ in collect_period
        }
        # 每个维度只保存可累加的耗时矩（有效条数、和、平方和），汇总行和明细行都能直接相加
        duration_moments: dict[str, dict[str, defaultdict[str, list[float]]]] = {
            period_key: {category: defaultdict(lambda: [0.0, 0.0, 0.0]) for category in _DURATION_CATEGORIES}
            for period_key, _ in collect_period
        }
        # 各维度只描述字段映射；同一组维度的解析、缓存归一化只做一次。
        dimensions = (
            ("type", REQ_CNT_BY_TYPE, IN_TOK_BY_TYPE, OUT_TOK_BY_TYPE, TOTAL_TOK_BY_TYPE,
             CACHE_HIT_TOK_BY_TYPE, CACHE_MISS_TOK_BY_TYPE, COST_BY_TYPE),
            ("user", REQ_CNT_BY_USER, IN_TOK_BY_USER, OUT_TOK_BY_USER, TOTAL_TOK_BY_USER,
             CACHE_HIT_TOK_BY_USER, CACHE_MISS_TOK_BY_USER, COST_BY_USER),
            ("model", REQ_CNT_BY_MODEL, IN_TOK_BY_MODEL, OUT_TOK_BY_MODEL, TOTAL_TOK_BY_MODEL,
             CACHE_HIT_TOK_BY_MODEL, CACHE_MISS_TOK_BY_MODEL, COST_BY_MODEL),
            ("module", REQ_CNT_BY_MODULE, IN_TOK_BY_MODULE, OUT_TOK_BY_MODULE, TOTAL_TOK_BY_MODULE,
             CACHE_HIT_TOK_BY_MODULE, CACHE_MISS_TOK_BY_MODULE, COST_BY_MODULE),
            ("task", REQ_CNT_BY_TASK, IN_TOK_BY_TASK, OUT_TOK_BY_TASK, TOTAL_TOK_BY_TASK,
             CACHE_HIT_TOK_BY_TASK, CACHE_MISS_TOK_BY_TASK, COST_BY_TASK),
        )
        classified: Dict[str, Dict[Tuple[str, str, str], Dict[str, float]]] = {
            period_key: {} for period_key, _ in collect_period
        }

        def add(period_keys: List[str], key: "UsageKey", metrics: "UsageMetrics") -> None:
            """把一组（维度, 指标）累加进给定的时间段；明细是一条请求，汇总是同一小时里的多条。"""
            if not period_keys:
                return
            (request_count, prompt_tokens, completion_tokens, hit_tokens, miss_tokens, cost,
             duration_count, duration_sum, duration_sum_sq, *_) = metrics
            total_tokens = prompt_tokens + completion_tokens
            request_type = key.request_type
            # 提取模块名：如果请求类型包含"."，取第一个"."之前的部分。
            module_name = request_type.split(".")[0] if "." in request_type else request_type
            names = (request_type, key.provider_name, key.model_name, module_name, key.task_name)
            chat_cost_key = key.session_id if key.session_id else GLOBAL_COST_SESSION_KEY
            # 在原始请求层面区分嵌入，避免同名模型或模块的两类调用混在一起。
            request_kind = "embedding" if key.task_name in EMBEDDING_TASK_NAMES else "generation"
            classification_keys = (
                ("model", request_kind, key.model_name), ("module", request_kind, module_name),
                ("task", request_kind, key.task_name), ("request_type", request_kind, request_type),
            )
            # 保存可累加的耗时矩，保证增量缓存合并后仍能正确计算均值和标准差。
            metric_items = (
                ("request_count", request_count), ("input_tokens", prompt_tokens),
                ("output_tokens", completion_tokens), ("cache_hit_tokens", hit_tokens),
                ("cache_miss_tokens", miss_tokens), ("total_cost", cost), ("duration_count", duration_count),
                ("duration_sum", duration_sum), ("duration_sum_sq", duration_sum_sq),
            )
            for period_key in period_keys:
                period = stats[period_key]
                period[TOTAL_REQ_CNT] += request_count
                period[TOTAL_COST] += cost
                period[CACHE_HIT_TOK] += hit_tokens
                period[CACHE_MISS_TOK] += miss_tokens
                period[TOTAL_TOK_BY_CHAT][chat_cost_key] += total_tokens
                period[COST_BY_CHAT][chat_cost_key] += cost
                period_moments = duration_moments[period_key]
                for name, (category, requests, inputs, outputs, tokens, hits, misses, costs) in zip(
                    names, dimensions, strict=True,
                ):
                    period[requests][name] += request_count
                    period[inputs][name] += prompt_tokens
                    period[outputs][name] += completion_tokens
                    period[tokens][name] += total_tokens
                    period[hits][name] += hit_tokens
                    period[misses][name] += miss_tokens
                    period[costs][name] += cost
                    if duration_count:
                        moments = period_moments[category][name]
                        moments[0] += duration_count
                        moments[1] += duration_sum
                        moments[2] += duration_sum_sq
                period_classified = classified[period_key]
                for classification_key in classification_keys:
                    totals = period_classified.setdefault(classification_key, {})
                    for metric, value in metric_items:
                        totals[metric] = totals.get(metric, 0.0) + value

        if usage_hourly is not None and usage_hourly.ready and now is not None:
            StatisticOutputTask._collect_usage_with_hourly(collect_period, usage_hourly.cursor, now, add)
        else:
            # 以最早周期的起点查询模型记录，timestamp 使用 datetime 进行比较。
            for record in fetch_model_usage_since(collect_period[-1][1]):
                record_timestamp = cast(datetime, record["timestamp"])
                key, metrics = normalize_usage_record(record)
                add(
                    [period_key for period_key, period_start in collect_period if record_timestamp >= period_start],
                    key,
                    metrics,
                )

        # 输出时才序列化分类键，沿用现有缓存格式，避免每条请求、每个周期重复编码。
        for period_key, totals in classified.items():
            stats[period_key]["classified_requests"] = {
                json.dumps(key, ensure_ascii=False): metrics for key, metrics in totals.items()
            }

        # 计算平均耗时和标准差
        for period_key in stats:
            for category, request_count_key, avg_key, std_key in [
                ("type", REQ_CNT_BY_TYPE, AVG_TIME_COST_BY_TYPE, STD_TIME_COST_BY_TYPE),
                ("user", REQ_CNT_BY_USER, AVG_TIME_COST_BY_USER, STD_TIME_COST_BY_USER),
                ("model", REQ_CNT_BY_MODEL, AVG_TIME_COST_BY_MODEL, STD_TIME_COST_BY_MODEL),
                ("module", REQ_CNT_BY_MODULE, AVG_TIME_COST_BY_MODULE, STD_TIME_COST_BY_MODULE),
                ("task", REQ_CNT_BY_TASK, AVG_TIME_COST_BY_TASK, STD_TIME_COST_BY_TASK),
            ]:
                category_data = cast(dict[str, int], stats[period_key].get(request_count_key, {}))
                avg_cost_data = cast(dict[str, float], stats[period_key].get(avg_key, {}))
                std_cost_data = cast(dict[str, float], stats[period_key].get(std_key, {}))

                for item_name in category_data:
                    moments = duration_moments[period_key][category].get(item_name)
                    if moments and moments[0] > 0:
                        count, total, total_sq = moments
                        avg_time_cost = total / count
                        avg_cost_data[item_name] = round(avg_time_cost, 3)
                        variance = max(total_sq / count - avg_time_cost * avg_time_cost, 0.0)
                        std_cost_data[item_name] = round(variance**0.5, 3)
                    else:
                        avg_cost_data[item_name] = 0.0
                        std_cost_data[item_name] = 0.0

                stats[period_key][avg_key] = avg_cost_data
                stats[period_key][std_key] = std_cost_data

        return stats

    @staticmethod
    def _collect_usage_with_hourly(
        collect_period: List[Tuple[str, datetime]],
        cursor: int,
        now: datetime,
        add: "Callable[[List[str], UsageKey, UsageMetrics], None]",
    ) -> None:
        """整点区间读小时汇总，其余读明细；各部分在时间或 id 上互不重叠，不会重复计数。

        - 汇总：每个时间段的 [ceil(起点), floor(now))，只含 id <= cursor 的记录
        - 明细（头）：[起点, ceil(起点))；时间段不足一个整点区间时为 [起点, 现在之后)
        - 明细（尾）：[floor(now), 现在之后)，用到汇总的时间段共用一次读取
        - 明细（新）：id > cursor 且时间落在整点区间里的记录，即汇总刷新之后才写入的
        """
        hour_ranges = {period_key: clamp_hour_range(period_start, now) for period_key, period_start in collect_period}
        hourly_ranges = [hour_range for hour_range in hour_ranges.values() if hour_range]

        def periods_covering(timestamp: datetime) -> List[str]:
            return [
                period_key for period_key, hour_range in hour_ranges.items()
                if hour_range and hour_range[0] <= timestamp < hour_range[1]
            ]

        if hourly_ranges:
            earliest = min(hour_range[0] for hour_range in hourly_ranges)
            latest = max(hour_range[1] for hour_range in hourly_ranges)
            for bucket, key, metrics in fetch_usage_hourly(earliest, latest):
                add(periods_covering(bucket), key, metrics)

            tail_periods = [period_key for period_key, hour_range in hour_ranges.items() if hour_range]
            for record in fetch_usage_records_between(usage_bucket_start(now), None):
                add(tail_periods, *normalize_usage_record(record))

            for record in fetch_usage_records_after_id(cursor):
                timestamp = record["timestamp"]
                if isinstance(timestamp, datetime):
                    add(periods_covering(timestamp), *normalize_usage_record(record))

        for period_key, period_start in collect_period:
            hour_range = hour_ranges[period_key]
            if hour_range is None:
                records = fetch_usage_records_between(period_start, None)
            elif period_start < hour_range[0]:
                records = fetch_usage_records_between(period_start, hour_range[0])
            else:
                continue
            for record in records:
                add([period_key], *normalize_usage_record(record))

    @staticmethod
    def _collect_online_time_for_period(
        collect_period: list[tuple[str, datetime]],
        now: datetime,
    ) -> dict[str, dict[str, float]]:
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
        records = fetch_online_time_since(query_start_time)
        for record_start_timestamp, record_end_timestamp in records:
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

    def _collect_message_count_for_period(
        self,
        collect_period: list[tuple[str, datetime]],
    ) -> dict[str, dict[str, object]]:
        """
        收集指定时间段的消息统计数据

        :param collect_period: 统计时间段
        """
        if not collect_period:
            return {}

        collect_period.sort(key=lambda x: x[1], reverse=True)

        stats: dict[str, dict[str, object]] = {
            period_key: {
                TOTAL_MSG_CNT: 0,
                MSG_CNT_BY_CHAT: defaultdict(int),
                TOTAL_REPLY_CNT: 0,
            }
            for period_key, _ in collect_period
        }

        for period_key, period_start_dt in collect_period:
            for message_row in fetch_message_count_by_chat_since(period_start_dt):
                chat_id = cast(str, message_row["chat_id"])
                chat_name = cast(str, message_row["chat_name"])
                message_count = cast(int, message_row["message_count"])
                latest_timestamp = cast(datetime, message_row["latest_timestamp"])
                latest_time_ts = latest_timestamp.timestamp()

                try:
                    if chat_id in self.name_mapping:
                        if chat_name != self.name_mapping[chat_id][0] and latest_time_ts > self.name_mapping[chat_id][1]:
                            self.name_mapping[chat_id] = (chat_name, latest_time_ts)
                    else:
                        self.name_mapping[chat_id] = (chat_name, latest_time_ts)
                except (IndexError, TypeError) as e:
                    logger.warning(f"更新 name_mapping 时发生错误，chat_id: {chat_id}, 错误: {e}")
                    self.name_mapping[chat_id] = (chat_name, latest_time_ts)

                StatisticOutputTask._add_int_stat(stats[period_key], TOTAL_MSG_CNT, message_count)
                StatisticOutputTask._add_defaultdict_int(stats[period_key], MSG_CNT_BY_CHAT, chat_id, message_count)

        # 使用 ToolRecord 中的 reply 工具次数作为回复数基准
        try:
            for period_key, period_start_dt in collect_period:
                reply_count = count_tool_records_since(period_start_dt, "reply")
                StatisticOutputTask._add_int_stat(stats[period_key], TOTAL_REPLY_CNT, reply_count)
        except Exception as e:
            logger.warning(f"统计 reply 工具次数失败，将回复数视为 0，错误信息：{e}")

        return stats

    def _collect_all_statistics(self, now: datetime) -> StatPeriodMapping:
        """
        收集各时间段的统计数据
        :param now: 基准当前时间
        """

        last_all_time_stat: dict[str, object] | None = None

        try:
            cached_full_statistics = full_statistics_cache.load()
            cached_stat_data = (
                cached_full_statistics.get("stat_data") if isinstance(cached_full_statistics, dict) else None
            )
            # 新增统计维度后旧缓存缺少历史数据，首次刷新重新收集完整历史。
            if (
                isinstance(cached_stat_data, dict)
                and REQ_CNT_BY_TASK in cached_stat_data
                and TOTAL_TOK_BY_CHAT in cached_stat_data
                and "classified_requests" in cached_stat_data
            ):
                # 如果存在上次完整统计数据，则使用该数据进行增量统计
                last_stat = cast(dict[str, object], cached_full_statistics)

                # 修复 name_mapping 数据类型不匹配问题
                # JSON 中存储为列表，但代码期望为元组
                raw_name_mapping = cast(dict[str, object], last_stat["name_mapping"])
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
                last_all_time_stat = cast(dict[str, object], last_stat["stat_data"])  # 上次完整统计的统计数据
                last_stat_timestamp = datetime.fromtimestamp(self._to_float_timestamp(last_stat["timestamp"]))
                self.stat_period = [
                    item for item in self.stat_period if item[0] != "all_time"
                ]  # 删除"所有时间"的统计时段
                self.stat_period.append(("all_time", now - last_stat_timestamp, "自部署以来的"))
        except Exception as e:
            logger.warning(f"加载上次完整统计数据失败，进行全量统计，错误信息：{e}")

        stat_start_timestamp = [(period[0], now - period[1]) for period in self.stat_period]

        stat = {item[0]: {} for item in self.stat_period}

        model_req_stat = self._collect_model_request_for_period(
            stat_start_timestamp, getattr(self, "_usage_hourly", None), now
        )
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

        self._refresh_all_time_duration_stats(stat["all_time"])

        # 更新上次完整统计数据的时间戳
        # 将所有defaultdict转换为普通dict以避免类型冲突
        clean_stat_data = self._convert_defaultdict_to_dict(stat["all_time"])
        self._drop_cached_time_cost_lists(clean_stat_data)

        # 将 name_mapping 中的元组转换为列表，因为JSON不支持元组
        json_safe_name_mapping = {}
        for chat_id, (chat_name, timestamp) in self.name_mapping.items():
            json_safe_name_mapping[chat_id] = [chat_name, timestamp]

        full_statistics_cache.save(
            {
                "name_mapping": json_safe_name_mapping,
                "stat_data": clean_stat_data,
                "timestamp": now.timestamp(),
            }
        )

        return cast(StatPeriodMapping, stat)

    def _all_time_duration_records(self) -> list[dict[str, object]]:
        """全量耗时矩：小时汇总就绪时读汇总表（整点之后）加起点所在半小时的明细和尚未汇总的新记录。"""
        usage_hourly = getattr(self, "_usage_hourly", None)
        start = self.all_time_start_time
        if usage_hourly is None or not usage_hourly.ready:
            return fetch_model_duration_aggregates_since(start)

        first_bucket = ceil_hour(start)
        moments: defaultdict[tuple[str, str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        for row in fetch_usage_duration_moments(first_bucket):
            key = (str(row["request_type"]), str(row["provider_name"]), str(row["model_name"]), str(row["task_name"]))
            totals = moments[key]
            totals[0] += cast(int, row["count"])
            totals[1] += cast(float, row["sum"])
            totals[2] += cast(float, row["sum_sq"])

        raw_records = list(fetch_usage_records_between(start, first_bucket)) if start < first_bucket else []
        raw_records += [
            record for record in fetch_usage_records_after_id(usage_hourly.cursor)
            if isinstance(record["timestamp"], datetime) and record["timestamp"] >= first_bucket
        ]
        for record in raw_records:
            key, metrics = normalize_usage_record(record)
            if metrics[6]:
                totals = moments[(key.request_type, key.provider_name, key.model_name, key.task_name)]
                totals[0] += metrics[6]
                totals[1] += metrics[7]
                totals[2] += metrics[8]

        return [
            {
                "request_type": request_type, "model_api_provider_name": provider_name,
                "model_assign_name": model_name, "model_name": model_name, "task_name": task_name,
                "count": int(count), "sum": total, "sum_sq": total_sq,
            }
            for (request_type, provider_name, model_name, task_name), (count, total, total_sq) in moments.items()
        ]

    def _refresh_all_time_duration_stats(self, stat_data: StatPeriodData) -> None:
        """全量耗时均值/标准差从数据库现算，不依赖 local_store 中的原始耗时列表。"""
        duration_stats: dict[str, defaultdict[str, dict[str, float]]] = {
            "type": defaultdict(lambda: {"count": 0.0, "sum": 0.0, "sum_sq": 0.0}),
            "user": defaultdict(lambda: {"count": 0.0, "sum": 0.0, "sum_sq": 0.0}),
            "model": defaultdict(lambda: {"count": 0.0, "sum": 0.0, "sum_sq": 0.0}),
            "module": defaultdict(lambda: {"count": 0.0, "sum": 0.0, "sum_sq": 0.0}),
            "task": defaultdict(lambda: {"count": 0.0, "sum": 0.0, "sum_sq": 0.0}),
        }

        records = self._all_time_duration_records()
        for record in records:
            request_type = cast(str | None, record["request_type"]) or "unknown"
            user_id = cast(str | None, record["model_api_provider_name"]) or "unknown"
            model_assign_name = cast(str | None, record["model_assign_name"])
            model_name = model_assign_name or cast(str | None, record["model_name"]) or "unknown"
            module_name = request_type.split(".")[0] if "." in request_type else request_type
            # 历史记录可能没有任务名，单独归为未记录，不根据请求前缀或当前配置推断。
            task_name = cast(str | None, record.get("task_name")) or "未记录"
            count = float(cast(int, record["count"]))
            time_cost_sum = cast(float, record["sum"])
            time_cost_sq_sum = cast(float, record["sum_sq"])

            for category, item_name in [
                ("type", request_type),
                ("user", user_id),
                ("model", model_name),
                ("module", module_name),
                ("task", task_name),
            ]:
                item_stats = duration_stats[category][item_name]
                item_stats["count"] += count
                item_stats["sum"] += time_cost_sum
                item_stats["sum_sq"] += time_cost_sq_sum

        for category, avg_key, std_key in [
            ("type", AVG_TIME_COST_BY_TYPE, STD_TIME_COST_BY_TYPE),
            ("user", AVG_TIME_COST_BY_USER, STD_TIME_COST_BY_USER),
            ("model", AVG_TIME_COST_BY_MODEL, STD_TIME_COST_BY_MODEL),
            ("module", AVG_TIME_COST_BY_MODULE, STD_TIME_COST_BY_MODULE),
            ("task", AVG_TIME_COST_BY_TASK, STD_TIME_COST_BY_TASK),
        ]:
            avg_data = cast(defaultdict[str, float], stat_data[avg_key])
            std_data = cast(defaultdict[str, float], stat_data[std_key])
            avg_data.clear()
            std_data.clear()

            for item_name, item_stats in duration_stats[category].items():
                count = item_stats["count"]
                if count <= 0:
                    continue
                avg_time_cost = item_stats["sum"] / count
                variance = max(item_stats["sum_sq"] / count - avg_time_cost * avg_time_cost, 0.0)
                avg_data[item_name] = round(avg_time_cost, 3)
                std_data[item_name] = round(variance**0.5, 3)

    @staticmethod
    def _drop_cached_time_cost_lists(stat_data: object) -> None:
        """不把原始耗时列表写入 local_store；需要时从数据库重新统计。"""
        if not isinstance(stat_data, dict):
            return

        for key in [TIME_COST_BY_TYPE, TIME_COST_BY_USER, TIME_COST_BY_MODEL, TIME_COST_BY_MODULE]:
            stat_data.pop(key, None)

    def _convert_defaultdict_to_dict(self, data: object) -> object:
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

    @staticmethod
    def _to_float_timestamp(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    # -- 以下为统计数据格式化方法 --

    @staticmethod
    def _format_total_stat(stats: StatPeriodData) -> str:
        """格式化控制台总览，只保留核心统计。"""
        # 计算总token数（从所有模型的token数中累加）
        total_tokens = sum(stats[TOTAL_TOK_BY_MODEL].values())
        total_input_tokens = sum(stats[IN_TOK_BY_MODEL].values())
        total_output_tokens = sum(stats[OUT_TOK_BY_MODEL].values())
        cache_hit_tokens = cast(int, stats.get(CACHE_HIT_TOK, 0))
        cache_miss_tokens = cast(int, stats.get(CACHE_MISS_TOK, 0))
        total_replies = stats.get(TOTAL_REPLY_CNT, 0)

        output = [
            f"在线 {_format_online_time(int(stats[ONLINE_TIME]))}  "
            f"消息 {_format_large_number(stats[TOTAL_MSG_CNT])}  "
            f"回复 {_format_large_number(total_replies)}  "
            f"请求 {_format_large_number(stats[TOTAL_REQ_CNT])}",
            f"Token {_format_large_number(total_tokens)} "
            f"(输入 {_format_large_number(total_input_tokens)} / 输出 {_format_large_number(total_output_tokens)})  "
            f"缓存命中 {_format_cache_hit_rate(cache_hit_tokens, cache_miss_tokens)}",
            f"花费 {stats[TOTAL_COST]:.2f}¥",
        ]
        return "\n".join(output)

    @staticmethod
    def _format_model_classified_stat(stats: StatPeriodData) -> str:
        """格式化按模型分类的控制台核心统计。"""
        if stats[TOTAL_REQ_CNT] <= 0:
            return ""
        data_fmt = "{:<32}  {:>6}  {:>8}  {:>6.2f}¥  {:>6.1f}s  {:>7}"
        output = [
            "模型                              调用     Token     花费      耗时     缓存命中",
        ]
        for model_name, count in sorted(stats[REQ_CNT_BY_MODEL].items()):
            name = f"{model_name[:29]}..." if len(model_name) > 32 else model_name
            cache_hit_rate = _format_cache_hit_rate(
                stats[CACHE_HIT_TOK_BY_MODEL][model_name],
                stats[CACHE_MISS_TOK_BY_MODEL][model_name],
            )
            # 格式化大数字，省略输入输出拆分及每次回复等派生指标。
            output.append(
                data_fmt.format(
                    name,
                    _format_large_number(count),
                    _format_large_number(stats[TOTAL_TOK_BY_MODEL][model_name]),
                    stats[COST_BY_MODEL][model_name],
                    stats[AVG_TIME_COST_BY_MODEL][model_name],
                    cache_hit_rate,
                )
            )
        return "\n".join(output)

    @staticmethod
    def _format_module_classified_stat(stats: StatPeriodData) -> str:
        """格式化按模块分类的控制台核心统计。"""
        if stats[TOTAL_REQ_CNT] <= 0:
            return ""
        data_fmt = "{:<32}  {:>6}  {:>8}  {:>6.2f}¥  {:>6.1f}s  {:>7}"
        output = [
            "模块                              调用     Token     花费      耗时     缓存命中",
        ]
        for module_name, count in sorted(stats[REQ_CNT_BY_MODULE].items()):
            name = f"{module_name[:29]}..." if len(module_name) > 32 else module_name
            cache_hit_rate = _format_cache_hit_rate(
                stats[CACHE_HIT_TOK_BY_MODULE][module_name],
                stats[CACHE_MISS_TOK_BY_MODULE][module_name],
            )
            # 格式化大数字，省略输入输出拆分及每次回复等派生指标。
            output.append(
                data_fmt.format(
                    name,
                    _format_large_number(count),
                    _format_large_number(stats[TOTAL_TOK_BY_MODULE][module_name]),
                    stats[COST_BY_MODULE][module_name],
                    stats[AVG_TIME_COST_BY_MODULE][module_name],
                    cache_hit_rate,
                )
            )
        return "\n".join(output)

    def _format_chat_stat(self, stats: StatPeriodData) -> str:
        """
        格式化聊天统计数据
        """
        if stats[TOTAL_MSG_CNT] <= 0:
            return ""
        output = ["聊天消息（前5）:"]
        chats = sorted(stats[MSG_CNT_BY_CHAT].items(), key=lambda item: item[1], reverse=True)
        for chat_id, count in chats[:5]:
            try:
                chat_name = self.name_mapping.get(chat_id, ("未知聊天", 0))[0]
                formatted_count = _format_large_number(count)
                output.append(f"{chat_name[:32]:<32}  {formatted_count:>10}")
            except (IndexError, TypeError) as e:
                logger.warning(f"格式化聊天统计时发生错误，chat_id: {chat_id}, 错误: {e}")
                formatted_count = _format_large_number(count)
                output.append(f"{'未知聊天':<32}  {formatted_count:>10}")
        if len(chats) > 5:
            other_count = sum(count for _, count in chats[5:])
            output.append(f"其余 {len(chats) - 5} 个会话合计: {_format_large_number(other_count)} 条")
        return "\n".join(output)

    def _get_chat_display_name_from_id(self, chat_id: str) -> str:
        """从chat_id获取显示名称"""
        try:
            # 首先尝试从chat_stream获取真实群组名称
            from src.chat.message_receive.chat_manager import chat_manager as _stat_chat_manager

            if chat_id in _stat_chat_manager.sessions:
                name = _stat_chat_manager.get_session_name(chat_id)
                if name and name.strip():
                    return name.strip()

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

    @staticmethod
    def _calculate_cache_hit_rate_value(hit_tokens: int, miss_tokens: int) -> float | None:
        total_cache_tokens = hit_tokens + miss_tokens
        if total_cache_tokens <= 0:
            return None
        return hit_tokens / total_cache_tokens

    @classmethod
    def _build_breakdown_rows(
        cls,
        stat_data: StatPeriodData,
        dimension: str,
        failure_lookup: Mapping[Tuple[str, str], Tuple[int, int]] | None = None,
    ) -> List["DetailedStatisticsBreakdown"]:
        """将模型、任务组、模块和请求类型的分类统计转换为前端表格行。

        failure_lookup 以 (请求类别, 名称) 为键提供 (尝试数, 失败数)；只有模型和任务组维度能与
        model_task_usage 对应，其余维度不传，对应字段保持 None。
        """

        from src.webui.schemas.statistics import DetailedStatisticsBreakdown

        if dimension not in {"model", "module", "task", "request_type"}:
            raise ValueError(f"不支持的详细统计维度: {dimension}")

        total_replies = int(stat_data[TOTAL_REPLY_CNT])
        rows: List[DetailedStatisticsBreakdown] = []
        for key, totals in stat_data["classified_requests"].items():
            row_dimension, request_kind, name = json.loads(key)
            if row_dimension != dimension:
                continue
            request_count = int(totals["request_count"])
            input_tokens = int(totals["input_tokens"])
            output_tokens = int(totals["output_tokens"])
            total_tokens = input_tokens + output_tokens
            cache_hit_tokens = int(totals["cache_hit_tokens"])
            cache_miss_tokens = int(totals["cache_miss_tokens"])
            duration_count = totals["duration_count"]
            avg_time = totals["duration_sum"] / duration_count if duration_count > 0 else 0.0
            attempt_count = failure_count = None
            if failure_lookup is not None and (request_kind, name) in failure_lookup:
                attempt_count, failure_count = failure_lookup[(request_kind, name)]
            variance = max(totals["duration_sum_sq"] / duration_count - avg_time**2, 0.0) if duration_count > 0 else 0.0
            rows.append(
                DetailedStatisticsBreakdown(
                    name=name,
                    is_embedding=request_kind == "embedding",
                    request_count=request_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cache_hit_tokens=cache_hit_tokens,
                    cache_miss_tokens=cache_miss_tokens,
                    cache_hit_rate=cls._calculate_cache_hit_rate_value(cache_hit_tokens, cache_miss_tokens),
                    total_cost=totals["total_cost"],
                    avg_time_cost=round(avg_time, 3),
                    std_time_cost=round(variance**0.5, 3),
                    avg_calls_per_reply=request_count / total_replies if total_replies > 0 else None,
                    avg_tokens_per_reply=total_tokens / total_replies if total_replies > 0 else None,
                    avg_tokens_per_call=total_tokens / request_count if request_count > 0 else None,
                    attempt_count=attempt_count,
                    failure_count=failure_count,
                )
            )
        return sorted(rows, key=lambda row: (row.is_embedding, row.name))

    @staticmethod
    def _build_distribution_items(values: dict[str, float | int]) -> list["DetailedDistributionItem"]:
        from src.webui.schemas.statistics import DetailedDistributionItem

        return [
            DetailedDistributionItem(name=name, value=float(value))
            for name, value in sorted(values.items())
        ]

    def _build_period_distributions(
        self,
        stat_data: StatPeriodData,
    ) -> "DetailedStatisticsDistributions":
        from src.webui.schemas.statistics import DetailedStatisticsDistributions

        chat_messages: defaultdict[str, int] = defaultdict(int)
        for chat_id, count in stat_data[MSG_CNT_BY_CHAT].items():
            chat_name = str(self.name_mapping.get(chat_id, ("未知聊天", 0))[0])
            chat_messages[chat_name] += count

        chat_costs: defaultdict[str, float] = defaultdict(float)
        for chat_id, cost in stat_data[COST_BY_CHAT].items():
            chat_name = (
                "全局"
                if chat_id == GLOBAL_COST_SESSION_KEY
                else str(self.name_mapping.get(chat_id, (self._get_chat_display_name_from_id(chat_id), 0))[0])
            )
            chat_costs[chat_name] += cost
        owner_costs = _build_llm_owner_distribution(stat_data[COST_BY_TYPE])
        chat_tokens: DefaultDict[str, int] = defaultdict(int)
        for chat_id, tokens in stat_data[TOTAL_TOK_BY_CHAT].items():
            chat_name = (
                "全局"
                if chat_id == GLOBAL_COST_SESSION_KEY
                else str(self.name_mapping.get(chat_id, (self._get_chat_display_name_from_id(chat_id), 0))[0])
            )
            chat_tokens[chat_name] += tokens

        return DetailedStatisticsDistributions(
            owner_tokens=self._build_distribution_items(_build_llm_owner_distribution(stat_data[TOTAL_TOK_BY_TYPE])),
            model_tokens=self._build_distribution_items(dict(stat_data[TOTAL_TOK_BY_MODEL])),
            module_tokens=self._build_distribution_items(dict(stat_data[TOTAL_TOK_BY_MODULE])),
            request_type_tokens=self._build_distribution_items(dict(stat_data[TOTAL_TOK_BY_TYPE])),
            chat_tokens=self._build_distribution_items(dict(chat_tokens)),
            owner_costs=self._build_distribution_items(dict(owner_costs)),
            model_costs=self._build_distribution_items(dict(stat_data[COST_BY_MODEL])),
            module_costs=self._build_distribution_items(dict(stat_data[COST_BY_MODULE])),
            request_type_costs=self._build_distribution_items(dict(stat_data[COST_BY_TYPE])),
            chat_messages=self._build_distribution_items(chat_messages),
            chat_costs=self._build_distribution_items(chat_costs),
        )

    @classmethod
    def _build_detailed_summary(
        cls,
        *,
        total_cost: float,
        total_requests: int,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int,
        cache_miss_tokens: int,
        online_time: float,
        total_messages: int,
        total_replies: int,
    ) -> "DetailedStatisticsSummary":
        """当前时段与上一周期共用同一套汇总与派生指标口径。"""

        from src.webui.schemas.statistics import DetailedStatisticsSummary

        total_tokens = input_tokens + output_tokens
        received_messages = total_messages - total_replies
        online_hours = online_time / 3600.0
        return DetailedStatisticsSummary(
            online_time=online_time,
            total_messages=total_messages,
            total_replies=total_replies,
            total_requests=total_requests,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            cache_hit_rate=cls._calculate_cache_hit_rate_value(cache_hit_tokens, cache_miss_tokens),
            total_cost=total_cost,
            cost_per_100_messages=total_cost / total_messages * 100 if total_messages > 0 else 0.0,
            cost_per_100_messages_excluding_replies=(
                total_cost / received_messages * 100 if received_messages > 0 else 0.0
            ),
            cost_per_100_replies=total_cost / total_replies * 100 if total_replies > 0 else 0.0,
            cost_per_hour=total_cost / online_hours if online_hours > 0 else 0.0,
            tokens_per_hour=total_tokens / online_hours if online_hours > 0 else 0.0,
        )

    # 上一周期环比在类方法里计算，由 run() 在每轮开头写入本轮的小时汇总状态
    _usage_hourly_for_comparison: "UsageHourlyState | None" = None

    @classmethod
    def _build_previous_summary(cls, start_time: datetime, end_time: datetime) -> "DetailedStatisticsSummary":
        """按 [start_time, end_time) 直接汇总上一周期，口径与时段统计一致。"""

        totals = fetch_window_usage_totals(start_time, end_time, usage_hourly=cls._usage_hourly_for_comparison)
        return cls._build_detailed_summary(
            total_cost=totals["total_cost"],
            total_requests=totals["total_requests"],
            input_tokens=totals["input_tokens"],
            output_tokens=totals["output_tokens"],
            cache_hit_tokens=totals["cache_hit_tokens"],
            cache_miss_tokens=totals["cache_miss_tokens"],
            online_time=totals["online_time"],
            total_messages=count_chat_messages_between(start_time, end_time),
            total_replies=count_tool_records_between(start_time, end_time, "reply"),
        )

    @staticmethod
    def _build_reliability(
        period_start: datetime,
        now: datetime,
    ) -> Tuple["DetailedReliabilityData", Dict[Tuple[str, str], Tuple[int, int]], Dict[Tuple[str, str], Tuple[int, int]]]:
        """汇总调用可靠性，并返回模型、任务组表格行使用的失败次数查找表。"""

        from src.webui.schemas.statistics import DetailedReliabilityData, DetailedReliabilityItem

        retention_start = now - get_model_task_usage_retention()
        coverage_start = max(period_start, retention_start)
        rows = fetch_model_task_usage_groups(coverage_start, now)

        # 每个维度累计 [尝试数, 失败数, 成功耗时总和]。
        grouped: Dict[str, DefaultDict[str, List[float]]] = {
            dimension: defaultdict(lambda: [0, 0, 0.0]) for dimension in ("model", "provider", "task")
        }
        model_lookup: DefaultDict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
        task_lookup: DefaultDict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
        for row in rows:
            attempts = row["attempt_count"]
            failures = row["failure_count"]
            for dimension, name in (
                ("model", row["model_name"]), ("provider", row["provider_name"]), ("task", row["task_name"]),
            ):
                totals = grouped[dimension][name]
                totals[0] += attempts
                totals[1] += failures
                totals[2] += row["success_duration"]
            # 与 llm_usage 的分类口径一致：嵌入任务的调用单独成行。
            request_kind = "embedding" if row["task_name"] in EMBEDDING_TASK_NAMES else "generation"
            for lookup, name in ((model_lookup, row["model_name"]), (task_lookup, row["task_name"])):
                lookup[(request_kind, name)][0] += attempts
                lookup[(request_kind, name)][1] += failures

        def build_items(dimension: str) -> List[DetailedReliabilityItem]:
            items = []
            for name, (attempts, failures, duration) in grouped[dimension].items():
                successes = attempts - failures
                items.append(
                    DetailedReliabilityItem(
                        name=name,
                        attempt_count=int(attempts),
                        failure_count=int(failures),
                        failure_rate=failures / attempts if attempts > 0 else None,
                        avg_success_duration=duration / successes if successes > 0 else None,
                    )
                )
            return sorted(items, key=lambda item: (-(item.failure_rate or 0.0), -item.attempt_count, item.name))

        total_attempts = sum(row["attempt_count"] for row in rows)
        total_failures = sum(row["failure_count"] for row in rows)
        total_successes = total_attempts - total_failures
        total_duration = sum(row["success_duration"] for row in rows)
        reliability = DetailedReliabilityData(
            coverage_start=coverage_start.isoformat(),
            truncated=period_start < retention_start,
            attempt_count=total_attempts,
            failure_count=total_failures,
            failure_rate=total_failures / total_attempts if total_attempts > 0 else None,
            avg_success_duration=total_duration / total_successes if total_successes > 0 else None,
            by_model=build_items("model"),
            by_provider=build_items("provider"),
            by_task=build_items("task"),
        )
        return (
            reliability,
            {key: (value[0], value[1]) for key, value in model_lookup.items()},
            {key: (value[0], value[1]) for key, value in task_lookup.items()},
        )

    def _period_start_time(self, period_key: str, duration: timedelta, now: datetime) -> datetime:
        return self.all_time_start_time if period_key == "all_time" else now - duration

    def _build_period_trend_collector(self, now: datetime) -> PeriodTrendCollector:
        """为每个统计时段建立对齐的趋势时间桶；超过 30 天的部分截断并显式标记。"""

        earliest_start = now - PERIOD_TREND_MAX_RANGE
        windows: Dict[str, PeriodTrendWindow] = {}
        for period_key, duration, _ in self.stat_period:
            period_start = self._period_start_time(period_key, duration, now)
            windows[period_key] = PeriodTrendWindow(
                start=max(period_start, earliest_start),
                end=now,
                bucket_minutes=PERIOD_TREND_BUCKET_MINUTES[period_key],
                truncated=period_start < earliest_start,
            )
        return PeriodTrendCollector(windows)

    def _build_detailed_statistics_snapshot(
        self,
        stat: StatPeriodMapping,
        now: datetime,
        chart_data: dict[str, dict[str, object]],
        metrics_data: dict[str, object],
        period_trends: dict[str, dict[str, object]],
    ) -> "DetailedStatisticsData":
        """构造与当前 HTML 报告完全同源的 WebUI 详细统计快照。"""

        from src.webui.schemas.statistics import (
            DetailedChatStatistics,
            DetailedPeriodTrend,
            DetailedStatisticsData,
            DetailedStatisticsMetricsData,
            DetailedStatisticsPeriod,
            DetailedStatisticsTrendData,
        )

        periods = []
        for period_key, duration, _ in self.stat_period:
            stat_data = stat[period_key]
            period_start_time = self._period_start_time(period_key, duration, now)
            reliability, model_failures, task_failures = self._build_reliability(period_start_time, now)
            # 自部署以来没有等长的上一周期，不计算环比。
            previous_summary = (
                None
                if period_key == "all_time"
                else self._build_previous_summary(period_start_time - duration, period_start_time)
            )

            periods.append(
                DetailedStatisticsPeriod(
                    key=period_key,
                    start_time=period_start_time.isoformat(),
                    end_time=now.isoformat(),
                    summary=self._build_detailed_summary(
                        total_cost=stat_data[TOTAL_COST],
                        total_requests=stat_data[TOTAL_REQ_CNT],
                        input_tokens=sum(stat_data[IN_TOK_BY_MODEL].values()),
                        output_tokens=sum(stat_data[OUT_TOK_BY_MODEL].values()),
                        cache_hit_tokens=int(stat_data.get(CACHE_HIT_TOK, 0)),
                        cache_miss_tokens=int(stat_data.get(CACHE_MISS_TOK, 0)),
                        online_time=stat_data[ONLINE_TIME],
                        total_messages=int(stat_data[TOTAL_MSG_CNT]),
                        total_replies=int(stat_data.get(TOTAL_REPLY_CNT, 0)),
                    ),
                    previous_summary=previous_summary,
                    models=self._build_breakdown_rows(stat_data, "model", model_failures),
                    modules=self._build_breakdown_rows(stat_data, "module"),
                    task_groups=self._build_breakdown_rows(stat_data, "task", task_failures),
                    request_types=self._build_breakdown_rows(stat_data, "request_type"),
                    chats=[
                        DetailedChatStatistics(
                            name=str(self.name_mapping.get(chat_id, ("未知聊天", 0))[0]),
                            message_count=count,
                        )
                        for chat_id, count in sorted(stat_data[MSG_CNT_BY_CHAT].items())
                    ],
                    distributions=self._build_period_distributions(stat_data),
                    reliability=reliability,
                    trend=DetailedPeriodTrend.model_validate(period_trends[period_key]),
                )
            )

        return DetailedStatisticsData(
            generated_at=now.isoformat(),
            periods=periods,
            trends={
                range_key: DetailedStatisticsTrendData.model_validate(range_data)
                for range_key, range_data in chart_data.items()
            },
            metrics={
                range_key: DetailedStatisticsMetricsData.model_validate(range_data)
                for range_key, range_data in metrics_data.items()
            },
        )

    def _generate_html_report(self, stat: StatPeriodMapping, now: datetime):
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

        def _format_stat_data(stat_data: StatPeriodData, div_id: str, start_time: datetime) -> str:
            """
            格式化一个时间段的统计数据到html div块
            :param stat_data: 统计数据
            :param div_id: div的ID
            :param start_time: 统计时间段开始时间
            """
            # format总在线时间

            # 按模型分类统计
            total_replies = stat_data.get(TOTAL_REPLY_CNT, 0)
            total_input_tokens = sum(stat_data[IN_TOK_BY_MODEL].values()) if stat_data[IN_TOK_BY_MODEL] else 0
            total_output_tokens = sum(stat_data[OUT_TOK_BY_MODEL].values()) if stat_data[OUT_TOK_BY_MODEL] else 0
            total_cache_hit_tokens = cast(int, stat_data.get(CACHE_HIT_TOK, 0))
            total_cache_miss_tokens = cast(int, stat_data.get(CACHE_MISS_TOK, 0))
            model_rows = "\n".join(
                [
                    f"<tr>"
                    f"<td>{model_name}</td>"
                    f"<td>{_format_large_number(count, html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[IN_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[OUT_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[TOTAL_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[CACHE_HIT_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[CACHE_MISS_TOK_BY_MODEL][model_name], html=True)}</td>"
                    f"<td>{_format_cache_hit_rate(stat_data[CACHE_HIT_TOK_BY_MODEL][model_name], stat_data[CACHE_MISS_TOK_BY_MODEL][model_name])}</td>"
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
                else ["<tr><td colspan='14' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
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
                    f"<td>{_format_large_number(stat_data[CACHE_HIT_TOK_BY_TYPE][req_type], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[CACHE_MISS_TOK_BY_TYPE][req_type], html=True)}</td>"
                    f"<td>{_format_cache_hit_rate(stat_data[CACHE_HIT_TOK_BY_TYPE][req_type], stat_data[CACHE_MISS_TOK_BY_TYPE][req_type])}</td>"
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
                else ["<tr><td colspan='14' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
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
                    f"<td>{_format_large_number(stat_data[CACHE_HIT_TOK_BY_MODULE][module_name], html=True)}</td>"
                    f"<td>{_format_large_number(stat_data[CACHE_MISS_TOK_BY_MODULE][module_name], html=True)}</td>"
                    f"<td>{_format_cache_hit_rate(stat_data[CACHE_HIT_TOK_BY_MODULE][module_name], stat_data[CACHE_MISS_TOK_BY_MODULE][module_name])}</td>"
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
                else ["<tr><td colspan='14' style='text-align: center; color: #999;'>暂无数据</td></tr>"]
            )

            # 聊天消息统计
            chat_rows = []
            sorted_chat_ids = sorted(stat_data[MSG_CNT_BY_CHAT].keys())
            for chat_id, count in sorted(stat_data[MSG_CNT_BY_CHAT].items()):
                try:
                    chat_name = self.name_mapping.get(chat_id, ("未知聊天", 0))[0]
                    escaped_chat_name = escape(str(chat_name), quote=True)
                    chat_rows.append(
                        f"<tr><td>{escaped_chat_name}</td><td>{_format_large_number(count, html=True)}</td></tr>"
                    )
                except (IndexError, TypeError) as e:
                    logger.warning(f"生成HTML聊天统计时发生错误，chat_id: {chat_id}, 错误: {e}")
                    chat_rows.append(f"<tr><td>未知聊天</td><td>{_format_large_number(count, html=True)}</td></tr>")

            chat_rows_html = (
                "\n".join(chat_rows)
                if chat_rows
                else "<tr><td colspan='2' style='text-align: center; color: #999;'>暂无数据</td></tr>"
            )
            chat_labels = [str(self.name_mapping.get(chat_id, ("未知聊天", 0))[0]) for chat_id in sorted_chat_ids]
            chat_counts = [stat_data[MSG_CNT_BY_CHAT][chat_id] for chat_id in sorted_chat_ids]
            chat_labels_json = _json_for_html_script(chat_labels)
            chat_counts_json = _json_for_html_script(chat_counts)

            sorted_chat_cost_ids = sorted(stat_data[COST_BY_CHAT].keys())
            chat_cost_labels = [
                "全局"
                if chat_id == GLOBAL_COST_SESSION_KEY
                else str(self.name_mapping.get(chat_id, (self._get_chat_display_name_from_id(chat_id), 0))[0])
                for chat_id in sorted_chat_cost_ids
            ]
            chat_costs = [stat_data[COST_BY_CHAT][chat_id] for chat_id in sorted_chat_cost_ids]
            chat_cost_labels_json = _json_for_html_script(chat_cost_labels)
            chat_costs_json = _json_for_html_script(chat_costs)

            owner_costs_by_label = _build_llm_owner_distribution(stat_data[COST_BY_TYPE])
            owner_cost_labels = list(owner_costs_by_label.keys())
            owner_costs = [owner_costs_by_label[label] for label in owner_cost_labels]
            owner_cost_labels_json = _json_for_html_script(owner_cost_labels)
            owner_costs_json = _json_for_html_script(owner_costs)
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
                        <div class=\"kpi-value\">{_format_online_time(int(stat_data[ONLINE_TIME]))}</div>
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
                        <div class=\"kpi-title\">总输入Token</div>
                        <div class=\"kpi-value\">{_format_large_number(total_input_tokens, html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">总输出Token</div>
                        <div class=\"kpi-value\">{_format_large_number(total_output_tokens, html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">Prompt缓存命中率</div>
                        <div class=\"kpi-value\">{_format_cache_hit_rate(total_cache_hit_tokens, total_cache_miss_tokens)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">Prompt缓存命中Token</div>
                        <div class=\"kpi-value\">{_format_large_number(total_cache_hit_tokens, html=True)}</div>
                    </div>
                    <div class=\"kpi-card\">
                        <div class=\"kpi-title\">Prompt缓存未命中Token</div>
                        <div class=\"kpi-value\">{_format_large_number(total_cache_miss_tokens, html=True)}</div>
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
                        <thead><tr><th>模型名称</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>缓存命中Token</th><th>缓存未命中Token</th><th>缓存命中率</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr></thead>
                        <tbody>
                            {model_rows}
                        </tbody>
                    </table>
                </div>
                
                <h2>按模块分类统计</h2>
                <div class=\"table-wrap\">
                    <table>
                        <thead>
                            <tr><th>模块名称</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>缓存命中Token</th><th>缓存未命中Token</th><th>缓存命中率</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr>
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
                            <tr><th>请求类型</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>缓存命中Token</th><th>缓存未命中Token</th><th>缓存命中率</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th><th>每次回复平均调用次数</th><th>每次回复平均Token数</th><th>每次调用平均Token</th></tr>
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
                <div class="pie-chart-grid">
                    <div class="pie-chart-card">
                        <h3>调用来源花费分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="ownerPieChart_{div_id}"></canvas>
                        </div>
                        <div id="ownerPieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                    <div class="pie-chart-card">
                        <h3>模型花费分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="modelPieChart_{div_id}"></canvas>
                        </div>
                        <div id="modelPieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                    <div class="pie-chart-card">
                        <h3>模块花费分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="modulePieChart_{div_id}"></canvas>
                        </div>
                        <div id="modulePieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                    <div class="pie-chart-card">
                        <h3>请求类型花费分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="typePieChart_{div_id}"></canvas>
                        </div>
                        <div id="typePieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                    <div class="pie-chart-card">
                        <h3>聊天消息分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="chatPieChart_{div_id}"></canvas>
                        </div>
                        <div id="chatPieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                    <div class="pie-chart-card">
                        <h3>聊天流花费分布</h3>
                        <div class="pie-chart-canvas-wrap">
                            <canvas id="chatCostPieChart_{div_id}"></canvas>
                        </div>
                        <div id="chatCostPieLegend_{div_id}" class="pie-chart-legend"></div>
                    </div>
                </div>
                
                <script>
                    // 为当前统计卡片创建饼图
                    document.addEventListener('DOMContentLoaded', function() {{
                        createPieCharts_{div_id}();
                    }});
                    
                    function createPieCharts_{div_id}() {{
                        const colors = ['#b35b34', '#0d4b50', '#cfa54b', '#6f665b', '#dfc79a', '#8f4b38', '#74825a', '#854f46', '#2f5f62', '#b98556'];

                        function getPieColors(labelCount) {{
                            return Array.from({{ length: labelCount }}, (_, index) => colors[index % colors.length]);
                        }}

                        function renderPieLegend_{div_id}(chart, legendId) {{
                            const legendContainer = document.getElementById(legendId);
                            if (!legendContainer) return;

                            legendContainer.innerHTML = '';
                            const items = chart.options.plugins.legend.labels.generateLabels(chart);
                            items.forEach((item) => {{
                                const legendItem = document.createElement('button');
                                legendItem.type = 'button';
                                legendItem.className = 'pie-chart-legend-item' + (item.hidden ? ' is-hidden' : '');
                                legendItem.onclick = () => {{
                                    chart.toggleDataVisibility(item.index);
                                    chart.update();
                                    renderPieLegend_{div_id}(chart, legendId);
                                }};

                                const colorBox = document.createElement('span');
                                colorBox.className = 'pie-chart-legend-color';
                                colorBox.style.backgroundColor = item.fillStyle;

                                const labelText = document.createElement('span');
                                labelText.className = 'pie-chart-legend-label';
                                labelText.textContent = item.text;

                                legendItem.appendChild(colorBox);
                                legendItem.appendChild(labelText);
                                legendContainer.appendChild(legendItem);
                            }});
                        }}

                        function createPieChart_{div_id}(canvasId, legendId, chartData, labelFormatter) {{
                            const chart = new Chart(document.getElementById(canvasId), {{
                                type: 'pie',
                                data: chartData,
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{
                                        legend: {{
                                            display: false
                                        }},
                                        tooltip: {{
                                            callbacks: {{
                                                label: labelFormatter
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                            renderPieLegend_{div_id}(chart, legendId);
                            return chart;
                        }}

                        // 调用来源花费分布饼图
                        const ownerLabels = {owner_cost_labels_json};
                        if (ownerLabels.length > 0) {{
                            const ownerData = {{
                                labels: ownerLabels,
                                datasets: [{{
                                    data: {owner_costs_json},
                                    backgroundColor: getPieColors(ownerLabels.length),
                                    borderColor: getPieColors(ownerLabels.length),
                                    borderWidth: 2
                                }}]
                            }};

                            createPieChart_{div_id}('ownerPieChart_{div_id}', 'ownerPieLegend_{div_id}', ownerData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('ownerPieChart_{div_id}').style.display = 'none';
                            document.getElementById('ownerPieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#ownerPieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '调用来源花费分布 (无数据)';
                        }}

                        // 模型花费分布饼图
                        const modelLabels = {list(sorted(stat_data[COST_BY_MODEL].keys())) if stat_data[COST_BY_MODEL] else []};
                        if (modelLabels.length > 0) {{
                            const modelData = {{
                                labels: modelLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_MODEL][model_name] for model_name in sorted(stat_data[COST_BY_MODEL].keys())] if stat_data[COST_BY_MODEL] else []},
                                    backgroundColor: getPieColors(modelLabels.length),
                                    borderColor: getPieColors(modelLabels.length),
                                    borderWidth: 2
                                }}]
                            }};

                            createPieChart_{div_id}('modelPieChart_{div_id}', 'modelPieLegend_{div_id}', modelData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('modelPieChart_{div_id}').style.display = 'none';
                            document.getElementById('modelPieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#modelPieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '模型花费分布 (无数据)';
                        }}
                        
                        // 模块花费分布饼图
                        const moduleLabels = {list(sorted(stat_data[COST_BY_MODULE].keys())) if stat_data[COST_BY_MODULE] else []};
                        if (moduleLabels.length > 0) {{
                            const moduleData = {{
                                labels: moduleLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_MODULE][module_name] for module_name in sorted(stat_data[COST_BY_MODULE].keys())] if stat_data[COST_BY_MODULE] else []},
                                    backgroundColor: getPieColors(moduleLabels.length),
                                    borderColor: getPieColors(moduleLabels.length),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            createPieChart_{div_id}('modulePieChart_{div_id}', 'modulePieLegend_{div_id}', moduleData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('modulePieChart_{div_id}').style.display = 'none';
                            document.getElementById('modulePieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#modulePieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '模块花费分布 (无数据)';
                        }}
                        
                        // 请求类型花费分布饼图
                        const typeLabels = {list(sorted(stat_data[COST_BY_TYPE].keys())) if stat_data[COST_BY_TYPE] else []};
                        if (typeLabels.length > 0) {{
                            const typeData = {{
                                labels: typeLabels,
                                datasets: [{{
                                    data: {[stat_data[COST_BY_TYPE][req_type] for req_type in sorted(stat_data[COST_BY_TYPE].keys())] if stat_data[COST_BY_TYPE] else []},
                                    backgroundColor: getPieColors(typeLabels.length),
                                    borderColor: getPieColors(typeLabels.length),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            createPieChart_{div_id}('typePieChart_{div_id}', 'typePieLegend_{div_id}', typeData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('typePieChart_{div_id}').style.display = 'none';
                            document.getElementById('typePieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#typePieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '请求类型花费分布 (无数据)';
                        }}
                        
                        // 聊天消息分布饼图
                        const chatLabels = {chat_labels_json};
                        if (chatLabels.length > 0) {{
                            const chatData = {{
                                labels: chatLabels,
                                datasets: [{{
                                    data: {chat_counts_json},
                                    backgroundColor: getPieColors(chatLabels.length),
                                    borderColor: getPieColors(chatLabels.length),
                                    borderWidth: 2
                                }}]
                            }};
                            
                            createPieChart_{div_id}('chatPieChart_{div_id}', 'chatPieLegend_{div_id}', chatData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ' + context.parsed + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('chatPieChart_{div_id}').style.display = 'none';
                            document.getElementById('chatPieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#chatPieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '聊天消息分布 (无数据)';
                        }}

                        // 聊天流花费分布饼图
                        const chatCostLabels = {chat_cost_labels_json};
                        if (chatCostLabels.length > 0) {{
                            const chatCostData = {{
                                labels: chatCostLabels,
                                datasets: [{{
                                    data: {chat_costs_json},
                                    backgroundColor: getPieColors(chatCostLabels.length),
                                    borderColor: getPieColors(chatCostLabels.length),
                                    borderWidth: 2
                                }}]
                            }};

                            createPieChart_{div_id}('chatCostPieChart_{div_id}', 'chatCostPieLegend_{div_id}', chatCostData, function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ¥' + context.parsed.toFixed(2) + ' (' + percentage + '%)';
                            }});
                        }} else {{
                            document.getElementById('chatCostPieChart_{div_id}').style.display = 'none';
                            document.getElementById('chatCostPieLegend_{div_id}').style.display = 'none';
                            document.querySelector('#chatCostPieChart_{div_id}').closest('.pie-chart-card').querySelector('h3').textContent = '聊天流花费分布 (无数据)';
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
            _format_stat_data(
                stat["all_time"],
                "all_time",
                self.all_time_start_time,
            )
        )

        # 图表与指标趋势共用一次明细遍历，随后分别渲染。
        series_started_at = perf_counter()
        period_trend_collector = self._build_period_trend_collector(now)
        chart_data, metrics_data = self._generate_time_series(
            now, period_trend_collector, getattr(self, "_usage_hourly", None)
        )
        task_usage_start = max(now - get_model_task_usage_retention(), now - PERIOD_TREND_MAX_RANGE)
        for bucket in fetch_model_task_usage_minutes(task_usage_start, now):
            period_trend_collector.add_task_usage(
                bucket["bucket_time"], bucket["attempt_count"], bucket["failure_count"],
            )
        period_trends = period_trend_collector.result()
        tab_content_list.append(self._generate_chart_tab(chart_data))
        tab_content_list.append(self._generate_metrics_tab(metrics_data))
        logger.info(f"统计阶段耗时: 图表与指标趋势={perf_counter() - series_started_at:.2f}s")

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
        :root {
            --statistics-background: hsl(35.4 61.9% 87.6%);
            --statistics-foreground: hsl(189 72% 18.2%);
            --statistics-card: hsl(36 66% 89.6%);
            --statistics-card-strong: hsl(34.1 54.8% 81.8%);
            --statistics-muted: hsl(34.9 48.3% 82.5%);
            --statistics-muted-foreground: hsl(39.1 11.6% 39%);
            --statistics-primary: hsl(15.6 68.7% 45.1%);
            --statistics-primary-foreground: hsl(39.5 100% 92%);
            --statistics-accent: hsl(34.7 45.6% 75.5%);
            --statistics-border: hsl(188.1 74% 19.6%);
            --statistics-ring: hsl(15.6 68.7% 45.1%);
            --statistics-row-alt: hsl(34.1 54.8% 81.8% / 0.42);
        }

        html {
            box-sizing: border-box;
        }

        *, *::before, *::after {
            box-sizing: inherit;
        }

        body {
            font-family: "Bahnschrift Condensed", "Agency FB", "Arial Narrow", "Microsoft YaHei UI", system-ui, sans-serif;
            margin: 0;
            padding: 20px;
            background:
                linear-gradient(90deg, hsl(188.1 74% 19.6% / 0.05) 1px, transparent 1px),
                linear-gradient(0deg, hsl(188.1 74% 19.6% / 0.04) 1px, transparent 1px),
                var(--statistics-background);
            background-size: 28px 28px;
            color: var(--statistics-foreground);
            line-height: 1.6;
        }
        .container {
            width: 100%;
            max-width: none;
            margin: 20px auto;
            background-color: hsl(36 66% 89.6% / 0.94);
            padding: 25px;
            border-radius: 4px;
            box-shadow: none;
            border: 2px solid var(--statistics-border);
        }
        h1, h2 {
            color: var(--statistics-foreground);
            border-bottom: 2px solid var(--statistics-border);
            padding-bottom: 10px;
            margin-top: 0;
            letter-spacing: 0;
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
            background-color: var(--statistics-muted);
            padding: 8px 12px;
            border: 1px solid var(--statistics-border);
            border-radius: 3px;
            margin-bottom: 8px;
            font-size: 0.95em;
        }
        .info-item strong {
            color: var(--statistics-primary);
        }
        /* 新增：顶部工具条与按钮 */
        .toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
        .toolbar .right { display: flex; gap: 8px; align-items: center; }
        .btn {
            border: 1px solid var(--statistics-border);
            background-color: var(--statistics-card);
            color: var(--statistics-foreground);
            padding: 8px 12px;
            border-radius: 3px;
            cursor: pointer;
            transition: all .2s ease;
        }
        .btn:hover { border-color: var(--statistics-ring); color: var(--statistics-primary); background-color: var(--statistics-accent); }
        /* 新增：KPI 卡片 */
        .kpi-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 12px 0 6px; }
        .kpi-card {
            background: var(--statistics-card);
            border: 2px solid var(--statistics-border);
            border-radius: 4px;
            padding: 14px 16px;
            box-shadow: none;
        }
        .kpi-title { font-size: 12px; color: var(--statistics-muted-foreground); letter-spacing: 0; margin-bottom: 6px; }
        .kpi-value { font-size: 20px; font-weight: 800; letter-spacing: 0; color: var(--statistics-primary); }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 0.9em;
        }
        /* 新增：表格包裹容器，支持横向滚动 */
        .table-wrap { width: 100%; overflow-x: auto; border-radius: 3px; border: 1px solid var(--statistics-border); }
        th, td {
            border: 1px solid hsl(188.1 74% 19.6% / 0.35);
            padding: 10px;
            text-align: left;
        }
        th {
            background-color: var(--statistics-border);
            color: var(--statistics-primary-foreground);
            font-weight: bold;
            position: sticky;
            top: 0;
            z-index: 1;
        }
        tr:nth-child(even) {
            background-color: var(--statistics-row-alt);
        }
        .footer {
            text-align: center;
            margin-top: 30px;
            font-size: 0.8em;
            color: var(--statistics-muted-foreground);
        }
        .tabs {
            overflow: hidden;
            background: var(--statistics-card-strong);
            display: flex;
            flex-wrap: wrap;
            border: 2px solid var(--statistics-border);
            border-radius: 4px;
            box-shadow: none;
        }
        .tabs button {
            background: inherit; border: none; outline: none;
            padding: 12px 14px; cursor: pointer;
            transition: 0.2s; font-size: 15px;
            color: var(--statistics-foreground);
        }
        .tabs button:hover {
            background-color: var(--statistics-accent);
        }
        .tabs button.active {
            background-color: var(--statistics-primary);
            color: var(--statistics-primary-foreground);
        }
        .tab-content {
            display: none;
            padding: 20px;
            background-color: hsl(36 66% 89.6% / 0.76);
            border: 2px solid var(--statistics-border);
            border-top: none;
            border-radius: 0 0 4px 4px;
        }
        .tab-content.active {
            display: block;
        }
        canvas {
            max-width: 100%;
        }
        .pie-chart-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 20px;
            margin-top: 20px;
            align-items: stretch;
        }
        .pie-chart-card {
            display: flex;
            min-width: 0;
            flex-direction: column;
            gap: 12px;
            padding: 14px;
            border: 1px solid var(--statistics-border);
            border-radius: 4px;
            background: hsl(36 66% 89.6% / 0.55);
        }
        .pie-chart-card h3 {
            margin: 0;
            min-height: 1.6em;
            color: var(--statistics-foreground);
            font-size: 1.1em;
        }
        .pie-chart-canvas-wrap {
            width: 100%;
            height: 450px;
            min-height: 450px;
        }
        .pie-chart-canvas-wrap canvas {
            width: 100% !important;
            height: 100% !important;
        }
        .pie-chart-legend {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 6px 10px;
            max-height: 128px;
            min-height: 44px;
            overflow-y: auto;
            padding: 8px;
            border: 1px solid hsl(188.1 74% 19.6% / 0.35);
            border-radius: 3px;
            background: hsl(34.9 48.3% 82.5% / 0.5);
        }
        .pie-chart-legend-item {
            display: grid;
            grid-template-columns: 12px minmax(0, 1fr);
            align-items: center;
            gap: 6px;
            min-width: 0;
            padding: 3px 4px;
            border: 0;
            background: transparent;
            color: var(--statistics-foreground);
            cursor: pointer;
            font: inherit;
            line-height: 1.25;
            text-align: left;
        }
        .pie-chart-legend-item.is-hidden {
            opacity: 0.45;
            text-decoration: line-through;
        }
        .pie-chart-legend-color {
            width: 10px;
            height: 10px;
            border-radius: 2px;
            border: 1px solid hsl(188.1 74% 19.6% / 0.25);
        }
        .pie-chart-legend-label {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        @media (max-width: 760px) {
            .pie-chart-grid {
                grid-template-columns: 1fr;
            }
            .pie-chart-legend {
                grid-template-columns: 1fr;
            }
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

        record_file = Path(self.record_file_path)
        if record_file.parent != Path("."):
            record_file.parent.mkdir(parents=True, exist_ok=True)

        with open(record_file, "w", encoding="utf-8") as f:
            f.write(html_template)

        from src.services.statistics_service import store_detailed_statistics_snapshot

        store_detailed_statistics_snapshot(
            self._build_detailed_statistics_snapshot(stat, now, chart_data, metrics_data, period_trends)
        )

    @staticmethod
    def _generate_time_series(
        now: datetime,
        period_trends: PeriodTrendCollector | None = None,
        usage_hourly: "UsageHourlyState | None" = None,
    ) -> Tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, object]]]:
        """为全部图表和指标填充时间桶；小时汇总就绪时，干净的小时读汇总，其余小时读明细。"""
        from src.services.statistics_service import iter_messages_since

        from .utils import is_bot_self

        chart_now = datetime.now()
        start = min(chart_now - timedelta(hours=48), now - timedelta(days=30))
        if usage_hourly is not None and usage_hourly.ready:
            usage: "list[UsageEntry] | object" = StatisticOutputTask._time_series_usage_entries(
                start, now, chart_now, period_trends, usage_hourly.cursor
            )
            aggregate_limit: datetime | None = usage_bucket_start(now)
        else:
            usage = fetch_model_usage_since(start)
            aggregate_limit = None
        return collect_time_series(
            now, chart_now,
            cast("list[UsageEntry]", usage),
            iter_messages_since(start),
            fetch_online_time_since(now - timedelta(days=30)),
            is_bot_self,
            period_trends,
            aggregate_limit,
        )

    @staticmethod
    def _time_series_usage_entries(
        start: datetime,
        now: datetime,
        chart_now: datetime,
        period_trends: PeriodTrendCollector | None,
        cursor: int,
    ) -> list[UsageEntry]:
        """汇总行 + 需要明细的小时里的明细 + 汇总刷新后才写入的明细；明细按 id 去重。"""
        aggregate_limit = usage_bucket_start(now)
        first_hour = usage_bucket_start(start)
        chart_windows, metric_windows = build_series_windows(now, chart_now)
        windows = [
            *chart_windows.values(), *metric_windows.values(),
            *(period_trends.windows() if period_trends is not None else []),
        ]

        entries: list[UsageEntry] = []
        for bucket, key, metrics in fetch_usage_hourly(first_hour, aggregate_limit):
            request_type = key.request_type
            entries.append(UsageEntry(
                timestamp=bucket,
                cost=metrics[5],
                tokens=metrics[1] + metrics[2],
                requests=metrics[0],
                model=key.model_name,
                module=request_type.split(".")[0] if "." in request_type else request_type,
                source=SOURCE_HOURLY,
            ))

        records: dict[int, Mapping[str, object]] = {}
        for range_start, range_end in hours_needing_detail(windows, first_hour, aggregate_limit):
            for record in fetch_usage_records_between(range_start, range_end):
                records[cast(int, record["id"])] = record
        for record in fetch_usage_records_after_id(cursor):
            if isinstance(record["timestamp"], datetime):
                records[cast(int, record["id"])] = record
        for record_id, record in records.items():
            entries.append(
                usage_entry_from_record(record, SOURCE_UNAGGREGATED if record_id > cursor else SOURCE_RAW)
            )
        return entries

    def _generate_chart_tab(self, chart_data: dict[str, dict[str, object]]) -> str:
        # sourcery skip: extract-duplicate-method, move-assign-in-block
        """生成图表选项卡HTML内容"""

        # 生成不同颜色的调色板
        colors = [
            "#b35b34",
            "#0d4b50",
            "#cfa54b",
            "#6f665b",
            "#dfc79a",
            "#8f4b38",
            "#74825a",
            "#854f46",
            "#2f5f62",
            "#b98556",
        ]

        # 默认使用24小时数据生成数据集
        default_data = cast(dict[str, object], chart_data["24h"])
        cost_by_model = cast(dict[str, list[float]], default_data.get("cost_by_model", {}))
        cost_by_module = cast(dict[str, list[float]], default_data.get("cost_by_module", {}))
        message_by_chat = cast(dict[str, list[int]], default_data.get("message_by_chat", {}))

        # 为每个模型生成数据集
        model_datasets = []
        for i, (model_name, cost_data) in enumerate(cost_by_model.items()):
            color = colors[i % len(colors)]
            model_datasets.append(f"""{{
                label: {_json_for_html_script(str(model_name))},
                data: {_json_for_html_script(cost_data)},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(model_datasets)

        # 为每个模块生成数据集
        module_datasets = []
        for i, (module_name, cost_data) in enumerate(cost_by_module.items()):
            color = colors[i % len(colors)]
            module_datasets.append(f"""{{
                label: {_json_for_html_script(str(module_name))},
                data: {_json_for_html_script(cost_data)},
                borderColor: '{color}',
                backgroundColor: '{color}20',
                tension: 0.4,
                fill: false
            }}""")

        ",\n                    ".join(module_datasets)

        # 为每个聊天流生成消息数据集
        message_datasets = []
        for i, (chat_name, message_data) in enumerate(message_by_chat.items()):
            color = colors[i % len(colors)]
            message_datasets.append(f"""{{
                label: {_json_for_html_script(str(chat_name))},
                data: {_json_for_html_script(message_data)},
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
                    background-color: var(--statistics-card);
                    border: 1px solid var(--statistics-border);
                    color: var(--statistics-foreground);
                    padding: 8px 16px;
                    margin: 0 5px;
                    border-radius: 3px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                
                .time-range-btn:hover {{
                    background-color: var(--statistics-accent);
                }}
                
                .time-range-btn.active {{
                    background-color: var(--statistics-primary);
                    color: var(--statistics-primary-foreground);
                    border-color: var(--statistics-ring);
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
                    const colors = ['#b35b34', '#0d4b50', '#cfa54b', '#6f665b', '#dfc79a', '#8f4b38', '#74825a', '#854f46', '#2f5f62', '#b98556'];
                    
                    let datasets = [];
                    
                    if (chartType === 'totalCost') {{
                        datasets = [{{
                            label: config.title,
                            data: data[config.dataKey],
                            borderColor: colors[0],
                            backgroundColor: 'rgba(179, 91, 52, 0.12)',
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

    def _generate_metrics_tab(self, metrics_data: dict[str, object]) -> str:
        """生成指标趋势图表选项卡HTML内容"""
        colors = {
            "cost_per_100_messages": "#b35b34",
            "cost_per_hour": "#0d4b50",
            "tokens_per_hour": "#cfa54b",
            "cost_per_100_replies": "#74825a",
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
                    background-color: var(--statistics-card);
                    border: 1px solid var(--statistics-border);
                    color: var(--statistics-foreground);
                    padding: 8px 16px;
                    margin: 0 5px;
                    border-radius: 3px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                
                .time-scale-btn:hover {{
                    background-color: var(--statistics-accent);
                }}
                
                .time-scale-btn.active {{
                    background-color: var(--statistics-primary);
                    color: var(--statistics-primary-foreground);
                    border-color: var(--statistics-ring);
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

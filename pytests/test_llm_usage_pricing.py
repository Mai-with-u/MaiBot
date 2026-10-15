from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List
from unittest.mock import AsyncMock
import threading

import pytest
import tomlkit

from src.common.database.database_model import ModelUsage
from src.config.config_base import AttributeData
from src.config.config_utils import recursive_parse_item_to_table
from src.config.model_configs import APIProvider, ModelInfo, ModelPricePeriod, TaskConfig
from src.llm_models import utils, utils_model
from src.llm_models.exceptions import NetworkConnectionError, RespNotOkException
from src.llm_models.model_client.base_client import APIResponse, UsageRecord
from src.llm_models.utils import LLMUsageRecorder
from src.llm_models.utils_model import LLMExecutionResult, LLMOrchestrator, RequestType


def _period(start: str = "23:00", end: str = "07:00", **prices: float) -> Dict[str, Any]:
    return dict(start_time=start, end_time=end, price_in=1.0, price_out=4.0, cache_price_in=0.1) | prices


def _model(**overrides: Any) -> ModelInfo:
    values = dict(
        name="test-model",
        model_identifier="test-model-id",
        api_provider="test-provider",
        price_in=2.0,
        price_out=8.0,
        cache=True,
        cache_price_in=0.5,
        price_periods=[_period()],
    )
    return ModelInfo(**(values | overrides))


def _usage(hit: int = 250_000, miss: int = 750_000, *, reported: bool = False) -> UsageRecord:
    return UsageRecord("test-model", "test-provider", 1_000_000, 500_000, 1_500_000, hit, miss, reported)


@pytest.fixture
def recorded_usage(monkeypatch: pytest.MonkeyPatch) -> List[ModelUsage]:
    records: List[ModelUsage] = []

    @contextmanager
    def session() -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(add=records.append)

    class RecordedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 18, 15, 0, tzinfo=tz)

    monkeypatch.setattr(utils, "get_db_session", session)
    monkeypatch.setattr(utils, "datetime", RecordedDatetime)
    return records


@pytest.fixture(autouse=True)
def clear_cache_reporting_memory() -> Iterator[None]:
    """清空供应商缓存上报记忆，避免跨用例污染粘性判定。"""

    utils._CACHE_REPORTING_MODELS.clear()
    yield
    utils._CACHE_REPORTING_MODELS.clear()


@pytest.mark.parametrize(
    ("started_at", "expected"),
    [
        (datetime(2026, 9, 17, 22, 59, 59), 5.625),
        (datetime(2026, 9, 17, 23, 0), 2.775),
        (datetime(2026, 9, 18, 0, 0), 2.775),
        (datetime(2026, 9, 18, 6, 59, 59), 2.775),
        (datetime(2026, 9, 18, 7, 0), 5.625),
        (datetime(2026, 9, 18, 12, 0), 5.625),
    ],
)
def test_prices_follow_request_start_not_recording_time(recorded_usage, started_at, expected) -> None:
    LLMUsageRecorder().record_usage_to_database(_model(), _usage(), "system", "test", request_started_at=started_at)

    assert recorded_usage[0].cost == pytest.approx(expected)
    assert recorded_usage[0].timestamp == datetime(2026, 9, 18, 15, 0)


@pytest.mark.parametrize(
    ("cache", "hit", "miss", "expected"),
    # cache 为遗留兼容字段，不参与计价也不参与统计：计价仅取决于时段缓存单价是否大于 0
    [(False, 250_000, 750_000, 2.775), (True, 250_000, 0, 2.775), (True, 0, 0, 3.0)],
)
def test_period_prices_preserve_cache_accounting(recorded_usage, cache, hit, miss, expected) -> None:
    LLMUsageRecorder().record_usage_to_database(
        _model(cache=cache), _usage(hit, miss), "system", "test", request_started_at=datetime(2026, 9, 18, 1)
    )

    assert recorded_usage[0].cost == pytest.approx(expected)
    # 供应商从未上报缓存字段，统计不展示，与 cache 布尔和缓存单价无关
    assert recorded_usage[0].prompt_cache_enabled is False


def test_zero_cache_price_means_free_cache_hits(recorded_usage) -> None:
    """缓存单价为 0 表示缓存命中免费，未命中部分仍按 price_in 计费。"""
    LLMUsageRecorder().record_usage_to_database(
        _model(cache=True, price_periods=[_period(cache_price_in=0.0)]),
        _usage(250_000, 750_000),
        "system",
        "test",
        request_started_at=datetime(2026, 9, 18, 1),
    )

    # 命中 0.25M * 0 + 未命中 0.75M * 1.0 + 输出 0.5M * 4.0
    assert recorded_usage[0].cost == pytest.approx(2.75)
    # 计价与统计展示互不影响：未上报缓存字段就不展示
    assert recorded_usage[0].prompt_cache_enabled is False


def test_prompt_cache_stats_follow_provider_reporting(recorded_usage) -> None:
    """缓存统计展示由供应商是否上报缓存字段决定，与价格配置无关；首次上报后粘性生效。"""

    # 上报了缓存字段的模型即展示统计，缓存价 0 时命中免费、未命中按 price_in 计费
    LLMUsageRecorder().record_usage_to_database(
        _model(cache=True, cache_price_in=0.0, price_periods=[]),
        _usage(250_000, 750_000, reported=True),
        "system",
        "test",
        request_started_at=datetime(2026, 9, 18, 1),
    )
    # 命中 0.25M * 0（免费）+ 未命中 0.75M * 2.0 + 输出 0.5M * 8.0
    assert recorded_usage[0].cost == pytest.approx(5.5)
    assert recorded_usage[0].prompt_cache_enabled is True

    # 同一模型后续调用未命中（部分供应商零命中时不返回缓存字段），仍按已记住的支持缓存展示
    LLMUsageRecorder().record_usage_to_database(
        _model(cache=True, cache_price_in=0.0, price_periods=[]),
        _usage(0, 0),
        "system",
        "test",
        request_started_at=datetime(2026, 9, 18, 1),
    )
    assert recorded_usage[1].prompt_cache_enabled is True

    # 从未上报的模型即使配了缓存价也不展示统计
    LLMUsageRecorder().record_usage_to_database(
        _model(name="other-model", model_identifier="other-model-id"),
        _usage(),
        "system",
        "test",
        request_started_at=datetime(2026, 9, 18, 1),
    )
    assert recorded_usage[2].prompt_cache_enabled is False


def test_legacy_model_uses_default_prices(recorded_usage) -> None:
    values = _model().model_dump(exclude={"price_periods"})
    model = ModelInfo(**values)
    assert model.price_periods == []
    LLMUsageRecorder().record_usage_to_database(
        model, _usage(), "system", "test", request_started_at=datetime(2026, 9, 18, 1)
    )
    assert recorded_usage[0].cost == pytest.approx(5.625)


def test_zero_period_prices_do_not_use_defaults(recorded_usage) -> None:
    LLMUsageRecorder().record_usage_to_database(
        _model(price_periods=[_period(price_in=0, price_out=0, cache_price_in=0)]),
        _usage(),
        "system",
        "test",
        request_started_at=datetime(2026, 9, 18, 1),
    )
    assert recorded_usage[0].cost == 0


def test_aware_start_is_converted_to_server_local_time() -> None:
    model = _model()
    local_start = datetime(2026, 9, 18, 1).astimezone()
    other_timezone = local_start.astimezone(timezone(timedelta(hours=-9)))
    assert LLMUsageRecorder._resolve_prices(model, other_timezone) is model.price_periods[0]


@pytest.mark.parametrize(("hour", "expected"), [(0, 1.0), (7, 3.0), (22, 3.0), (23, 1.0)])
def test_adjacent_periods_can_cover_all_day(hour: int, expected: float) -> None:
    model = _model(price_periods=[_period(), _period("07:00", "23:00", price_in=3.0)])
    assert LLMUsageRecorder._resolve_prices(model, datetime(2026, 9, 18, hour)).price_in == expected


@pytest.mark.parametrize(
    "ranges",
    [
        [("08:00", "12:00"), ("11:00", "14:00")],
        [("23:00", "07:00"), ("06:00", "08:00")],
        [("23:00", "07:00"), ("22:00", "23:30")],
        [("23:00", "07:00"), ("23:30", "01:00")],
        [("08:00", "12:00"), ("08:00", "12:00")],
    ],
)
def test_overlapping_price_periods_are_rejected(ranges) -> None:
    with pytest.raises(ValueError, match="重叠"):
        _model(price_periods=[_period(start, end) for start, end in ranges])


@pytest.mark.parametrize("time_value", ["24:00", "12:60", "8:00", "12:00:00", " 08:00", "08:00\n", ""])
@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_invalid_period_time_is_rejected(time_value: str, field: str) -> None:
    with pytest.raises(ValueError):
        ModelPricePeriod(**(_period() | {field: time_value}))


def test_empty_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="不能相同"):
        ModelPricePeriod(**_period("00:00", "00:00"))


@pytest.mark.parametrize("price", [-1.0, float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["price_in", "price_out", "cache_price_in"])
def test_invalid_period_prices_are_rejected(price: float, field: str) -> None:
    with pytest.raises(ValueError):
        ModelPricePeriod(**(_period() | {field: price}))


def test_price_periods_round_trip_through_config_toml() -> None:
    model = _model(price_periods=[_period(), _period("07:00", "23:00", cache_price_in=0)])
    serialized = tomlkit.dumps(recursive_parse_item_to_table(model))
    parsed = tomlkit.loads(serialized).unwrap()
    restored = ModelInfo.from_dict(AttributeData(), parsed)
    assert restored.price_periods == model.price_periods


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_model", [False, True])
async def test_successful_attempt_time_survives_retry_or_model_switch(monkeypatch, switch_model) -> None:
    first_time = datetime(2026, 9, 18, 6, 59, 59).timestamp()
    success_time = datetime(2026, 9, 18, 7, 0).timestamp()
    clock = SimpleNamespace(value=first_time)
    models = [_model(), _model(name="second-model")]
    provider = APIProvider(name="test-provider", base_url="https://example.com", auth_type="none", max_retry=2)
    calls = []

    async def get_response(request):
        calls.append(request.model_info.name)
        if len(calls) == 1:
            clock.value = success_time
            if switch_model:
                raise RespNotOkException(400, "test failure")
            raise NetworkConnectionError("test retry")
        clock.value = success_time + 60
        return APIResponse(usage=_usage())

    client = SimpleNamespace(get_response=get_response)
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.task_name = "planner"
    orchestrator.request_type = "test"
    orchestrator.session_id = ""
    orchestrator.model_for_task = TaskConfig(model_list=[model.name for model in models])
    orchestrator.model_usage = {model.name: (0, 0, 1) for model in models}
    monkeypatch.setattr(
        orchestrator,
        "_select_model",
        lambda exclude_models, model_name: (models[1] if exclude_models else models[0], provider, client),
    )
    monkeypatch.setattr(utils_model, "time", SimpleNamespace(time=lambda: clock.value))
    monkeypatch.setattr(utils_model.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(utils_model, "save_failed_request_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(utils_model, "update_failed_request_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils_model, "mark_request_succeeded", lambda *args: None)

    result = await orchestrator._execute_request(RequestType.RESPONSE)

    assert result.request_started_at == datetime.fromtimestamp(success_time)
    assert result.model_info is models[1 if switch_model else 0]
    assert calls == [models[0].name, models[1 if switch_model else 0].name]
    assert LLMUsageRecorder._resolve_prices(result.model_info, result.request_started_at).price_in == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["text", "context", "image", "embedding", "image_embedding"])
async def test_usage_entrypoints_pass_start_time_without_blocking_loop(monkeypatch, entrypoint) -> None:
    started_at = datetime(2026, 9, 18, 6, 59)
    result = LLMExecutionResult(APIResponse(usage=_usage(), embedding=[1.0]), _model(), started_at)
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator.task_name = "planner"
    orchestrator.request_type = "test"
    orchestrator.session_id = ""
    orchestrator.model_for_task = TaskConfig()
    monkeypatch.setattr(orchestrator, "_refresh_task_config", lambda: None)
    monkeypatch.setattr(orchestrator, "_execute_request", AsyncMock(return_value=result))
    calls = []
    main_thread = threading.get_ident()

    def record(**kwargs):
        calls.append((threading.get_ident(), kwargs))

    monkeypatch.setattr(utils_model.llm_usage_recorder, "record_usage_to_database", record)
    if entrypoint == "text":
        await orchestrator.generate_response_async("test")
    elif entrypoint == "context":
        await orchestrator.generate_response_with_context_async(lambda client: [])
    elif entrypoint == "image":
        await orchestrator.generate_response_for_image("test", "AA==", "png")
    elif entrypoint == "embedding":
        await orchestrator.get_embedding("test")
    else:
        await orchestrator.get_image_embedding(b"image", mime_type="image/png", preprocess_version="v1")

    assert len(calls) == 1
    assert calls[0][0] != main_thread
    assert calls[0][1]["request_started_at"] == started_at
    assert calls[0][1]["model_info"] is result.model_info

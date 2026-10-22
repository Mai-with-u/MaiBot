from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.learners import expression_learner, jargon_learner
from src.maisaka import runtime as runtime_module
from src.maisaka.context.messages import ContentContextMessage, SessionBackedMessage


def messages(start, stop, user_id="user"):
    result = []
    for index in range(start, stop):
        message = SessionMessage(str(index), datetime.now(), "test")
        message.session_id = "test-session"
        message.message_info = MessageInfo(UserInfo(user_id, user_id))
        message.raw_message = MessageSequence([TextComponent(f"聊天内容 {index}")])
        message.processed_plain_text = f"聊天内容 {index}"
        result.append(
            SessionBackedMessage.from_session_message(
                message,
                raw_message=message.raw_message,
                visible_text=message.processed_plain_text,
            )
        )
    return result


@pytest.fixture(params=["expression", "jargon"])
def learner_case(request, monkeypatch):
    kind = request.param
    module = expression_learner if kind == "expression" else jargon_learner
    learner = module.ExpressionLearner("test-session") if kind == "expression" else module.JargonLearner("test-session")
    gate = module.expression_learning_batch_gate if kind == "expression" else module.jargon_learning_batch_gate
    monkeypatch.setattr(jargon_learner, "is_bot_self", lambda platform, user_id: user_id == "bot")
    monkeypatch.setattr(learner, "_resolve_learning_session_id", lambda items: "test-session")
    acquire = AsyncMock(return_value=SimpleNamespace(acquired=True))
    monkeypatch.setattr(gate, "acquire", acquire)
    monkeypatch.setattr(gate, "release", AsyncMock())

    async def execute(items, **kwargs):
        learner.learning_status, learner.learning_detail = "completed", "写入成功"
        return True

    batch = AsyncMock(side_effect=execute)
    monkeypatch.setattr(learner, "_run_learning_batch", batch)

    async def learn(context):
        if kind == "expression":
            return await learner.learn_from_context_messages(context, learned_contents=[])
        return await learner.learn_from_context_messages(context, SimpleNamespace(), learned_contents=[])

    return SimpleNamespace(learner=learner, acquire=acquire, batch=batch, learn=learn, kind=kind)


@pytest.mark.asyncio
async def test_under_threshold_accumulates_and_duplicates_do_not_fill_threshold(learner_case):
    case = learner_case
    first = messages(0, 9)
    assert not await case.learn(first)
    assert case.learner.learning_status == "waiting"
    assert "9/10" in case.learner.learning_detail
    assert not await case.learn(first)
    case.batch.assert_not_awaited()

    assert await case.learn(messages(9, 10))
    assert len(case.batch.await_args.args[0]) == 10
    assert len(case.learner.pending_messages) == 0


@pytest.mark.asyncio
async def test_internal_context_does_not_count_as_chat_material(learner_case):
    internal = ContentContextMessage(
        raw_message=MessageSequence([TextComponent("工具结果")]),
        visible_text="工具结果",
        timestamp=datetime.now(),
    )
    assert not await learner_case.learn([internal] * 31)
    assert learner_case.learner.learning_status == "skipped"
    assert "没有可学习的真实聊天消息" in learner_case.learner.learning_detail
    learner_case.batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_gate_retains_only_latest_100_and_retries(learner_case):
    case = learner_case
    case.acquire.return_value = SimpleNamespace(acquired=False, reason="global_limit", active_count=1, max_count=1)
    assert not await case.learn(messages(0, 90))
    assert not await case.learn(messages(90, 130))
    assert case.learner.learning_status == "waiting"
    assert "全局学习并发已满" in case.learner.learning_detail
    assert len(case.learner.pending_messages) == 100
    assert case.learner.pending_messages.dropped_count == 30
    case.batch.assert_not_awaited()

    case.acquire.return_value = SimpleNamespace(acquired=True)
    assert await case.learn([])
    batch = case.batch.await_args.args[0]
    originals = batch if case.kind == "expression" else [item.original_message for item in batch]
    assert [item.message_id for item in originals] == [str(index) for index in range(30, 130)]
    assert len(case.learner.pending_messages) == 0


@pytest.mark.asyncio
async def test_invalid_concurrency_configuration_is_skipped_with_material_retained(learner_case):
    case = learner_case
    case.acquire.return_value = SimpleNamespace(
        acquired=False,
        reason="max_expression_learner <= 0",
        active_count=0,
        max_count=0,
    )
    assert not await case.learn(messages(0, 10))
    assert case.learner.learning_status == "skipped"
    assert "配置小于或等于 0" in case.learner.learning_detail
    assert len(case.learner.pending_messages) == 10


@pytest.mark.asyncio
async def test_inflight_batch_does_not_consume_new_material(learner_case):
    case = learner_case
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(items, **kwargs):
        entered.set()
        await release.wait()
        return True

    case.batch.side_effect = execute
    task = asyncio.create_task(case.learn(messages(0, 10)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        case.learner.collect_context_messages(messages(10, 19))
    finally:
        release.set()
        await task
    assert len(case.learner.pending_messages) == 9
    assert not await case.learn([])
    assert "9/10" in case.learner.learning_detail


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "filtered", "hook_aborted", "written"])
async def test_expression_reports_actual_batch_outcome(monkeypatch, outcome):
    learner = expression_learner.ExpressionLearner("test-session")
    template = SimpleNamespace(add_context=lambda *args: None)
    monkeypatch.setattr(
        expression_learner,
        "prompt_manager",
        SimpleNamespace(
            get_prompt=lambda name: template,
            render_prompt=AsyncMock(return_value="prompt"),
        ),
    )
    monkeypatch.setattr(
        expression_learner,
        "global_config",
        SimpleNamespace(
            bot=SimpleNamespace(nickname="bot"),
            expression=SimpleNamespace(expression_self_reflect=False),
        ),
    )
    monkeypatch.setattr(
        expression_learner.express_learn_model,
        "generate_response_with_context",
        AsyncMock(
            return_value=SimpleNamespace(response="response"),
        ),
    )
    monkeypatch.setattr(learner, "_build_multi_learning_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(learner, "_log_learning_context_preview", lambda *args, **kwargs: None)
    candidates = [] if outcome == "empty" else [("情景", "表达", "1")]
    monkeypatch.setattr(expression_learner, "parse_expression_response", lambda response: (candidates, []))
    manager = SimpleNamespace(
        invoke_hook=AsyncMock(
            return_value=SimpleNamespace(
                aborted=outcome == "hook_aborted",
                kwargs={},
            )
        )
    )
    monkeypatch.setattr(learner, "_get_runtime_manager", lambda: manager)
    monkeypatch.setattr(
        learner, "_filter_expressions", lambda *args: [] if outcome == "filtered" else [("情景", "表达")]
    )
    monkeypatch.setattr(learner, "_get_session_display_name", lambda session_id: "测试聊天")
    monkeypatch.setattr(learner, "_upsert_expression_to_db", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(learner, "_sync_expression_vector_index_batch", AsyncMock())
    contents = []
    changed = await learner._run_learning_batch(
        [messages(0, 1)[0].original_message],
        learning_session_id="test-session",
        learned_contents=contents,
    )
    expected = {
        "empty": ("no_results", "模型未提取出表达候选"),
        "filtered": ("no_results", "均未通过内容或来源校验"),
        "hook_aborted": ("skipped", "Hook 中止"),
        "written": ("completed", "写入 1 条表达"),
    }
    status, reason = expected[outcome]
    assert learner.learning_status == status
    assert reason in learner.learning_detail
    assert changed == (outcome == "written")
    assert contents == (["情景 → 表达"] if changed else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [0, 1])
async def test_jargon_distinguishes_processed_candidates_from_database_changes(monkeypatch, saved):
    learner = jargon_learner.JargonLearner("test-session")
    monkeypatch.setattr(learner, "_log_jargon_update_process", lambda **kwargs: None)
    miner = SimpleNamespace(process_extracted_entries=AsyncMock(return_value=(saved, 0)))
    original = messages(0, 1)[0].original_message
    contents = []
    changed = await learner._process_jargon_entries(
        [("测试黑话", "1")],
        [jargon_learner.JargonLearningSourceItem(original, "原始正文")],
        miner,
        learned_contents=contents,
    )
    assert learner.learning_status == ("completed" if saved else "no_results")
    assert f"新增 {saved} 个、更新 0 个" in learner.learning_detail
    assert changed == bool(saved)
    assert contents == ["测试黑话"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_jargon", [False, True])
async def test_runtime_collects_during_active_batch_and_automatically_drains(monkeypatch, enable_jargon):
    runtime_class = runtime_module.MaisakaHeartFlowChatting
    runtime = runtime_class.__new__(runtime_class)
    runtime.log_prefix = "[test]"
    runtime._expression_learner = expression_learner.ExpressionLearner("test-session")
    runtime._jargon_learner = jargon_learner.JargonLearner("test-session")
    runtime._jargon_miner = SimpleNamespace()
    runtime._trimmed_history_learning_task = None
    runtime._queued_trimmed_learning_progress = None
    runtime._last_expression_extraction_time = 0.0
    runtime._min_extraction_interval = 0.01
    monkeypatch.setattr(runtime_class, "_enable_expression_learning", property(lambda self: True))
    monkeypatch.setattr(runtime_class, "_enable_jargon_learning", property(lambda self: enable_jargon))
    monkeypatch.setattr(jargon_learner, "is_bot_self", lambda platform, user_id: user_id == "bot")
    monkeypatch.setattr(runtime_module, "update_high_frequency_terms_from_context_messages", lambda *args, **kwargs: 0)
    learner = runtime._expression_learner
    monkeypatch.setattr(learner, "_resolve_learning_session_id", lambda items: "test-session")
    monkeypatch.setattr(
        expression_learner.expression_learning_batch_gate,
        "acquire",
        AsyncMock(return_value=SimpleNamespace(acquired=True)),
    )
    monkeypatch.setattr(expression_learner.expression_learning_batch_gate, "release", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()
    batches, jargon_batches, progress, queued_progress = [], [], [], []

    async def execute_jargon(items, **kwargs):
        jargon_batches.append(len(items))
        runtime._jargon_learner.learning_status = "no_results"
        runtime._jargon_learner.learning_detail = "模型未提取出黑话候选"
        return False

    monkeypatch.setattr(runtime._jargon_learner, "_resolve_learning_session_id", lambda items: "test-session")
    monkeypatch.setattr(runtime._jargon_learner, "_run_learning_batch", execute_jargon)
    monkeypatch.setattr(
        jargon_learner.jargon_learning_batch_gate, "acquire", AsyncMock(return_value=SimpleNamespace(acquired=True))
    )
    monkeypatch.setattr(jargon_learner.jargon_learning_batch_gate, "release", AsyncMock())

    async def execute(items, **kwargs):
        batches.append(len(items))
        if len(batches) == 1:
            entered.set()
            await release.wait()
        learner.learning_status, learner.learning_detail = "no_results", "模型未提取出表达候选"
        return False

    async def report(value):
        progress.append(value)

    async def report_queued(value):
        queued_progress.append(value)

    monkeypatch.setattr(learner, "_run_learning_batch", execute)
    await runtime._trigger_trimmed_history_learning(messages(0, 10, user_id="bot"), on_progress=report)
    task = runtime._trimmed_history_learning_task
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await runtime._trigger_trimmed_history_learning(messages(10, 120), on_progress=report_queued)
        assert queued_progress[-1]["status"] == "waiting"
        assert "淘汰最旧的 10 条" in queued_progress[-1]["learners"][0]["detail"]
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert batches == [10, 100]
    assert jargon_batches == ([100] if enable_jargon else [])
    assert len(learner.pending_messages) == 0
    assert progress[-1]["learners"][0]["status"] == "no_results"
    assert "模型未提取出表达候选" in progress[-1]["learners"][0]["detail"]
    assert "淘汰最旧的 10 条" in progress[-1]["learners"][0]["detail"]
    assert queued_progress[-1]["learners"][0]["status"] == "no_results"
    assert queued_progress[-1]["learners"][2]["status"] == "skipped"

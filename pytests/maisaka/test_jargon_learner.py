from datetime import datetime
from json import dumps

from scripts.jargon_learn_from_planner_logs import build_planner_candidate
from src.learners.jargon_learner import JargonLearner
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    FunctionCallOutputItem,
    ReasoningItem,
    ReasoningRepresentation,
    UserMessageItem,
)
from src.llm_models.request_snapshot import serialize_context_items_snapshot
from src.maisaka.context.messages import ModelOutputContextMessage


def test_extract_learning_sources_only_uses_assistant_body_items() -> None:
    """黑话学习只应读取模型可见正文，不能把 reasoning 当作语料。"""

    reasoning_message = ModelOutputContextMessage(
        output_item=ReasoningItem(
            meta=ContextItemMeta.create(),
            text_parts=("这是内部推理",),
            representation=ReasoningRepresentation.RAW_TEXT,
        )
    )
    assistant_message = ModelOutputContextMessage(
        output_item=AssistantMessageItem(
            meta=ContextItemMeta.create(),
            parts=(ContextTextPart("这是最终正文"),),
        )
    )

    sources = JargonLearner._extract_learning_sources_from_context(
        [reasoning_message, assistant_message]
    )

    assert len(sources) == 1
    assert sources[0].speaker_kind == "ASSISTANT"
    assert sources[0].content == "这是最终正文"


def test_planner_offline_learning_reads_v5_request_items(tmp_path) -> None:
    """离线黑话学习脚本应读取 v5 Items，并忽略独立 reasoning。"""

    timestamp = datetime(2026, 8, 5, 2, 14, 8)
    request_items = [
        UserMessageItem(
            meta=ContextItemMeta.create(timestamp=timestamp),
            parts=(
                ContextTextPart(
                    '<message msg_id="message-1" time="02:14:00" user="测试用户">这是什么梗'
                ),
            ),
        ),
        ReasoningItem(
            meta=ContextItemMeta.create(timestamp=timestamp),
            text_parts=("不能进入学习素材的内部推理",),
            representation=ReasoningRepresentation.RAW_TEXT,
        ),
        AssistantMessageItem(
            meta=ContextItemMeta.create(timestamp=timestamp),
            parts=(ContextTextPart("这是可见回复正文"),),
        ),
        FunctionCallOutputItem(
            meta=ContextItemMeta.create(logical_turn_id="legacy-turn-1", timestamp=timestamp),
            call_id="call-1",
            tool_name="fetch_new_message",
            output="新消息内容",
        ),
    ]
    planner_path = tmp_path / "1785876848000.json"
    planner_path.write_text(
        dumps(
            {
                "schema_version": 5,
                "request_items": serialize_context_items_snapshot(request_items),
                "output_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    candidate = build_planner_candidate("test-session", planner_path, 0)

    assert candidate is not None
    assert candidate.message_ids == {"message-1"}
    assert [source.content for source in candidate.source_items] == [
        '<message msg_id="message-1" time="02:14:00" user="测试用户">这是什么梗',
        "这是可见回复正文",
        "[tool_result:success]\ntool_call_id: call-1\ntool_name: fetch_new_message\n[content]\n新消息内容",
    ]

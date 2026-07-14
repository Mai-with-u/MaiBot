from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data" / "benchmarks"


def _fixture_files() -> list[Path]:
    return sorted(DATA_DIR.glob("group_chat_stream_memory_benchmark*.json"))


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_fixture_matches_current_design_constraints(dataset: dict) -> None:
    assert dataset["meta"]["scenario_id"]

    assert dataset["session"]["group_id"]
    assert dataset["session"]["platform"] == "qq"

    simulated_batches = dataset["simulated_stream_batches"]
    assert len(simulated_batches) >= 5

    positive_batches = [item for item in simulated_batches if item["bot_participated"]]
    negative_batches = [item for item in simulated_batches if not item["bot_participated"]]

    assert len(positive_batches) >= 4
    assert len(negative_batches) >= 1
    assert any(item["expected_behavior"] == "ignored_by_summarizer_without_bot_message" for item in negative_batches)

    for batch in positive_batches:
        assert "Mai" in batch["participants"]
        assert batch["message_count"] >= 10
        assert len(batch["combined_text"]) >= 300
        assert batch["start_time"] < batch["end_time"]
        assert len(batch["expected_memory_targets"]) >= 4

    runtime_streams = dataset["runtime_trigger_streams"]
    assert len(runtime_streams) >= 2

    runtime_positive = [item for item in runtime_streams if item["bot_participated"]]
    runtime_negative = [item for item in runtime_streams if not item["bot_participated"]]

    assert len(runtime_positive) >= 1
    assert len(runtime_negative) >= 1

    for stream in runtime_streams:
        stream_text = "\n".join(stream["messages"])
        assert stream["trigger_mode"] == "time_threshold"
        assert stream["elapsed_since_last_check_hours"] >= 8.0
        assert stream["message_count"] >= 20
        assert len(stream["messages"]) == stream["message_count"]
        assert len(stream_text) >= 1000
        assert stream["start_time"] < stream["end_time"]

    assert any(
        item["expected_check_outcome"] == "should_trigger_topic_check_and_pass_bot_gate" for item in runtime_positive
    )
    assert any(
        item["expected_check_outcome"] == "should_trigger_topic_check_but_be_discarded_without_bot_message"
        for item in runtime_negative
    )

    records = dataset["chat_history_records"]
    assert len(records) >= 4
    for record in records:
        assert "Mai" in record["participants"]
        assert len(record["summary"]) >= 40
        assert len(record["original_text"]) >= 200
        assert record["start_time"] < record["end_time"]

    assert len(dataset["person_writebacks"]) >= 3
    assert len(dataset["search_cases"]) >= 4
    assert len(dataset["time_cases"]) >= 3
    assert len(dataset["episode_cases"]) >= 4
    assert len(dataset["knowledge_fetcher_cases"]) >= 3
    assert len(dataset["profile_cases"]) >= 3
    assert len(dataset["negative_control_cases"]) >= 1


def test_group_chat_stream_fixture_matches_current_design_constraints():
    files = _fixture_files()
    assert files, "未找到 group_chat_stream_memory_benchmark*.json fixture"
    for path in files:
        _assert_fixture_matches_current_design_constraints(_load_fixture(path))

from datetime import datetime
from pathlib import Path

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.utils.runtime_payloads import (
    argument_tokens,
    build_source,
    coerce_datetime,
    merge_argument_tokens,
    merge_tokens,
    optional_float,
    optional_int,
    resolve_knowledge_type,
    safe_json_loads,
    time_meta,
    tokens,
)


def test_runtime_payload_tokens_keep_order_and_drop_empty_duplicates() -> None:
    assert tokens([" Alice ", "", None, "Alice", "Bob", 42]) == ["Alice", "Bob", "42"]
    assert merge_tokens(["Alice", "Bob"], ["Bob", "Carol"], None) == ["Alice", "Bob", "Carol"]
    assert argument_tokens("Alice") == ["Alice"]
    assert merge_argument_tokens(["Alice"], "Bob", ["Alice", "Carol"]) == ["Alice", "Bob", "Carol"]


def test_runtime_payload_source_and_time_helpers_match_kernel_semantics() -> None:
    assert build_source("chat_summary", "chat-1", []) == "chat_summary:chat-1"
    assert build_source("person_fact", "chat-1", ["person-1"]) == "person_fact:person-1"
    assert build_source("manual", "chat-1", []) == "manual:chat-1"
    assert build_source("", "", []) == "memory"

    assert resolve_knowledge_type("person_fact") == "factual"
    assert resolve_knowledge_type("chat_summary") == "narrative"
    assert resolve_knowledge_type("manual") == "mixed"

    assert time_meta(1.0, 2.0, 3.0) == {
        "event_time": 1.0,
        "event_time_start": 2.0,
        "event_time_end": 3.0,
        "time_granularity": "minute",
        "time_confidence": 0.95,
    }
    assert time_meta(None, None, None) == {}


def test_runtime_payload_json_datetime_and_optional_number_helpers() -> None:
    raw_dict = {"name": "Alice"}
    assert safe_json_loads(raw_dict) is raw_dict
    assert safe_json_loads("{name: 'Alice', score: 1}") == {"name": "Alice", "score": 1}
    assert safe_json_loads("[1, 2, 3]") == {}

    value = datetime(2026, 1, 2, 3, 4, 5)
    assert coerce_datetime(value) is value
    assert coerce_datetime("2026-01-02T03:04:05") == value
    assert coerce_datetime("") is None

    assert optional_float("1.5") == 1.5
    assert optional_float("bad") is None
    assert optional_int("7") == 7
    assert optional_int("bad") is None


def test_sdk_memory_kernel_keeps_runtime_payload_compatibility_wrappers() -> None:
    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    assert kernel._tokens([" Alice ", "Alice", "Bob"]) == ["Alice", "Bob"]
    assert kernel._merge_tokens(["Alice"], ["Bob", "Alice"]) == ["Alice", "Bob"]
    assert kernel._argument_tokens("Alice") == ["Alice"]
    assert kernel._merge_argument_tokens(["Alice"], "Bob") == ["Alice", "Bob"]
    assert kernel._build_source("person_fact", "chat-1", ["person-1"]) == "person_fact:person-1"
    assert kernel._resolve_knowledge_type("chat_summary") == "narrative"
    assert kernel._time_meta(1.0, None, 3.0)["event_time_end"] == 3.0
    assert kernel._safe_json_loads("{name: 'Alice'}") == {"name": "Alice"}
    assert kernel._optional_float("2.5") == 2.5
    assert kernel._optional_int("8") == 8

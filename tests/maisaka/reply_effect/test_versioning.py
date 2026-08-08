from typing import Any, Dict

import json

from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v37_to_v38 import migrate_v37_to_v38
from src.common.reply_effect_fingerprint import extract_generation_fingerprints


def _build_metadata(user_text: str, *, item_id: str, timestamp: str) -> Dict[str, Any]:
    return {
        "monitor_detail": {
            "metrics": {"model_name": "test-model"},
            "request_messages": [
                {
                    "item_type": "SystemMessageItem",
                    "meta": {"item_id": item_id, "timestamp": timestamp},
                    "parts": [{"type": "text", "text": "稳定的系统提示词"}],
                },
                {
                    "item_type": "UserMessageItem",
                    "meta": {"item_id": f"user-{item_id}", "timestamp": timestamp},
                    "parts": [{"type": "text", "text": user_text}],
                },
            ],
        }
    }


def test_prompt_version_fingerprint_ignores_dynamic_request_content() -> None:
    first = extract_generation_fingerprints(
        _build_metadata("第一条聊天消息", item_id="first", timestamp="2026-01-01T00:00:00")
    )
    second = extract_generation_fingerprints(
        _build_metadata("完全不同的聊天消息", item_id="second", timestamp="2026-01-02T00:00:00")
    )

    assert first[0] == second[0] == "test-model"
    assert first[1] != second[1]
    assert first[2] == second[2]
    assert first[2]


def test_prompt_version_fingerprint_changes_with_system_prompt() -> None:
    first_metadata = _build_metadata("聊天消息", item_id="first", timestamp="2026-01-01T00:00:00")
    second_metadata = _build_metadata("聊天消息", item_id="second", timestamp="2026-01-01T00:00:00")
    second_metadata["monitor_detail"]["request_messages"][0]["parts"][0]["text"] = "新版系统提示词"

    first = extract_generation_fingerprints(first_metadata)
    second = extract_generation_fingerprints(second_metadata)

    assert first[2] != second[2]


def test_v37_to_v38_migration_splits_existing_fingerprint() -> None:
    metadata = _build_metadata("历史聊天消息", item_id="legacy", timestamp="2026-01-01T00:00:00")
    record_payload = {
        "schema_version": 2,
        "reply": {
            "prompt_fingerprint": "legacy-request-fingerprint",
            "reply_metadata": metadata,
        },
    }
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE maisaka_reply_effects (
                effect_id VARCHAR(36) PRIMARY KEY NOT NULL,
                prompt_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
                record_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO maisaka_reply_effects (effect_id, prompt_fingerprint, record_json) VALUES (?, ?, ?)",
            ("effect-1", "legacy-request-fingerprint", json.dumps(record_payload, ensure_ascii=False)),
        )
        context = MigrationExecutionContext(
            connection=connection,
            current_version=37,
            target_version=38,
            step_index=1,
            step_name="v37_to_v38",
            total_steps=1,
        )

        migrate_v37_to_v38(context)
        migrate_v37_to_v38(context)

        row = connection.exec_driver_sql(
            "SELECT request_fingerprint, prompt_fingerprint, record_json FROM maisaka_reply_effects"
        ).mappings().one()

    migrated_payload = json.loads(row["record_json"])
    expected_prompt_fingerprint = extract_generation_fingerprints(metadata)[2]
    assert row["request_fingerprint"] == "legacy-request-fingerprint"
    assert row["prompt_fingerprint"] == expected_prompt_fingerprint
    assert migrated_payload["reply"]["request_fingerprint"] == "legacy-request-fingerprint"
    assert migrated_payload["reply"]["prompt_fingerprint"] == expected_prompt_fingerprint

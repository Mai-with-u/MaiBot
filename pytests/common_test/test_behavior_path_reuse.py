from contextlib import contextmanager
from typing import Generator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import pytest

import src.learners.behavior_pattern_store as pattern_store
from src.learners.behavior_scenario import BehaviorScenarioProfile, BehaviorScenarioTagCluster


@pytest.fixture(name="behavior_reuse_engine")
def behavior_reuse_engine_fixture() -> Generator:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


def _patch_pattern_store_session(monkeypatch: pytest.MonkeyPatch, engine) -> None:
    @contextmanager
    def fake_get_db_session(auto_commit: bool = True):
        with Session(engine) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(pattern_store, "get_db_session", fake_get_db_session)


def _profile() -> BehaviorScenarioProfile:
    return BehaviorScenarioProfile(
        summary="群友通过越界请求与麦麦玩梗",
        tag_clusters=[
            BehaviorScenarioTagCluster(kind="domain", tags=["机器人玩梗互动", "越界请求"]),
            BehaviorScenarioTagCluster(kind="need", tags=["轻松角色互动", "拒绝式调侃"]),
            BehaviorScenarioTagCluster(kind="attitude", tags=["群友玩梗活跃", "氛围轻松"]),
        ],
        confidence=0.9,
    )


def test_upsert_behavior_experience_does_not_reuse_similar_surface_text(
    monkeypatch: pytest.MonkeyPatch,
    behavior_reuse_engine,
) -> None:
    _patch_pattern_store_session(monkeypatch, behavior_reuse_engine)

    first_path = pattern_store.upsert_behavior_experience(
        action="在群聊中通过@麦麦并发送禁言请求来玩梗互动",
        outcome="引发麦麦调侃式回应，维持群聊搞笑氛围",
        source_ids=["1", "2"],
        session_id="chat-a",
        scenario_profile=_profile(),
        scene_start="机器人玩梗互动",
        actor_type=pattern_store.ACTOR_OTHER_USER,
        learning_type=pattern_store.LEARNING_OBSERVED,
    )
    second_path = pattern_store.upsert_behavior_experience(
        action="在群聊中通过@麦麦并请求发色图来玩梗调侃",
        outcome="麦麦以拒绝方式回应，互动保持轻松搞笑",
        source_ids=["3", "4"],
        session_id="chat-a",
        scenario_profile=_profile(),
        scene_start="机器人玩梗互动",
        actor_type=pattern_store.ACTOR_OTHER_USER,
        learning_type=pattern_store.LEARNING_OBSERVED,
    )

    assert first_path is not None
    assert second_path is not None
    assert second_path.id != first_path.id
    assert first_path.count == 1
    assert second_path.count == 1

    with Session(behavior_reuse_engine) as session:
        stored_path = session.get(pattern_store.BehaviorExperiencePath, first_path.id)
        assert stored_path is not None
        evidence_items = pattern_store._load_json_list(stored_path.evidence_list)

    assert len(evidence_items) == 1
    assert evidence_items[0]["action"] == "在群聊中通过@麦麦并发送禁言请求来玩梗互动"
    assert evidence_items[0]["profile_tag_distribution"]
    assert any(
        str(item.get("tag") or "").startswith("need:")
        for item in evidence_items[0]["profile_tag_distribution"]
    )


def test_upsert_behavior_experience_does_not_reuse_across_chat_ids(
    monkeypatch: pytest.MonkeyPatch,
    behavior_reuse_engine,
) -> None:
    _patch_pattern_store_session(monkeypatch, behavior_reuse_engine)

    first_path = pattern_store.upsert_behavior_experience(
        action="在群聊中通过@麦麦并发送禁言请求来玩梗互动",
        outcome="引发麦麦调侃式回应，维持群聊搞笑氛围",
        source_ids=["1"],
        session_id="chat-a",
        scenario_profile=_profile(),
        scene_start="机器人玩梗互动",
        actor_type=pattern_store.ACTOR_OTHER_USER,
        learning_type=pattern_store.LEARNING_OBSERVED,
    )
    second_path = pattern_store.upsert_behavior_experience(
        action="在群聊中通过@麦麦并请求发色图来玩梗调侃",
        outcome="麦麦以拒绝方式回应，互动保持轻松搞笑",
        source_ids=["2"],
        session_id="chat-b",
        scenario_profile=_profile(),
        scene_start="机器人玩梗互动",
        actor_type=pattern_store.ACTOR_OTHER_USER,
        learning_type=pattern_store.LEARNING_OBSERVED,
    )

    assert first_path is not None
    assert second_path is not None
    assert second_path.id != first_path.id
    assert first_path.count == 1
    assert second_path.count == 1

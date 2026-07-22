from pathlib import Path
from typing import Any

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.utils import profile_policy


def _config_getter(values: dict[str, Any]):
    return lambda key, default=None: values.get(key, default)


def test_should_auto_enqueue_episode_respects_episode_switches() -> None:
    assert (
        profile_policy.should_auto_enqueue_episode(
            _config_getter({"episode.enabled": False}),
            source_type="chat_summary",
        )
        is False
    )
    assert (
        profile_policy.should_auto_enqueue_episode(
            _config_getter({"episode.generation_enabled": False}),
            source_type="chat_summary",
        )
        is False
    )


def test_should_auto_enqueue_episode_respects_disabled_source_types() -> None:
    config_getter = _config_getter({"episode.disabled_source_types": ["person_fact", "manual"]})

    assert profile_policy.should_auto_enqueue_episode(config_getter, source_type="manual") is False
    assert profile_policy.should_auto_enqueue_episode(config_getter, source_type="chat_summary") is True


def test_person_profile_refresh_policy_clamps_values() -> None:
    config_getter = _config_getter(
        {
            "person_profile.refresh_queue_interval_seconds": -5,
            "person_profile.refresh_queue_batch_size": -2,
            "person_profile.refresh_debounce_seconds": -3,
            "person_profile.refresh_retry_backoff_seconds": -4,
            "person_profile.max_retry": -1,
        }
    )

    assert profile_policy.person_profile_refresh_queue_interval_seconds(config_getter) == 1.0
    assert profile_policy.person_profile_refresh_queue_batch_size(config_getter) == 1
    assert profile_policy.person_profile_refresh_debounce_seconds(config_getter) == 0.0
    assert profile_policy.person_profile_refresh_retry_backoff_seconds(config_getter) == 0.0
    assert profile_policy.person_profile_refresh_max_retry(config_getter) == 0


def test_profile_policy_kernel_compatibility_wrappers() -> None:
    kernel = SDKMemoryKernel(
        plugin_root=Path.cwd(),
        config={
            "episode": {
                "disabled_source_types": ["person_fact"],
            },
            "person_profile": {
                "refresh_queue_interval_seconds": 12,
                "refresh_queue_batch_size": 4,
                "refresh_debounce_seconds": 6,
                "refresh_retry_backoff_seconds": 9,
                "max_retry": 2,
            },
        },
    )

    assert kernel._should_auto_enqueue_episode(source_type="chat_summary") is True
    assert kernel._should_auto_enqueue_episode(source_type="person_fact") is False
    assert kernel._person_profile_refresh_queue_interval_seconds() == 12.0
    assert kernel._person_profile_refresh_queue_batch_size() == 4
    assert kernel._person_profile_refresh_debounce_seconds() == 6.0
    assert kernel._person_profile_refresh_retry_backoff_seconds() == 9.0
    assert kernel._person_profile_refresh_max_retry() == 2

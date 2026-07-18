from __future__ import annotations

from typing import Any, Callable

from .runtime_payloads import argument_tokens

ConfigGetter = Callable[[str, Any], Any]


def should_auto_enqueue_episode(config_getter: ConfigGetter, *, source_type: str) -> bool:
    if not bool(config_getter("episode.enabled", True)):
        return False
    if not bool(config_getter("episode.generation_enabled", True)):
        return False

    normalized_source_type = str(source_type or "").strip().lower()
    disabled_types = {
        str(item or "").strip().lower()
        for item in argument_tokens(config_getter("episode.disabled_source_types", ["person_fact"]))
    }
    return normalized_source_type not in disabled_types


def person_profile_refresh_queue_interval_seconds(config_getter: ConfigGetter) -> float:
    return max(1.0, float(config_getter("person_profile.refresh_queue_interval_seconds", 60) or 60))


def person_profile_refresh_queue_batch_size(config_getter: ConfigGetter) -> int:
    return max(1, int(config_getter("person_profile.refresh_queue_batch_size", 10) or 10))


def person_profile_refresh_debounce_seconds(config_getter: ConfigGetter) -> float:
    return max(0.0, float(config_getter("person_profile.refresh_debounce_seconds", 120) or 0))


def person_profile_refresh_retry_backoff_seconds(config_getter: ConfigGetter) -> float:
    return max(0.0, float(config_getter("person_profile.refresh_retry_backoff_seconds", 300) or 0))


def person_profile_refresh_max_retry(config_getter: ConfigGetter) -> int:
    return max(0, int(config_getter("person_profile.max_retry", 3) or 0))

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel
from src.A_memorix.core.utils import feedback_policy


def test_feedback_policy_signal_and_noise_detection() -> None:
    assert feedback_policy.feedback_contains_signal("你记错了，实际是绿色")
    assert not feedback_policy.feedback_contains_signal("好的收到")

    assert feedback_policy.feedback_noise("")
    assert feedback_policy.feedback_noise("好的")
    assert not feedback_policy.feedback_noise("不是绿色，是蓝色")


def test_feedback_policy_config_accessors_clamp_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(
            feedback_correction_enabled=True,
            feedback_correction_window_hours=-5,
            feedback_correction_check_interval_minutes=-2,
            feedback_correction_batch_size=-3,
            feedback_correction_auto_apply_threshold=9,
            feedback_correction_max_feedback_messages=-4,
            feedback_correction_prefilter_enabled=False,
            feedback_correction_paragraph_mark_enabled=False,
            feedback_correction_paragraph_hard_filter_enabled=False,
            feedback_correction_profile_refresh_enabled=False,
            feedback_correction_profile_force_refresh_on_read=False,
            feedback_correction_episode_rebuild_enabled=False,
            feedback_correction_episode_query_block_enabled=False,
            feedback_correction_reconcile_interval_minutes=-5,
            feedback_correction_reconcile_batch_size=-6,
            fuzzy_modify_enabled=False,
            fuzzy_modify_auto_execute_enabled=True,
            fuzzy_modify_confirm_threshold=0.72,
            fuzzy_modify_candidate_limit=-7,
            fuzzy_modify_max_targets=-8,
            fuzzy_modify_allow_global_scope=True,
        ),
    )

    assert feedback_policy.feedback_cfg_enabled()
    assert feedback_policy.feedback_cfg_window_hours() == 0.1
    assert feedback_policy.feedback_cfg_check_interval_seconds() == 60.0
    assert feedback_policy.feedback_cfg_batch_size() == 1
    assert feedback_policy.feedback_cfg_auto_apply_threshold() == 1.0
    assert feedback_policy.feedback_cfg_max_messages() == 1
    assert not feedback_policy.feedback_cfg_prefilter_enabled()
    assert not feedback_policy.feedback_cfg_paragraph_mark_enabled()
    assert not feedback_policy.feedback_cfg_paragraph_hard_filter_enabled()
    assert not feedback_policy.feedback_cfg_profile_refresh_enabled()
    assert not feedback_policy.feedback_cfg_profile_force_refresh_on_read()
    assert not feedback_policy.feedback_cfg_episode_rebuild_enabled()
    assert not feedback_policy.feedback_cfg_episode_query_block_enabled()
    assert feedback_policy.feedback_cfg_reconcile_interval_seconds() == 60.0
    assert feedback_policy.feedback_cfg_reconcile_batch_size() == 1

    assert not feedback_policy.fuzzy_modify_cfg_enabled()
    assert feedback_policy.fuzzy_modify_cfg_auto_execute_enabled()
    assert feedback_policy.fuzzy_modify_cfg_confirm_threshold() == 0.72
    assert feedback_policy.fuzzy_modify_cfg_candidate_limit() == 1
    assert feedback_policy.fuzzy_modify_cfg_max_targets() == 1
    assert feedback_policy.fuzzy_modify_cfg_allow_global_scope()


def test_feedback_policy_window_label_uses_compact_hour_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(feedback_correction_window_hours=2),
    )
    assert feedback_policy.feedback_cfg_window_label() == "2h"

    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(feedback_correction_window_hours=1.5),
    )
    assert feedback_policy.feedback_cfg_window_label() == "1.50h"


def test_feedback_policy_kernel_and_service_compatibility_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feedback_policy,
        "_integration_config",
        lambda: SimpleNamespace(
            feedback_correction_enabled=True,
            feedback_correction_window_hours=3,
            feedback_correction_batch_size=7,
            fuzzy_modify_enabled=True,
            fuzzy_modify_candidate_limit=11,
            fuzzy_modify_max_targets=4,
        ),
    )

    kernel = SDKMemoryKernel(plugin_root=Path.cwd(), config={})

    assert kernel._feedback_contains_signal("不对，应该是红色")
    assert kernel._feedback_cfg_enabled()
    assert kernel._feedback_cfg_window_hours() == 3.0
    assert kernel._feedback_cfg_batch_size() == 7
    assert kernel._feedback_cfg_window_label() == "3h"
    assert kernel._fuzzy_modify_cfg_candidate_limit() == 11
    assert kernel._fuzzy_modify_cfg_max_targets() == 4

    correction_service = kernel._correction_admin_service
    assert correction_service._fuzzy_modify_cfg_enabled()
    assert correction_service._fuzzy_modify_cfg_candidate_limit() == 11
    assert correction_service._fuzzy_modify_cfg_max_targets() == 4

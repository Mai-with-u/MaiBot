

from src.A_memorix.core.utils import feedback_policy


def test_feedback_policy_signal_and_noise_detection() -> None:
    assert feedback_policy.feedback_contains_signal("你记错了，实际是绿色")
    assert not feedback_policy.feedback_contains_signal("好的收到")

    assert feedback_policy.feedback_noise("")
    assert feedback_policy.feedback_noise("好的")
    assert not feedback_policy.feedback_noise("不是绿色，是蓝色")

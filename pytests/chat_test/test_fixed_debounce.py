import pytest

from src.chat.debounce import MessageDebouncer


def _observe(debouncer, time, *, author="alice", image=False, emoji=False, at=False, lag=0, mode="natural"):
    debouncer.observe(
        author=author,
        is_bare_image=image,
        is_pure_emoji=emoji,
        at_bot=at,
        received_at=1790000000 + time - lag,
        wall_time=1790000000 + time,
        now=1000 + time,
        mode=mode,
    )


def test_first_message_waits_two_seconds():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    assert debouncer.deadline == 1002


@pytest.mark.parametrize("gap,wait", [(5, 5), (30, 5), (30.001, 2), (300, 2)])
def test_activity_boundary_uses_previous_message_across_batches(gap, wait):
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    debouncer.consume()
    _observe(debouncer, gap)
    assert debouncer.deadline == pytest.approx(1000 + gap + wait)


def test_continuations_follow_the_immediately_previous_sender():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    _observe(debouncer, 1)
    assert debouncer.deadline == 1003
    _observe(debouncer, 1.5, author="bob")
    assert debouncer.deadline == 1002
    _observe(debouncer, 1.75, author="bob")
    assert debouncer.deadline == 1003.75
    assert debouncer.changed.is_set()


def test_bare_image_only_has_a_floor_when_opening_a_batch():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0, image=True)
    assert debouncer.deadline == 1004
    _observe(debouncer, 1, author="bob")
    assert debouncer.deadline == 1001.5  # 后来的文字可以提前结束。
    debouncer.consume()
    _observe(debouncer, 10, image=True)
    assert debouncer.deadline == 1015  # 活跃时 5 秒，不缩成 4 秒。
    debouncer.consume()
    _observe(debouncer, 50)
    _observe(debouncer, 51, image=True)
    assert debouncer.deadline == 1053  # 中途来图不另加 4 秒。


def test_pure_emoji_cannot_extend_but_can_shorten_existing_batch():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    _observe(debouncer, 1, emoji=True)
    assert debouncer.deadline == 1002
    _observe(debouncer, 1.2, author="bob", emoji=True)
    assert debouncer.deadline == 1001.7


def test_pure_emoji_can_open_a_normal_batch():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0, emoji=True)
    assert debouncer.deadline == 1002
    debouncer.consume()
    _observe(debouncer, 5, emoji=True)
    assert debouncer.deadline == 1010


def test_continuous_messages_never_reset_eight_second_cap():
    debouncer = MessageDebouncer()
    for time in [0, 1.5, 3, 4.5, 6, 7.5]:
        _observe(debouncer, time)
    assert debouncer.deadline == 1008
    _observe(debouncer, 9)
    assert debouncer.deadline == 1008  # 到期但忙碌未消费时，不能重新续长。
    debouncer.consume()
    _observe(debouncer, 10)
    assert debouncer.deadline == 1015


def test_earliest_mention_limit_wins_over_later_messages():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    _observe(debouncer, 0.5, at=True)
    assert debouncer.deadline == 1001.5
    _observe(debouncer, 1, at=True)
    assert debouncer.deadline == 1001.5
    debouncer.consume()
    _observe(debouncer, 10, image=True, at=True)
    assert debouncer.deadline == 1011  # @ 上限优先于裸图等待下限。


def test_preprocessing_time_is_not_waited_again():
    debouncer = MessageDebouncer()
    _observe(debouncer, 20, lag=6)
    assert debouncer.deadline == 1016  # 收到后 2 秒，已过期，立即可消费。
    debouncer.consume()
    _observe(debouncer, 30, lag=3, at=True)
    assert debouncer.deadline == 1028


def test_late_media_does_not_move_activity_clock_backwards():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0)
    _observe(debouncer, 1, lag=5)
    assert debouncer.deadline == 1002
    debouncer.consume()
    _observe(debouncer, 30.5)
    assert debouncer.deadline == 1032.5  # 上一条有效时间仍为 0，间隔已超过 30 秒。


def test_sessions_have_independent_activity_and_deadlines():
    first, second = MessageDebouncer(), MessageDebouncer()
    _observe(first, 0)
    first.consume()
    _observe(first, 5)
    _observe(second, 5)
    assert first.deadline == 1010
    assert second.deadline == 1007


@pytest.mark.parametrize(
    "mode,quiet,active,same,different",
    [
        ("fast", 1, 3, 1, 0.5),
        ("natural", 2, 5, 2, 0.5),
        ("steady", 4, 7, 4, 2),
    ],
)
def test_three_modes_only_change_wait_times(mode, quiet, active, same, different):
    debouncer = MessageDebouncer()
    _observe(debouncer, 0, mode=mode)
    assert debouncer.deadline == 1000 + quiet
    debouncer.consume()
    _observe(debouncer, 5, mode=mode)
    assert debouncer.deadline == 1005 + active
    _observe(debouncer, 5.25, mode=mode)
    assert debouncer.deadline == 1005.25 + same
    _observe(debouncer, 5.5, author="bob", mode=mode)
    assert debouncer.deadline == 1005.5 + different


@pytest.mark.parametrize("mode", ["fast", "natural", "steady"])
def test_special_rules_and_caps_apply_in_every_mode(mode):
    debouncer = MessageDebouncer()
    _observe(debouncer, 0, image=True, mode=mode)
    assert debouncer.deadline == 1004
    old = debouncer.deadline
    _observe(debouncer, 3.75, emoji=True, mode=mode)
    assert debouncer.deadline <= old
    debouncer.consume()
    _observe(debouncer, 10, mode=mode)
    for tick in range(1, 16):
        _observe(debouncer, 10 + tick * 0.5, mode=mode)
    assert debouncer.deadline <= 1018
    debouncer.consume()
    _observe(debouncer, 30, image=True, at=True, mode=mode)
    assert debouncer.deadline == 1031


def test_switching_modes_does_not_restart_batch_cap():
    debouncer = MessageDebouncer()
    _observe(debouncer, 0, mode="natural")
    for tick in range(1, 8):
        _observe(debouncer, tick, mode="steady")
    assert debouncer.deadline == 1008
    _observe(debouncer, 7.5, mode="fast")
    assert debouncer.deadline == 1008

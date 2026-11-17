import pytest

from src.chat.debounce import MessageDebouncer


@pytest.mark.parametrize(
    "is_group,mode,quiet,active,same,different,batch,mention",
    [
        (True, "fast", 1, 3, 1, 0.5, 8, 1),
        (True, "natural", 2.5, 6, 2.5, 1, 10, 2),
        (True, "steady", 5, 9, 5, 3, 12, 3.5),
        (False, "fast", 1, 3, 1, 0.5, 8, 1),
        (False, "natural", 2, 5, 2, 0.5, 8, 1),
        (False, "steady", 4, 7, 4, 2, 8, 1),
    ],
)
def test_chat_debounce_profiles(is_group, mode, quiet, active, same, different, batch, mention):
    debouncer = MessageDebouncer()

    def receive(now: float, author: str = "a", at_bot: bool = False) -> None:
        debouncer.observe(
            author=author, is_bare_image=False, is_pure_emoji=False, at_bot=at_bot,
            received_at=now, wall_time=now, now=now, mode=mode, is_group=is_group,
        )

    receive(100)
    assert debouncer.deadline == 100 + quiet
    debouncer.consume()
    receive(110)
    assert debouncer.deadline == 110 + active
    receive(110.1)
    assert debouncer.deadline == 110.1 + same
    receive(110.2, author="b")
    assert debouncer.deadline == 110.2 + different
    for step in range(3, int(batch * 10)):
        receive(110 + step / 10, author="b")
    assert debouncer.deadline == 110 + batch

    debouncer.consume()
    receive(130, at_bot=True)
    assert debouncer.deadline == 130 + mention


def test_switching_group_mode_does_not_extend_existing_batch_limit():
    debouncer = MessageDebouncer()
    for step in range(120):
        now = 100 + step / 10
        debouncer.observe(
            author="a", is_bare_image=False, is_pure_emoji=False, at_bot=False,
            received_at=now, wall_time=now, now=now,
            mode="natural" if step == 0 else "steady", is_group=True,
        )
    assert debouncer.deadline == 110

from typing import Callable, Dict, Generic, Iterable, List, TypeVar


T = TypeVar("T")
MAX_PENDING_LEARNING_MESSAGES = 100


class PendingLearningBuffer(Generic[T]):
    """按消息 ID 去重的会话学习缓冲；超限淘汰最旧素材，避免长期积压。"""

    def __init__(self, message_id: Callable[[T], str]) -> None:
        self._message_id = message_id
        self._items: Dict[str, T] = {}
        self.dropped_count = 0

    def extend(self, items: Iterable[T]) -> None:
        for item in items:
            self._items.setdefault(self._message_id(item), item)
            if len(self._items) > MAX_PENDING_LEARNING_MESSAGES:
                del self._items[next(iter(self._items))]
                self.dropped_count += 1

    def snapshot(self) -> List[T]:
        return list(self._items.values())

    def discard(self, items: Iterable[T]) -> None:
        """只消费已取得执行名额的批次，不影响运行期间收进来的新素材。"""
        for item in items:
            self._items.pop(self._message_id(item), None)
        self.dropped_count = 0

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self.dropped_count = 0

    def waiting_detail(self, minimum: int) -> str:
        reason = "等待下次裁切补足" if len(self) < minimum else "已达到门槛，等待执行条件允许"
        detail = f"已累计 {len(self)}/{minimum} 条有效消息，{reason}（最多保留 {MAX_PENDING_LEARNING_MESSAGES} 条）"
        if self.dropped_count:
            detail += f"；已淘汰最旧的 {self.dropped_count} 条素材"
        return detail

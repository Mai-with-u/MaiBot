"""管理普通收发消息的引用计数与快照，不扫描上下文或查询数据库。"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from src.chat.message_receive.message import SessionMessage


@dataclass(frozen=True)
class ReplyQuoteContext:
    # None 仅表示已确认记录过、但已离开最近 100 条窗口的目标。
    target_distance: int | None
    previous_sender_id: str
    previous_seconds: float
    previous_burst: int


class ReplyQuoteCounter:
    """按进入 runtime / 成功发送的顺序计数；普通消息只记录一次。

    最近窗口只保存 100 条 ID/序号，不持有消息正文。连续条数最多保留公式
    所需的 16；消息对象上的当前实例标记可区分已淘汰目标和未记录目标。
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._token = object()
        self._positions: OrderedDict[str, int] = OrderedDict()
        self._sequence = 0
        self._sender_id = ""
        self._previous_time: datetime | None = None
        self._burst = 0

    def record(self, message: SessionMessage) -> None:
        if message.session_id != self.session_id:
            raise ValueError("引用计数器不能记录其他会话的消息")
        if message.is_notify:
            return
        if not message.message_id:
            raise ValueError("引用计数消息缺少 message_id")
        position = message._quote_counter_position
        if position is not None and position[0] is self._token:
            return
        if message.message_id in self._positions:
            message._quote_counter_position = (self._token, self._positions[message.message_id])
            return

        self._sequence += 1
        message._quote_counter_position = (self._token, self._sequence)
        self._positions[message.message_id] = self._sequence
        if len(self._positions) > 100:
            self._positions.popitem(last=False)
        sender_id = message.message_info.user_info.user_id
        self._burst = min(self._burst + 1, 16) if sender_id == self._sender_id else 1
        self._sender_id = sender_id
        # 出站消息没有 received_at，使用它自身的发送时间。
        self._previous_time = message.received_at or message.timestamp

    def snapshot(self, target: SessionMessage, *, now: datetime | None = None) -> ReplyQuoteContext | None:
        position = target._quote_counter_position
        if target.session_id != self.session_id or target.is_notify:
            raise ValueError("引用目标必须是当前会话的普通消息")
        # 历史回填可能重新构造同一消息对象；只按窗口内已知 ID 关联序号，
        # 不把回填消息作为新发言计数。合法目标无法恢复序号时返回 None，
        # 交给引用规则直接引用，不虚构距离或改变连发状态。
        if target.message_id in self._positions:
            position = (self._token, self._positions[target.message_id])
        if position is None or position[0] is not self._token:
            return None
        if self._previous_time is None:
            raise ValueError("引用计数器尚未记录普通消息")
        current_time = now if now is not None else datetime.now(tz=self._previous_time.tzinfo)
        distance = self._sequence - position[1]
        return ReplyQuoteContext(
            target_distance=distance if distance < 100 else None,
            previous_sender_id=self._sender_id,
            previous_seconds=max(0.0, (current_time - self._previous_time).total_seconds()),
            previous_burst=self._burst,
        )

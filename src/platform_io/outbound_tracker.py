"""跟踪 Platform IO 层的出站投递状态。

当前实现基于两组 ``dict + heapq``：
- ``_pending`` 和 ``_pending_expire_heap`` 负责管理待完成的出站记录
- ``_receipts_by_external_id`` 和 ``_receipt_expire_heap`` 负责管理已完成回执索引

这样就不需要在每次读写时全表扫描过期项，而是通过懒清理逐步弹出已经过期
或已经失效的堆节点。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import heapq
import time

from .types import DeliveryReceipt, RouteKey


@dataclass(slots=True)
class PendingOutboundRecord:
    """表示一条仍在等待完成的出站投递记录。

    Attributes:
        internal_message_id: 正在跟踪的内部 ``SessionMessage.message_id``。
        route_key: 该出站投递开始时使用的路由键。
        driver_id: 负责这次出站投递的驱动 ID。
        created_at: 开始跟踪时记录的单调时钟时间戳。
        expires_at: 该待完成记录预计过期的单调时钟时间戳。
        metadata: 与待完成记录一同保留的额外 Broker 侧元数据。
    """

    internal_message_id: str
    route_key: RouteKey
    driver_id: str
    created_at: float = field(default_factory=time.monotonic)
    expires_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StoredDeliveryReceipt:
    """表示一条已完成并暂存的出站回执。

    Attributes:
        receipt: 规范化后的出站投递回执。
        stored_at: 回执被写入索引时记录的单调时钟时间戳。
        expires_at: 该回执索引预计过期的单调时钟时间戳。
    """

    receipt: DeliveryReceipt
    stored_at: float = field(default_factory=time.monotonic)
    expires_at: float = 0.0


class OutboundTracker:
    """统一跟踪出站消息的 pending 状态与最终回执。

    主要用于解决出站消息在发送过程中“状态散落在不同路径里”的问题：
    - 发送开始后，需要在最终回执返回前保留一份 pending 状态
    - 平台返回 ``external_message_id`` 后，需要保留一段时间的回执索引

    当前实现使用 ``dict + heapq`` 做 TTL 管理：
    - ``dict`` 提供 ``O(1)`` 级别的主键查询
    - ``heapq`` 提供按过期时间排序的懒清理能力

    这比“每次 begin/finish/get 都全表扫描”的实现更适合高吞吐出站场景。

    Notes:
        复杂度说明如下，设 ``p`` 为当前有效 pending 数量，``r`` 为当前有效回执数量：

        - ``begin_tracking()``、``finish_tracking()`` 的常见路径时间复杂度接近
          ``O(log p)`` 或 ``O(log r)``
        - ``get_pending()``、``get_receipt_by_external_id()`` 的查询本身是 ``O(1)``
          ，连同懒清理一起看，长期摊还复杂度接近 ``O(log n)``
        - 如果某次调用恰好触发一批过期节点的集中清理，则该次调用的最坏时间复杂度
          可达到 ``O(k log n)``，其中 ``k`` 为本次被弹出的节点数量
        - 空间复杂度为 ``O(p + r)``
    """

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        """初始化出站跟踪器。

        Args:
            ttl_seconds: 待完成记录与按外部消息 ID 建立的回执索引保留时长，
                单位为秒。

        Raises:
            ValueError: 当 ``ttl_seconds`` 非正数时抛出。
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")

        self._ttl_seconds = ttl_seconds
        self._pending: Dict[str, PendingOutboundRecord] = {}
        self._pending_expire_heap: List[Tuple[float, str]] = []
        self._receipts_by_external_id: Dict[str, StoredDeliveryReceipt] = {}
        self._receipt_expire_heap: List[Tuple[float, str]] = []

    def begin_tracking(
        self,
        internal_message_id: str,
        route_key: RouteKey,
        driver_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PendingOutboundRecord:
        """开始跟踪一次出站投递。

        Args:
            internal_message_id: 正在投递的内部消息 ID。
            route_key: 这次出站投递选择的路由键。
            driver_id: 负责本次投递的驱动 ID。
            metadata: 可选的额外元数据，会一并保存在待完成记录中。

        Returns:
            PendingOutboundRecord: 新创建的待完成记录。

        Raises:
            ValueError: 当同一个 ``internal_message_id`` 已经存在未完成记录时抛出。
        """
        now = time.monotonic()
        self._cleanup_expired(now)

        if internal_message_id in self._pending:
            raise ValueError(f"消息 {internal_message_id} 已存在未完成的出站跟踪记录")

        expires_at = now + self._ttl_seconds
        record = PendingOutboundRecord(
            internal_message_id=internal_message_id,
            route_key=route_key,
            driver_id=driver_id,
            created_at=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        self._pending[internal_message_id] = record
        heapq.heappush(self._pending_expire_heap, (expires_at, internal_message_id))
        return record

    def finish_tracking(self, receipt: DeliveryReceipt) -> Optional[PendingOutboundRecord]:
        """使用最终回执结束一条出站跟踪。

        Args:
            receipt: 规范化后的最终投递回执。

        Returns:
            Optional[PendingOutboundRecord]: 若此前存在待完成记录，则返回该记录。
        """
        now = time.monotonic()
        self._cleanup_expired(now)

        pending_record = self._pending.pop(receipt.internal_message_id, None)
        if receipt.external_message_id:
            expires_at = now + self._ttl_seconds
            self._receipts_by_external_id[receipt.external_message_id] = StoredDeliveryReceipt(
                receipt=receipt,
                stored_at=now,
                expires_at=expires_at,
            )
            heapq.heappush(self._receipt_expire_heap, (expires_at, receipt.external_message_id))
        return pending_record

    def get_pending(self, internal_message_id: str) -> Optional[PendingOutboundRecord]:
        """根据内部消息 ID 查询待完成记录。

        Args:
            internal_message_id: 要查询的内部消息 ID。

        Returns:
            Optional[PendingOutboundRecord]: 若记录仍存在，则返回对应待完成记录。
        """
        self._cleanup_expired(time.monotonic())
        return self._pending.get(internal_message_id)

    def get_receipt_by_external_id(self, external_message_id: str) -> Optional[DeliveryReceipt]:
        """根据外部平台消息 ID 查询已完成回执。

        Args:
            external_message_id: 要查询的平台侧消息 ID。

        Returns:
            Optional[DeliveryReceipt]: 若存在对应回执，则返回该回执。
        """
        self._cleanup_expired(time.monotonic())
        stored_receipt = self._receipts_by_external_id.get(external_message_id)
        return stored_receipt.receipt if stored_receipt else None

    def clear(self) -> None:
        """清空全部待完成记录与已保存回执。"""
        self._pending.clear()
        self._pending_expire_heap.clear()
        self._receipts_by_external_id.clear()
        self._receipt_expire_heap.clear()

    def _cleanup_expired(self, now: float) -> None:
        """清理内存中已经过期的待完成记录与已保存回执。

        Args:
            now: 当前单调时钟时间戳。
        """
        self._cleanup_expired_pending(now)
        self._cleanup_expired_receipts(now)

    def _cleanup_expired_pending(self, now: float) -> None:
        """清理已经过期的待完成记录。

        Args:
            now: 当前单调时钟时间戳。

        Notes:
            堆中可能存在已经失效的旧节点。例如某条记录提前 ``finish`` 后，
            它原本的过期节点仍可能留在堆里。这里会通过和 ``dict`` 中当前记录的
            ``expires_at`` 对比，跳过这类旧节点。
        """
        while self._pending_expire_heap and self._pending_expire_heap[0][0] <= now:
            expires_at, internal_message_id = heapq.heappop(self._pending_expire_heap)
            current_record = self._pending.get(internal_message_id)
            if current_record is None:
                continue
            if current_record.expires_at != expires_at:
                continue
            self._pending.pop(internal_message_id, None)

    def _cleanup_expired_receipts(self, now: float) -> None:
        """清理已经过期的回执索引。

        Args:
            now: 当前单调时钟时间戳。

        Notes:
            同一个 ``external_message_id`` 在极端情况下可能被重复写入索引，
            因此这里同样需要通过 ``expires_at`` 和当前 ``dict`` 中的值比对，
            跳过已经失效的旧堆节点。
        """
        while self._receipt_expire_heap and self._receipt_expire_heap[0][0] <= now:
            expires_at, external_message_id = heapq.heappop(self._receipt_expire_heap)
            current_receipt = self._receipts_by_external_id.get(external_message_id)
            if current_receipt is None:
                continue
            if current_receipt.expires_at != expires_at:
                continue
            self._receipts_by_external_id.pop(external_message_id, None)

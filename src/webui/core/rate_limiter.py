"""
WebUI 请求频率限制模块
防止暴力破解和 API 滥用
"""

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from src.common.logger import get_logger

logger = get_logger("webui.rate_limiter")


class RateLimiter:
    """
    简单的内存请求频率限制器

    使用滑动窗口算法实现
    """

    # 追踪的请求键（IP/规则组合）数量上限：公网环境下 IP 数可能持续增长，
    # 超过上限时清理过期键并淘汰最旧键，防止字典无界膨胀。
    MAX_TRACKED_REQUEST_KEYS = 10000

    def __init__(self):
        # 存储格式: {key: [(timestamp, count), ...]}
        self._requests: Dict[str, List] = defaultdict(list)
        # 被封禁的 IP: {ip: unblock_timestamp}
        self._blocked: Dict[str, float] = {}

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端 IP 地址"""
        # 检查代理头
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # 取第一个 IP（最原始的客户端）
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # 直接连接的客户端
        if request.client:
            return request.client.host

        return "unknown"

    def _cleanup_old_requests(self, key: str, window_seconds: int):
        """清理过期的请求记录"""
        now = time.time()
        cutoff = now - window_seconds
        self._requests[key] = [(ts, count) for ts, count in self._requests[key] if ts > cutoff]
        # 窗口内已无记录的键直接删除，避免空键随历史 IP 数无界积累。
        if not self._requests[key]:
            del self._requests[key]
        self._enforce_request_key_limit(cutoff)

    def _enforce_request_key_limit(self, cutoff: float) -> None:
        """限制追踪键总数：超限时先清除全部过期键，仍超额则按插入顺序淘汰最旧键。"""

        if len(self._requests) <= self.MAX_TRACKED_REQUEST_KEYS:
            return
        stale_keys = [
            stale_key
            for stale_key, records in self._requests.items()
            if all(ts <= cutoff for ts, _ in records)
        ]
        for stale_key in stale_keys:
            del self._requests[stale_key]
        overflow = len(self._requests) - self.MAX_TRACKED_REQUEST_KEYS
        if overflow <= 0:
            return
        # 优先淘汰常规请求键，保护处于活跃窗口内的认证失败记录，防止防爆破策略被大流量冲刷弱化。
        for stale_key in list(self._requests):
            if overflow <= 0:
                break
            if stale_key.endswith(":auth_failures"):
                continue
            del self._requests[stale_key]
            overflow -= 1
        if overflow > 0:
            for stale_key in list(self._requests)[:overflow]:
                del self._requests[stale_key]

    def _cleanup_expired_blocks(self):
        """清理过期的封禁"""
        now = time.time()
        expired = [ip for ip, unblock_time in self._blocked.items() if now > unblock_time]
        for ip in expired:
            del self._blocked[ip]
            logger.info(f"🔓 IP {ip} 封禁已解除")

    def is_blocked(self, request: Request) -> Tuple[bool, Optional[int]]:
        """
        检查 IP 是否被封禁

        Returns:
            (是否被封禁, 剩余封禁秒数)
        """
        self._cleanup_expired_blocks()
        ip = self._get_client_ip(request)

        if ip in self._blocked:
            remaining = int(self._blocked[ip] - time.time())
            return True, max(0, remaining)

        return False, None

    def check_rate_limit(
        self, request: Request, max_requests: int, window_seconds: int, key_suffix: str = ""
    ) -> Tuple[bool, int]:
        """
        检查请求是否超过频率限制

        Args:
            request: FastAPI Request 对象
            max_requests: 窗口期内允许的最大请求数
            window_seconds: 窗口时间（秒）
            key_suffix: 键后缀，用于区分不同的限制规则

        Returns:
            (是否允许, 剩余请求数)
        """
        ip = self._get_client_ip(request)
        key = f"{ip}:{key_suffix}" if key_suffix else ip

        # 清理过期记录
        self._cleanup_old_requests(key, window_seconds)

        # 计算当前窗口内的请求数
        current_count = sum(count for _, count in self._requests[key])

        if current_count >= max_requests:
            return False, 0

        # 记录新请求
        now = time.time()
        self._requests[key].append((now, 1))

        remaining = max_requests - current_count - 1
        return True, remaining

    def block_ip(self, request: Request, duration_seconds: int):
        """
        封禁 IP

        Args:
            request: FastAPI Request 对象
            duration_seconds: 封禁时长（秒）
        """
        ip = self._get_client_ip(request)
        self._blocked[ip] = time.time() + duration_seconds
        logger.warning(f"🔒 IP {ip} 已被封禁 {duration_seconds} 秒")

    def record_failed_attempt(
        self, request: Request, max_failures: int = 5, window_seconds: int = 300, block_duration: int = 600
    ) -> Tuple[bool, int]:
        """
        记录失败尝试（如登录失败）

        如果在窗口期内失败次数过多，自动封禁 IP

        Args:
            request: FastAPI Request 对象
            max_failures: 允许的最大失败次数
            window_seconds: 统计窗口（秒）
            block_duration: 封禁时长（秒）

        Returns:
            (是否被封禁, 剩余尝试次数)
        """
        ip = self._get_client_ip(request)
        key = f"{ip}:auth_failures"

        # 清理过期记录
        self._cleanup_old_requests(key, window_seconds)

        # 计算当前失败次数
        current_failures = sum(count for _, count in self._requests[key])

        # 记录本次失败
        now = time.time()
        self._requests[key].append((now, 1))
        current_failures += 1

        remaining = max_failures - current_failures

        # 检查是否需要封禁
        if current_failures >= max_failures:
            self.block_ip(request, block_duration)
            logger.warning(f"⚠️ IP {ip} 认证失败次数过多 ({current_failures}/{max_failures})，已封禁")
            return True, 0

        if current_failures >= max_failures - 2:
            logger.warning(f"⚠️ IP {ip} 认证失败 {current_failures}/{max_failures} 次")

        return False, max(0, remaining)

    def reset_failures(self, request: Request):
        """
        重置失败计数（认证成功后调用）
        """
        ip = self._get_client_ip(request)
        key = f"{ip}:auth_failures"
        if key in self._requests:
            del self._requests[key]


# 全局单例
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """获取 RateLimiter 单例"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


async def check_auth_rate_limit(request: Request):
    """
    认证接口的频率限制依赖

    规则：
    - 每个 IP 每分钟最多 10 次认证请求
    - 连续失败 5 次后封禁 10 分钟
    """
    limiter = get_rate_limiter()

    # 检查是否被封禁
    blocked, remaining_block = limiter.is_blocked(request)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请在 {remaining_block} 秒后重试",
            headers={"Retry-After": str(remaining_block)},
        )

    # 检查频率限制
    allowed, remaining = limiter.check_rate_limit(
        request,
        max_requests=10,  # 每分钟 10 次
        window_seconds=60,
        key_suffix="auth",
    )

    if not allowed:
        raise HTTPException(status_code=429, detail="认证请求过于频繁，请稍后重试", headers={"Retry-After": "60"})


async def check_api_rate_limit(request: Request):
    """
    普通 API 的频率限制依赖

    规则：每个 IP 每分钟最多 100 次请求
    """
    limiter = get_rate_limiter()

    # 检查是否被封禁
    blocked, remaining_block = limiter.is_blocked(request)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请在 {remaining_block} 秒后重试",
            headers={"Retry-After": str(remaining_block)},
        )

    # 检查频率限制
    allowed, _ = limiter.check_rate_limit(
        request,
        max_requests=100,  # 每分钟 100 次
        window_seconds=60,
        key_suffix="api",
    )

    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试", headers={"Retry-After": "60"})

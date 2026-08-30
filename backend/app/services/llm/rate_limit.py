"""令牌桶速率限制器"""

import asyncio
import time


class RateLimiter:
    """令牌桶速率限制器

    控制 API 请求速率，避免触发 429 Too Many Requests。
    使用令牌桶算法：以恒定速率补充令牌，请求前消耗一个令牌，
    无可用令牌时等待。

    Attributes:
        max_rpm: 每分钟最大请求数
        _tokens: 当前可用令牌数
        _last_refill: 上次补充令牌的时间戳
        _lock: 异步锁，保证线程安全
    """

    def __init__(self, max_rpm: int = 10):
        self.max_rpm = max_rpm
        self._tokens = float(max_rpm)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取一个令牌，等待直到有可用令牌"""
        if self.max_rpm <= 0:
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.max_rpm, self._tokens + elapsed * (self.max_rpm / 60.0))
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1.0 - self._tokens) * (60.0 / self.max_rpm)
            await asyncio.sleep(wait_time)
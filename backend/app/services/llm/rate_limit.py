"""令牌桶速率限制器

阶段 4.4：从"只有一个进程级桶"扩展为"可以按 key 分桶"（按用户、按供应商），
因为**一个桶意味着一个用户可以饿死其他所有人**。

本模块只提供数据结构，不含任何配置读取 —— 桶的 rpm 由调用方传入。
这样 4.4 的公平性策略（谁先谁后、默认开不开）全部留在网关一处，
不会散落到这里。
"""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger("engramnote.llm")


class RateLimiter:
    """令牌桶速率限制器（单个桶）

    控制 API 请求速率，避免触发 429 Too Many Requests。
    使用令牌桶算法：以恒定速率补充令牌，请求前消耗一个令牌，
    无可用令牌时等待。

    Attributes:
        max_rpm: 每分钟最大请求数（<= 0 表示不限，`acquire` 直接返回）
        _tokens: 当前可用令牌数
        _last_refill: 上次补充令牌的时间戳
        _lock: 异步锁，串行化"补充 + 消耗"这段临界区
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


class KeyedRateLimiter:
    """按 key 分桶的令牌桶集合（阶段 4.4）

    ## 为什么需要它

    4.0 的实现只有一个进程级桶（10 RPM）。那在"只有一个人在用"时没问题，
    但它同时是**总闸门**和**单人闸门**：任何一个用户把桶占满，
    其他人的请求就只能在 `asyncio.sleep` 里排队 —— 表现为"别人一跑理解任务，
    我的问答就转圈"，而且从日志上看不出是谁造成的。

    分桶之后，每个用户有自己的桶，"某个用户超量"只会拖慢他自己。

    ## `max_rpm <= 0` 表示"不限"，且**不建桶**

    不建桶有两个好处：默认关闭时零开销（连一次字典写入都没有），
    以及"不限"不会在内存里留下永远不会被读到的空桶。

    ## 桶的数量上界 = key 的基数

    key 只允许用**低基数**的东西（用户 id、供应商名）。若传入每请求都不同的
    key（如 note_id、请求 id），桶会无界增长 —— 那是内存泄漏，不是限流。
    因此调用方必须保证 key 的低基数性，本类不做校验（校验也拦不住设计错误）。
    """

    def __init__(self):
        self._buckets: Dict[str, RateLimiter] = {}

    async def acquire(self, key: str, max_rpm: int) -> None:
        """获取 `key` 对应桶里的一个令牌（`max_rpm <= 0` 时直接放行）"""
        if max_rpm <= 0:
            return
        bucket = self._buckets.get(key)
        if bucket is None or bucket.max_rpm != max_rpm:
            # rpm 变了就换桶：旧桶的余量与补充速率都属于旧配置，
            # 继续用它会让"调小限额"在桶耗尽前不生效。
            bucket = RateLimiter(max_rpm=max_rpm)
            self._buckets[key] = bucket
        await bucket.acquire()

    @property
    def bucket_count(self) -> int:
        """当前已建桶数（供测试与诊断用）"""
        return len(self._buckets)


__all__ = ["KeyedRateLimiter", "RateLimiter"]

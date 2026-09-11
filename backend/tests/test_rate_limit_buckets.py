"""阶段 4.4：分桶限流测试

## 这份测试要证明什么

4.4 的命题是"单用户不饿死他人"。它的反面（改造前的实现）是：
**只有一个进程级桶**，它同时充当总闸门和单人闸门 ——
一个人跑理解任务把桶占满，别人的问答就只能排队。

限流最容易骗人的地方是"看起来限了，其实限错了对象"：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 所有用户共用一个桶 | 一个人拖慢所有人（= 改造前的状态） | `test_users_have_separate_buckets` |
| `max_rpm=0` 也建桶 | "不限"变成"限 0 次"（死锁）或留下空桶 | `test_zero_means_unlimited_and_creates_no_bucket` |
| 桶按 rpm 缓存但不感知变化 | 调小限额在桶耗尽前不生效 | `test_rpm_change_rebuilds_bucket` |
| 无用户上下文的调用免检 | "没接上下文"成为绕过限流的办法 | `test_anonymous_calls_share_one_bucket` |

## 为什么用"虚拟时钟"而不是"把 sleep 换成空操作"

只替换 `asyncio.sleep` 会让 `RateLimiter` 的补充逻辑永远等不到令牌 ——
它靠 `time.monotonic()` 计算补充量，真实时间不前进就永远补不满，
于是 `while True` 会一直空转（本轮实测：rpm=1 的用例真的空转了 60 秒才通过，
整个文件从 0.1 秒变成 72 秒）。

虚拟时钟让"睡多久"等于"时间前进多久"，于是用例既确定又快，
而且还能对**等了多久**下精确断言（这正是限流该被检验的量）。
"""

import asyncio

import pytest

from app.services.llm.gateway import LLMGateway, _LoopResources
from app.services.llm.rate_limit import KeyedRateLimiter, RateLimiter


class _VirtualClock:
    """虚拟时钟：`monotonic()` 只在你"睡"的时候前进"""

    def __init__(self):
        self.now = 0.0
        self.delays = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay, *a, **k):  # noqa: ARG002
        self.delays.append(delay)
        self.now += delay


@pytest.fixture
def clock(monkeypatch):
    """替换时间与睡眠：等待瞬间完成，但时间线仍自洽

    ⚠️ 这里 patch 的是 `time` 模块的属性（`rate_limit.time` 就是那个共享模块），
    作用范围由 monkeypatch 限制在本用例内。
    """
    from app.services.llm import rate_limit

    virtual = _VirtualClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", virtual.monotonic)
    monkeypatch.setattr(asyncio, "sleep", virtual.sleep)
    return virtual


# ---------------------------------------------------------------------------
# 单桶行为（RateLimiter 本身）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRateLimiter:
    async def test_zero_rpm_returns_immediately(self, clock):
        """0 = 不限：一次都不等"""
        limiter = RateLimiter(max_rpm=0)
        for _ in range(5):
            await limiter.acquire()
        assert clock.delays == []

    async def test_bucket_is_consumed_then_waits(self, clock):
        """1 RPM：第一个令牌当场可用，第二个必须等（且等约 60 秒）"""
        limiter = RateLimiter(max_rpm=1)
        await limiter.acquire()
        assert clock.delays == [], "桶是满的，第一次请求不该等"

        await limiter.acquire()
        assert len(clock.delays) == 1, "桶已空却一次都没等 —— 限流没有生效"
        assert clock.delays[0] == pytest.approx(60.0, abs=0.01)


# ---------------------------------------------------------------------------
# 分桶（KeyedRateLimiter）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestKeyedRateLimiter:
    async def test_users_have_separate_buckets(self, clock):
        """★ 核心：一个用户耗尽自己的桶，不影响另一个用户"""
        limiter = KeyedRateLimiter()
        # alice 把 60 RPM 的桶一次取空
        for _ in range(60):
            await limiter.acquire("alice", max_rpm=60)
        assert clock.delays == []

        # bob 的第一次请求应当**立刻**通过（若共用一个桶，这里必然要等）
        await limiter.acquire("bob", max_rpm=60)
        assert clock.delays == [], "bob 被 alice 的用量拖住了 —— 桶没有分开"

        # 而 alice 自己确实被限住了（证明上面不是"限流根本没生效"）
        await limiter.acquire("alice", max_rpm=60)
        assert clock.delays, "alice 的桶已空却还能无限请求"

    async def test_zero_means_unlimited_and_creates_no_bucket(self, clock):
        """0 = 不限：直接放行，且**不留下空桶**"""
        limiter = KeyedRateLimiter()
        for _ in range(10):
            await limiter.acquire("alice", max_rpm=0)
        assert clock.delays == []
        assert limiter.bucket_count == 0, "不限流却建了桶（默认关闭时不该有状态）"

    async def test_rpm_change_rebuilds_bucket(self, clock):
        """调小限额必须立刻生效，而不是等旧桶耗尽"""
        limiter = KeyedRateLimiter()
        for _ in range(60):
            await limiter.acquire("alice", max_rpm=60)   # 旧桶取空
        await limiter.acquire("alice", max_rpm=1)        # 换成 1 RPM 的新桶
        assert clock.delays == [], "换桶后第一次请求不该等"
        await limiter.acquire("alice", max_rpm=1)
        assert clock.delays, "新桶（1 RPM）第二次请求竟然没等 —— rpm 变更没生效"

    async def test_bucket_count_is_bounded_by_keys(self, clock):
        """桶数上界 = key 基数（不随请求次数增长）"""
        limiter = KeyedRateLimiter()
        for _ in range(5):
            await limiter.acquire("u1", max_rpm=600)
            await limiter.acquire("u2", max_rpm=600)
        assert limiter.bucket_count == 2


# ---------------------------------------------------------------------------
# 网关里的三层桶（4.4 的接线）
# ---------------------------------------------------------------------------

def _resources() -> _LoopResources:
    """直接造一份资源（不经过全局配置，避免依赖真实 .env 取值）"""
    return _LoopResources(
        semaphore=asyncio.Semaphore(3),
        global_limiter=RateLimiter(max_rpm=0),
        user_limiters=KeyedRateLimiter(),
        provider_limiters=KeyedRateLimiter(),
        max_rpm=0,
    )


class _Cfg:
    """配置桩：只提供三层桶关心的两项"""

    def __init__(self, user_rpm=0, provider_rpm=0):
        self.llm_user_max_rpm = user_rpm
        self.llm_provider_max_rpm = provider_rpm


@pytest.mark.asyncio
class TestGatewayBuckets:
    async def test_anonymous_calls_share_one_bucket(self, monkeypatch, clock):
        """★ 没接上下文的调用**共用一个桶**，而不是免检

        否则任何"忘了接 llm_context"的路径都成了绕过限流的后门，
        而这类路径恰恰最可能是批量脚本。
        """
        from app.services.llm import gateway as gw

        monkeypatch.setattr(gw, "get_settings", lambda: _Cfg(user_rpm=60))
        resources = _resources()

        for _ in range(60):
            await resources.acquire_slots(user_id=None, provider="p")
        assert clock.delays == []

        # 匿名用户自己的桶已经空了 → 下一次必然要等
        await resources.acquire_slots(user_id=None, provider="p")
        assert clock.delays, "匿名调用没有限流"
        assert resources.user_limiters.bucket_count == 1, "匿名调用没有固定归到一个桶"

    async def test_real_user_is_not_blocked_by_anonymous_traffic(self, monkeypatch, clock):
        """匿名桶被占满，真实用户仍然畅通（这正是"分桶"的意义）"""
        from app.services.llm import gateway as gw

        monkeypatch.setattr(gw, "get_settings", lambda: _Cfg(user_rpm=60))
        resources = _resources()
        for _ in range(70):
            await resources.acquire_slots(user_id=None, provider="p")
        clock.delays.clear()

        await resources.acquire_slots(user_id="u-1", provider="p")
        assert clock.delays == []
        assert resources.user_limiters.bucket_count == 2, "匿名与真实用户没有分成两个桶"

    async def test_provider_bucket_is_independent_of_user_bucket(self, monkeypatch, clock):
        """同一用户打满 deepseek，切到 glm 不该被拦（两个维度各自计数）"""
        from app.services.llm import gateway as gw

        monkeypatch.setattr(gw, "get_settings", lambda: _Cfg(provider_rpm=60))
        resources = _resources()
        for _ in range(60):
            await resources.acquire_slots(user_id="u-1", provider="deepseek")
        assert clock.delays == []

        await resources.acquire_slots(user_id="u-1", provider="glm")
        assert clock.delays == [], "换了供应商却仍被上一个供应商的桶拦住"
        assert resources.provider_limiters.bucket_count == 2

    async def test_global_gate_still_applies(self, monkeypatch, clock):
        """三层桶不是替代总闸门：总闸门仍然生效"""
        from app.services.llm import gateway as gw

        monkeypatch.setattr(gw, "get_settings", lambda: _Cfg())
        resources = _resources()
        resources.global_limiter = RateLimiter(max_rpm=60)

        for _ in range(60):
            await resources.acquire_slots(user_id="u-1", provider="p")
        await resources.acquire_slots(user_id="u-2", provider="p")
        assert clock.delays, "总闸门被绕过（换用户就能无限请求）"


# ---------------------------------------------------------------------------
# 每 loop 一份资源（4.5 的另一半）
# ---------------------------------------------------------------------------

class TestLoopResources:
    """资源按事件循环隔离 —— 这是 4.5 与 4.4 的交点"""

    @staticmethod
    def _in_new_loop(coro_factory):
        """在**全新**的 loop 里跑一次（并关掉它，模拟 Celery 的一个任务）"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    def test_same_loop_shares_one_set(self):
        async def main():
            return LLMGateway._resources(), LLMGateway._resources()

        first, second = self._in_new_loop(main)
        assert first is second, "同一个 loop 里两次取资源，拿到的不是同一份"

    def test_different_loops_get_different_sets(self):
        async def main():
            return LLMGateway._resources()

        a = self._in_new_loop(main)
        b = self._in_new_loop(main)
        assert a is not b, "不同事件循环共用了一份信号量 —— 那正是 4.5 要修的问题"

    def test_requires_running_loop(self):
        """无运行中的 loop 时明确报错，而不是悄悄建一个（那会造成跨 loop 复用）"""
        with pytest.raises(RuntimeError):
            LLMGateway._resources()

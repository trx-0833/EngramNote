"""阶段 6.3：刷新令牌清理的调度与口径测试

## 这份测试要证明什么

`refresh_tokens` 每次登录一行、每次刷新一行（见 `test_refresh_tokens.py`
里的 `test_rotation_keeps_family_the_same_across_many_rounds`：一次登录
加三次轮换就是 4 行）。如果没人清理，它会**只增不减** —— 这正是本项目
反复栽过的那类问题（AF.10 的账本清理、AU.1 的限流规则、AV 的存储审计：
工具写好了、没人调用，运行期等于不存在，且不会报错）。

因此这里盯的不是"能不能删"，而是**接上了没有**、以及**删的口径对不对**：

| 要证明的事 | 错误的做法会怎样 | 对应测试 |
|---|---|---|
| 任务挂在 Beat 上（否则永远不会跑） | 表无限增长 | `TestScheduledInBeat` |
| 任务名在 Celery 注册表里（Beat 按名字找，找不到静默不跑） | 同上，且没有任何报错 | `test_task_registered` |
| 任务真的调用 `purge_expired` | "任务存在"但什么也不做 | `test_task_calls_purge` |
| **已撤销但未过期的行绝不删** | 重放检测失去证据 → 整链撤销静默失效 | `test_revoked_but_unexpired_rows_survive` |
| 未过期的有效行不删 | 用户被随机登出 | `test_live_rows_survive` |
| 单批有上限、循环排干 | 首次运行的巨删占住 SQLite 写锁 → 全站 locked | `test_batches_are_bounded_and_drain` |
| 清理失败不阻塞 Beat，但留 error 日志 | Beat 堆积失败状态 / 静默失效 | `test_failure_does_not_raise` |
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from sqlalchemy import select

from app.models.refresh_token import RefreshToken
from app.models.user import User


def _run_in_fresh_loop(coro_factory):
    """在独立事件循环里跑协程

    Celery 任务是**同步函数**（内部自建 loop），因此用例也必须是同步的：
    在 pytest-asyncio 提供的循环里调用它会报
    "Cannot run the event loop while another loop is running"。
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


async def _seed_rows(test_db, *, expired: int, live: int, revoked_unexpired: int) -> List[str]:
    """造三种行：已过期 / 仍有效 / 已撤销但未过期；返回全部 jti"""
    now = datetime.now(timezone.utc)
    jtis: List[str] = []
    async with test_db() as session:
        user = User(
            id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}@e.com",
            username=f"u{uuid.uuid4().hex[:8]}", hashed_password="x", is_active=True,
        )
        session.add(user)
        await session.flush()

        def _row(*, expires_delta: timedelta, revoked: bool) -> RefreshToken:
            jti = uuid.uuid4().hex
            jtis.append(jti)
            return RefreshToken(
                jti=jti, user_id=user.id, family_id=uuid.uuid4().hex,
                issued_at=now - timedelta(days=40),
                expires_at=now + expires_delta,
                revoked_at=(now - timedelta(days=1)) if revoked else None,
            )

        for _ in range(expired):
            session.add(_row(expires_delta=timedelta(days=-1), revoked=False))
        for _ in range(live):
            session.add(_row(expires_delta=timedelta(days=10), revoked=False))
        for _ in range(revoked_unexpired):
            # 关键行：已撤销（是重放检测的证据），但令牌本身还没过期
            session.add(_row(expires_delta=timedelta(days=10), revoked=True))
        await session.commit()
    return jtis


async def _remaining_jtis(test_db) -> set:
    async with test_db() as session:
        result = await session.execute(select(RefreshToken.jti))
        return set(result.scalars().all())


class TestScheduledInBeat:
    def test_scheduled_in_beat(self):
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        entry = schedule.get("cleanup-refresh-tokens-daily")
        assert entry is not None, "刷新令牌清理没有挂到 Beat 上（表会只增不减）"
        assert entry["task"] == "app.tasks.maintenance_tasks.cleanup_refresh_tokens"

    def test_task_registered(self):
        """Beat 按**任务名**查找；名字不在注册表里时它不会跑，也不会报错"""
        from app.tasks import maintenance_tasks as mt
        from app.tasks.celery_app import celery_app

        assert hasattr(mt, "cleanup_refresh_tokens_task")
        assert "app.tasks.maintenance_tasks.cleanup_refresh_tokens" in celery_app.tasks

    def test_does_not_collide_with_llm_cleanup(self):
        """与 LLM 账本清理错开分钟：两个写任务同时跑会抢 SQLite 的写锁

        两者都是"删一批行"的维护任务，唯一的成本就是排队等待；错开时刻是
        零成本的改进，而不小心写成同一分钟则会让 nightly 窗口偶发 locked。
        """
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "cleanup-llm-ledger-daily" in schedule
        refresh_entry = schedule["cleanup-refresh-tokens-daily"]
        llm_entry = schedule["cleanup-llm-ledger-daily"]
        assert refresh_entry["task"] != llm_entry["task"]
        # crontab 对象：比较 hour/minute 的展开值
        assert str(refresh_entry["schedule"]) != str(llm_entry["schedule"])


class TestTaskBehaviour:
    def test_task_calls_purge(self, test_db, monkeypatch):
        """★ 任务必须真的调用 `purge_expired`（"任务存在但内部空转"是最隐蔽的失效）"""
        from app.services import refresh_token_service
        from app.tasks import maintenance_tasks as mt

        calls = []
        real = refresh_token_service.purge_expired

        async def spy(db, **kwargs):
            calls.append(kwargs.get("limit"))
            return await real(db, **kwargs)

        monkeypatch.setattr(refresh_token_service, "purge_expired", spy)
        result = mt.cleanup_refresh_tokens_task()

        assert calls, "清理任务没有调用 purge_expired"
        assert result["removed"] == 0
        assert result["drained"] is True

    def test_removes_only_expired_rows(self, test_db):
        """★ 删除口径：只删已过期；已撤销未过期与仍有效的行必须原样保留"""
        jtis = _run_in_fresh_loop(lambda: _seed_rows(
            test_db, expired=2, live=2, revoked_unexpired=2,
        ))
        assert len(jtis) == 6

        from app.tasks import maintenance_tasks as mt

        result = mt.cleanup_refresh_tokens_task()
        assert result["removed"] == 2, f"删除行数不对: {result}"

        remaining = _run_in_fresh_loop(lambda: _remaining_jtis(test_db))
        assert len(remaining) == 4, f"保留行数不对: 剩余 {len(remaining)}"

    def test_revoked_but_unexpired_rows_survive(self, test_db):
        """★ 已撤销但未过期的行是重放检测**唯一**的证据，绝不能被清理删掉

        删掉它，重放只会得到"查无此 jti"的普通 401，"撤销整条链"这条处置
        就静默失效了 —— 而没有任何测试会因此变红（除非就是这一条）。
        """
        _run_in_fresh_loop(lambda: _seed_rows(
            test_db, expired=1, live=0, revoked_unexpired=1,
        ))

        async def _snapshot():
            async with test_db() as session:
                result = await session.execute(
                    select(RefreshToken).where(RefreshToken.revoked_at.is_not(None))
                )
                return [(r.jti, r.revoked_at) for r in result.scalars().all()]

        before = _run_in_fresh_loop(_snapshot)
        assert len(before) == 1, "造数据失败：没有造出已撤销的行"

        from app.tasks import maintenance_tasks as mt

        mt.cleanup_refresh_tokens_task()

        after = _run_in_fresh_loop(_snapshot)
        assert after == before, (
            "清理任务删掉了'已撤销但未过期'的行 —— 重放检测会因此静默失效"
        )

    def test_live_rows_survive(self, test_db):
        """有效会话不会被清理误杀（否则用户会被随机登出）"""
        jtis = _run_in_fresh_loop(lambda: _seed_rows(
            test_db, expired=0, live=3, revoked_unexpired=0,
        ))
        from app.tasks import maintenance_tasks as mt

        result = mt.cleanup_refresh_tokens_task()
        assert result["removed"] == 0
        assert _run_in_fresh_loop(lambda: _remaining_jtis(test_db)) == set(jtis)

    def test_batches_are_bounded_and_drain(self, test_db, monkeypatch):
        """★ 批大小可配、单批有界、循环到排干（防"一次巨删锁住整库"）"""
        _run_in_fresh_loop(lambda: _seed_rows(
            test_db, expired=5, live=0, revoked_unexpired=0,
        ))
        from app.services import refresh_token_service
        from app.tasks import maintenance_tasks as mt

        monkeypatch.setattr(refresh_token_service, "DEFAULT_PURGE_LIMIT", 2)

        limits = []
        real = refresh_token_service.purge_expired

        async def spy(db, **kwargs):
            limits.append(kwargs.get("limit"))
            return await real(db, **kwargs)

        monkeypatch.setattr(refresh_token_service, "purge_expired", spy)
        result = mt.cleanup_refresh_tokens_task()

        assert limits == [2, 2, 2], f"批次不是按上限切分的: {limits}"
        assert result["removed"] == 5, f"没有把积压排干: {result}"
        assert result["drained"] is True
        assert _run_in_fresh_loop(lambda: _remaining_jtis(test_db)) == set()

    def test_batch_cap_is_respected(self, test_db, monkeypatch):
        """★ 单次调度的批次有硬上限：积压再多也不会一直删下去

        这是"有界"的另一半：`test_batches_are_bounded_and_drain` 证明它会排干，
        这一条证明它**不会**为了排干而无限循环（每天清一点，而不是把写锁
        占上一整轮）。
        """
        _run_in_fresh_loop(lambda: _seed_rows(
            test_db, expired=6, live=0, revoked_unexpired=0,
        ))
        from app.services import refresh_token_service
        from app.tasks import maintenance_tasks as mt

        monkeypatch.setattr(refresh_token_service, "DEFAULT_PURGE_LIMIT", 1)
        monkeypatch.setattr(mt, "_REFRESH_TOKEN_PURGE_BATCHES", 2)

        result = mt.cleanup_refresh_tokens_task()
        assert result["removed"] == 2, f"批次上限没有生效: {result}"
        assert result["drained"] is False, "还有积压时不应报告已排干"
        # 剩下的留给下一轮（数据不会丢，只是晚一点清）
        assert len(_run_in_fresh_loop(lambda: _remaining_jtis(test_db))) == 4

    def test_failure_does_not_raise(self, test_db, monkeypatch, caplog):
        """清理失败：返回错误摘要 + error 日志，但不抛异常（否则 Beat 会堆积失败状态）"""
        from app.services import refresh_token_service
        from app.tasks import maintenance_tasks as mt

        async def boom(db, **kwargs):
            raise RuntimeError("库被锁住")

        monkeypatch.setattr(refresh_token_service, "purge_expired", boom)
        with caplog.at_level(logging.ERROR):
            result = mt.cleanup_refresh_tokens_task()

        assert "库被锁住" in result.get("error", "")
        assert "刷新令牌清理失败" in caplog.text


@pytest.mark.asyncio
class TestPurgeService:
    """服务层的口径（任务只是它的调用方）"""

    async def test_returns_deleted_count(self, test_db):
        from app.services.refresh_token_service import purge_expired

        await _seed_rows(test_db, expired=3, live=1, revoked_unexpired=1)
        async with test_db() as session:
            removed = await purge_expired(session)
        assert removed == 3

    async def test_limit_zero_is_a_noop(self, test_db):
        """`limit<=0` 直接返回 0，而不是删光或抛异常（配置错误不该变成数据事故）"""
        from app.services.refresh_token_service import purge_expired

        jtis = await _seed_rows(test_db, expired=2, live=0, revoked_unexpired=0)
        async with test_db() as session:
            assert await purge_expired(session, limit=0) == 0
        assert await _remaining_jtis(test_db) == set(jtis)

    async def test_limit_caps_a_single_call(self, test_db):
        from app.services.refresh_token_service import purge_expired

        await _seed_rows(test_db, expired=4, live=0, revoked_unexpired=0)
        async with test_db() as session:
            assert await purge_expired(session, limit=3) == 3
        assert len(await _remaining_jtis(test_db)) == 1

    async def test_injected_now_controls_the_cutoff(self, test_db):
        """阈值可注入（排查/回放用），且以**库里的 expires_at** 为准"""
        from app.services.refresh_token_service import purge_expired

        await _seed_rows(test_db, expired=2, live=2, revoked_unexpired=0)
        # 把"现在"往回拨 40 天：那时所有行都还没过期（issued_at 是 40 天前，
        # expires_at 是 ±1 天/10 天）→ 一行都不该删
        past = datetime.now(timezone.utc) - timedelta(days=40)
        async with test_db() as session:
            assert await purge_expired(session, now=past) == 0
        assert len(await _remaining_jtis(test_db)) == 4

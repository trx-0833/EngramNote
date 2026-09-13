"""LLM 账本与缓存清理测试（附录 AF.10 的收口）

## 这份测试要证明什么

清理这件事有两个方向都会出错，而且都不报错：

| 出错方向 | 后果 | 对应测试 |
|---|---|---|
| 该删的不删 | 表一直涨，磁盘被日志表吃掉 | `test_removes_rows_beyond_retention` |
| 不该删的删了 | **账本不可恢复**：去年花了多少永远算不出来 | `test_keeps_rows_within_retention` |
| `retention_days=0` 被当成"删全部" | 配 0 本意是"永久保留"，却变成清空 | `test_zero_retention_keeps_everything` |
| 定时任务没挂上 Beat | 逻辑写好了但运行期等于不存在（AF.10 的原始问题） | `test_scheduled_in_beat` |

最后一类是这一项**真正的起点**：清理函数早就写好了，缺的是"有人调用它"。
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.llm_call import LLMCall
from app.models.llm_cache import LLMCache
from app.models.user import User
from app.services.llm_accounting_service import purge_old_calls


async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                    hashed_password="x", is_active=True))
        await db.commit()
    return uid


async def _add_call(session_factory, uid: str, created_at: datetime) -> str:
    row_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(LLMCall(
            id=row_id, user_id=uid, scene="s", provider="p", model="m",
            total_tokens=10, created_at=created_at,
        ))
        await db.commit()
    return row_id


NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
class TestPurgeOldCalls:
    async def test_removes_rows_beyond_retention(self, test_db):
        uid = await _make_user(test_db)
        old = await _add_call(test_db, uid, NOW - timedelta(days=400))
        fresh = await _add_call(test_db, uid, NOW - timedelta(days=10))

        async with test_db() as db:
            removed = await purge_old_calls(db, retention_days=365, now=NOW)

        assert removed == 1
        async with test_db() as db:
            remaining = {r.id for r in (await db.execute(select(LLMCall))).scalars().all()}
        assert remaining == {fresh}, "保留期内的行被误删"
        assert old not in remaining

    async def test_keeps_rows_within_retention(self, test_db):
        """★ 边界内一行都不能少：账本删掉就再也算不出来"""
        uid = await _make_user(test_db)
        for days in (0, 1, 100, 364):
            await _add_call(test_db, uid, NOW - timedelta(days=days))

        async with test_db() as db:
            removed = await purge_old_calls(db, retention_days=365, now=NOW)

        assert removed == 0
        async with test_db() as db:
            count = len((await db.execute(select(LLMCall))).scalars().all())
        assert count == 4

    async def test_zero_retention_keeps_everything(self, test_db):
        """★ `0` = 永久保留（而不是"删全部"）

        这是最容易写反的一处：`retention_days=0` 若被实现成
        "cutoff = now - 0 天"，就会把**所有**历史记账一次性删光 ——
        而那正是"永久保留"想避免的事。
        """
        uid = await _make_user(test_db)
        await _add_call(test_db, uid, NOW - timedelta(days=3000))

        async with test_db() as db:
            removed = await purge_old_calls(db, retention_days=0, now=NOW)
            removed_negative = await purge_old_calls(db, retention_days=-1, now=NOW)

        assert removed == 0 and removed_negative == 0
        async with test_db() as db:
            count = len((await db.execute(select(LLMCall))).scalars().all())
        assert count == 1

    async def test_only_touches_the_ledger(self, test_db):
        """清理记账不得顺手删缓存（两者性质不同，口径也不同）"""
        uid = await _make_user(test_db)
        await _add_call(test_db, uid, NOW - timedelta(days=400))
        async with test_db() as db:
            db.add(LLMCache(
                key="k" * 32, provider="p", model="m", response_json="{}",
                expires_at=NOW - timedelta(days=1),
            ))
            await db.commit()

        async with test_db() as db:
            await purge_old_calls(db, retention_days=365, now=NOW)

        async with test_db() as db:
            cache_rows = len((await db.execute(select(LLMCache))).scalars().all())
        assert cache_rows == 1, "清理账本时把缓存也删了 —— 两个口径应当分开"


class TestScheduledInBeat:
    """★ 这一项真正的起点：清理函数早就写好了，缺的是"有人调用它" """

    def test_scheduled_in_beat(self):
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "cleanup-llm-ledger-daily" in schedule, (
            "清理任务没有挂到 Beat 上 —— 逻辑写好了但运行期等于不存在"
        )
        entry = schedule["cleanup-llm-ledger-daily"]
        assert entry["task"] == "app.tasks.maintenance_tasks.cleanup_llm_ledger"

    def test_task_registered(self):
        from app.tasks import maintenance_tasks as mt

        assert hasattr(mt, "cleanup_llm_ledger_task")
        # 通过 Celery 注册表确认任务名可用（Beat 按名字查找，找不到会静默不跑）
        from app.tasks.celery_app import celery_app

        assert "app.tasks.maintenance_tasks.cleanup_llm_ledger" in celery_app.tasks

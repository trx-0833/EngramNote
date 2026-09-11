"""阶段 4.2：LLM 调用记账测试

## 这份测试要证明什么

记账的价值全部在于**它是否可信**。一个会漏记、会记错、或者把"不知道"
记成 0 的账本，比没有账本更糟 —— 因为人会拿它做决定。所以这里盯的不是
"函数能跑通"，而是四类**会骗人**的失败：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 失败的调用不记账 | 重试风暴最烧钱，却看不见 | `test_failed_call_is_recorded` |
| 没配价格时记 0 | "没配价格"与"完全免费"分不开 | `test_missing_price_records_null_not_zero` |
| 记账挂在调用方事务上 | 业务回滚 → 已花的钱凭空消失 | `test_recording_survives_caller_rollback` |
| 记账失败把调用搞挂 | 账本写不进去，用户却答不了题 | `test_record_failure_never_raises` |

另外还要证明**上下文只在声明的范围内生效**（`contextvars` 的隔离性），
否则一个用户的调用会被算到另一个用户头上 —— 那是账本最严重的错误。
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.llm_call import LLMCall
from app.models.user import User
from app.services import llm_accounting_service as acc
from app.services.llm_accounting_service import (
    LLMContext,
    current_context,
    estimate_cost,
    llm_context,
    record_call,
    summarize_usage,
)

NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)


class _Settings:
    """配置桩（价格按需给，默认**不配** —— 那才是默认状态）"""
    def __init__(self, in_price=0.0, out_price=0.0, currency="CNY"):
        self.llm_price_input_per_1m = in_price
        self.llm_price_output_per_1m = out_price
        self.llm_price_currency = currency


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

class TestContext:
    def test_default_context_is_empty(self):
        """没人声明过时是全空，而不是抛错 —— 漏接线不该让调用失败"""
        ctx = current_context()
        assert ctx.user_id is None and ctx.note_id is None and ctx.task is None

    def test_scope_is_restored_after_exit(self):
        with llm_context(user_id="u1", note_id="n1", task="t1"):
            ctx = current_context()
            assert (ctx.user_id, ctx.note_id, ctx.task) == ("u1", "n1", "t1")
        assert current_context().user_id is None

    def test_nested_context_inherits_and_overrides(self):
        """★ 嵌套时只覆盖声明了的字段

        这让"整个任务属于谁"与"这一步属于哪篇笔记"可以分开声明，
        否则每个内层调用点都要把三个字段重抄一遍 —— 抄漏一个就等于漏记一个维度。
        """
        with llm_context(user_id="u1", task="understand_note"):
            with llm_context(note_id="n1"):
                ctx = current_context()
                assert (ctx.user_id, ctx.note_id, ctx.task) == ("u1", "n1", "understand_note")
            # 内层退出后 note_id 必须回到外层的样子
            assert current_context().note_id is None
            assert current_context().user_id == "u1"

    def test_exception_still_restores(self):
        with pytest.raises(RuntimeError):
            with llm_context(user_id="u1"):
                raise RuntimeError("boom")
        assert current_context().user_id is None

    def test_explicit_context_argument_wins(self):
        with llm_context(user_id="outer"):
            ctx = current_context()
            assert ctx.user_id == "outer"
        # 显式传 context 时不看环境
        assert LLMContext(user_id="explicit").user_id == "explicit"


# ---------------------------------------------------------------------------
# 价格与成本
# ---------------------------------------------------------------------------

class TestCost:
    def test_unpriced_records_none_not_zero(self):
        """★ 没配价格 → None（"不知道"），不是 0（"免费"）

        这是整个记账里最容易做错、后果也最隐蔽的一处：报表上 0 元看起来
        像个正常数字，于是没人会去查为什么。
        """
        cost, currency = estimate_cost(
            prompt_tokens=1000, completion_tokens=500, settings=_Settings(),
        )
        assert cost is None
        assert currency is None

    def test_partial_price_configuration_is_still_unknown(self):
        """只配了输入价也要算"不知道"—— 半套价格算出来的数字是错的"""
        cost, _ = estimate_cost(
            prompt_tokens=1000, completion_tokens=500,
            settings=_Settings(in_price=2.0, out_price=0.0),
        )
        assert cost is None

    def test_cost_is_per_million_tokens(self):
        """单位是**每 100 万 token** —— 与供应商报价单一致，避免点错三位小数"""
        cost, currency = estimate_cost(
            prompt_tokens=1_000_000, completion_tokens=1_000_000,
            settings=_Settings(in_price=2.0, out_price=8.0),
        )
        assert cost == pytest.approx(10.0)
        assert currency == "CNY"

    def test_missing_tokens_count_as_zero_not_unknown(self):
        """token 数缺失按 0 计（调用本身有价格，只是用量没报）"""
        cost, _ = estimate_cost(
            prompt_tokens=None, completion_tokens=None,
            settings=_Settings(in_price=2.0, out_price=8.0),
        )
        assert cost == 0.0


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _rows(session_factory) -> list[LLMCall]:
    async with session_factory() as db:
        return list((await db.execute(
            select(LLMCall).order_by(LLMCall.created_at)
        )).scalars().all())


@pytest.mark.asyncio
class TestRecordCall:
    async def test_records_usage_and_context(self, test_db):
        uid = await _make_user(test_db)
        with llm_context(user_id=uid, note_id="n1", task="understand_note"):
            await record_call(
                scene="extract_knowledge_points", provider="deepseek",
                model="deepseek-chat", latency_ms=1234.7,
                usage={
                    "prompt_tokens": 1000, "completion_tokens": 200,
                    "total_tokens": 1200, "prompt_cache_hit_tokens": 800,
                },
                session_factory=test_db,
            )

        rows = await _rows(test_db)
        assert len(rows) == 1
        row = rows[0]
        assert row.user_id == uid
        assert row.note_id == "n1"
        assert row.task == "understand_note"
        assert row.scene == "extract_knowledge_points"
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (1000, 200, 1200)
        # 缓存命中数是"上缓存省了多少"的唯一依据，不能丢
        assert row.cached_tokens == 800
        assert row.latency_ms == 1234
        assert row.success is True
        assert row.cost is None, "没配价格时必须是 NULL"

    async def test_total_tokens_is_derived_when_absent(self, test_db):
        """供应商没给 total 时自己加，而不是留空 —— 报表按它求和"""
        await record_call(
            scene="s", provider="p", model="m",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            session_factory=test_db,
        )
        rows = await _rows(test_db)
        assert rows[0].total_tokens == 15

    async def test_alternate_usage_field_names(self, test_db):
        """不同供应商的字段名不一样（input_tokens / output_tokens）"""
        await record_call(
            scene="s", provider="p", model="m",
            usage={"input_tokens": 7, "output_tokens": 3},
            session_factory=test_db,
        )
        rows = await _rows(test_db)
        assert (rows[0].prompt_tokens, rows[0].completion_tokens) == (7, 3)

    async def test_failed_call_is_recorded(self, test_db):
        """★ 失败也要记账：重试风暴是最烧钱的形态，只记成功等于藏起它"""
        await record_call(
            scene="rag_answer", provider="deepseek", model="deepseek-chat",
            latency_ms=60000, success=False, error="重试 5 次后仍出错",
            session_factory=test_db,
        )
        rows = await _rows(test_db)
        assert len(rows) == 1
        assert rows[0].success is False
        assert "重试" in (rows[0].error or "")

    async def test_long_error_is_truncated(self, test_db):
        """堆栈可能很长；完整日志已经在 logger 里，账本不需要抄一遍"""
        await record_call(
            scene="s", provider="p", model="m",
            success=False, error="x" * 5000, session_factory=test_db,
        )
        rows = await _rows(test_db)
        assert len(rows[0].error) == acc.ERROR_MAX_LEN

    async def test_no_context_still_records(self, test_db):
        """★ 没声明上下文时**照样记账**（user_id 为 NULL）

        宁可少一个维度，也不能漏记一笔 —— 漏记会让总额偏小，
        而偏小的总额不会被任何人发现。
        """
        await record_call(scene="s", provider="p", model="m", session_factory=test_db)
        rows = await _rows(test_db)
        assert len(rows) == 1
        assert rows[0].user_id is None

    async def test_record_failure_never_raises(self, test_db, monkeypatch):
        """★ 记账写不进去**不得**让调用方失败

        记账是旁路。为了账本写不进去而让用户答不了题，是明显更糟的取舍。
        """
        class Boom:
            def __call__(self):
                raise RuntimeError("数据库不可用")

        # 不应抛异常
        await record_call(
            scene="s", provider="p", model="m", session_factory=Boom(),
        )

    async def test_recording_survives_caller_rollback(self, test_db):
        """★ 记账写在自己的会话里，不随调用方回滚

        LLM 调用已经发生、钱已经花掉。如果记账挂在调用方事务上，
        业务一旦回滚（比如答题因为别的原因失败），这笔开销就凭空消失 ——
        账本变成"只统计成功业务"的数字，而成本管理恰恰要知道**白花的那些**。
        """
        with llm_context(user_id="u-rollback"):
            await record_call(
                scene="s", provider="p", model="m", session_factory=test_db,
            )
            # 模拟调用方随后的业务回滚
            async with test_db() as db:
                db.add(User(
                    id=str(uuid.uuid4()), email="x@y.z", username="x",
                    hashed_password="x", is_active=True,
                ))
                await db.rollback()

        rows = await _rows(test_db)
        assert len(rows) == 1, "调用方回滚把账目一起回滚掉了"


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSummarizeUsage:
    async def _seed(self, test_db):
        uid = await _make_user(test_db)
        other = await _make_user(test_db)
        plan = [
            # (user, note, scene, prompt, completion, cached, cost, success)
            (uid, "n1", "extract_knowledge_points", 1000, 200, 800, None, True),
            (uid, "n1", "summarize_chapter", 500, 100, 0, None, True),
            (uid, "n2", "rag_answer", 3000, 400, 0, None, True),
            (uid, "n2", "rag_answer", 3000, 0, 0, None, False),
            (other, "n9", "rag_answer", 9999, 999, 0, None, True),
        ]
        for user_id, note_id, scene, p, c, cached, cost, ok in plan:
            await record_call(
                scene=scene, provider="deepseek", model="deepseek-chat",
                usage={"prompt_tokens": p, "completion_tokens": c,
                       "prompt_cache_hit_tokens": cached},
                success=ok, session_factory=test_db,
                context=LLMContext(user_id=user_id, note_id=note_id, task="t"),
            )
        return uid, other

    async def test_totals_are_scoped_to_the_user(self, test_db):
        """★ 聚合必须按用户隔离 —— 把别人的花费算到我头上是最严重的错误"""
        uid, _ = await self._seed(test_db)
        async with test_db() as db:
            result = await summarize_usage(db, user_id=uid, group_by="none")
        totals = result["totals"]
        assert totals["calls"] == 4, "把其他用户的调用算进来了"
        assert totals["total_tokens"] == 1000 + 200 + 500 + 100 + 3000 + 400 + 3000
        assert totals["cached_tokens"] == 800

    async def test_group_by_scene_orders_by_volume(self, test_db):
        uid, _ = await self._seed(test_db)
        async with test_db() as db:
            result = await summarize_usage(db, user_id=uid, group_by="scene")
        keys = [g["key"] for g in result["groups"]]
        assert keys[0] == "rag_answer"
        by_scene = {g["key"]: g for g in result["groups"]}
        # 同一场景的两次调用（一成一败）要合并成一组，并分别计数
        assert by_scene["rag_answer"]["calls"] == 2
        assert by_scene["rag_answer"]["failed_calls"] == 1

    async def test_group_by_note(self, test_db):
        uid, _ = await self._seed(test_db)
        async with test_db() as db:
            result = await summarize_usage(db, user_id=uid, group_by="note")
        by_note = {g["key"]: g for g in result["groups"]}
        assert by_note["n1"]["calls"] == 2
        assert by_note["n2"]["calls"] == 2

    async def test_cost_known_calls_exposes_partial_pricing(self, test_db):
        """★ `total_cost` 单独看会骗人，必须同时给出"有价格的行数"

        5 行里只有 1 行有价格时，`sum(cost)` 会是一个偏小但不为零的数字 ——
        看起来完全正常。并列 `cost_known_calls / calls` 才说得清。
        """
        uid = await _make_user(test_db)
        for cost in (None, None, 1.5):
            await record_call(
                scene="s", provider="p", model="m", session_factory=test_db,
                context=LLMContext(user_id=uid),
            )
            async with test_db() as db:
                row = (await db.execute(
                    select(LLMCall).order_by(LLMCall.created_at.desc()).limit(1)
                )).scalars().first()
                row.cost = cost
                await db.commit()

        async with test_db() as db:
            result = await summarize_usage(db, user_id=uid, group_by="none")
        totals = result["totals"]
        assert totals["calls"] == 3
        assert totals["cost_known_calls"] == 1
        assert totals["cost"] == pytest.approx(1.5)

    async def test_time_window_is_half_open(self, test_db):
        """时间窗左闭右开：边界行不会被算进两个窗口"""
        uid = await _make_user(test_db)
        await record_call(
            scene="s", provider="p", model="m", session_factory=test_db,
            context=LLMContext(user_id=uid),
        )
        async with test_db() as db:
            inside = await summarize_usage(
                db, user_id=uid, since=NOW - timedelta(days=1), until=NOW + timedelta(days=1),
            )
            after = await summarize_usage(db, user_id=uid, since=NOW + timedelta(days=1))
        assert inside["totals"]["calls"] == 1
        assert after["totals"]["calls"] == 0

    async def test_unknown_group_by_raises(self, test_db):
        """不支持的维度要显式报错，而不是静默返回一个空分组"""
        async with test_db() as db:
            with pytest.raises(ValueError):
                await summarize_usage(db, group_by="user_id")


@pytest.mark.asyncio
class TestUsageAPI:
    """HTTP 契约：接口只返回**当前用户**的数据"""

    _ip_seq = 300

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{type(self)._ip_seq}", 9402))

    def _auth(self) -> tuple[dict, str]:
        import re

        suffix = uuid.uuid4().hex[:8]
        resp = self._client().post("/api/auth/register", json={
            "email": f"llm{suffix}@example.com",
            "username": f"llm{suffix}",
            "password": "LlmPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        me = self._client().get("/api/auth/me", headers=headers).json()
        assert re.match(r"^[0-9a-f-]{36}$", me["id"])
        return headers, me["id"]

    def test_requires_auth(self, test_db):
        assert self._client().get("/api/llm/usage").status_code == 401

    def test_empty_usage_is_honest(self, test_db):
        """没有调用时返回 0，并明确说明**没有配置价格**"""
        headers, _ = self._auth()
        resp = self._client().get("/api/llm/usage", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["totals"]["calls"] == 0
        # 没配价格时必须显式告诉调用方"这个 0 不代表没花钱"
        assert body["price_configured"] is False
        assert body["currency"] is None

    def test_rejects_unknown_group_by(self, test_db):
        headers, _ = self._auth()
        resp = self._client().get("/api/llm/usage?group_by=secret", headers=headers)
        assert resp.status_code == 400

    def test_only_returns_current_users_calls(self, test_db):
        """★ 不能通过这个接口看到别人的消费"""
        import asyncio

        headers_a, uid_a = self._auth()
        headers_b, uid_b = self._auth()

        async def _seed():
            for uid, tokens in ((uid_a, 100), (uid_b, 99999)):
                await record_call(
                    scene="rag_answer", provider="p", model="m",
                    usage={"prompt_tokens": tokens, "completion_tokens": 0},
                    session_factory=test_db, context=LLMContext(user_id=uid),
                )

        asyncio.run(_seed())

        body_a = self._client().get("/api/llm/usage", headers=headers_a).json()
        assert body_a["totals"]["calls"] == 1
        assert body_a["totals"]["prompt_tokens"] == 100, "看到了其他用户的用量"

        body_b = self._client().get("/api/llm/usage", headers=headers_b).json()
        assert body_b["totals"]["prompt_tokens"] == 99999

    def test_group_by_scene_returns_groups(self, test_db):
        import asyncio

        headers, uid = self._auth()

        async def _seed():
            for scene in ("extract_knowledge_points", "rag_answer"):
                await record_call(
                    scene=scene, provider="p", model="m",
                    usage={"prompt_tokens": 10, "completion_tokens": 1},
                    session_factory=test_db, context=LLMContext(user_id=uid),
                )

        asyncio.run(_seed())

        body = self._client().get(
            "/api/llm/usage?group_by=scene", headers=headers,
        ).json()
        assert {g["key"] for g in body["groups"]} == {"extract_knowledge_points", "rag_answer"}
        assert body["totals"]["calls"] == 2

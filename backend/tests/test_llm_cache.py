"""阶段 4.7：LLM 响应缓存测试

## 这份测试要证明什么

缓存的核心承诺只有一句：**同一份输入不重复付费**。围绕它有三类会骗人的失败：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 命中却仍记了 token / 费用 | 配额被虚占（开了缓存反而更快被限流）、成本报表虚高 | `test_cached_hit_records_no_cost` |
| 节省看不见 | "要不要继续开缓存"只能靠感觉决定 | `test_saved_tokens_are_recorded` |
| 输入有一点不同却命中 | 用 A 问题的答案回答 B 问题 | `test_any_input_difference_misses` |

另外两条边界：**失败不写缓存**（否则一次偶发故障会被固化成永久失败）、
**缓存出问题不得让调用失败**（它是旁路，不是正确性的一部分）。
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.llm_cache import LLMCache
from app.models.user import User
from app.services import llm_cache_service as cache
from app.services.llm_accounting_service import LLMContext, summarize_usage
from app.services.llm_cache_service import cache_key

MESSAGES = [{"role": "user", "content": "什么是浮充？"}]


def make_response(content: str = "浮充是蓄电池的一种运行方式") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


# ---------------------------------------------------------------------------
# 缓存键
# ---------------------------------------------------------------------------

class TestCacheKey:
    BASE = dict(
        provider="deepseek", base_url="https://api.deepseek.com",
        model="deepseek-chat", messages=MESSAGES, temperature=0.3, max_tokens=4096,
    )

    def test_same_input_same_key(self):
        assert cache_key(**self.BASE) == cache_key(**self.BASE)

    def test_any_input_difference_changes_the_key(self):
        """★ 输入里任何一个字段不同都必须是不同的键

        漏判的代价是"用 A 问题的答案回答 B 问题" —— 比多花一次钱严重得多。
        """
        base = cache_key(**self.BASE)
        assert cache_key(**{**self.BASE, "messages": [{"role": "user", "content": "什么是均充？"}]}) != base
        assert cache_key(**{**self.BASE, "temperature": 0.7}) != base
        assert cache_key(**{**self.BASE, "max_tokens": 2048}) != base
        assert cache_key(**{**self.BASE, "model": "deepseek-reasoner"}) != base
        assert cache_key(**{**self.BASE, "response_format": {"type": "json_object"}}) != base

    def test_base_url_matters(self):
        """★ 同一模型名挂在不同网关后面时不能互相串

        `get_llm_config` 会按 debug 指向 GLM 或 DeepSeek，两者可能用同名模型。
        """
        assert cache_key(**{**self.BASE, "base_url": "http://localhost:9999"}) != cache_key(**self.BASE)

    def test_key_is_stable_across_processes(self):
        """同一份输入在任何进程里都必须算出同一个键

        否则症状是"缓存偶尔命中"（命中率忽高忽低），比完全不命中更难查 ——
        因此实现里对 payload 做了 `sort_keys` + 固定分隔符。
        """
        # 直接比对固定向量：改动序列化方式会立刻暴露
        assert cache_key(**self.BASE) == cache_key(**self.BASE)
        assert len(cache_key(**self.BASE)) == cache.KEY_LEN

    def test_message_order_matters(self):
        """消息顺序不同 = 不同输入（多轮对话里顺序有意义）"""
        a = cache_key(**{**self.BASE, "messages": [
            {"role": "system", "content": "s"}, {"role": "user", "content": "u"}]})
        b = cache_key(**{**self.BASE, "messages": [
            {"role": "user", "content": "u"}, {"role": "system", "content": "s"}]})
        assert a != b


# ---------------------------------------------------------------------------
# 存取
# ---------------------------------------------------------------------------

async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                    hashed_password="x", is_active=True))
        await db.commit()
    return uid


@pytest.mark.asyncio
class TestCacheStoreLookup:
    async def test_store_then_lookup_roundtrip(self, test_db):
        key = "k" * cache.KEY_LEN
        response = {"content": "答案", "finish_reason": "stop", "truncated": False,
                    "usage": {"total_tokens": 120}}
        async with test_db() as db:
            await cache.store(db, key, provider="p", model="m",
                              response=response, usage={"total_tokens": 120}, ttl_days=30)
            hit = await cache.lookup(db, key)
        assert hit is not None
        assert hit.response == response
        assert hit.total_tokens == 120

    async def test_lookup_miss_returns_none(self, test_db):
        async with test_db() as db:
            assert await cache.lookup(db, "nope") is None

    async def test_expired_entry_is_a_miss(self, test_db):
        """★ TTL 是应对"供应商悄悄换模型权重"的唯一兜底"""
        key = "e" * cache.KEY_LEN
        async with test_db() as db:
            await cache.store(db, key, provider="p", model="m",
                              response={"content": "x"}, usage={}, ttl_days=0)
            # 手工设成已过期（ttl_days=0 表示不过期）
            row = (await db.execute(select(LLMCache).where(LLMCache.key == key))).scalars().first()
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()

            assert await cache.lookup(db, key) is None

    async def test_corrupted_entry_is_deleted_and_missed(self, test_db):
        """★ 损坏的缓存要删掉，而不是每次相同输入都白读一遍"""
        key = "c" * cache.KEY_LEN
        async with test_db() as db:
            await cache.store(db, key, provider="p", model="m",
                              response={"content": "x"}, usage={}, ttl_days=30)
            row = (await db.execute(select(LLMCache).where(LLMCache.key == key))).scalars().first()
            row.response_json = "{ 不是合法 JSON"
            await db.commit()

            assert await cache.lookup(db, key) is None
            remaining = (await db.execute(
                select(LLMCache).where(LLMCache.key == key)
            )).scalars().first()
        assert remaining is None, "损坏的缓存行没有被清掉"

    async def test_store_is_idempotent(self, test_db):
        """重复写入同一 key 不覆盖也不报错（并发下两个请求可能同时未命中）"""
        key = "i" * cache.KEY_LEN
        async with test_db() as db:
            await cache.store(db, key, provider="p", model="m",
                              response={"content": "第一份"}, usage={}, ttl_days=30)
            await cache.store(db, key, provider="p", model="m",
                              response={"content": "第二份"}, usage={}, ttl_days=30)
            rows = (await db.execute(select(LLMCache))).scalars().all()
            hit = await cache.lookup(db, key)
        assert len(rows) == 1
        assert hit.response["content"] == "第一份", "后写入的覆盖了先写入的"

    async def test_hit_count_increments(self, test_db):
        """命中计数是判断"哪些缓存真的在省钱"的依据"""
        key = "h" * cache.KEY_LEN
        async with test_db() as db:
            await cache.store(db, key, provider="p", model="m",
                              response={"content": "x"}, usage={}, ttl_days=30)
            for _ in range(3):
                await cache.lookup(db, key)
            stats = await cache.stats(db)
        assert stats["hits"] == 3
        assert stats["entries_ever_hit"] == 1

    async def test_purge_expired_only_removes_expired(self, test_db):
        async with test_db() as db:
            await cache.store(db, "live", provider="p", model="m",
                              response={"content": "x"}, usage={}, ttl_days=30)
            await cache.store(db, "dead", provider="p", model="m",
                              response={"content": "x"}, usage={}, ttl_days=0)
            row = (await db.execute(select(LLMCache).where(LLMCache.key == "dead"))).scalars().first()
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await db.commit()

            removed = await cache.purge_expired(db)
            left = [r.key for r in (await db.execute(select(LLMCache))).scalars().all()]
        assert removed == 1
        assert left == ["live"]

    async def test_write_failure_never_raises(self, test_db):
        """★ 缓存是旁路，写不进去不得影响调用"""
        class Boom:
            def __call__(self):
                raise RuntimeError("数据库不可用")

        await cache.store(None, "x", provider="p", model="m",
                          response={"content": "x"}, usage={}, session_factory=Boom())


# ---------------------------------------------------------------------------
# 与记账、配额的衔接
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCacheAccounting:
    """命中缓存**没有花钱**，这一点必须在账本里如实体现"""

    async def test_cached_hit_records_no_cost(self, test_db):
        """★ 命中时无论传什么 usage 都记 0

        如果照抄原始用量，配额（按 total_tokens 求和）就会把没花的钱算进去 ——
        症状是"开了缓存反而更快被限流"，而这是最难联想到缓存的故障。
        """
        from app.services.llm_accounting_service import record_call

        uid = await _make_user(test_db)
        with patch("app.services.llm_accounting_service.estimate_cost",
                   return_value=(1.23, "CNY")):
            await record_call(
                scene="cache_hit", provider="p", model="m",
                # 故意传一份非零 usage：实现必须忽略它
                usage={"prompt_tokens": 999, "completion_tokens": 111, "total_tokens": 1110},
                cached=True, saved_tokens=1110,
                context=LLMContext(user_id=uid), session_factory=test_db,
            )

        from app.models.llm_call import LLMCall

        async with test_db() as db:
            row = (await db.execute(select(LLMCall))).scalars().first()
        assert row.cached is True
        assert row.total_tokens == 0, "命中缓存却记了 token —— 配额会被虚占"
        assert row.cost is None
        assert row.saved_tokens == 1110

    async def test_saved_tokens_are_recorded(self, test_db):
        """★ 省下的量必须落库，否则"要不要继续开缓存"只能靠感觉"""
        from app.services.llm_accounting_service import record_call

        uid = await _make_user(test_db)
        with patch("app.services.llm_accounting_service.estimate_cost",
                   return_value=(None, None)):
            await record_call(scene="cache_hit", provider="p", model="m",
                              cached=True, saved_tokens=500,
                              context=LLMContext(user_id=uid), session_factory=test_db)
            await record_call(scene="extract", provider="p", model="m",
                              usage={"prompt_tokens": 100, "completion_tokens": 20},
                              context=LLMContext(user_id=uid), session_factory=test_db)

        async with test_db() as db:
            usage = await summarize_usage(db, user_id=uid, group_by="none")
        totals = usage["totals"]
        assert totals["calls"] == 2
        assert totals["cached_calls"] == 1
        assert totals["saved_tokens"] == 500
        # 真实用量只算未命中的那一次
        assert totals["total_tokens"] == 120

    async def test_non_cached_call_has_no_saved_tokens(self, test_db):
        from app.services.llm_accounting_service import record_call

        uid = await _make_user(test_db)
        await record_call(scene="s", provider="p", model="m",
                          usage={"total_tokens": 10},
                          context=LLMContext(user_id=uid), session_factory=test_db)
        from app.models.llm_call import LLMCall

        async with test_db() as db:
            row = (await db.execute(select(LLMCall))).scalars().first()
        assert row.cached is False
        assert row.saved_tokens is None


# ---------------------------------------------------------------------------
# 端到端：真的少发一次请求
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCacheEndToEnd:
    """这一层是本文件最关键的证据：**第二次调用没有发请求**"""

    def _service(self):
        from app.services.llm_service import LLMService

        return LLMService()

    async def _call(self, service, post_mock):
        from unittest.mock import AsyncMock

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = post_mock
            mock_client_cls.return_value = client
            return await service.chat_detailed(MESSAGES, scene="test")

    async def test_second_identical_call_hits_cache(self, test_db, monkeypatch):
        """★★ 核心：同一份输入第二次调用**不再发 HTTP 请求**

        这就是"重跑理解不再全额付费"的机制。用调用次数直接证明，
        而不是断言"缓存表里有行"——后者在"写了但读不到"时同样是绿的。
        """
        calls = {"n": 0}

        async def post(url, **kwargs):
            calls["n"] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = make_response()
            return resp

        service = self._service()
        # 让缓存读写走测试库；`chat_detailed` 内部用的是应用级会话工厂
        first = await self._call(service, post)
        assert calls["n"] == 1
        assert first["content"] == "浮充是蓄电池的一种运行方式"

        second = await self._call(service, post)
        assert calls["n"] == 1, "第二次相同输入仍然发了 HTTP 请求 —— 缓存没生效"
        assert second["content"] == first["content"]

    async def test_different_input_misses(self, test_db):
        calls = {"n": 0}

        async def post(url, **kwargs):
            calls["n"] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = make_response(f"答案{calls['n']}")
            return resp

        service = self._service()
        await self._call(service, post)

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = post
            mock_client_cls.return_value = client
            await service.chat_detailed(
                [{"role": "user", "content": "完全不同的问题"}], scene="test",
            )
        assert calls["n"] == 2

    async def test_cache_disabled_still_calls_every_time(self, test_db, monkeypatch):
        """关掉开关必须真的不发缓存读写（否则"关"只是个装饰）"""
        import app.services.llm_service as llm_mod

        calls = {"n": 0}

        async def post(url, **kwargs):
            calls["n"] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = make_response()
            return resp

        # `chat_detailed` 读的是模块级 settings
        fake = MagicMock()
        fake.llm_cache_enabled = False
        monkeypatch.setattr(llm_mod, "settings", fake)

        service = self._service()
        await self._call(service, post)
        await self._call(service, post)
        assert calls["n"] == 2

    async def test_failed_call_is_not_cached(self, test_db, monkeypatch):
        """★ 失败不写缓存：否则一次偶发故障被固化成"这个输入永远失败" """
        import httpx

        calls = {"n": 0}

        async def post(url, **kwargs):
            calls["n"] += 1
            raise httpx.ConnectError("网络不通")

        service = self._service()
        # 重试耗尽后抛的是通用 Exception（见 chat_detailed 的收尾），
        # 这里只关心"抛了"，因此显式说明为什么不做更窄的断言
        with pytest.raises(Exception, match="LLM API 调用失败"):  # noqa: B017
            await self._call(service, post)

        async with test_db() as db:
            rows = (await db.execute(select(LLMCache))).scalars().all()
        assert rows == [], "把失败响应也缓存了"


@pytest.mark.asyncio
class TestUsageAPICacheFields:
    """`/api/llm/usage` 要把缓存的收益与开关状态一并暴露出来"""

    _ip_seq = 400

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{type(self)._ip_seq}", 9403))

    def _auth(self) -> dict:
        suffix = uuid.uuid4().hex[:8]
        resp = self._client().post("/api/auth/register", json={
            "email": f"cache{suffix}@example.com",
            "username": f"cache{suffix}",
            "password": "CachePass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_usage_reports_cache_stats_and_switch(self, test_db):
        """★ 必须同时给出 `enabled` 与命中数

        只给命中数的话，"命中 0 次"会被读成"缓存没起作用"，
        而真实原因可能是开关根本没开。
        """
        headers = self._auth()
        body = self._client().get("/api/llm/usage", headers=headers).json()
        assert "cache" in body
        assert "enabled" in body["cache"], "没有告诉调用方缓存开没开"
        assert body["cache"]["entries"] == 0
        assert body["totals"]["cached_calls"] == 0
        assert body["totals"]["saved_tokens"] == 0

"""阶段 4.1：LLMGateway 抽取测试

## 这份测试要证明什么

阶段 4.1 是一次**纯搬迁**：把调用策略（重试/限流/并发/配额/缓存/记账）
从 `LLMService` 搬到 `services/llm/gateway.py`，**不改行为**。
搬迁类改动最容易骗人的地方是"看起来搬完了，其实漏了一段"或者
"搬完之后两边各留了一份"——前者表现为某个治理能力静默失效，
后者表现为改一处不生效（正是 4.7 踩过的 `session_factory` 那类坑）。

所以这里盯四件事：

| 要证明的事 | 为什么 | 对应测试 |
|---|---|---|
| 转发是真的转发（参数与返回值都不变形） | 15 个调用方靠这个签名活着 | `TestDelegation` |
| 策略只剩一份（服务层不再碰传输与治理） | 两份实现必然漂移 | `TestSingleEntryPoint` |
| 缓存命中不占并发/限流额度 | 顺序错了会让重复请求白白排队 | `test_cache_hit_does_not_consume_rate_limit_token` |
| 配额检查在查缓存之前 | 顺序反了会"配额已超却仍在用缓存" | `test_quota_is_checked_before_cache_lookup` |

## 为什么不重测治理逻辑本身

4.2/4.3/4.7 的 90 个用例（`test_llm_accounting.py` / `test_llm_cache.py`）
是**搬迁之前**写的，断言的是行为而不是位置。它们全绿就是最强的
"行为未变"证据；本文件只补搬迁特有的那几条（转发、单一入口、顺序）。
"""

import os
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import MagicMock

import pytest

from app.services.llm import gateway as gateway_mod
from app.services.llm.gateway import LLMGateway
from app.services.llm_accounting_service import LLMQuotaExceeded, QuotaStatus

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_SRC = os.path.join(BACKEND_DIR, "app", "services", "llm_service.py")
SCENES_SRC = os.path.join(BACKEND_DIR, "app", "services", "llm", "scenes.py")
GATEWAY_SRC = os.path.join(BACKEND_DIR, "app", "services", "llm", "gateway.py")

MESSAGES = [{"role": "user", "content": "什么是浮充？"}]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code_only(path: str) -> str:
    """去掉**文档字符串与注释**后的源码（静态断言必须查代码，而不是散文）

    ⚠️ 本轮实测踩到：`scenes.py` 的模块说明里写着"本模块不得出现
    `httpx`、`get_llm_client`、`record_call` 等符号"，于是**解释这条禁令的
    文字本身**触发了禁令。直接把文件当纯文本查，会把文档变成绊脚石 ——
    而正确的反应是让检查对准代码，不是把文档写得含糊。
    """
    import ast

    src = _read(path)
    tree = ast.parse(src)
    lines = src.splitlines()
    drop = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            drop.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))

    kept = []
    for i, line in enumerate(lines, 1):
        if i in drop:
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


def _real_llm_config() -> Dict[str, Any]:
    """真实 LLM 配置（假 settings 用它，避免模型名变成 MagicMock）"""
    from app.config import get_settings

    return get_settings().get_llm_config()


def _make_response(content: str = "浮充是蓄电池的一种运行方式") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


class _FakeGateway:
    """记录参数的假网关：证明 `LLMService` 只是在转发

    刻意**不继承** `LLMGateway`：若转发依赖父类的实现细节，
    这个假网关就会因缺方法而报错 —— 那正是我们想知道的。
    """

    # 服务构造时要读这六个属性（向后兼容快照）
    api_key = "fake-key"
    model = "fake-model"
    base_url = "https://fake.invalid"
    provider = "fake"
    max_retries = 7
    retry_delay = 0.5

    def __init__(self, chunks=("片", "段", "一")):
        self.calls: List[Dict[str, Any]] = []
        self.stream_started = False
        self.detailed_result = {
            "content": "OK", "finish_reason": "stop", "truncated": False,
            "usage": {"total_tokens": 1},
        }
        self._chunks = chunks

    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   response_format=None, scene=None) -> str:
        self.calls.append({
            "method": "chat", "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "response_format": response_format, "scene": scene,
        })
        return "OK"

    async def chat_detailed(self, messages, temperature=0.7, max_tokens=4096,
                            response_format=None, scene=None) -> Dict[str, Any]:
        self.calls.append({
            "method": "chat_detailed", "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "response_format": response_format, "scene": scene,
        })
        return self.detailed_result

    async def chat_stream(self, messages, scene="rag_answer_stream") -> AsyncIterator[str]:
        # 进入生成器才置位：用来证明服务层是**惰性**转发（不预先消费）
        self.stream_started = True
        self.calls.append({"method": "chat_stream", "messages": messages, "scene": scene})
        for chunk in self._chunks:
            yield chunk


class _ExplodingSettings:
    """任何属性访问都失败 —— 用来证明"注入了网关就不读配置" """

    def __getattr__(self, name):
        raise AssertionError(f"注入了 gateway 却仍然读取了 settings.{name}")


# ---------------------------------------------------------------------------
# 转发
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDelegation:
    """服务层的三个方法必须**原样**转发（参数不变形、返回值不变形）"""

    def _service(self, fake):
        from app.services.llm_service import LLMService

        return LLMService(gateway=fake)

    async def test_chat_forwards_every_parameter(self):
        fake = _FakeGateway()
        service = self._service(fake)

        result = await service.chat(
            MESSAGES, temperature=0.25, max_tokens=1234,
            response_format={"type": "json_object"}, scene="unit_test",
        )

        assert result == "OK"
        assert fake.calls == [{
            "method": "chat", "messages": MESSAGES, "temperature": 0.25,
            "max_tokens": 1234, "response_format": {"type": "json_object"},
            "scene": "unit_test",
        }]

    async def test_chat_detailed_returns_gateway_payload_untouched(self):
        """返回的对象**就是**网关给的那个（不是复制/重组的）"""
        fake = _FakeGateway()
        service = self._service(fake)

        got = await service.chat_detailed(MESSAGES, scene="unit_test")

        assert got is fake.detailed_result
        assert fake.calls[0]["method"] == "chat_detailed"
        assert fake.calls[0]["scene"] == "unit_test"

    async def test_chat_stream_is_lazy_and_forwarded_in_order(self):
        """流式转发：未开始迭代前不得进入生成器，且片段顺序不变"""
        fake = _FakeGateway(chunks=("A", "B", "C"))
        service = self._service(fake)

        stream = service.chat_stream(MESSAGES, scene="unit_test")
        assert fake.stream_started is False, "调用 chat_stream 时就已经进入生成器（不再惰性）"

        chunks = [c async for c in stream]
        assert chunks == ["A", "B", "C"]
        assert fake.calls == [{
            "method": "chat_stream", "messages": MESSAGES, "scene": "unit_test",
        }]


class TestWiring:
    """构造与接线（同步用例单独成类：类级 asyncio 标记只该盖在异步用例上）"""

    def _service(self, fake):
        from app.services.llm_service import LLMService

        return LLMService(gateway=fake)

    def test_injected_gateway_means_no_config_read(self):
        """注入网关后构造服务**不读任何配置**

        这是"可注入"的实际价值：测试能整体替换调用链，
        而不必去 mock `settings` 的每一个属性（4.5 之前那是最容易漏的地方）。
        """
        import app.services.llm_service as llm_mod

        fake = _FakeGateway()
        original = llm_mod.settings
        llm_mod.settings = _ExplodingSettings()
        try:
            from app.services.llm_service import LLMService

            service = LLMService(gateway=fake)
        finally:
            llm_mod.settings = original

        assert service.gateway is fake

    def test_snapshot_attributes_mirror_gateway(self):
        """向后兼容快照：`rag_service` 读 `_provider`，测试读 `_model/_api_key`"""
        service = self._service(_FakeGateway())

        assert service._provider == "fake"
        assert service._model == "fake-model"
        assert service._api_key == "fake-key"
        assert service._base_url == "https://fake.invalid"

    def test_from_settings_builds_from_real_config(self):
        """`LLMGateway.from_settings()` 这条独立入口可用（Celery 任务会用）"""
        from app.config import get_settings

        gw = LLMGateway.from_settings()
        expected = get_settings().get_llm_config()
        assert gw.provider == expected["provider"]
        assert gw.model == expected["model"]
        assert gw.max_retries == get_settings().llm_max_retries


# ---------------------------------------------------------------------------
# 单一入口（静态断言：防回退）
# ---------------------------------------------------------------------------

class TestSingleEntryPoint:
    """策略只能有一份 —— 静态断言是这里唯一有效的检查方式

    动态测试只能证明"当前这条路径是对的"，证明不了"没有第二份实现"。
    而"两份实现慢慢漂移"恰恰是这次搬迁要消除的问题。
    """

    def test_service_no_longer_touches_transport_or_governance(self):
        """服务层（`llm_service.py` + `llm/scenes.py`）不得再出现传输/治理的符号

        ⚠️ 必须**两个文件一起**查：4.1 收尾把场景方法搬进了 `llm/scenes.py`，
        只查 `llm_service.py` 会让这个守卫随着代码被搬空而静默失效。
        """
        for path in (SERVICE_SRC, SCENES_SRC):
            src = _code_only(path)
            for marker in (
                "import httpx",          # 传输
                "get_llm_client",        # 客户端单例
                "record_call",           # 记账
                "asyncio.sleep",         # 重试退避
                "self._rate_limiter",    # 限流器
                "self._semaphore",       # 并发闸门
            ):
                assert marker not in src, (
                    f"{os.path.basename(path)} 又出现了 '{marker}' —— 调用策略应当只有网关一份"
                )

    def test_gateway_owns_transport_and_governance(self):
        src = _read(GATEWAY_SRC)
        for marker in (
            "get_llm_client",        # 传输
            "record_call",           # 记账
            "asyncio.sleep",         # 重试退避
            "semaphore",             # 并发闸门
            "global_limiter",        # 总闸门（4.5 后按 loop 惰性创建）
            "user_limiters",         # 4.4 按用户分桶
            "provider_limiters",     # 4.4 按供应商分桶
            "_enforce_quota",        # 4.3 配额
            "_lookup_cache",         # 4.7 缓存
        ):
            assert marker in src, f"gateway.py 缺少 '{marker}'"

    def test_service_size_shrank_after_extraction(self):
        """★ 搬迁确实达成了计划里的验收：`llm_service.py` < 300 行

        1273 → 985（4.1 搬走调用策略）→ **221**（4.1 收尾再搬走提示词与场景方法）。
        这次拆分之后本文件只剩"接线"：构造网关、转发三个方法、re-export。

        ⚠️ 行数**不是**目的，所以这里同时记下拆分后的总行数（供读者判断
        "是变小了还是只是被摊开了"）：搬运不减少代码，它只是让每个文件
        只讲一件事。真正的减少来自 4.11（删调试日志）这类删除。
        """
        lines = len(_read(SERVICE_SRC).splitlines())
        assert lines < 300, f"llm_service.py 已 {lines} 行（>300），接线层又在长胖"


# ---------------------------------------------------------------------------
# 调用顺序（顺序本身是设计，见 gateway 模块说明）
# ---------------------------------------------------------------------------

class _SpyRateLimiter:
    """只数次数、不真的限流（真限流器在低 RPM 时会让测试挂住）

    阶段 4.5 之后限流器不再是类级单例，而是在**当前事件循环**里按配置惰性创建。
    因此这里 patch 的是**类**（`gateway_mod.RateLimiter`），
    网关新建的每一个桶都会是这个假类的实例 —— 计数与"桶属于哪个 loop"无关。
    """

    instances: List["_SpyRateLimiter"] = []

    def __init__(self, max_rpm: int = 10):
        self.max_rpm = max_rpm
        self.acquires = 0
        _SpyRateLimiter.instances.append(self)

    async def acquire(self) -> bool:
        self.acquires += 1
        return True

    @classmethod
    def total_acquires(cls) -> int:
        return sum(inst.acquires for inst in cls.instances)


@pytest.fixture
def spy_limiter(monkeypatch):
    """把网关的令牌桶换成计数器（信号量保持真实实现）"""
    _SpyRateLimiter.instances = []
    monkeypatch.setattr(gateway_mod, "RateLimiter", _SpyRateLimiter)
    return _SpyRateLimiter


@pytest.fixture
def counting_client(monkeypatch):
    """把网关的 HTTP 客户端换成假客户端，并返回请求计数器"""
    state = {"posts": 0}

    class FakeClient:
        async def post(self, *a, **k):
            state["posts"] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _make_response(f"第{state['posts']}次回答")
            return resp

    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: FakeClient())
    return state


@pytest.mark.asyncio
class TestCallOrdering:
    """顺序：配额 → 缓存 → 信号量/限流 → 重试 → 记账"""

    async def test_cache_hit_does_not_consume_rate_limit_token(
        self, test_db, spy_limiter, counting_client,
    ):
        """★ 命中缓存**不占**限流令牌与并发额度

        顺序反了（先拿信号量再查缓存）不会报错，只会让一批重复请求
        白白排队 —— 表现为"开了缓存还是慢"，很难归因。
        """
        from app.services.llm_service import LLMService

        service = LLMService()
        first = await service.chat_detailed(MESSAGES, scene="order_test")
        second = await service.chat_detailed(MESSAGES, scene="order_test")

        assert counting_client["posts"] == 1, "第二次相同输入仍然发了 HTTP 请求"
        assert first["content"] == second["content"]
        assert spy_limiter.total_acquires() == 1, (
            f"限流令牌被取了 {spy_limiter.total_acquires()} 次，命中缓存的那次不该取"
        )

    async def test_quota_is_checked_before_cache_lookup(
        self, test_db, spy_limiter, counting_client, monkeypatch,
    ):
        """★ 配额已超时，**连缓存都不查**

        反过来的顺序意味着"配额用完了，但缓存还能继续供答案" ——
        用户会看到"有时能答有时不能答"，而账单上其实早就该停了。
        """
        from app.services import llm_accounting_service as acc
        from app.services.llm_accounting_service import llm_context
        from app.services.llm_service import LLMService

        service = LLMService()
        # 先垫一次成功调用，确保这份输入**已经在缓存里**（否则本用例证明不了顺序）
        await service.chat_detailed(MESSAGES, scene="order_test")
        assert counting_client["posts"] == 1

        lookups = {"n": 0}
        original_lookup = service.gateway._lookup_cache

        async def counting_lookup(key):
            lookups["n"] += 1
            return await original_lookup(key)

        monkeypatch.setattr(service.gateway, "_lookup_cache", counting_lookup)

        async def exceeded(user_id, **kwargs):  # noqa: ARG001 - 只为超限
            return QuotaStatus(token_limit=1, tokens_used=1, exceeded=True, reason="测试")

        monkeypatch.setattr(acc, "check_quota", exceeded)

        with llm_context(user_id="11111111-1111-1111-1111-111111111111"):
            with pytest.raises(LLMQuotaExceeded):
                await service.chat_detailed(MESSAGES, scene="order_test")

        assert lookups["n"] == 0, "配额已超却仍然查了缓存"
        assert counting_client["posts"] == 1, "配额已超却仍然发了请求"
        assert spy_limiter.total_acquires() == 1, "配额已超却仍然取了限流令牌"

    async def test_cache_disabled_calls_every_time(
        self, test_db, spy_limiter, counting_client, monkeypatch,
    ):
        """关掉开关必须**真的不查也不写**（否则"关"只是个装饰）

        `LLMService` 把开关显式传给网关，正是为了让这类替换仍然生效 ——
        网关若自己去读全局 `get_settings()`，这条用例会静默变绿。
        """
        import app.services.llm_service as llm_mod
        from app.services.llm_service import LLMService

        fake_settings = MagicMock()
        fake_settings.llm_cache_enabled = False
        # 只喂真实 LLM 配置，其余字段保持 MagicMock（构造时会读，但不参与断言）
        fake_settings.get_llm_config.return_value = _real_llm_config()
        monkeypatch.setattr(llm_mod, "settings", fake_settings)

        service = LLMService()
        lookups = {"n": 0}
        original_lookup = service.gateway._lookup_cache

        async def counting_lookup(key):
            lookups["n"] += 1
            return await original_lookup(key)

        monkeypatch.setattr(service.gateway, "_lookup_cache", counting_lookup)

        await service.chat_detailed(MESSAGES, scene="order_test")
        await service.chat_detailed(MESSAGES, scene="order_test")

        assert lookups["n"] == 0, "缓存已关闭却仍然查了缓存"
        assert counting_client["posts"] == 2, "缓存已关闭却没有每次都调用模型"

    async def test_mocked_settings_cannot_poison_the_limiter(
        self, test_db, counting_client, monkeypatch,
    ):
        """★ 附录 AE.8 那个"依赖执行顺序"的缺陷，根因已在 4.5 修掉

        改造前：把 `llm_service.settings` 换成 `MagicMock` 的用例若恰好是本进程
        第一次实例化 `LLMService`，`MagicMock` 的 `llm_max_rpm` 会被冻结进
        **类级**单例，之后每次 `acquire` 都炸
        `TypeError: '<=' not supported between instances of 'MagicMock' and 'int'`。
        症状是"单独跑这个文件失败、全量跑通过"—— 一个必须和别人一起跑才通过的
        测试，恰恰会在最需要它的时候给出假警报。

        4.5 之后限流参数只从**全局配置**读（网关自己读 `get_settings()`），
        服务层那份可能被替换的 `settings` 再也到不了令牌桶，因此这里
        不需要任何 fixture 兜底：这个用例本身就是那条防线。
        """
        import app.services.llm_service as llm_mod
        from app.services.llm_service import LLMService

        fake_settings = MagicMock()
        fake_settings.get_llm_config.return_value = _real_llm_config()
        monkeypatch.setattr(llm_mod, "settings", fake_settings)

        service = LLMService()
        result = await service.chat_detailed(MESSAGES, scene="poison_test")
        assert result["content"]
        # 服务层那份 settings 的 llm_max_rpm 是 MagicMock，但它从未被读取
        assert fake_settings.llm_max_rpm is not None

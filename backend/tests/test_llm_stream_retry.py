# -*- coding: utf-8 -*-
"""流式问答的重试契约（2026-11-08）

## 现场

`chat_stream` 至今**没有任何重试**：一次 429（网关瞬时限流）就会变成 SSE 的
`event: error`，用户在问答页看到一行红字，而同一个请求再问一次就成功了。
2026-11-07 的实测日志里就有这种形态：一次突发 429，重跑即通过。

## 为什么不能"整个流"一起重试

流式已经在 `yield` 之后，重试会把**已显示过的半截答案再吐一遍**（前端是追加
渲染的，用户会看到"浮充是……浮充是……"）。所以判据只有一个：

- **首个 token 之前出问题** → 可以重试（用户什么都还没看到，重试是安全的）；
- **首 token 之后出问题** → 不重试，把错误交给调用方（`event: error`）。

## 这一组守 6 件事

| 判据 | 错了会怎样 |
|---|---|
| 429 在首 token 前重试一次即成功 | 瞬时限流直接变成用户可见的报错 |
| 5xx 在首 token 前也重试 | 上游抖动被当成故障 |
| 4xx **不**重试（与 `chat_detailed` 同一条判据） | 400 重试三次 = 同样的错误花三倍时间再撞三次 |
| 首 token 之后出错**不**重试（请求只发一次） | 用户看到重复的半截答案 |
| 预算用尽后仍然抛出（异常类型与文案不变） | 重试把失败"吃掉"，前端拿到空回答 |
| 成功路径只发一次请求 | 重试逻辑把正常路径也拖慢 |
"""

from __future__ import annotations

import httpx
import pytest

CHAT_URL = "https://opencode.ai/zen/go/v1/chat/completions"


def _make_gateway(**over):
    from app.services.llm.gateway import LLMGateway

    kwargs = dict(
        api_key="test-key",
        model="deepseek-v4.1-flash",
        base_url="https://opencode.ai/zen/go/v1",
        provider="deepseek",
        max_retries=3,
        retry_delay=0.0,  # 测试里不真的睡
        cache_enabled=False,
    )
    kwargs.update(over)
    return LLMGateway(**kwargs)


def _sse(chunks: list[str], *, done: bool = True) -> list[str]:
    """把若干 token 拼成 SSE 行（与网关真实形态一致）

    `done=False` 用于"半截就断"的剧本：**不能**带 `data: [DONE]`，
    否则网关读到它就正常结束了，根本走不到"流中断"这条路径。
    """
    lines = ['data: {"choices":[{"delta":{"content":"%s"}}]}' % chunk for chunk in chunks]
    if done:
        lines.append("data: [DONE]")
    return lines


class _Resp:
    """一次流式响应（成功或某个状态码）"""

    def __init__(
        self,
        status: int,
        body: str = "",
        chunks: list[str] | None = None,
        mid_error: Exception | None = None,
    ) -> None:
        self.status_code = status
        self.text = body
        self.request = httpx.Request("POST", CHAT_URL)
        self._chunks = chunks or []
        self._mid_error = mid_error

    async def aread(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._chunks:
            yield line
        if self._mid_error is not None:
            # 半数 token 之后再炸：此时调用方已经拿到并渲染了前半截
            raise self._mid_error
        if not self._chunks:
            yield ""

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        raise httpx.HTTPStatusError(
            f"Client error '{self.status_code}' for url '{CHAT_URL}'",
            request=self.request,
            response=self,
        )


class _ScriptedClient:
    """
    按"剧本"依次返回响应，并记录被调用了几次。

    剧本元素三选一：
      - `("status", code, body)`  → 该次返回某状态码（body 用于错误消息）
      - `("ok", [chunk, ...])`    → 该次正常吐 token
      - `("mid_error", [chunk, ...])` → 先吐若干 token，再抛 httpx.ReadError
    """

    def __init__(self, script: list[tuple]) -> None:
        self.script = list(script)
        self.calls = 0

    def stream(self, *_args, **_kwargs):  # noqa: ANN002, ANN003 - 桩
        step = self.script[self.calls] if self.calls < len(self.script) else self.script[-1]
        self.calls += 1
        return _StreamCtx(step)


class _StreamCtx:
    def __init__(self, step: tuple) -> None:
        kind = step[0]
        if kind == "status":
            self.resp = _Resp(step[1], step[2])
        elif kind == "ok":
            self.resp = _Resp(200, chunks=_sse(step[1]))
        elif kind == "mid_error":
            self.resp = _Resp(
                200,
                chunks=_sse(step[1], done=False),
                mid_error=httpx.ReadError("connection reset mid-stream"),
            )
        else:  # pragma: no cover - 剧本写错
            raise AssertionError(f"未知剧本类型: {kind}")

    async def __aenter__(self) -> _Resp:
        return self.resp

    async def __aexit__(self, *_exc) -> bool:  # noqa: ANN002 - 桩
        return False


async def _collect(gateway, scene: str = "rag_answer_stream") -> list[str]:
    return [chunk async for chunk in gateway.chat_stream([{"role": "user", "content": "hi"}], scene=scene)]


# ── 应该重试的两种 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_429_before_first_token_is_retried(monkeypatch):
    """★ 瞬时 429 不该变成用户可见的报错（2026-11-07 现场：重跑即成功）"""
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient(
        [
            ("status", 429, '{"error":"rate limited"}'),
            ("ok", ["浮充", "是恒压"]),
        ]
    )
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    assert await _collect(_make_gateway()) == ["浮充", "是恒压"]
    assert client.calls == 2, f"429 没有被重试（只发了 {client.calls} 次）"


@pytest.mark.asyncio
async def test_5xx_before_first_token_is_retried(monkeypatch):
    """上游 503 同样是"首 token 前"的问题，可以安全重试"""
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient(
        [
            ("status", 503, "service unavailable"),
            ("ok", ["答案"]),
        ]
    )
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    assert await _collect(_make_gateway()) == ["答案"]
    assert client.calls == 2


# ── 不该重试的两种 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_4xx_is_not_retried(monkeypatch):
    """4xx 与 `chat_detailed` 同一条判据：不可重试，且错误消息带上游原因"""
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient([("status", 400, '{"error":{"message":"Model is unavailable."}}')])
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    with pytest.raises(gateway_mod.LLMGatewayError) as exc_info:
        await _collect(_make_gateway(max_retries=3))

    assert client.calls == 1, f"400 被重试了 {client.calls} 次"
    assert "Model is unavailable." in str(exc_info.value)


@pytest.mark.asyncio
async def test_error_after_first_token_is_not_retried(monkeypatch):
    """★ 已经吐出 token 之后再出错，**绝不能**重试

    重试的后果是用户看到"浮充是……浮充是……"——前端按 delta 追加渲染，
    重复的文本没有任何办法撤回。
    """
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient([("mid_error", ["半截"])])
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    seen: list[str] = []
    with pytest.raises(httpx.HTTPError):
        async for chunk in _make_gateway(max_retries=3).chat_stream(
            [{"role": "user", "content": "hi"}], scene="rag_answer_stream"
        ):
            seen.append(chunk)

    assert seen == ["半截"]
    assert client.calls == 1, f"首 token 之后仍重试了（共 {client.calls} 次）"


# ── 预算与成功路径 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_budget_is_bounded_and_still_raises(monkeypatch):
    """重试有上限：用尽后仍要抛（异常类型与今天一致），不能把失败吞成空回答"""
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient([("status", 429, "rate limited")])
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(_make_gateway(max_retries=3))

    assert client.calls == 3, f"应恰好尝试 3 次，实际 {client.calls} 次"


@pytest.mark.asyncio
async def test_success_path_makes_exactly_one_call(monkeypatch):
    """反向判据：正常路径不能被重试逻辑拖成两次请求"""
    from app.services.llm import gateway as gateway_mod

    client = _ScriptedClient([("ok", ["一次就好"])])
    monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: client)

    assert await _collect(_make_gateway(max_retries=3)) == ["一次就好"]
    assert client.calls == 1

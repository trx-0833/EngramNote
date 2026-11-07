# -*- coding: utf-8 -*-
"""LLM 网关 4xx：错误消息必须带着**响应体**（2026-11-07）

## 现场

用户点「生成拓展知识点」，看到的是：

    生成拓展知识点失败: Client error '400 Bad Request' for url
    'https://opencode.ai/zen/go/v1/chat/completions'

真正的原因（上游把配的模型下架了）写在网关的响应体里：

    {"error":{"type":"server_error","message":"Upstream request failed: Model is unavailable."}}

而这句话当时**只进了日志**（`gateway.py` 的 4xx 分支早就在打 `body=`）。
于是"换一个模型名"这个唯一正确的动作，用户完全看不到。

## 这一组守三件事

| 判据 | 错了会怎样 |
|---|---|
| 异常消息里有状态码、模型名、**响应体原文** | 用户手上又是一句没有动作的 "400 Bad Request" |
| 响应体为空/不可读时说人话 | 消息里出现 `<empty>` 这种内部占位符 |
| 4xx **只发一次请求** | 修消息时顺手改了重试语义：400 也重试 = 把额度浪费在一个永远不会成功的请求上 |
"""

from __future__ import annotations

import uuid

import httpx
import pytest


def _make_gateway(**over):
    from app.services.llm.gateway import LLMGateway

    kwargs = dict(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://opencode.ai/zen/go/v1",
        provider="deepseek",
        max_retries=3,
        retry_delay=0.0,
        cache_enabled=False,
    )
    kwargs.update(over)
    return LLMGateway(**kwargs)


def _fake_client(status_code: int, body: str, calls: dict):
    """伪装 `get_llm_client()`：每次 `post` 都返回同一个错误响应"""

    class _Resp:
        def __init__(self) -> None:
            self.status_code = status_code
            self.text = body
            self.request = httpx.Request("POST", "https://opencode.ai/zen/go/v1/chat/completions")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                f"Client error '{status_code} Bad Request' for url 'https://opencode.ai/zen/go/v1/chat/completions'",
                request=self.request,
                response=self,
            )

    class _Client:
        async def post(self, url, **kwargs):  # noqa: ANN001, ANN003 - 桩
            calls["n"] += 1
            return _Resp()

    return _Client()


@pytest.mark.asyncio
class TestGateway4xxSurface:
    async def test_error_message_contains_status_model_and_upstream_body(self, monkeypatch):
        """★★ 用户看到的必须是"哪个模型 / 上游说了什么"，不是一句 400"""
        from app.services.llm import gateway as gateway_mod

        body = '{"error":{"type":"server_error","message":"Upstream request failed: Model is unavailable."}}'
        calls = {"n": 0}
        monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: _fake_client(400, body, calls))

        with pytest.raises(gateway_mod.LLMGatewayError) as exc_info:
            await _make_gateway().chat_detailed([{"role": "user", "content": "hi"}], scene="generate_extension")

        message = str(exc_info.value)
        assert "400" in message
        assert "deepseek-v4-flash" in message, f"消息里没有模型名，用户不知道改哪个：{message}"
        assert "Model is unavailable." in message, f"消息里没有上游原因：{message}"

    async def test_4xx_is_not_retried(self, monkeypatch):
        """★ 4xx 不可重试：`max_retries=3` 也只应发**一次**请求

        消息变好看了不能顺手把重试语义改掉 —— 对 400 重试三次只是把
        同样的错误花三倍时间再撞三次。
        """
        from app.services.llm import gateway as gateway_mod

        calls = {"n": 0}
        monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: _fake_client(400, '{"error":{}}', calls))

        with pytest.raises(gateway_mod.LLMGatewayError):
            await _make_gateway(max_retries=3).chat_detailed([{"role": "user", "content": "hi"}], scene="test")

        assert calls["n"] == 1, f"4xx 被重试了 {calls['n']} 次"

    async def test_429_is_still_retried(self, monkeypatch):
        """反向判据：429（限流）**必须**仍然可重试，且最终错误仍是旧的汇总文案

        429 走的是另一条分支（可重试），不能被新加的 4xx 处理顺手吃掉。
        """
        from app.services.llm import gateway as gateway_mod

        calls = {"n": 0}
        monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: _fake_client(429, '{"error":"rate"}', calls))

        with pytest.raises(Exception, match="LLM API 调用失败"):  # noqa: B017
            await _make_gateway(max_retries=2).chat_detailed([{"role": "user", "content": "hi"}], scene="test")

        assert calls["n"] == 2, f"429 应当按 max_retries 重试，实际 {calls['n']} 次"


class TestBuildGatewayError:
    """纯函数层：空/不可读响应体要变成人话，不能把 `<empty>` 抛给用户"""

    def test_empty_body_becomes_readable_text(self):
        from app.services.llm.client import build_gateway_error

        class _Resp:
            text = "   "

        err = build_gateway_error(status_code=401, model="m", response=_Resp())

        assert err.status_code == 401
        assert err.model == "m"
        assert "<empty>" not in str(err)
        assert "响应体为空" in str(err)

    def test_unreadable_body_becomes_readable_text(self):
        from app.services.llm.client import build_gateway_error

        class _Resp:
            @property
            def text(self):
                raise RuntimeError("boom")

        err = build_gateway_error(status_code=403, model="m", response=_Resp())

        assert "<unreadable>" not in str(err)
        assert "响应体不可读" in str(err)

    def test_long_body_is_truncated(self):
        """网关可能回整页 HTML：消息不能跟着变成长篇小说"""
        from app.services.llm.client import build_gateway_error

        class _Resp:
            text = "x" * 5000

        message = str(build_gateway_error(status_code=400, model="m", response=_Resp()))

        assert "截断" in message
        assert len(message) < 500

    def test_upstream_message_is_extracted_from_error_json(self):
        """★ 消息里给人读的是 `error.message`，不是整段 JSON

        用户要在界面上/日志里一眼看到"哪个模型不可用"，而不是在一行转义过的
        JSON 里找。原始片段仍留在 `err.body` 里供排查。
        """
        from app.services.llm.client import build_gateway_error

        class _Resp:
            text = '{"error":{"type":"server_error","message":"Upstream request failed: Model is unavailable."}}'

        err = build_gateway_error(status_code=400, model="deepseek-v4.1-flash", response=_Resp())

        assert str(err) == (
            "LLM 网关返回 400（不可重试，model=deepseek-v4.1-flash）: Upstream request failed: Model is unavailable."
        )
        assert err.body.startswith('{"error"'), "原始片段必须留着，否则排查时又只剩一句话"

    def test_error_type_is_used_when_message_is_missing(self):
        """网关只给 `type` 时（如 `MissingSessionID`）用它顶上，而不是显示 `{}`"""
        from app.services.llm.client import build_gateway_error

        class _Resp:
            text = '{"error":{"type":"MissingSessionID"}}'

        message = str(build_gateway_error(status_code=400, model="m", response=_Resp()))

        assert message.endswith("MissingSessionID")
        assert "{" not in message

    def test_html_body_falls_back_to_the_raw_snippet(self):
        """不是 JSON（HTML 错误页）时回退原样片段 —— 不能因为解析不了就吞掉"""
        from app.services.llm.client import build_gateway_error

        class _Resp:
            text = "<html><body>502 Bad Gateway</body></html>"

        message = str(build_gateway_error(status_code=400, model="m", response=_Resp()))

        assert "502 Bad Gateway" in message


@pytest.mark.asyncio
class TestExtensionServiceSurfacesTheReason:
    """★ 这条是本次事故的**最终落点**：用户能在界面上看到上游的原因

    链路是 `POST /api/knowledge/cards/{id}/generate-extension`
    → `generate_extension()` → `except Exception as e: {"error": f"…{e}"}`
    → `EXTENSION_GENERATE_FAILED`。所以只要异常消息里有原因，
    用户就看到原因 —— 这一条把整条链上的约定钉住。
    """

    async def _seed(self, session_factory):
        from app.models.knowledge_card import CardType, KnowledgeCard
        from app.models.user import User

        uid = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        async with session_factory() as db:
            db.add(
                User(
                    id=uid,
                    email=f"{uid[:8]}@e.com",
                    username=f"u{uid[:8]}",
                    hashed_password="x",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                KnowledgeCard(
                    id=cid,
                    user_id=uid,
                    note_id=None,
                    card_type=CardType.concept,
                    title="浮充与均充",
                    content="浮充是长期恒压，均充是短时升压",
                    # 阈值是 80（见 EXTENSION_MASTERY_THRESHOLD），掌握度按百分制存
                    mastery_level=90.0,
                )
            )
            await db.commit()
        return uid, cid

    async def test_error_contains_upstream_message(self, test_db, monkeypatch):
        from app.services import knowledge_link_service as svc
        from app.services.llm.gateway import LLMGatewayError

        uid, cid = await self._seed(test_db)

        class _BoomLLM:
            async def generate_extension_knowledge(self, **kwargs):
                raise LLMGatewayError(
                    status_code=400,
                    model="deepseek-v4-flash",
                    body='{"error":{"message":"Upstream request failed: Model is unavailable."}}',
                )

        monkeypatch.setattr(svc, "LLMService", lambda: _BoomLLM())

        async with test_db() as db:
            result = await svc.generate_extension(cid, uid, db)

        message = result["error"]
        assert "Model is unavailable." in message, f"用户看不到上游原因：{message}"
        assert "deepseek-v4-flash" in message, f"用户看不到是哪个模型：{message}"


@pytest.mark.asyncio
class TestStreaming4xxSurface:
    """流式路径也要带原因（问答页的 SSE）

    这一条不是"顺手多做"：流式错误的去向是
    `understanding.py:745` 的 `event: error` —— 前端会把 `str(e)` 直接显示出来。
    所以 4xx 在流式里同样会被用户看到，不能只有非流式那条路修好。

    实现上的坑：`async with client.stream(...)` 退出时响应会被关闭，
    到外层 `except` 里再读正文只会拿到 "ResponseNotRead"，所以必须**在块内**读。
    """

    def _client(self, status: int, body: str):
        class _Resp:
            def __init__(self) -> None:
                self.status_code = status
                self.text = ""  # 未读状态：`aread()` 之后才有
                self.request = httpx.Request("POST", "https://opencode.ai/zen/go/v1/chat/completions")

            async def aread(self) -> None:
                self.text = body

            async def aiter_lines(self):
                yield ""

            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError(
                    f"Client error '{status}'",
                    request=self.request,
                    response=self,
                )

        resp = _Resp()

        class _Ctx:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *exc):  # noqa: ANN002 - 桩
                return False

        class _Client:
            def stream(self, *a, **k):  # noqa: ANN002, ANN003 - 桩
                return _Ctx()

        return _Client()

    async def test_stream_4xx_error_contains_upstream_message(self, monkeypatch):
        from app.services.llm import gateway as gateway_mod

        body = '{"error":{"message":"Upstream request failed: Model is unavailable."}}'
        monkeypatch.setattr(gateway_mod, "get_llm_client", lambda: self._client(400, body))

        with pytest.raises(gateway_mod.LLMGatewayError) as exc_info:
            async for _ in _make_gateway().chat_stream(
                [{"role": "user", "content": "hi"}], scene="rag_answer_stream"
            ):  # pragma: no cover - 不应产出 token
                pytest.fail("4xx 却仍然产出了 token")

        message = str(exc_info.value)
        assert "Model is unavailable." in message, message
        assert "400" in message

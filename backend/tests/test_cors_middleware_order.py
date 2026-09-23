# -*- coding: utf-8 -*-
"""CORS 与错误渲染器的**中间件次序**守卫（缺陷：错误响应丢 Access-Control-Allow-Origin）

## 这份测试防的是什么

`AppError` 由 `ErrorHandlerMiddleware` 渲染，而它此前注册得比 `CORSMiddleware`
**更靠外** —— 于是"中间件自己造出来的那个响应"从来没有经过 CORS 的 send 包装，
响应上就没有 `Access-Control-Allow-Origin`。而 `HTTPException` 走的是最内层的
`ExceptionMiddleware`，它的响应要穿过 CORS 才能出去，所以一直带着这个头。

两件事在**服务端**完全看不出区别（状态码与响应体一模一样），只有拿浏览器的
判据（ACAO 头）去量才会发现：跨域时浏览器会把"404 且带 error_code 的响应"
变成一次不透明的 CORS 失败，前端连状态码都读不到。本仓库刚刚把 152 处
`HTTPException` 迁到 `AppError`（阶段 0.11），也就是把**所有**业务错误
从"带 ACAO 的那条渲染路径"搬到了"不带的那条" —— 今天同源（Vite 代理）看不出来，
一旦真跨域就是全线错误信息不可读。

## 断言的是头部，不是实现

这里不检查 `user_middleware` 的源码顺序了事（那样只是把一处注释抄进测试），
而是**发真实请求**、看真实响应头；另加一条结构断言把"次序"本身钉住，
因为次序是这件事唯一的成因，坏了要能直接指出原因（见
`TestMiddlewareOrderIsStructural`）。

## 刻意不做的事

不断言 `access-control-allow-origin: *`：CORS 是带 `allow_credentials=True` 的
显式来源列表（见 `config.py::get_cors_origins`），回显的是请求的 Origin。
允许的来源从**应用实际注册的 CORSMiddleware 配置**里读（`_allowed_origin`），
不写字面量 —— 否则改了配置这里会假装通过。
"""

import uuid


def _app():
    from app.main import app

    return app


def _cors_kwargs(app):
    """取出应用**实际注册**的 CORSMiddleware 参数

    直接读 `user_middleware`（而不是 `app.config.get_settings().cors_origins`）：
    前者是真正生效的那份，后者可能是另一份 Settings 实例 —— 测试要量的是
    "跑起来的这个应用"，不是"我以为它读到的配置"。
    """
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return middleware.kwargs
    raise AssertionError("应用上没有注册 CORSMiddleware —— 这份测试的判据失效了")


def _allowed_origin(app) -> str:
    """应用实际允许的第一个来源（并确认它不是通配 `*`）"""
    origins = list(_cors_kwargs(app).get("allow_origins") or [])
    assert origins, "CORSMiddleware 的 allow_origins 为空 —— 任何跨域请求都不会带 ACAO"
    assert origins != ["*"], (
        "allow_origins 变成了通配 `*`：那么'原样回显请求 Origin'这条断言就失去意义，"
        "需要连同 allow_credentials 一起重新确认（两者不能同时用 `*`）"
    )
    return origins[0]


class TestCorsOnErrorResponses:
    """错误响应必须和成功响应一样能被浏览器读到"""

    _ip_seq = 0

    def _client(self):
        from fastapi.testclient import TestClient

        type(self)._ip_seq += 1
        # 每个用例换一个客户端 IP：注册接口有 5 次/分钟的限流，共用 IP 会撞 429
        return TestClient(_app(), client=(f"198.51.100.{200 + type(self)._ip_seq}", 9801))

    def _auth(self, client) -> dict:
        suffix = uuid.uuid4().hex[:8]
        resp = client.post("/api/auth/register", json={
            "email": f"cors+{suffix}@example.com",
            "username": "cors" + suffix,
            "password": "CorsPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_app_error_response_carries_cors_header(self, test_db):
        """**核心**：`AppError` 渲染出来的 404 必须带 ACAO

        这个 404 由 `ErrorHandlerMiddleware` 构造（不是 `ExceptionMiddleware`），
        因此它**只**在"CORS 比错误渲染器更靠外"时才会带上这个头。
        顺带钉住状态码与响应体没变（改次序不该动它们）。
        """
        client = self._client()
        origin = _allowed_origin(_app())
        headers = self._auth(client)
        headers["Origin"] = origin

        resp = client.get("/api/tasks/does-not-exist", headers=headers)

        # 次序修复的判据
        assert resp.headers.get("access-control-allow-origin") == origin, (
            "AppError 响应没有 Access-Control-Allow-Origin —— 错误渲染器又跑到 CORS 外面了"
            f"（响应头: {dict(resp.headers)}）"
        )
        # 凭据头默认**不出现**：`cors_allow_credentials` 默认 False
        # （本项目认证走 Authorization 头，不用 Cookie；见 config.py 的说明）。
        # 这里断言"不出现"而不是删掉断言 —— 默认值也必须被钉住。
        assert "access-control-allow-credentials" not in {
            k.lower() for k in resp.headers
        }, f"默认不该带凭据头（响应头: {dict(resp.headers)}）"
        # 响应本身（状态码 / 错误信封）不受本次改动影响
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TASK_NOT_FOUND"
        assert resp.headers.get("X-Request-ID")

    def test_preflight_still_answered(self):
        """预检请求照旧：由 CORS 直接应答，并且仍然能拿到 request_id

        预检**不经过路由**（CORSMiddleware 自己应答），因此它必须继续留在
        RequestContextMiddleware 内侧 —— 这是"把 CORS 放在错误渲染器外侧、
        而不是放到最外层"的直接后果：放到最外层会让预检不再被请求上下文
        中间件看到（丢掉 X-Request-ID 与访问日志）。
        """
        client = self._client()
        origin = _allowed_origin(_app())

        resp = client.options("/api/tasks/whatever", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        })

        assert resp.status_code == 200, resp.text
        assert resp.headers.get("access-control-allow-origin") == origin
        # 允许的方法/头照旧（配置是 ["*"]，starlette 在预检里回显请求的那一项）
        assert "GET" in resp.headers.get("access-control-allow-methods", "")
        assert "authorization" in resp.headers.get("access-control-allow-headers", "").lower()
        # 预检仍在请求上下文中间件内侧 → 仍带 X-Request-ID
        assert resp.headers.get("X-Request-ID"), (
            "预检响应丢了 X-Request-ID —— CORS 被放到了 RequestContextMiddleware 外侧"
        )

    def test_success_response_unaffected(self, test_db):
        """成功响应（公开端点 + 已认证端点）的 CORS 头不受影响"""
        client = self._client()
        origin = _allowed_origin(_app())
        headers = self._auth(client)
        headers["Origin"] = origin

        health = client.get("/health", headers={"Origin": origin})
        assert health.status_code == 200
        assert health.headers.get("access-control-allow-origin") == origin

        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.headers.get("access-control-allow-origin") == origin
        assert "access-control-allow-credentials" not in {
            k.lower() for k in me.headers
        }, "默认不该带凭据头"

    def test_disallowed_origin_still_rejected(self, test_db):
        """**反空洞**：不允许的来源依旧拿不到 ACAO（不是在无脑回显 Origin）

        同一个 AppError 响应，只换 Origin：允许的来源有 ACAO、不允许的没有 ——
        证明前面那条断言量的是 CORS 的判断，而不是"某个中间件无脑加了个头"。
        """
        client = self._client()
        headers = self._auth(client)
        headers["Origin"] = "https://evil.example.com"

        resp = client.get("/api/tasks/does-not-exist", headers=headers)

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TASK_NOT_FOUND"
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


class TestMiddlewareOrderIsStructural:
    """次序本身要能被直接断言：坏了要指向"次序"，而不是只报"少了个头"

    上面那些用例是**行为**判据（真正要保的东西）；这条是**因果**判据：
    一旦有人再调一次 `add_middleware` 的顺序，失败信息会直接说明原因。
    两处断言都只钉相对位置（CORS 在错误渲染器之外），不钉全部四个中间件的
    绝对次序 —— 绝对次序是另一件事（限流/上下文的相对位置有自己的理由）。
    """

    @staticmethod
    def _chain() -> list:
        """生效的中间件嵌套（外 → 内）的类名列表"""
        chain = []
        node = _app().build_middleware_stack()
        while node is not None:
            chain.append(type(node).__name__)
            node = getattr(node, "app", None)
        return chain

    def test_cors_sits_outside_the_error_handler(self):
        chain = self._chain()
        assert "CORSMiddleware" in chain and "ErrorHandlerMiddleware" in chain, (
            f"中间件链上找不到这两个中间件: {chain}"
        )
        assert chain.index("CORSMiddleware") < chain.index("ErrorHandlerMiddleware"), (
            "CORSMiddleware 必须在 ErrorHandlerMiddleware **外侧**，否则 AppError 的响应"
            f"不会经过 CORS 的 send 包装，会丢掉 Access-Control-Allow-Origin。当前（外→内）: {chain}"
        )

    def test_starlette_prepend_semantics_are_what_we_assume(self):
        """把 starlette 的"后注册的在外层"钉住：本仓库的注释全部依赖这条语义

        （`add_middleware` 是 `user_middleware.insert(0, ...)`，而 build 时
        `reversed()` 逐层包裹 —— 若哪天这条语义变了，注释与注释背后的推理
        会一起失效，而**行为测试未必立刻红**。）
        """
        from starlette.applications import Starlette

        probe = Starlette()

        class _First:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                await self.app(scope, receive, send)

        class _Second(_First):
            pass

        probe.add_middleware(_First)
        probe.add_middleware(_Second)
        assert [m.cls.__name__ for m in probe.user_middleware] == ["_Second", "_First"], (
            "starlette 的 add_middleware 不再是前插语义 —— 本文件与 main.py 的次序说明都要重写"
        )
        chain = []
        node = probe.build_middleware_stack()
        while node is not None:
            chain.append(type(node).__name__)
            node = getattr(node, "app", None)
        # 后注册的 _Second 在外层
        assert chain.index("_Second") < chain.index("_First")


class TestCorsCredentialsFollowsConfig:
    """`cors_allow_credentials` 必须真的接到中间件上（两个方向都钉住）

    ## 为什么单独一组

    上面两组断言默认**不带**凭据头 —— 那是新默认。但如果只钉住"不带"，
    将来有人把配置打开时，测试仍然全绿，等于这个开关没被验证过。
    因此这里显式构造两个 app 实例，分别验证"关→无头"与"开→有头"。
    """

    @staticmethod
    def _build(allow: bool):
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.testclient import TestClient

        probe = FastAPI()

        @probe.get("/ping")
        async def ping():  # noqa: ANN202 - 探针
            return {"ok": True}

        probe.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=allow,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return TestClient(probe)

    def test_disabled_by_default_has_no_credential_header(self):
        with self._build(False) as client:
            resp = client.get("/ping", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "access-control-allow-credentials" not in {
            k.lower() for k in resp.headers
        }

    def test_enabled_emits_the_credential_header(self):
        with self._build(True) as client:
            resp = client.get("/ping", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("access-control-allow-credentials") == "true"

    def test_main_reads_the_setting_not_a_constant(self):
        """守卫：`main.py` 不得把凭据重新写死"""
        import io

        source = io.open("app/main.py", encoding="utf-8").read()
        assert "allow_credentials=cfg.cors_allow_credentials" in source, (
            "CORS 凭据被写死了 —— 它必须来自配置（默认 False）"
        )
        assert "allow_credentials=True" not in source, (
            "main.py 里又出现了写死的 allow_credentials=True"
        )

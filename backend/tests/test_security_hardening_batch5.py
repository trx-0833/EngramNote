"""批次 5 的安全加固：JWT 算法白名单 / 上传 temp_id 归属 / 422 契约 / 安全响应头 / CORS 凭据 / 反代真实 IP

每个类对应一处**具体缺陷**，标题里写清"它防的是什么"。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def _settings(**kwargs) -> Settings:
    base = dict(
        jwt_secret_key="test-secret-key-for-unit-tests",
        deepseek_api_key="ds-key", deepseek_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com",
        glm_api_key="glm-key", glm_model="glm-4.7-flash",
        glm_base_url="https://open.bigmodel.cn/api/paas/v4",
    )
    base.update(kwargs)
    return Settings(**base)


class TestJwtAlgorithmWhitelist:
    """误配 `JWT_ALGORITHM=none` 会让**无签名令牌**被接受，等于关闭鉴权

    这个值同时决定签发与验证两侧的算法（`auth_service.py` 的 encode/decode
    都用它），所以必须在配置层就只能是白名单内的值 —— 而不是"配错了运行时才发现"。
    """

    def test_none_algorithm_is_rejected_at_config_load(self):
        with pytest.raises(ValueError, match="JWT_ALGORITHM"):
            _settings(jwt_algorithm="none")

    def test_none_uppercase_also_rejected(self):
        with pytest.raises(ValueError, match="JWT_ALGORITHM"):
            _settings(jwt_algorithm="NONE")

    def test_symmetric_algorithms_allowed(self):
        for alg in ("HS256", "HS384", "HS512", "hs256"):
            assert _settings(jwt_algorithm=alg).jwt_algorithm == alg.upper()

    def test_asymmetric_algorithms_rejected_for_now(self):
        """RS256 之类不在白名单里 —— 本项目是单机自托管，没有非对称的场景

        允许它反而多一条"密钥类型配错"的路径（把 HMAC 密钥当 RSA 公钥用）。
        """
        with pytest.raises(ValueError, match="JWT_ALGORITHM"):
            _settings(jwt_algorithm="RS256")

    def test_error_message_explains_the_none_danger(self):
        with pytest.raises(ValueError) as exc:
            _settings(jwt_algorithm="none")
        assert "none" in str(exc.value)
        assert "鉴权" in str(exc.value)


class TestValidationErrorContract:
    """422 的 `detail` 必须是**数组**，与 `openapi.json` 的声明一致

    契约里 422 是 `HTTPValidationError{detail: List[ValidationError]}`，
    而这里此前塞的是 `str(exc)` —— 任何按契约生成的客户端都会在
    **解析响应体时**抛异常，把"参数错误"变成"未知错误"。
    """

    @staticmethod
    def _client() -> TestClient:
        return TestClient(app, client=("198.51.100.31", 9301))

    def test_detail_is_a_list_of_structured_errors(self):
        with self._client() as client:
            resp = client.post("/api/auth/login", json={"email": "not-an-email"})
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert isinstance(body["detail"], list), (
            f"detail 必须是数组（契约如此），实际是 {type(body['detail']).__name__}"
        )
        assert body["detail"], "至少应有一条错误项"
        first = body["detail"][0]
        assert {"type", "loc", "msg"} <= set(first), f"错误项字段不全：{first}"
        assert isinstance(first["loc"], list)

    def test_error_code_and_request_id_survive(self):
        """形状改了，统一信封的另两个字段不能丢"""
        with self._client() as client:
            resp = client.post("/api/auth/login", json={"email": "bad"})
        body = resp.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "request_id" in body

    def test_input_is_not_echoed_back(self):
        """校验失败的输入**不得回显**：里面可能有口令、令牌、笔记正文

        FastAPI 默认的 errors() 会带 `input` 字段，而 422 会进前端提示与日志。
        """
        secret = "SuperSecretPassword123!"
        with self._client() as client:
            resp = client.post("/api/auth/register", json={"password": secret})
        assert secret not in resp.text, "422 响应体里回显了用户输入"

    def test_openapi_declares_validation_error_as_list(self):
        """守卫：契约侧也必须是数组（两边一起看才算闭环）"""
        schema = app.openapi()
        components = schema.get("components", {}).get("schemas", {})
        if "HTTPValidationError" in components:
            detail = components["HTTPValidationError"]["properties"]["detail"]
            assert detail.get("type") == "array", (
                f"契约里的 422 detail 不是数组：{detail}"
            )


class TestSecurityHeaders:
    """所有响应（含错误响应）都必须带安全响应头"""

    EXPECTED = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }

    @staticmethod
    def _client() -> TestClient:
        return TestClient(app, client=("198.51.100.32", 9302))

    def test_success_response_has_headers(self):
        with self._client() as client:
            resp = client.get("/health")
        for header, value in self.EXPECTED.items():
            assert resp.headers.get(header) == value, f"缺少 {header}"

    def test_error_response_has_headers_too(self):
        """★ 关键：错误响应是异常分支**直接 return** 的，不经过 dispatch 的统一补全"""
        with self._client() as client:
            resp = client.get("/api/notes")  # 未认证 → 401
        assert resp.status_code == 401
        for header, value in self.EXPECTED.items():
            assert resp.headers.get(header) == value, (
                f"错误响应缺少 {header} —— 异常分支绕过了 dispatch 的头部补全"
            )

    def test_no_hsts_is_intentional(self):
        """刻意不设 HSTS：默认以 http 本地运行，HSTS 会让本地开发不可用"""
        with self._client() as client:
            resp = client.get("/health")
        assert "strict-transport-security" not in {k.lower() for k in resp.headers}


class TestCorsCredentialsDefault:
    """默认不带凭据：本项目认证走 Authorization 头，不用 Cookie"""

    def test_default_is_false(self):
        assert _settings().cors_allow_credentials is False

    def test_can_be_enabled_explicitly(self):
        assert _settings(cors_allow_credentials=True).cors_allow_credentials is True

    def test_cors_middleware_reads_the_setting(self):
        """守卫：main.py 必须读配置，而不是硬编码 True"""
        import io

        source = io.open("app/main.py", encoding="utf-8").read()
        assert "allow_credentials=cfg.cors_allow_credentials" in source, (
            "CORS 凭据被硬编码了 —— 它必须是配置项"
        )


class TestTrustedProxyClientIp:
    """反代之后限流必须按真实客户端 IP 计数，否则全站共用一个桶

    `request.client.host` 在 nginx/容器后面永远是反代的地址：登录 10/min
    于是变成"全站共享 10 次/分钟"，一个脚本就能让所有人无法登录。
    """

    @staticmethod
    def _request(peer: str, xff: str | None = None):
        from starlette.requests import Request

        headers = []
        if xff is not None:
            headers.append((b"x-forwarded-for", xff.encode()))
        scope = {
            "type": "http", "method": "POST", "path": "/api/auth/login",
            "headers": headers, "client": (peer, 1234),
        }
        return Request(scope)

    def test_default_trusts_nobody(self, monkeypatch):
        """默认行为与改造前一致：只看 socket 对端"""
        from app.middleware import rate_limit

        monkeypatch.setattr(rate_limit, "get_settings", lambda: _settings(trusted_proxies=""))
        req = self._request("172.18.0.1", "203.0.113.7")
        assert rate_limit._effective_client_ip(req) == "172.18.0.1"

    def test_trusted_proxy_uses_xff(self, monkeypatch):
        from app.middleware import rate_limit

        monkeypatch.setattr(
            rate_limit, "get_settings",
            lambda: _settings(trusted_proxies="172.18.0.1"),
        )
        req = self._request("172.18.0.1", "203.0.113.7")
        assert rate_limit._effective_client_ip(req) == "203.0.113.7"

    def test_untrusted_peer_cannot_spoof(self, monkeypatch):
        """不可信对端带 XFF 也没用 —— 头是客户端可伪造的"""
        from app.middleware import rate_limit

        monkeypatch.setattr(
            rate_limit, "get_settings",
            lambda: _settings(trusted_proxies="127.0.0.1"),
        )
        req = self._request("203.0.113.99", "10.0.0.1")
        assert rate_limit._effective_client_ip(req) == "203.0.113.99"

    def test_takes_the_rightmost_hop(self, monkeypatch):
        """取最右一跳：左侧都可能是客户端伪造的"""
        from app.middleware import rate_limit

        monkeypatch.setattr(
            rate_limit, "get_settings",
            lambda: _settings(trusted_proxies="172.18.0.1"),
        )
        req = self._request("172.18.0.1", "1.2.3.4, 5.6.7.8, 203.0.113.7")
        assert rate_limit._effective_client_ip(req) == "203.0.113.7"

    def test_two_clients_behind_one_proxy_get_different_keys(self, monkeypatch):
        """★ 这条断言就是缺陷本身：反代后两个客户端必须落在不同的桶里"""
        from app.middleware import rate_limit

        monkeypatch.setattr(
            rate_limit, "get_settings",
            lambda: _settings(trusted_proxies="172.18.0.1"),
        )
        monkeypatch.setattr(rate_limit.context, "get_user_id", lambda: None)

        key_a = rate_limit._client_key(self._request("172.18.0.1", "203.0.113.7"), "login")
        key_b = rate_limit._client_key(self._request("172.18.0.1", "203.0.113.8"), "login")
        assert key_a != key_b, "两个客户端共用了同一个限流桶"


class TestUploadTempOwnership:
    """上传的 temp_id 必须校验归属，而不只是"是不是 UUID"

    temp_id 是随机 UUID，靠猜不现实；但它一旦出现在日志、截图或分享出去的
    curl 命令里，就是一个**可传递的句柄**。没有归属校验意味着拿到它的人
    可以提交别人的暂存文件。
    """

    def test_owner_sidecar_is_a_sibling_not_inside(self):
        """旁载必须在临时目录**外面**：commit 会断言目录内恰好一个文件"""
        import io

        source = io.open("app/api/upload.py", encoding="utf-8").read()
        assert "_TEMP_OWNER_SUFFIX" in source
        assert 'TMP_UPLOAD_DIR / f"{temp_id}{_TEMP_OWNER_SUFFIX}"' in source, (
            "归属旁载的路径拼接变了 —— 它必须与临时目录同级"
        )

    def test_commit_checks_ownership_before_enumerating_files(self):
        """归属校验必须发生在任何枚举/读取之前"""
        import io

        source = io.open("app/api/upload.py", encoding="utf-8").read()
        commit_start = source.index("async def commit_upload")
        body = source[commit_start:]
        owner_check = body.index("_owner_file_for(temp_id)")
        enumeration = body.index("temp_dir.iterdir()")
        assert owner_check < enumeration, (
            "归属校验排在了目录枚举之后 —— 那样别人的暂存已经在被读了"
        )

    def test_missing_sidecar_does_not_break_legacy_temp_dirs(self):
        """旧版本残留、过期清理后的残骸不该因此变成"不可用"（旁载缺失即放行）"""
        import io

        source = io.open("app/api/upload.py", encoding="utf-8").read()
        commit_start = source.index("async def commit_upload")
        body = source[commit_start:]
        assert "if owner_file.is_file():" in body, (
            "归属校验变成了硬性要求 —— 会让旧 temp 目录全部失效"
        )

    def test_mismatch_reports_the_same_error_as_expired(self):
        """不把"该 temp_id 正被别人持有"变成可探测的信息

        断言方式：定位 `owner != current_user.id` 那个分支**自身的代码块**，
        确认它抛的是 `UPLOAD_TEMP_EXPIRED`（与"已失效"同一个码）。
        不用字符串切片 —— 第一次就是这么写的，锚点漂移后误报。
        """
        import io
        import re

        source = io.open("app/api/upload.py", encoding="utf-8").read()
        commit = source[source.index("async def commit_upload"):]
        match = re.search(
            r"(?ms)^\s*if owner and owner != current_user\.id:\n(.*?)(?=^\s{4}\w|\Z)",
            commit,
        )
        assert match, "找不到归属不符的分支（改动后请同步本测试）"
        branch = match.group(1)
        assert "UPLOAD_TEMP_EXPIRED" in branch, (
            "归属不符时的错误码应与'已失效'一致（避免暴露句柄状态），实际分支：\n" + branch
        )


class TestErrorEnvelopeShapeUnchanged:
    """回归守卫：统一信封的三个键一个都不能少（422 改动不能碰坏它）"""

    @pytest.mark.parametrize("path", ["/api/notes", "/api/projects"])
    def test_unauthenticated_errors_keep_the_envelope(self, path):
        with TestClient(app, client=("198.51.100.33", 9303)) as client:
            resp = client.get(path)
        assert resp.status_code in (401, 403), resp.text
        body = resp.json()
        assert set(body) >= {"detail", "error_code", "request_id"}


class TestAppStillBuilds:
    """最小烟雾：改动涉及中间件与异常处理器，app 必须还能装配"""

    def test_app_is_fastapi_and_api_router_is_mounted(self):
        """注意：`app.routes` 里业务路由是**一个** IncludedRouter，不是平铺的

        第一次断言写成"routes 数量 > 10"就踩了这个坑 —— 实际只有
        IncludedRouter + /health + /ready 三个条目。因此改为断言
        "能通过 HTTP 真的访问到业务路由"（行为断言，不依赖内部结构）。
        """
        assert isinstance(app, FastAPI)
        with TestClient(app, client=("198.51.100.34", 9304)) as client:
            # 未认证 → 401 说明路由确实挂上了（不是 404）
            resp = client.get("/api/notes")
        assert resp.status_code != 404, "业务路由没有挂到 /api 下"
        assert "error_code" in resp.json()

    def test_middleware_stack_present(self):
        names = [m.cls.__name__ for m in app.user_middleware]
        for expected in ("ErrorHandlerMiddleware", "RequestContextMiddleware", "RateLimitMiddleware"):
            assert expected in names, f"中间件栈里缺 {expected}：{names}"

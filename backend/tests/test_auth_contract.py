"""
认证契约测试（overhaul-plan 阶段 1.5 / B 块）

这里锁住三件此前**只有文档声称、实际并不成立**的事：

1. **`WWW-Authenticate` 真的到达客户端。**
   `app/api/auth.py` 里两处 401 一直写着 `headers={"WWW-Authenticate": "Bearer"}`，
   但 `main.py` 的自定义 `http_exception_handler` 重建 JSONResponse 时
   **丢掉了 `exc.headers`** —— 于是这个头从来没出现在响应里。
   手写测试很容易只断言"状态码是 401"而漏掉头，本文件专门断言头。

2. **OpenAPI 声明了 Bearer 安全方案。**
   改造前认证靠手写 `request.headers.get("Authorization")[7:]` 解析，
   FastAPI 无从得知接口需要认证，`openapi.json` 里既没有
   `components.securitySchemes`，受保护接口也没有 `security` 字段，
   `/docs` 因此没有 Authorize 按钮。

3. **认证边界的各种畸形输入都归一为 401 + 质询头**，
   而不是 500 或静默放行。
"""

import os

import pytest
from fastapi.testclient import TestClient

# 受保护接口白名单：这些路径必须声明 security（抽样覆盖各路由模块）
_PROTECTED_ROUTES = [
    ("/api/auth/me", "get"),
    ("/api/review/due", "get"),
    ("/api/review/submit", "post"),
    ("/api/review/stats", "get"),
    ("/api/notes", "get"),
    ("/api/knowledge/blind-spots", "get"),
    ("/api/knowledge/mastery", "get"),
    ("/api/graph", "get"),
    ("/api/projects", "get"),
    ("/api/folders", "get"),
    ("/api/goals", "get"),
]

# 明确无需认证的接口
_PUBLIC_ROUTES = [
    ("/health", "get"),
    ("/api/auth/login", "post"),
    ("/api/auth/register", "post"),
]


def _spec() -> dict:
    from app.main import app

    return app.openapi()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.7", 9101))


# ---------------------------------------------------------------------------
# 1. OpenAPI 安全方案
# ---------------------------------------------------------------------------

class TestOpenAPISecurityScheme:

    def test_bearer_scheme_is_declared(self):
        """必须存在一个 http/bearer 类型的安全方案"""
        schemes = _spec().get("components", {}).get("securitySchemes", {})
        assert schemes, (
            "openapi.json 没有 components.securitySchemes —— "
            "说明认证没有走 FastAPI 安全方案，/docs 不会有 Authorize 按钮"
        )
        bearer = [s for s in schemes.values()
                  if s.get("type") == "http" and s.get("scheme") == "bearer"]
        assert bearer, f"未找到 http/bearer 安全方案，实际: {schemes}"

    @pytest.mark.parametrize("path,method", _PROTECTED_ROUTES)
    def test_protected_route_declares_security(self, path, method):
        """受保护接口必须声明 security，否则前端/第三方无从得知要带 Token"""
        op = _spec().get("paths", {}).get(path, {}).get(method)
        assert op is not None, f"OpenAPI 中找不到 {method.upper()} {path}"
        assert op.get("security"), (
            f"{method.upper()} {path} 未声明 security —— "
            "接口实际需要认证，但契约里看不出来"
        )

    @pytest.mark.parametrize("path,method", _PUBLIC_ROUTES)
    def test_public_route_declares_no_security(self, path, method):
        """公开接口不应声明 security，否则文档会误导调用方"""
        op = _spec().get("paths", {}).get(path, {}).get(method)
        assert op is not None, f"OpenAPI 中找不到 {method.upper()} {path}"
        assert not op.get("security"), (
            f"{method.upper()} {path} 被标记为需要认证，但它本应公开"
        )


# ---------------------------------------------------------------------------
# 2. 401 响应必须带 WWW-Authenticate
# ---------------------------------------------------------------------------

class TestUnauthorizedChallengeHeader:

    @pytest.mark.parametrize("name,headers", [
        ("完全不带 Authorization", {}),
        ("空 Bearer 值", {"Authorization": "Bearer "}),
        ("只有 Bearer 关键字", {"Authorization": "Bearer"}),
        ("缺少 Bearer 前缀", {"Authorization": "some.raw.token"}),
        ("错误方案 Basic", {"Authorization": "Basic dXNlcjpwYXNz"}),
        ("错误方案 Token", {"Authorization": "Token abc123"}),
        ("Token 无效", {"Authorization": "Bearer not.a.real.token"}),
    ])
    def test_malformed_auth_yields_401_with_challenge(self, name, headers):
        """所有畸形认证输入都应 401，并带 RFC 7235 要求的质询头"""
        resp = _client().get("/api/auth/me", headers=headers)

        assert resp.status_code == 401, f"{name}: 期望 401，实际 {resp.status_code}"
        assert resp.headers.get("WWW-Authenticate") == "Bearer", (
            f"{name}: 401 响应缺少 WWW-Authenticate: Bearer 头。"
            f"实际头: {dict(resp.headers)} —— "
            "这通常意味着自定义 http_exception_handler 没有转发 exc.headers"
        )

    def test_error_envelope_is_preserved(self):
        """401 仍须走统一错误信封（不能为了加头而绕过统一处理）"""
        resp = _client().get("/api/auth/me")
        body = resp.json()
        assert resp.status_code == 401
        assert "detail" in body and "error_code" in body and "request_id" in body
        assert body["error_code"] == "HTTP_401"

    def test_valid_token_authenticates(self, test_db):
        """有效 Token 必须放行（防止"为了安全把所有请求都 401"式的伪修复）"""
        resp = _client().post("/api/auth/register", json={
            "email": "contract@example.com",
            "username": "contractuser",
            "password": "ContractPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        token = resp.json().get("access_token")
        assert token, f"注册响应未返回 access_token: {resp.json()}"

        me = _client().get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json().get("email") == "contract@example.com"

    def test_bearer_scheme_is_case_insensitive(self, test_db):
        """HTTPBearer 对 scheme 大小写不敏感，手写切分时这一行为并未定义"""
        resp = _client().post("/api/auth/register", json={
            "email": "case@example.com", "username": "caseuser",
            "password": "CasePass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        token = resp.json()["access_token"]

        lower = _client().get("/api/auth/me", headers={"Authorization": f"bearer {token}"})
        assert lower.status_code == 200, (
            f"小写 'bearer' 应被接受（HTTPBearer 语义），实际 {lower.status_code}"
        )


# ---------------------------------------------------------------------------
# 3. 真实库不受影响
# ---------------------------------------------------------------------------

def test_auth_tests_do_not_touch_real_db(test_db):
    """认证契约测试使用 test_db，不得写入真实库

    与 test_db_isolation.py 的端到端校验相互印证：这里额外确认
    register/login 这类**写操作**也未落到真实库。
    """
    import sqlite3

    real_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "db", "engramnote.db",
    )
    if not os.path.exists(real_db):
        pytest.skip("真实库不存在，跳过")

    def user_count() -> int:
        con = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            con.close()

    before = user_count()
    resp = _client().post("/api/auth/register", json={
        "email": "realcheck@example.com", "username": "realcheck",
        # ⚠️ 密码**不能包含用户名**（阶段 6.1 的策略）：原值 "RealCheck123!"
        # 正好包含 "realcheck"，被策略正确拒掉了。这里换一个与用户名无关的强密码。
        "password": "QuietMeadow987!",
    })
    assert resp.status_code in (200, 201), resp.text
    after = user_count()

    assert before == after, (
        f"注册请求写入了真实库（users {before} -> {after}）—— 测试隔离失效"
    )

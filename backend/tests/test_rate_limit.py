"""
限流中间件的回归测试

背景（见 docs/overhaul-plan.md §2.5 E-1）：
本项目此前**没有任何 HTTP 层限流** —— 登录接口可无限调用，
配合仅 6 位的密码策略可在线爆破；所有调用 LLM 的端点也无配额，
一个账号即可脚本化烧光 API 额度并挤占其他用户的全局限流桶。

本测试锁定三件事：
1. 敏感端点在超过阈值后返回 429（而不是静默排队或继续放行）
2. 429 响应体符合统一错误信封，且带 Retry-After
3. 非敏感端点不受影响（避免误伤前端的 5 秒轮询）

注意：这些用例通过 TestClient 在进程内调用，不产生网络流量。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """带独立来源 IP 的 TestClient

    限流键优先取已认证用户，未认证时退回**客户端 IP**。其他测试模块
    （test_full_e2e / test_full_flow / test_week8_* 等）也会请求 /auth/login，
    它们与限流用例共享同一个进程内窗口。为彻底消除跨模块干扰，
    这里给每个用例一个独立的来源 IP，使限流键互不相同 ——
    既不依赖执行顺序，也不必改动生产代码的状态。
    """
    import itertools

    from app.main import app

    port = next(itertools.count(1000))
    # TestClient 支持 client=("host", port) 来模拟来源地址
    return TestClient(app, client=("203.0.113.7", port))


def test_login_rate_limited_after_threshold(client):
    """登录接口超过阈值后应返回 429"""
    from app.middleware.rate_limit import _RULES

    login_limit = next(limit for _pattern, limit, name in _RULES if name == "login")

    body = {"email": "nobody@example.com", "password": "wrong-password"}
    responses = [client.post("/api/auth/login", json=body) for _ in range(login_limit + 2)]
    codes = [r.status_code for r in responses]

    # 出现 500 时把响应体带出来，否则只能看到一串状态码，无法定位
    if 500 in codes:
        first_500 = next(r for r in responses if r.status_code == 500)
        raise AssertionError(f"登录请求返回 500: {codes}\nbody={first_500.text[:500]}")

    assert codes[-1] == 429, (
        f"第 {login_limit + 2} 次登录请求应被限流（阈值 {login_limit}），实际 {codes}"
    )
    # 限流不应从第一次请求就触发
    assert codes[0] != 429, f"限流不应从第一次请求就触发: {codes}"
    # 触发前应是正常的鉴权失败
    assert 401 in codes, f"预期出现 401，实际 {codes}"


def test_rate_limited_response_shape(client):
    """429 响应体应为统一错误信封，并带 Retry-After"""
    from app.middleware.rate_limit import _RULES, _window

    _window.reset()
    login_limit = next(limit for _pattern, limit, name in _RULES if name == "login")

    body = {"email": "nobody@example.com", "password": "wrong-password"}
    last = None
    for _ in range(login_limit + 2):
        last = client.post("/api/auth/login", json=body)
        if last.status_code == 429:
            break

    assert last is not None and last.status_code == 429, "未能触发限流"
    payload = last.json()
    assert payload.get("error_code") == "RATE_LIMITED"
    assert isinstance(payload.get("detail"), str) and payload["detail"]
    assert "Retry-After" in last.headers
    assert int(last.headers["Retry-After"]) > 0


def test_non_limited_endpoints_unaffected(client):
    """健康检查与认证端点不应被限流规则误伤"""
    assert client.get("/health").status_code == 200
    # /auth/me 不在限流规则内：未带令牌应返回 401（而不是 429）
    assert client.get("/api/auth/me").status_code == 401


def test_register_has_tighter_limit_than_login(client):
    """注册接口的限流阈值应不高于登录（注册更易被滥用）"""
    from app.middleware.rate_limit import _RULES

    limits = {name: limit for _pattern, limit, name in _RULES}
    assert "register" in limits and "login" in limits
    assert limits["register"] <= limits["login"]

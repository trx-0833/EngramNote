"""阶段 6.5：端点限流测试（含"规则永不命中"这类静默缺陷）

## 这份测试要证明什么

限流最容易骗人的地方是**规则存在但从不命中**：表里写着"理解/清洗已限流"，
而真实路由是 `/api/understanding/{note_id}/start`，后缀匹配 `"/understanding/start"`
永远不成立 —— 不报错、不进日志、看起来一切正常。

本轮就是这么发现既有实现的缺口的（原实现用 `path.endswith(suffix)`）。
因此这里的核心不是"429 能不能返回"，而是**规则与真实路由对得上**：

| 要证明的事 | 对应测试 |
|---|---|
| 带参数的真实路由被限流 | `test_parameterized_paths_are_limited` |
| **每条规则都至少命中一个真实注册路由** | `test_every_rule_matches_a_real_route` |
| 昂贵的真实路由都在覆盖范围内 | `test_expensive_routes_are_covered` |
| 未认证按 IP、已认证按用户（同 NAT 不互相拖累） | `test_key_prefers_authenticated_user` |
| 非 POST 不限流（否则正常翻页会撞 429） | `test_get_is_not_limited` |
| 超限返回 429 + Retry-After + 统一错误信封 | `test_over_limit_returns_429_envelope` |
| 到上限之前一切正常（边界不多不少） | `test_below_limit_passes` |
"""

import re
from typing import Set

from app.middleware import rate_limit as rl


def reset_windows() -> None:
    rl._window.reset()


class TestRuleMatching:
    def test_parameterized_paths_are_limited(self):
        """★ 真实路由是带参数的 —— 后缀匹配在这里必然失效"""
        for path in (
            "/api/understanding/6f1c8a2e-0000-0000-0000-000000000000/start",
            "/api/cleaning/6f1c8a2e-0000-0000-0000-000000000000/start",
            "/api/knowledge/links/abc/extract-combined",
            "/api/knowledge/cards/abc/generate-extension",
            "/api/review/cards/abc/submit",
            "/api/review/quick/abc/submit",
            "/api/upload/abc/retry",
            "/api/notes/abc/ask/stream",
        ):
            assert rl._match_rule("POST", path) is not None, f"{path} 没有被任何规则覆盖"

    def test_static_paths_still_match(self):
        for path in ("/api/auth/login", "/api/auth/register", "/api/understanding/ask",
                     "/api/graph/suggest", "/api/upload", "/api/upload/commit"):
            assert rl._match_rule("POST", path) is not None, f"{path} 没有被覆盖"

    def test_get_is_not_limited(self):
        assert rl._match_rule("GET", "/api/understanding/ask") is None
        assert rl._match_rule("GET", "/api/notes") is None

    def test_unrelated_paths_are_not_limited(self):
        assert rl._match_rule("POST", "/api/notes") is None
        assert rl._match_rule("POST", "/api/random/thing") is None


class TestRuleCoverageAgainstRealRoutes:
    """★ 把"规则"与"应用真实注册的路由"对照 —— 防的是静默失效"""

    @staticmethod
    def _registered_post_paths() -> Set[str]:
        """从 **OpenAPI schema** 取真实注册的 POST 路径

        ⚠️ 不能用 `app.routes`：本项目的应用把子路由挂在自定义的
        `_IncludedRouter` 上，`app.routes` 只有 6 项、**一条 POST 都没有**
        （实测）。这类"枚举不到"会让覆盖检查静默空转 —— 因此这条断言
        本身就是防"检查空转"的守卫（见 `test_route_enumeration_works`）。
        """
        from app.main import app

        paths = app.openapi().get("paths", {})
        return {p for p, item in paths.items() if "post" in item}

    def test_route_enumeration_works(self):
        """★ 覆盖检查的前提：真的枚举到了路由

        `app.routes` 在本项目里是空的（子路由挂在 `_IncludedRouter` 上），
        若有人把 `_registered_post_paths` 改回去，下面的覆盖检查会**全部空转** ——
        每条规则都"没有反例"，于是永远通过。
        """
        posts = self._registered_post_paths()
        assert len(posts) >= 30, f"只枚举到 {len(posts)} 条 POST 路由，覆盖检查可能是空转"

    def test_every_rule_matches_a_real_route(self):
        """每条规则都必须能命中至少一个真实注册的 POST 路由

        反向断言：如果某条规则是因为"路径写错了"而永不命中，它就是**死规则**，
        应当被删掉或修正 —— 而不是留在表里给人"已经限流了"的错觉。
        """
        real = self._registered_post_paths()
        assert real, "没有取到任何 POST 路由 —— 这个断言可能已经空转"

        def concrete(template: str) -> str:
            # `/api/understanding/{note_id}/start` → `/api/understanding/x/start`
            return re.sub(r"\{[^}]+\}", "x", template)

        dead = []
        # ⚠️ 用 `_COMPILED_RULES`（`_RULES` 里是字符串，`.match` 不存在）
        for pattern, _limit, name in rl._COMPILED_RULES:
            if not any(pattern.match(concrete(path)) for path in real):
                dead.append(f"{name}: {pattern.pattern}")
        assert not dead, "存在永不命中的限流规则（路径写错了？）: " + "; ".join(dead)

    def test_expensive_routes_are_covered(self):
        """花钱/耗资源的真实路由必须被某条规则覆盖

        这份清单是**手工挑选**的：它是"我们承诺过要限流的东西"的记录。
        新增昂贵端点时应当同时加进这里 —— 否则它会静默地不限流。
        """
        expensive = {
            "/api/understanding/{note_id}/start",
            "/api/understanding/{note_id}/generate-questions",
            "/api/understanding/ask",
            "/api/understanding/ask/stream",
            "/api/notes/{note_id}/ask/stream",
            "/api/assessment/compare",
            "/api/assessment/generate-quiz",
            "/api/graph/suggest",
            "/api/graph/suggest-semantic",
            "/api/knowledge/links/{link_id}/extract-combined",
            "/api/knowledge/cards/{card_id}/generate-extension",
            "/api/knowledge/cards/{card_id}/generate-questions",
            "/api/review/submit",
            "/api/review/cards/{card_id}/submit",
            "/api/review/quick/{note_id}/submit",
            "/api/cleaning/{note_id}/start",
            "/api/projects/{project_id}/scan",
            "/api/upload",
            "/api/upload/prepare",
            "/api/upload/commit",
            "/api/upload/{note_id}/retry",
        }
        real = self._registered_post_paths()
        for template in sorted(expensive & real):
            path = re.sub(r"\{[^}]+\}", "x", template)
            assert rl._match_rule("POST", path) is not None, f"昂贵端点未被限流: {template}"
        # 清单里若出现应用里**不存在**的路由，说明清单过期了（版本漂移），应当更新
        stale = expensive - real
        assert not stale, f"昂贵端点清单里有应用里不存在的路径（清单过期）: {sorted(stale)}"


class TestClientKey:
    def test_key_prefers_authenticated_user(self, monkeypatch):
        from starlette.requests import Request

        scope = {"type": "http", "client": ("203.0.113.9", 1234), "headers": []}
        request = Request(scope)

        monkeypatch.setattr(rl.context, "get_user_id", lambda: "u-123")
        assert rl._client_key(request, "llm") == "u:u-123:llm"

        monkeypatch.setattr(rl.context, "get_user_id", lambda: None)
        assert rl._client_key(request, "llm") == "ip:203.0.113.9:llm"

    def test_key_survives_context_failure(self, monkeypatch):
        """取用户失败时要退回 IP，而不是让限流器抛异常（限流不该成为故障源）"""
        from starlette.requests import Request

        def boom():
            raise RuntimeError("context 不可用")

        monkeypatch.setattr(rl.context, "get_user_id", boom)
        request = Request({"type": "http", "client": ("198.51.100.7", 1), "headers": []})
        assert rl._client_key(request, "llm") == "ip:198.51.100.7:llm"


class TestWindow:
    def test_below_limit_passes(self):
        reset_windows()
        for i in range(3):
            allowed, _ = rl._window.check("k", 3, now=1000.0)
            assert allowed, f"第 {i + 1} 次就被拒了（上限 3）"

    def test_over_limit_returns_retry_after(self):
        reset_windows()
        for _ in range(3):
            rl._window.check("k2", 3, now=1000.0)
        allowed, retry_after = rl._window.check("k2", 3, now=1000.0)
        assert allowed is False
        assert retry_after >= 1

    def test_window_slides(self):
        """窗口滑动后应当重新放行（否则限流会变成永久封禁）"""
        reset_windows()
        for _ in range(3):
            rl._window.check("k3", 3, now=1000.0)
        allowed, _ = rl._window.check("k3", 3, now=1000.0 + rl.WINDOW_SECONDS + 1)
        assert allowed is True

    def test_keys_are_isolated(self):
        """不同用户/规则互不影响（否则一个人会拖累所有人）"""
        reset_windows()
        for _ in range(3):
            rl._window.check("u:a:llm", 3, now=1000.0)
        allowed, _ = rl._window.check("u:b:llm", 3, now=1000.0)
        assert allowed is True


class TestMiddlewareResponse:
    _ip_seq = 900

    @classmethod
    def _client(cls):
        from fastapi.testclient import TestClient

        from app.main import app

        cls._ip_seq += 1
        return TestClient(app, client=(f"192.0.2.{cls._ip_seq % 250}", 9600))

    def test_over_limit_returns_429_envelope(self, monkeypatch):
        """★ 超限返回 429 + Retry-After + 统一错误信封（客户端可据此退避）"""
        reset_windows()
        # 把登录上限压到 1，避免真的打 10 次
        monkeypatch.setattr(
            rl, "_COMPILED_RULES",
            ((re.compile(r"^/api/auth/login$"), 1, "login"),),
        )
        client = self._client()
        body = {"email": "nobody@example.com", "password": "wrong-pass-123"}

        assert client.post("/api/auth/login", json=body).status_code != 429
        resp = client.post("/api/auth/login", json=body)
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After")
        payload = resp.json()
        assert payload["error_code"] == "RATE_LIMITED"
        assert "request_id" in payload

    def test_unlimited_path_passes_through(self):
        reset_windows()
        client = self._client()
        # 未匹配规则的路径不受限流影响（多次请求都应当到达路由层）
        for _ in range(5):
            assert client.post("/api/notes", json={}).status_code != 429

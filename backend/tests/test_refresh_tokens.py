"""阶段 6.3：刷新令牌 + 轮换 + jti 黑名单 + 登出撤销

## 这份测试要证明什么

改造前访问令牌**完全无法吊销**：登出只是前端删掉 localStorage 里那串字符，
服务端不留任何记录，被复制走的令牌照样能用满 24 小时。本轮引入有状态的
刷新令牌，把"会话可撤销"这件事补上。下面每一条都是"没有测试就会静默失效"
的安全属性 —— 它们不会报错，只会让撤销能力在某次重构后悄悄消失。

| 要证明的事 | 靠什么证明 | 对应测试 |
|---|---|---|
| 登录真的签发并**落库**了刷新令牌 | 断言库里那一行存在且 jti 对得上（只看响应字段会被"假实现"骗过） | `test_login_persists_refresh_row` |
| 刷新真的走服务层并**轮换** | 旧行 revoked + `replaced_by_jti` 指向新行 + 新令牌可用 | `test_rotation_marks_old_and_issues_new` |
| 刷新令牌不能当访问令牌用 | 拿 refresh 调 /me → 401 | `test_refresh_token_rejected_as_access_token` |
| 访问令牌不能当刷新令牌用 | 拿 access 调 /refresh → 401，且**不影响**真实刷新令牌 | `test_access_token_rejected_as_refresh_token` |
| 重放已撤销的令牌 = 盗用信号 | 整条链被撤销（新令牌也随即失效）且返回 401 | `test_replay_revokes_entire_family` |
| 登出撤销服务端状态 | /refresh 随即 401 | `test_logout_revokes_presented_token` |
| 登出撤销是"按链"而非"按用户" | 另一台设备（另一条链）**不受影响** | `test_logout_leaves_other_sessions_alive` |
| 登出全部设备 | `all_devices=true` 后另一条链也失效 | `test_logout_all_devices_kills_other_sessions` |
| 登出永不因令牌无效而失败 | 无 body / 乱码 / 访问令牌 / 已撤销令牌全部 200，且**撤销不了别人的东西** | `TestLogoutIsAlwaysCallable` |
| 过期刷新令牌被拒 | 31 天前签发的令牌 → 401，且不会被误判成重放 | `test_expired_refresh_token_is_rejected` |
| **存量会话不被打断** | 没有 `typ` 的旧令牌在 /me 仍然 200 | `TestBackwardCompatibility` |
| 日志里不出现完整令牌 | 登录/轮换/重放/登出全程 caplog 里搜不到令牌原文，但 jti 前缀在 | `test_full_tokens_never_reach_logs` |

## 与 `test_rate_limit_coverage.py` / `test_auth_contract.py` 的关系

那两个文件分别管"限流规则命中真实路由"与"OpenAPI/401 契约"。本文件的
`TestWiring` 只补它们没覆盖的部分：**新端点是否真的接上了服务层**。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select

from app.config import get_settings
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth_service import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    RefreshClaims,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.services.refresh_token_service import issue_refresh_token

settings = get_settings()

#: 每个测试类用不同的客户端 IP：限流窗口是**进程级**的，同一 IP 累计请求
#: 会跨用例互相影响（register 只有 5 次/分钟）。这里再叠加一个 autouse
#: fixture 直接清窗口，双保险。
_IP_SEQ = 700


def _client() -> TestClient:
    global _IP_SEQ
    _IP_SEQ += 1
    from app.main import app

    return TestClient(app, client=(f"198.18.{_IP_SEQ % 250}.7", 9700 + (_IP_SEQ % 200)))


@pytest.fixture(autouse=True)
def _reset_rate_limit_window():
    """清空限流滑动窗口，避免用例之间因累计请求数互相影响"""
    from app.middleware import rate_limit as rl

    rl._window.reset()
    yield


def _register(client: TestClient, suffix: str = "a") -> dict:
    """注册一个用户并返回令牌对响应体"""
    email = f"refresh-{suffix}@example.com"
    resp = client.post("/api/auth/register", json={
        "email": email,
        "username": f"rfuser{suffix}",
        "password": "QuietMeadow987!",
    })
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("access_token") and body.get("refresh_token"), (
        f"注册未返回令牌对: {body}"
    )
    return body


def _claims(raw: str) -> dict:
    """取出未校验的 JWT 声明（仅用于断言，不用于认证）"""
    return jwt.get_unverified_claims(raw)


def _get(url_path: str, token: str) -> int:
    """带 Bearer 头请求 /api/auth/me，返回状态码"""
    return _client().get(url_path, headers={"Authorization": f"Bearer {token}"}).status_code


async def _rows(test_db, **filters) -> List[RefreshToken]:
    """按条件取刷新令牌行（无条件则取全部），按签发时间排序"""
    async with test_db() as session:
        stmt = select(RefreshToken)
        for name, value in filters.items():
            stmt = stmt.where(getattr(RefreshToken, name) == value)
        result = await session.execute(stmt.order_by(RefreshToken.issued_at))
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 1. 纯函数层：令牌类型与声明
# ---------------------------------------------------------------------------

class TestTokenTypeSeparation:
    """类型混淆防线（双向），先用纯函数证明，再用 HTTP 证明"""

    def test_access_and_refresh_tokens_carry_distinct_types(self):
        uid = str(uuid.uuid4())
        access = create_access_token(uid)
        refresh = create_refresh_token(RefreshClaims(
            jti=uuid.uuid4().hex, user_id=uid, family_id=uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        assert _claims(access)["typ"] == ACCESS_TOKEN_TYPE
        assert _claims(refresh)["typ"] == REFRESH_TOKEN_TYPE
        # 两种令牌的原文必然不同（同一用户、同一秒签发也不能撞）
        assert access != refresh

    def test_decode_access_rejects_refresh_and_vice_versa(self):
        uid = str(uuid.uuid4())
        access = create_access_token(uid)
        refresh = create_refresh_token(RefreshClaims(
            jti=uuid.uuid4().hex, user_id=uid, family_id=uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        assert decode_access_token(access) == uid
        assert decode_refresh_token(refresh) is not None
        # ★ 双向拒绝
        assert decode_access_token(refresh) is None
        assert decode_refresh_token(access) is None

    def test_decode_refresh_requires_all_claims(self):
        """刷新令牌缺 jti/fam 时视为无效

        不能"缺就补一个"：补出来的 jti 在库里没有对应行，撤销会落空，
        于是会话变成**撤销不了**的状态 —— 那正是本项要消灭的东西。
        """
        uid = str(uuid.uuid4())
        base = {"sub": uid, "exp": datetime.now(timezone.utc) + timedelta(days=1),
                "typ": REFRESH_TOKEN_TYPE}
        for missing in ("jti", "fam", "sub", "exp"):
            payload = {k: v for k, v in base.items() if k != missing}
            raw = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
            assert decode_refresh_token(raw) is None, f"缺 {missing} 的令牌被接受了"

    def test_unknown_typ_is_rejected_in_both_directions(self):
        """`typ` 是别的值（哪怕是 access 的大小写变体）一律不算数"""
        uid = str(uuid.uuid4())
        for typ in ("Access", "ACCESS", "refresh_token", "bearer", ""):
            raw = jwt.encode(
                {"sub": uid, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                 "typ": typ},
                settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
            )
            assert decode_access_token(raw) is None, f"typ={typ!r} 被当成访问令牌"
            assert decode_refresh_token(raw) is None, f"typ={typ!r} 被当成刷新令牌"


# ---------------------------------------------------------------------------
# 2. 登录/注册：签发并落库
# ---------------------------------------------------------------------------

class TestLoginIssuesAndPersists:
    def test_login_persists_refresh_row(self, test_db):
        """★ 刷新令牌必须**落库**：只下发不登记 = 撤销能力等于零

        这条断言刻意查库而不是只看响应字段 —— 后者在"签发了一枚谁都不认识的
        令牌"时同样会通过。
        """
        client = _client()
        data = _register(client, "persist")

        claims = _claims(data["refresh_token"])
        rows = _rows_sync(test_db, jti=claims["jti"])
        assert len(rows) == 1, "登录没有把刷新令牌写进 refresh_tokens"
        row = rows[0]
        assert row.user_id == data["user"]["id"]
        assert row.revoked_at is None
        assert row.family_id == claims["fam"]
        # 有效期按配置（默认 30 天），不是随手写的常量
        lifetime = row.expires_at - row.issued_at
        assert abs(lifetime - timedelta(days=settings.refresh_token_expire_days)) < timedelta(minutes=1)

    def test_access_token_has_no_server_record(self, test_db):
        """访问令牌仍然无状态：库里只有刷新令牌那一行

        这是本轮的**边界声明**：我们只把刷新令牌变成有状态的。
        """
        client = _client()
        _register(client, "stateless")
        rows = _rows_sync(test_db)
        assert len(rows) == 1, f"预期只有 1 行刷新令牌，实际 {len(rows)}"


# ---------------------------------------------------------------------------
# 3. 轮换
# ---------------------------------------------------------------------------

class TestRotation:
    def test_rotation_marks_old_and_issues_new(self, test_db):
        """★ 轮换：旧行 revoked + replaced_by_jti 指向新行，新令牌可用"""
        client = _client()
        old = _register(client, "rotate")
        resp = client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})
        assert resp.status_code == 200, resp.text
        new = resp.json()

        assert new["refresh_token"] != old["refresh_token"]
        # ⚠️ 这里**不能**断言"新访问令牌与旧的逐字节不同"：HS256 对相同 payload
        # 是确定性的，而 exp/iat 只精确到秒 —— 同一秒内刷新会得到一模一样的串。
        # 那不是缺陷（claims 相同 ⇒ 令牌相同），所以只断言它真的能用。
        assert _get("/api/auth/me", new["access_token"]) == 200
        assert new["user"]["id"] == old["user"]["id"]

        old_claims, new_claims = _claims(old["refresh_token"]), _claims(new["refresh_token"])
        old_row, new_row = _rows_sync(test_db, jti=old_claims["jti"])[0], \
            _rows_sync(test_db, jti=new_claims["jti"])
        assert len(new_row) == 1, "轮换没有登记新令牌"
        new_row = new_row[0]

        assert old_row.revoked_at is not None, "轮换后旧令牌没有被标记撤销"
        assert old_row.replaced_by_jti == new_claims["jti"], "轮换链断了"
        assert new_row.revoked_at is None
        # 同一条链：否则重放检测会误判成另一台设备
        assert new_row.family_id == old_row.family_id
        assert new_row.family_id == old_claims["fam"]

    def test_rotation_keeps_family_the_same_across_many_rounds(self, test_db):
        """连续轮换始终同链，且每次只增加一行（这解释了为什么需要清理任务）"""
        client = _client()
        data = _register(client, "chain")
        family = _claims(data["refresh_token"])["fam"]
        raw = data["refresh_token"]
        for _ in range(3):
            resp = client.post("/api/auth/refresh", json={"refresh_token": raw})
            assert resp.status_code == 200, resp.text
            raw = resp.json()["refresh_token"]

        rows = _rows_sync(test_db, family_id=family)
        assert len(rows) == 4, f"1 次登录 + 3 次轮换应为 4 行，实际 {len(rows)}"
        assert {r.family_id for r in rows} == {family}
        # 只有最后一枚还有效
        alive = [r for r in rows if r.revoked_at is None]
        assert len(alive) == 1
        assert alive[0].jti == _claims(raw)["jti"]
        # 轮换链完整：除最后一枚外，每行都指向下一枚
        replaced = {r.replaced_by_jti for r in rows if r.replaced_by_jti}
        assert len(replaced) == 3, "replace 链不完整，无法追溯轮换过程"

    def test_new_logins_open_separate_families(self, test_db):
        """每次登录新开一条链 —— 这是"登出只影响本设备"的前提"""
        client = _client()
        first = _register(client, "dev1")
        second = client.post("/api/auth/login", json={
            "email": "refresh-dev1@example.com", "password": "QuietMeadow987!",
        })
        assert second.status_code == 200, second.text
        f1 = _claims(first["refresh_token"])["fam"]
        f2 = _claims(second.json()["refresh_token"])["fam"]
        assert f1 != f2, "两次登录共用了同一条链（会导致登出误杀其他设备）"


# ---------------------------------------------------------------------------
# 4. 类型混淆（HTTP 层）
# ---------------------------------------------------------------------------

class TestTypeConfusionOverHTTP:
    def test_refresh_token_rejected_as_access_token(self, test_db):
        """★ 刷新令牌不能调业务接口

        它的有效期是 30 天；如果它能当访问令牌用，等于把整条会话的有效期
        从 24 小时悄悄拉到 30 天。
        """
        client = _client()
        data = _register(client, "confuse1")
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {data['refresh_token']}"},
        )
        assert resp.status_code == 401, f"刷新令牌被当成了访问令牌: {resp.text}"
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        # 对照：同一用户的访问令牌必须能过（防止"全都 401"式的伪修复）
        assert _get("/api/auth/me", data["access_token"]) == 200

    def test_access_token_rejected_as_refresh_token(self, test_db):
        """★ 反向：访问令牌不能当刷新令牌用，且**不影响**真实刷新令牌

        反方向如果漏掉，任何一枚短期令牌都能被拿去换一枚 30 天的长期令牌
        （权限提升）。后半句同样重要：拒绝一个非法输入不能产生副作用。
        """
        client = _client()
        data = _register(client, "confuse2")
        resp = client.post("/api/auth/refresh", json={"refresh_token": data["access_token"]})
        assert resp.status_code == 401, f"访问令牌被当成了刷新令牌: {resp.text}"

        # 真实刷新令牌仍然可用 —— 证明上面那次拒绝没有误伤（没有撤销整链）
        ok = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert ok.status_code == 200, f"非法刷新请求把有效会话一起弄坏了: {ok.text}"

    def test_revoked_refresh_token_still_cannot_be_an_access_token(self, test_db):
        """已被撤销的刷新令牌在任何位置都不能用"""
        client = _client()
        data = _register(client, "confuse3")
        assert client.post("/api/auth/logout", json={
            "refresh_token": data["refresh_token"],
        }).status_code == 200
        assert _get("/api/auth/me", data["refresh_token"]) == 401


# ---------------------------------------------------------------------------
# 5. 重放检测
# ---------------------------------------------------------------------------

class TestReplayDetection:
    def test_replay_revokes_entire_family(self, test_db):
        """★ 重放已撤销的刷新令牌 = 盗用信号：整条链立刻作废

        场景：攻击者复制了令牌 A，用户已经用 A 换成了 B。
        两边都会继续用 —— 后用的那一方必然触发本次检测。
        处置必须是"整链撤销"：只拒绝这一次，等于让攻击者与用户
        继续各持一枚有效令牌赛跑。
        """
        client = _client()
        data = _register(client, "replay1")
        first = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert first.status_code == 200
        rotated = first.json()

        # 重放已经用掉的那一枚
        replay = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert replay.status_code == 401, f"重放已撤销的令牌竟然成功了: {replay.text}"

        # 整条链（含刚轮换出来的那一枚）全部被撤销
        family = _claims(data["refresh_token"])["fam"]
        rows = _rows_sync(test_db, family_id=family)
        assert len(rows) >= 2
        assert all(r.revoked_at is not None for r in rows), (
            "重放检测没有撤销整条链，仍有效的行: "
            f"{[r.jti[:8] for r in rows if r.revoked_at is None]}"
        )
        # 刚换出来的那一枚也必须失效（这才是"整链"的意义）
        still = client.post("/api/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
        assert still.status_code == 401, "轮换出的新令牌在整链撤销后仍然可用"

    def test_replay_does_not_affect_other_families(self, test_db):
        """整链撤销的作用域是**一条链**：其他设备的会话不受影响"""
        client = _client()
        victim = _register(client, "replay2")
        other = client.post("/api/auth/login", json={
            "email": "refresh-replay2@example.com", "password": "QuietMeadow987!",
        }).json()

        assert client.post("/api/auth/refresh", json={
            "refresh_token": victim["refresh_token"],
        }).status_code == 200
        assert client.post("/api/auth/refresh", json={
            "refresh_token": victim["refresh_token"],
        }).status_code == 401  # 触发整链撤销

        ok = client.post("/api/auth/refresh", json={"refresh_token": other["refresh_token"]})
        assert ok.status_code == 200, f"另一台设备的会话被误杀: {ok.text}"

    def test_replay_of_logout_revoked_token_is_rejected(self, test_db):
        """登出撤销的令牌再被使用同样拒绝（撤销原因不影响判据）"""
        client = _client()
        data = _register(client, "replay3")
        assert client.post("/api/auth/logout", json={
            "refresh_token": data["refresh_token"],
        }).json()["revoked"] == 1

        replay = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert replay.status_code == 401


# ---------------------------------------------------------------------------
# 6. 登出
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_revokes_presented_token(self, test_db):
        """★ 登出后刷新令牌立刻失效（这就是"token 可吊销"的验收点）"""
        client = _client()
        data = _register(client, "logout1")
        assert _get("/api/auth/me", data["access_token"]) == 200

        resp = client.post("/api/auth/logout", json={"refresh_token": data["refresh_token"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked"] == 1

        row = _rows_sync(test_db, jti=_claims(data["refresh_token"])["jti"])[0]
        assert row.revoked_at is not None
        # 会话无法再被延长
        assert client.post("/api/auth/refresh", json={
            "refresh_token": data["refresh_token"],
        }).status_code == 401

    def test_logout_leaves_other_sessions_alive(self, test_db):
        """★ 登出是"按链"的：另一台设备（另一条链）完全不受影响

        如果实现成"按用户撤销"，用户在一个标签页登出会把手机也踢下线。
        """
        client = _client()
        phone = _register(client, "logout2")
        laptop = client.post("/api/auth/login", json={
            "email": "refresh-logout2@example.com", "password": "QuietMeadow987!",
        }).json()

        assert client.post("/api/auth/logout", json={
            "refresh_token": phone["refresh_token"],
        }).json()["revoked"] == 1

        assert client.post("/api/auth/refresh", json={
            "refresh_token": phone["refresh_token"],
        }).status_code == 401
        alive = client.post("/api/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
        assert alive.status_code == 200, f"登出把另一台设备也踢下线了: {alive.text}"

    def test_logout_all_devices_kills_other_sessions(self, test_db):
        """★ `all_devices=true`：该用户所有链一并撤销（"退出所有设备"）"""
        client = _client()
        phone = _register(client, "logout3")
        laptop = client.post("/api/auth/login", json={
            "email": "refresh-logout3@example.com", "password": "QuietMeadow987!",
        }).json()

        resp = client.post("/api/auth/logout", json={
            "refresh_token": phone["refresh_token"], "all_devices": True,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked"] >= 2, "没有把另一台设备的令牌一起撤销"

        for name, pair in (("手机", phone), ("笔记本", laptop)):
            dead = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
            assert dead.status_code == 401, f"{name}的会话没有被撤销"

    def test_documented_limitation_access_token_survives_until_expiry(self, test_db):
        """⚠️ **已知取舍**（不是缺陷，是刻意的边界）：访问令牌在 exp 前仍然可用

        访问令牌是无状态的：要让它在登出瞬间失效，就必须在每个请求上多查
        一次黑名单，并且把"清理过期行"与"会话是否有效"耦合起来
        （清理掉证据就会误杀有效会话）。本轮不做，改为让**会话无法被延长** ——
        下面的断言把这个边界写清楚：如果想改成"登出即失效"，
        这条测试就是需要被推翻的那一条（见附录 BA.3）。
        """
        client = _client()
        data = _register(client, "logout4")
        assert client.post("/api/auth/logout", json={
            "refresh_token": data["refresh_token"],
        }).json()["revoked"] == 1

        # 已签发的访问令牌仍能用（无状态令牌的固有性质）
        assert _get("/api/auth/me", data["access_token"]) == 200
        # 但会话无法延长：刷新分支已死
        assert client.post("/api/auth/refresh", json={
            "refresh_token": data["refresh_token"],
        }).status_code == 401


class TestLogoutIsAlwaysCallable:
    """登出必须在任何情况下都能调用（要求 5 的关键论证）

    "清掉服务端状态"这件事如果依赖令牌仍然有效，就会在最需要它的时候失效：
    令牌已过期、已被轮换、已被撤销 —— 这些恰恰是用户来点登出的常见状态。
    因此本类逐条验证"无效输入不会让登出失败"，**同时**验证"无效输入也
    撤销不了任何东西"（否则免认证的登出就成了任意注销他人会话的入口）。
    """

    @pytest.mark.parametrize("body", [
        {},
        {"refresh_token": None},
        {"refresh_token": ""},
        {"refresh_token": "not-a-jwt"},
        {"refresh_token": "a.b.c"},
        {"all_devices": True},
    ])
    def test_invalid_input_still_returns_200(self, test_db, body):
        resp = _client().post("/api/auth/logout", json=body)
        assert resp.status_code == 200, f"登出对 {body} 返回了 {resp.status_code}"
        assert resp.json()["revoked"] == 0

    def test_no_request_body_at_all_still_returns_200(self, test_db):
        """连请求体都不带也必须能登出（客户端状态残缺是最常见的现实）

        这是"登出永远可调用"的最后一道形态：`LogoutRequest` 里的字段全可选，
        整个请求体本身也是可选的（否则 FastAPI 会回 422，而 422 意味着
        "服务端什么都没清"）。
        """
        resp = _client().post("/api/auth/logout")
        assert resp.status_code == 200, f"不带请求体的登出返回了 {resp.status_code}"
        assert resp.json()["revoked"] == 0

    def test_logout_with_access_token_revokes_nothing(self, test_db):
        """拿访问令牌登出不会撤销任何东西，也不会破坏真实会话

        两件事一起验：`revoked=0`（没撤销）**且**真实刷新令牌随后仍可用。
        只断言前者的话，一个"按 sub 撤销全部令牌"的实现也能通过。
        """
        client = _client()
        data = _register(client, "logout5")
        resp = client.post("/api/auth/logout", json={"refresh_token": data["access_token"]})
        assert resp.status_code == 200
        assert resp.json()["revoked"] == 0

        alive = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert alive.status_code == 200, "拿访问令牌登出竟然撤销了真实会话"

    def test_logout_is_idempotent(self, test_db):
        """重复登出（多标签页先后收尾）不报错、第二次撤销 0 行"""
        client = _client()
        data = _register(client, "logout6")
        first = client.post("/api/auth/logout", json={"refresh_token": data["refresh_token"]})
        second = client.post("/api/auth/logout", json={"refresh_token": data["refresh_token"]})
        assert (first.status_code, first.json()["revoked"]) == (200, 1)
        assert (second.status_code, second.json()["revoked"]) == (200, 0)

    def test_logout_all_devices_with_invalid_token_revokes_nothing(self, test_db):
        """`all_devices=true` 也不能凭一个无法验证的令牌去撤销别人的会话"""
        client = _client()
        data = _register(client, "logout7")
        resp = client.post("/api/auth/logout", json={
            "refresh_token": "not-a-jwt", "all_devices": True,
        })
        assert resp.json()["revoked"] == 0
        alive = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert alive.status_code == 200, "无效令牌竟然撤销了他人全部会话"


# ---------------------------------------------------------------------------
# 7. 过期
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExpiry:
    async def test_expired_refresh_token_is_rejected(self, test_db):
        """★ 过期刷新令牌 → 401（签名校验就会拦下，根本不查表）"""
        async with test_db() as session:
            user = User(
                id=str(uuid.uuid4()), email="expired@example.com", username="expireduser",
                hashed_password="x", is_active=True,
            )
            session.add(user)
            await session.commit()
            # 31 天前签发（默认有效期 30 天）→ 出生即过期
            stale, _ = await issue_refresh_token(
                session, user.id,
                now=datetime.now(timezone.utc) - timedelta(days=31),
            )
            await session.commit()

        resp = _client().post("/api/auth/refresh", json={"refresh_token": stale})
        assert resp.status_code == 401, resp.text
        assert "重新登录" in resp.json()["detail"]

    async def test_expired_token_is_not_treated_as_replay(self, test_db):
        """过期不等于重放：不能因为"过期的令牌又被拿来用"就撤销整链

        判据必须落在**签名/过期校验**上（连表都不用查）。若实现改成"先查表、
        看见就撤"，那么一枚过期令牌的重放会误杀用户当前有效的会话。
        """
        async with test_db() as session:
            user = User(
                id=str(uuid.uuid4()), email="expired2@example.com", username="expireduser2",
                hashed_password="x", is_active=True,
            )
            session.add(user)
            await session.commit()
            stale, stale_row = await issue_refresh_token(
                session, user.id,
                now=datetime.now(timezone.utc) - timedelta(days=31),
            )
            fresh, _ = await issue_refresh_token(session, user.id)  # 另一条链，仍有效
            await session.commit()
            stale_jti = stale_row.jti

        client = _client()
        assert client.post("/api/auth/refresh", json={"refresh_token": stale}).status_code == 401

        # 过期那一行没有被"顺手"标记撤销（说明拒绝发生在查表之前）
        rows = await _rows(test_db, jti=stale_jti)
        assert rows[0].revoked_at is None
        # 有效的那条链照常工作
        assert client.post("/api/auth/refresh", json={"refresh_token": fresh}).status_code == 200

    async def test_expired_token_can_still_log_out_itself(self, test_db):
        """过期令牌登出返回 200（幂等），但撤销不到任何东西 —— 如实返回 0"""
        async with test_db() as session:
            user = User(
                id=str(uuid.uuid4()), email="expired3@example.com", username="expireduser3",
                hashed_password="x", is_active=True,
            )
            session.add(user)
            await session.commit()
            stale, _ = await issue_refresh_token(
                session, user.id,
                now=datetime.now(timezone.utc) - timedelta(days=31),
            )
            await session.commit()

        resp = _client().post("/api/auth/logout", json={"refresh_token": stale})
        assert resp.status_code == 200
        assert resp.json()["revoked"] == 0


# ---------------------------------------------------------------------------
# 8. 向后兼容（要求 6）
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_legacy_access_token_without_typ_still_works(self, test_db):
        """★ 改造前签发的令牌（没有 typ）必须继续可用到自然过期

        这正是"升级动作不能把正在使用的人踢下线"的验收点。用真实签名密钥
        手工构造一枚旧格式令牌 —— 与 `create_access_token` 改造前的实现一致。
        """
        client = _client()
        data = _register(client, "legacy")
        legacy = jwt.encode(
            {"sub": data["user"]["id"],
             "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
            settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
        )
        assert "typ" not in _claims(legacy), "构造出来的不是旧格式令牌，用例无效"

        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
        assert resp.status_code == 200, f"存量令牌被升级动作弄失效了: {resp.text}"
        assert resp.json()["email"] == "refresh-legacy@example.com"

    def test_new_access_tokens_do_carry_typ(self, test_db):
        """兼容规则不能掩盖"新令牌其实没写 typ"这种实现错误"""
        data = _register(_client(), "legacy2")
        claims = _claims(data["access_token"])
        assert claims["typ"] == ACCESS_TOKEN_TYPE
        assert claims.get("iat"), "新访问令牌缺少 iat"

    def test_legacy_token_cannot_be_used_as_refresh_token(self, test_db):
        """兼容只针对访问令牌方向：旧令牌不能拿来换新令牌对"""
        client = _client()
        data = _register(client, "legacy3")
        legacy = jwt.encode(
            {"sub": data["user"]["id"],
             "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
            settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
        )
        resp = client.post("/api/auth/refresh", json={"refresh_token": legacy})
        assert resp.status_code == 401

    def test_access_token_lifetime_unchanged(self):
        """访问令牌有效期保持 1440 分钟（要求 7：没有理由就不改）

        改它会改变所有客户端的续期频率，属于独立的、需要单独观察的变更。
        """
        assert settings.jwt_expire_minutes == 1440

    def test_refresh_lifetime_default(self):
        """刷新令牌有效期默认 30 天，且真的被用进了 exp"""
        from app.config import Settings

        assert Settings(jwt_secret_key="k").refresh_token_expire_days == 30

    def test_refresh_exp_matches_configured_lifetime(self, test_db):
        data = _register(_client(), "legacy4")
        claims = _claims(data["refresh_token"])
        exp = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        expected = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
        assert abs(exp - expected) < timedelta(minutes=2)


# ---------------------------------------------------------------------------
# 8.5 配置防御：非正的有效期必须显式失败
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestConfigGuard:
    async def test_non_positive_lifetime_is_rejected(self, test_db, monkeypatch):
        """★ `refresh_token_expire_days <= 0` 必须报错，而不是签发一堆废令牌

        非正数会签出"出生即过期"的令牌：登录看起来成功（HTTP 200、字段齐全），
        但下一次刷新必然 401 —— 症状是"用户每 24 小时被登出一次"，
        而没有任何一条日志说得出原因。配置错误应当当场失败。
        """
        from app.services import refresh_token_service as rts

        monkeypatch.setattr(rts.settings, "refresh_token_expire_days", 0)
        async with test_db() as session:
            with pytest.raises(ValueError, match="refresh_token_expire_days"):
                await rts.issue_refresh_token(session, "u-1")
        # 拒绝发生在写库之前：库里不留半截数据
        assert await _rows(test_db) == []


# ---------------------------------------------------------------------------
# 9. 日志不泄露令牌（要求 9）
# ---------------------------------------------------------------------------
class TestLogging:
    def test_full_tokens_never_reach_logs(self, test_db, caplog):
        """★ 登录/轮换/重放/登出全程，日志里不得出现完整令牌

        "没打令牌"这件事很容易在一次重构里丢掉（有人加一条
        `logger.info("token=%s", raw)` 就完了）。因此这里既断言**不出现**，
        也断言 jti 前缀**出现了** —— 后者保证断言不是"因为压根没记日志"而通过。
        """
        client = _client()
        with caplog.at_level(logging.INFO, logger="app.services.refresh_token_service"):
            data = _register(client, "logging")
            rotated = client.post("/api/auth/refresh", json={
                "refresh_token": data["refresh_token"],
            }).json()
            client.post("/api/auth/refresh", json={  # 重放 → 走 warning 分支
                "refresh_token": data["refresh_token"],
            })
            client.post("/api/auth/logout", json={"refresh_token": rotated["refresh_token"]})

        text = caplog.text
        assert text.strip(), "没有任何日志被捕获 —— 这条断言会空转"
        for raw in (data["access_token"], data["refresh_token"], rotated["refresh_token"]):
            assert raw not in text, "日志里出现了完整令牌"
        # 可观测性仍然存在：jti 前缀被记下来了
        assert _claims(data["refresh_token"])["jti"][:8] in text


# ---------------------------------------------------------------------------
# 10. 接线：端点真的调用了服务层
# ---------------------------------------------------------------------------

class TestWiring:
    def test_endpoints_are_registered(self):
        """刷新/登出必须真实注册（OpenAPI 里能查到的非空断言）"""
        from app.main import app

        paths = app.openapi()["paths"]
        assert "/api/auth/refresh" in paths, "刷新端点没有注册"
        assert "/api/auth/logout" in paths, "登出端点没有注册"
        assert "post" in paths["/api/auth/refresh"]
        assert "post" in paths["/api/auth/logout"]
        # 刷新/登出的凭证在 body 里，因此**不应**声明 Bearer 安全方案
        assert not paths["/api/auth/refresh"]["post"].get("security")
        assert not paths["/api/auth/logout"]["post"].get("security")

    def test_refresh_endpoint_calls_service(self, test_db, monkeypatch):
        """★ 端点必须真的经过 `rotate_refresh_token`（而不是自己糊一套）"""
        from app.api import auth as auth_api
        from app.services import refresh_token_service

        calls = []
        real = refresh_token_service.rotate_refresh_token

        async def spy(db, raw_token, **kwargs):
            calls.append(raw_token)
            return await real(db, raw_token, **kwargs)

        monkeypatch.setattr(auth_api, "rotate_refresh_token", spy)
        client = _client()
        data = _register(client, "wiring")
        resp = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert resp.status_code == 200, resp.text
        assert calls == [data["refresh_token"]], "刷新端点没有把令牌交给服务层"

    def test_login_endpoint_persists_through_service(self, test_db, monkeypatch):
        """★ 登录必须真的签发刷新令牌（把服务层换成桩，验证调用次数）"""
        from app.api import auth as auth_api
        from app.services import refresh_token_service

        calls = []
        real = refresh_token_service.issue_refresh_token

        async def spy(db, user_id, **kwargs):
            calls.append(user_id)
            return await real(db, user_id, **kwargs)

        monkeypatch.setattr(auth_api, "issue_refresh_token", spy)
        client = _client()
        data = _register(client, "wiring2")
        assert calls == [data["user"]["id"]], f"登录没有签发刷新令牌: {calls}"

    def test_rate_limit_rules_cover_new_endpoints(self):
        """新端点是"未认证 + 可写库"，必须挂上限流规则（否则是免费的口令喷射台）"""
        from app.middleware import rate_limit as rl

        for path in ("/api/auth/refresh", "/api/auth/logout"):
            assert rl._match_rule("POST", path) is not None, f"{path} 没有被限流覆盖"


# ---------------------------------------------------------------------------
# 工具：同步测试里查库（TestClient 用例是同步函数）
# ---------------------------------------------------------------------------

def _rows_sync(test_db, **filters) -> List[RefreshToken]:
    """在同步用例里跑一次异步查询（自建事件循环，避免与 pytest-asyncio 冲突）"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_rows(test_db, **filters))
    finally:
        loop.close()

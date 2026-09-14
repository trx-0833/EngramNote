# -*- coding: utf-8 -*-
"""`GET /api/review/reminders` 的兜底不能吞掉已知错误契约（阶段 0.11 收尾）

## 这份测试防的是什么

该端点有一段"已知异常透传 + 其余兜底成 500"的写法：

    except HTTPException: raise
    except Exception as e: ... raise AppError(REVIEW_REMINDERS_FAILED, ..., 500)

阶段 0.11 把全仓 152 处 `HTTPException` 迁到 `AppError` 之后，第一条就**只认旧类型**
了：服务层若（现在或将来）抛出 `AppError`，它会掉进第二条，被改写成
"500 + REVIEW_REMINDERS_FAILED"。状态码与 `error_code` 双双变化，而这**不会报错**：
请求照样有响应、日志照样有记录，只有按码分流的前端会静默走错分支。

## 分支今天可达吗

**不可达**：`NotificationService.get_reminders` 目前只做三次只读 `db.execute`
并返回 dict，既不抛 `HTTPException` 也不抛 `AppError`。所以这里不去"触发"它，
而是用 monkeypatch **注入**一个，把意图钉在测试里 —— 一旦有人重构那段兜底，
这里会红，而不是等到线上某个 4xx 变成 500。

三条用例分别钉住三种输入的命运：
    注入 AppError        → 状态码与 error_code **原样透传**（本次修复的判据）
    注入 HTTPException   → 同样原样透传（原本就想表达的那条）
    注入其它异常         → 仍然是 500 + REVIEW_REMINDERS_FAILED（兜底没被削弱）
外加一条真实请求的成功路径，确认这个端点本身没被测试手法弄坏。
"""

import uuid

import pytest

#: 注入用：任何一个**已声明**的错误码都行，这里选一个 409 的，好与兜底的 500 区分
_INJECTED_STATUS = 409


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    # 每个用例换一个客户端 IP：注册接口 5 次/分钟，共用 IP 会撞 429
    _client._seq = getattr(_client, "_seq", 0) + 1
    return TestClient(app, client=(f"198.51.100.{230 + _client._seq}", 9851))


def _auth(client) -> dict:
    suffix = uuid.uuid4().hex[:8]
    resp = client.post("/api/auth/register", json={
        "email": f"reminders+{suffix}@example.com",
        "username": "rem" + suffix,
        "password": "RemindPass123!",
    })
    assert resp.status_code in (200, 201), resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _inject(monkeypatch, exc: BaseException):
    """让服务层的 get_reminders 抛出指定异常

    注入在**服务层**（而不是替换路由函数）：这样走的还是那条 try/except，
    测的正是它的分类行为。
    """
    from app.api import review

    async def _raise(user_id, db):
        raise exc

    monkeypatch.setattr(review._notification_service, "get_reminders", _raise)


class TestRemindersSwallowingGuard:

    def test_app_error_passes_through_with_its_own_status_and_code(
        self, test_db, monkeypatch
    ):
        """**核心**：注入的 AppError 必须原样透传（而不是变成 500）"""
        from app.core.app_error import CARD_REVIEW_STATE_UNAVAILABLE, AppError

        _inject(monkeypatch, AppError(
            CARD_REVIEW_STATE_UNAVAILABLE, "注入的业务错误", _INJECTED_STATUS,
        ))
        client = _client()

        resp = client.get("/api/review/reminders", headers=_auth(client))

        assert resp.status_code == _INJECTED_STATUS, (
            "服务层抛出的 AppError 被兜底改写了状态码 —— "
            f"响应: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert body["error_code"] == CARD_REVIEW_STATE_UNAVAILABLE, (
            "服务层抛出的 AppError 被兜底改写成了别的错误码（很可能是 "
            f"REVIEW_REMINDERS_FAILED）: {body}"
        )
        assert body["detail"] == "注入的业务错误"

    def test_http_exception_also_passes_through(self, test_db, monkeypatch):
        """原本就想表达的那条（HTTPException）没被改坏"""
        from fastapi import HTTPException

        _inject(monkeypatch, HTTPException(status_code=418, detail="注入的 HTTP 异常"))
        client = _client()

        resp = client.get("/api/review/reminders", headers=_auth(client))

        assert resp.status_code == 418
        assert resp.json()["error_code"] == "HTTP_418"

    def test_unknown_exception_still_becomes_the_stable_error_code(
        self, test_db, monkeypatch
    ):
        """兜底**没有被削弱**：真正的意外仍然收敛成 500 + 稳定错误码

        这条与上面两条同样重要 —— 修"透传面太窄"时很容易把兜底一起删掉，
        那会让内部异常直接以未知形态冒到中间件（详情还会进响应体）。
        """
        _inject(monkeypatch, RuntimeError("数据库连接炸了"))
        client = _client()

        resp = client.get("/api/review/reminders", headers=_auth(client))

        assert resp.status_code == 500
        assert resp.json()["error_code"] == "REVIEW_REMINDERS_FAILED"
        # 内部细节不外泄（判据同 tests/test_error_leakage.py）
        assert "数据库连接炸了" not in resp.text

    def test_happy_path_still_returns_reminders(self, test_db):
        """真实服务层（空库）走通：响应形状不变，且没有多余的异常分类副作用"""
        client = _client()

        resp = client.get("/api/review/reminders", headers=_auth(client))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["due_count"] == 0
        assert body["due_in_1h_count"] == 0
        assert body["weak_point_count"] == 0
        assert "last_reminded_at" in body


@pytest.mark.parametrize("exc_status", [400, 404, 409])
def test_injected_status_is_not_normalised_to_500(test_db, monkeypatch, exc_status):
    """状态码不因经过这段兜底而被"归一"成 500（三个取值各测一次）"""
    from app.core.app_error import REVIEW_QUIZ_NOT_FOUND, AppError

    _inject(monkeypatch, AppError(REVIEW_QUIZ_NOT_FOUND, "注入", exc_status))
    client = _client()

    resp = client.get("/api/review/reminders", headers=_auth(client))

    assert resp.status_code == exc_status
    assert resp.json()["error_code"] == REVIEW_QUIZ_NOT_FOUND

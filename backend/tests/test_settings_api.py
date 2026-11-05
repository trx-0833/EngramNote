"""设置接口的端到端契约：注册 → 读默认 → 改 → 读回 → 复习接口生效

这条用例把"设置页的 PATCH"与"复习接口的 daily_limit"接在一起，
证明联动不是只发生在 service 单测里。
"""

from fastapi.testclient import TestClient

from app.main import app


def _client() -> TestClient:
    # 固定客户端地址，避免注册限流按 IP 把同一测试进程的多次注册算到一起
    return TestClient(app, client=("198.51.100.23", 9231))


def _register(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": "settings-e2e@example.com",
            "username": "settingse2e",
            "password": "TestPass123!",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_settings_patch_flows_into_review_stats(test_db):
    client = _client()
    headers = _register(client)

    snapshot = client.get("/api/settings", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["study"]["daily_review_limit"] == 10
    assert body["reminders"]["email_hour"] == 9
    assert body["account"]["email"] == "settings-e2e@example.com"

    usage = client.get("/api/settings/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    assert usage.json()["storage"]["used_bytes"] == 0
    assert usage.json()["storage"]["quota_bytes"] >= 0

    patched = client.patch(
        "/api/settings",
        headers=headers,
        json={"study": {"daily_review_limit": 25}, "reminders": {"email_hour": 7}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["study"]["daily_review_limit"] == 25
    assert patched.json()["reminders"]["email_hour"] == 7

    # 复习接口读的是同一份有效设置，不是模块常量
    stats = client.get("/api/review/stats", headers=headers)
    assert stats.status_code == 200, stats.text
    assert stats.json()["daily_limit"] == 25

    # 重新读取仍然存在（不是只在响应里改了一下）
    again = client.get("/api/settings", headers=headers)
    assert again.json()["study"]["daily_review_limit"] == 25


def test_settings_rejects_unknown_field_and_out_of_range(test_db):
    client = _client()
    headers = _register(client)

    unknown = client.patch(
        "/api/settings",
        headers=headers,
        json={"study": {"daily_limit": 20}},
    )
    assert unknown.status_code == 422

    out_of_range = client.patch(
        "/api/settings",
        headers=headers,
        json={"study": {"daily_review_limit": 4}},
    )
    assert out_of_range.status_code == 422

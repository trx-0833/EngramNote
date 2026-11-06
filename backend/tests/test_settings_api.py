"""设置接口的端到端契约：注册 → 读默认 → 改 → 读回 → 复习接口生效

这条用例把"设置页的 PATCH"与"复习接口的 daily_limit"接在一起，
证明联动不是只发生在 service 单测里。
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import settings as settings_api
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
    assert body["study"]["desired_retention"] == 0.9
    assert body["reminders"]["email_hour"] == 9
    assert body["content"]["output_locale"] == "follow"
    assert body["content"]["card_max_new_per_run"] == 500
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

    content_patch = client.patch(
        "/api/settings",
        headers=headers,
        json={
            "study": {"desired_retention": 0.95, "max_interval_days": 365},
            "content": {
                "output_locale": "en",
                "card_max_new_per_run": 50,
                "clean_simplify_formula": True,
                "clean_normalize_spacing": False,
                "clean_normalize_heading_levels": False,
            },
        },
    )
    assert content_patch.status_code == 200, content_patch.text
    content = content_patch.json()
    assert content["study"]["desired_retention"] == 0.95
    assert content["study"]["max_interval_days"] == 365
    assert content["content"]["output_locale"] == "en"
    assert content["content"]["card_max_new_per_run"] == 50
    assert content["content"]["clean_simplify_formula"] is True
    assert content["content"]["clean_normalize_spacing"] is False
    assert content["content"]["clean_normalize_heading_levels"] is False

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


def test_email_configured_requires_password(test_db, monkeypatch):
    """只有 host/user、没有 SMTP 密码时不能宣称“可发信”

    真实发信在 notification_service 里才失败，并被吞成 logger.error；设置页
    若显示 email_configured=true，用户会以为邮件通道正常。
    """
    client = _client()
    headers = _register(client)
    fake = SimpleNamespace(
        email_reminder_enabled=True,
        smtp_host="smtp.example.com",
        smtp_user="mailer",
        smtp_password="",
        app_env="dev",
    )
    monkeypatch.setattr(settings_api, "get_settings", lambda: fake)

    without_password = client.get("/api/settings", headers=headers)
    assert without_password.status_code == 200, without_password.text
    assert without_password.json()["reminders"]["email_configured"] is False

    fake.smtp_password = "secret"
    with_password = client.get("/api/settings", headers=headers)
    assert with_password.status_code == 200, with_password.text
    assert with_password.json()["reminders"]["email_configured"] is True


"""用户设置：默认值、覆盖、隔离与校验

这些用例不经过 HTTP，而是直接打 service / schema —— 设置的"联动"发生在
服务层（review 读每日上限、reminder 读发送小时），API 层只是搬运。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.user_settings import UserSettings
from app.schemas.settings import SettingsPatch
from app.services import user_settings_service


@pytest.mark.asyncio
async def test_get_effective_returns_env_defaults_without_row(test_db, test_user_factory):
    user_id, _, _ = await test_user_factory(1)

    async with test_db() as db:
        values = await user_settings_service.get_effective_settings(db, user_id)
        assert values["daily_review_limit"] == 10
        assert values["email_hour"] == 9

        # GET 不写库：没有行就是没有行
        rows = await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        assert rows.scalars().first() is None


@pytest.mark.asyncio
async def test_update_creates_row_and_partial_update_keeps_other_field(
    test_db, test_user_factory
):
    user_id, _, _ = await test_user_factory(1)

    async with test_db() as db:
        first = await user_settings_service.update_effective_settings(
            db, user_id, daily_review_limit=25
        )
        assert first["daily_review_limit"] == 25
        assert first["email_hour"] == 9
        assert first["desired_retention"] == 0.9

        second = await user_settings_service.update_effective_settings(
            db, user_id, email_hour=7
        )
        assert second["daily_review_limit"] == 25
        assert second["email_hour"] == 7

        third = await user_settings_service.update_effective_settings(
            db, user_id, email_hour=8
        )
        assert third["daily_review_limit"] == 25
        assert third["email_hour"] == 8


@pytest.mark.asyncio
async def test_settings_are_isolated_between_users(test_db, test_user_factory):
    alice, _, _ = await test_user_factory(1)
    bob, _, _ = await test_user_factory(2)

    async with test_db() as db:
        await user_settings_service.update_effective_settings(
            db, alice, daily_review_limit=5
        )
        bob_values = await user_settings_service.get_effective_settings(db, bob)
        assert bob_values["daily_review_limit"] == 10


@pytest.mark.asyncio
async def test_update_rejects_out_of_range_values_at_database_layer(
    test_db, test_user_factory
):
    """应用层校验之外，数据库 CHECK 也必须挡住越界（防止脚本/未来入口绕过）"""
    from sqlalchemy.exc import IntegrityError

    user_id, _, _ = await test_user_factory(1)
    async with test_db() as db:
        db.add(UserSettings(user_id=user_id, daily_review_limit=4, email_hour=9))
        with pytest.raises(IntegrityError):
            await db.commit()


def test_patch_schema_rejects_out_of_range_and_unknown_fields():
    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"study": {"daily_review_limit": 4}})

    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"study": {"daily_review_limit": 101}})

    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"study": {"daily_limit": 20}})

    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"reminders": {"email_hour": 24}})

    for bad in (
        {"study": {"desired_retention": 0.92}},
        {"study": {"max_interval_days": 400}},
        {"content": {"output_locale": "fr"}},
        {"content": {"card_max_new_per_run": 10}},
    ):
        with pytest.raises(ValidationError):
            SettingsPatch.model_validate(bad)

    # 合法值 + 只给一段
    patch = SettingsPatch.model_validate({"reminders": {"email_hour": 7}})
    assert patch.study is None
    assert patch.reminders is not None
    assert patch.reminders.email_hour == 7


def test_settings_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/settings" in paths
    assert "/api/settings/usage" in paths


def test_should_send_now_uses_business_timezone_and_daily_dedupe():
    from app.tasks.reminder_tasks import should_send_now

    # 2026-11-04 01:00 UTC = 09:00 Asia/Shanghai
    nine_am = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    ten_am = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)

    user = SimpleNamespace(last_reminded_at=None)
    pref = SimpleNamespace(email_hour=9)

    assert should_send_now(user, pref, nine_am) is True
    assert should_send_now(user, pref, ten_am) is False

    # 同一天已经发过（08:30 上海）→ 不再发
    user.last_reminded_at = datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc)
    assert should_send_now(user, pref, nine_am) is False

    # 昨天的记录 → 今天可以再发
    user.last_reminded_at = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
    assert should_send_now(user, pref, nine_am) is True

    # 没有设置行 → 回落到环境默认 9 点
    assert should_send_now(SimpleNamespace(last_reminded_at=None), None, nine_am) is True


@pytest.mark.asyncio
async def test_daily_max_is_honored_by_stats_and_submit(
    test_db, test_user_factory, monkeypatch
):
    """设置不只是存起来：review 服务必须按传入的 daily_max 计算与拦截"""
    from app.models.quiz_item import QuestionType, QuizItem
    from app.services import review_service

    user_id, _, _ = await test_user_factory(1)
    async with test_db() as db:
        quiz = QuizItem(
            user_id=user_id,
            question_type=QuestionType.choice,
            question="1+1=?",
            answer="2",
            next_review_at=None,
        )
        db.add(quiz)
        await db.commit()
        await db.refresh(quiz)

        stats = await review_service.get_review_stats(user_id, db, daily_max=7)
        assert stats["daily_limit"] == 7

        async def _seven_settled(*args, **kwargs):
            return 7

        monkeypatch.setattr(review_service, "_count_settled_today", _seven_settled)
        blocked = await review_service.submit_answer(
            quiz.id,
            user_id,
            "2",
            100,
            db,
            skip_due_check=True,
            daily_max=7,
        )
        assert "error" in blocked
        assert "7" in blocked["error"]

        # 上限放宽到 8 时同一道题可以进入判分流程（不再被限额拦截）
        allowed = await review_service.submit_answer(
            quiz.id,
            user_id,
            "2",
            100,
            db,
            skip_due_check=True,
            daily_max=8,
        )
        assert "error" not in allowed


@pytest.mark.asyncio
async def test_review_api_helper_reads_user_override(test_db, test_user_factory):
    """API 层解析的有效上限必须来自用户行，而不是模块常量"""
    from app.api.review import _effective_daily_limit

    user_id, _, _ = await test_user_factory(1)
    async with test_db() as db:
        await user_settings_service.update_effective_settings(
            db, user_id, daily_review_limit=25
        )
        assert await _effective_daily_limit(db, user_id) == 25


def test_email_task_is_scheduled_hourly():
    """Beat 必须每小时跑：写死 9:00 的话用户改 email_hour 永远不会生效"""
    from app.tasks.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule["send-daily-review-email"]["schedule"]
    assert schedule.minute == {0}
    assert len(schedule.hour) == 24


@pytest.mark.asyncio
async def test_reminder_task_selects_users_by_their_own_hour(
    test_db, test_user_factory
):
    """任务选人必须按每行 email_hour，而不是全局 9 点"""
    from app.tasks.reminder_tasks import select_due_recipients

    nine, _, _ = await test_user_factory(1)
    seven, _, _ = await test_user_factory(2)
    default_nine, _, _ = await test_user_factory(3)
    async with test_db() as db:
        db.add(UserSettings(user_id=seven, email_hour=7))
        db.add(UserSettings(user_id=nine, email_hour=9))
        await db.commit()

        # 2026-11-04 01:00 UTC = 09:00 Asia/Shanghai
        at_nine = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
        selected = await select_due_recipients(db, at_nine)
        selected_ids = {user.id for user, _ in selected}
        assert nine in selected_ids
        assert default_nine in selected_ids
        assert seven not in selected_ids

        # 7 点：只有 seven；9 点的那两位不选
        at_seven = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
        selected_seven = await select_due_recipients(db, at_seven)
        assert {user.id for user, _ in selected_seven} == {seven}

        # 当日已发过 → 去重
        from app.models.user import User

        user_row = await db.get(User, nine)
        user_row.last_reminded_at = datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc)
        await db.commit()
        selected_again = await select_due_recipients(db, at_nine)
        assert nine not in {user.id for user, _ in selected_again}


def test_resolve_output_locale_follows_user_and_falls_back():
    resolve = user_settings_service.resolve_output_locale
    assert resolve("follow", "en") == "en"
    assert resolve("follow", "zh-CN") == "zh-CN"
    assert resolve("zh-CN", "en") == "zh-CN"
    assert resolve("en", "zh-CN") == "en"
    assert resolve("follow", "klingon") == "zh-CN"
    assert resolve(None, None) == "zh-CN"


def test_scheduling_settings_does_not_mutate_global_config():
    base = SimpleNamespace(
        review_scheduler="fsrs",
        review_fuzz_ratio=0.05,
        fsrs_request_retention=0.9,
        fsrs_max_interval_days=3650,
    )
    view = user_settings_service.scheduling_settings(
        {"desired_retention": 0.8, "max_interval_days": 365}, base=base
    )
    assert view.fsrs_request_retention == 0.8
    assert view.fsrs_max_interval_days == 365
    # 全局配置不能被某个用户的覆盖值改掉
    assert base.fsrs_request_retention == 0.9
    assert base.fsrs_max_interval_days == 3650


def test_higher_retention_shortens_next_interval():
    """留存率不是只存起来：它必须真的进入 FSRS 的下一次间隔计算"""
    from app.models.review_state import ReviewState, ReviewStateKind
    from app.services import scheduler_service

    state = ReviewState(
        user_id="u",
        item_type="card",
        item_id="c",
        interval_days=7,
        repetition=2,
        easiness_factor=2.5,
        state=ReviewStateKind.review,
    )
    now = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    base = SimpleNamespace(
        review_scheduler="fsrs",
        review_fuzz_ratio=0.0,
        fsrs_request_retention=0.9,
        fsrs_max_interval_days=3650,
    )
    lower = scheduler_service.advance(
        state,
        4,
        method="self_rating",
        now=now,
        settings=user_settings_service.scheduling_settings(
            {"desired_retention": 0.8, "max_interval_days": 3650}, base=base
        ),
        rand=0.5,
    )
    higher = scheduler_service.advance(
        state,
        4,
        method="self_rating",
        now=now,
        settings=user_settings_service.scheduling_settings(
            {"desired_retention": 0.95, "max_interval_days": 3650}, base=base
        ),
        rand=0.5,
    )
    assert lower.interval_days > higher.interval_days


def test_max_interval_is_enforced_from_user_view():
    from app.models.review_state import ReviewStateKind
    from app.services import scheduler_service
    from app.services.scheduler_service import ScheduleOutcome

    now = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    outcome = ScheduleOutcome(
        interval_days=10000,
        repetition=1,
        easiness_factor=2.5,
        next_review_at=now,
        state=ReviewStateKind.review,
        algorithm="fsrs",
    )
    clamped = scheduler_service.apply_scheduling_policy(
        outcome,
        now=now,
        settings=SimpleNamespace(review_fuzz_ratio=0.0, fsrs_max_interval_days=365),
        rand=0.5,
    )
    assert clamped.interval_days <= 365


def test_clean_rules_respects_user_override():
    from app.services.cleaning_service import clean_rules

    text = "# Title\n\n\n\nA  +  B\n"
    off = SimpleNamespace(
        clean_simplify_formula=False,
        clean_normalize_spacing=False,
        clean_normalize_heading_levels=False,
    )
    on = SimpleNamespace(
        clean_simplify_formula=True,
        clean_normalize_spacing=True,
        clean_normalize_heading_levels=True,
    )
    off_text, _ = clean_rules(text, settings_override=off)
    on_text, _ = clean_rules(text, settings_override=on)
    assert off_text != on_text

"""
学习度量层测试（overhaul-plan 阶段 3.14）

## 本文件的重点不是 happy path

度量层的价值全在**它不说谎**：样本不足时必须明确说不足，而不是画一条
由 3 个点决定的曲线让用户据以判断自己的记忆状况。

现场实测（`scripts/_audit_metrics_data.py`，2026-09-11）的真实数据是：

    review_logs 194 条，全部 grading_method='legacy'，self_rating 全为 NULL
    191 道题中 188 道只复习过 1 次；仅有的 3 组重复复习间隔均为 0 天
    单用户

也就是说：**保持率曲线与校准曲线在真实数据上都是零样本**。
因此这里重点测三件事：

1. 配对逻辑正确（相邻配对、跨卡片不混、无法归属的记录不参与）
2. 样本不足时返回 `insufficient_data=True` 且**不返回假桶**
3. 刻意构造出充足数据后，曲线数值算得对
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.review_log import ReviewLog
from app.models.review_state import ITEM_TYPE_CARD, ReviewState, ReviewStateKind
from app.models.user import User
from app.services import learning_metrics_service as metrics
from app.services.learning_metrics_service import (
    MIN_SAMPLE,
    build_review_pairs,
    compute_calibration_curve,
    compute_lapse_distribution,
    compute_retention_curve,
    compute_review_load,
    summarize_forecast,
)

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _record(item_key: str, days_ago: float, quality: int, self_rating=None) -> dict:
    return {
        "item_key": item_key,
        "review_at": NOW - timedelta(days=days_ago),
        "quality": quality,
        "self_rating": self_rating,
    }


# ---------------------------------------------------------------------------
# 纯函数层
# ---------------------------------------------------------------------------

class TestReviewPairs:
    """相邻配对逻辑"""

    def test_pairs_are_adjacent_not_first_to_last(self):
        """必须配**相邻**两次，而不是"首次 vs 末次"

        用首次配末次会跨越中间的全部复习，算出的间隔没有对应的记忆
        强度含义（中间那几次已经把记忆刷新过了）。
        """
        records = [
            _record("card:a", 30, 5),
            _record("card:a", 20, 5),
            _record("card:a", 5, 5),
        ]
        pairs = build_review_pairs(records)
        assert len(pairs) == 2, "3 次复习应产生 2 个相邻配对"
        gaps = sorted(round(p["gap_days"]) for p in pairs)
        assert gaps == [10, 15], f"配对应为相邻间隔 10/15 天，实际 {gaps}"

    def test_different_items_are_never_paired(self):
        """跨学习项绝不能配对 —— 那会凭空造出不存在的复习间隔"""
        records = [
            _record("card:a", 30, 5),
            _record("card:b", 1, 5),
        ]
        assert build_review_pairs(records) == []

    def test_single_review_produces_no_pair(self):
        """只复习过一次的项没有配对（实测库里 188/191 都是这种）"""
        assert build_review_pairs([_record("card:a", 5, 5)]) == []

    def test_records_without_item_key_are_dropped(self):
        """无法归属的记录应被丢弃，而不是聚成一个神秘项"""
        records = [
            _record("card:a", 10, 5),
            {"item_key": None, "review_at": NOW, "quality": 5, "self_rating": None},
        ]
        pairs = build_review_pairs(records)
        assert all(p["item_key"] == "card:a" for p in pairs)

    def test_naive_datetime_is_normalized(self):
        """naive datetime 不得导致减法崩溃（SQLite 老数据的常态）"""
        records = [
            {"item_key": "card:a", "review_at": datetime(2026, 9, 1), "quality": 5, "self_rating": None},
            {"item_key": "card:a", "review_at": datetime(2026, 9, 5), "quality": 5, "self_rating": None},
        ]
        pairs = build_review_pairs(records)
        assert len(pairs) == 1
        assert pairs[0]["gap_days"] == pytest.approx(4.0)


class TestRetentionCurve:

    def test_zero_samples_reports_insufficient(self):
        """零样本：必须明确说"不足"，且**不能返回任何桶**

        这是本文件最重要的一条：实测真实库就是这个状态
        （3 组重复复习的间隔全为 0，被下面第二条规则排除）。
        """
        result = compute_retention_curve([])
        assert result["sample_size"] == 0
        assert result["insufficient_data"] is True
        assert result["buckets"] == [], "零样本时不应返回任何桶（否则前端会画出假曲线）"
        assert result["min_sample"] == MIN_SAMPLE

    def test_short_gaps_are_excluded(self):
        """间隔过短的重复复习不构成保持率证据

        含两种情形，都必须排除：
        - 精确 0 天（同一次会话内的连续提交）
        - **5 分钟**（0.0035 天）—— 用户答错后立刻重做。它经过了时间，
          但不足以检验记忆；计入会系统性高估保持率（刚看完答案当然答得对）。
        """
        for minutes in (0, 5, 60):
            pairs = build_review_pairs([
                _record("card:a", 1, 5),
                {
                    "item_key": "card:a",
                    "review_at": NOW - timedelta(days=1) + timedelta(minutes=minutes),
                    "quality": 5, "self_rating": None,
                },
            ])
            assert len(pairs) == 1
            assert pairs[0]["gap_days"] < metrics.MIN_RETENTION_GAP_DAYS
            result = compute_retention_curve(pairs)
            assert result["sample_size"] == 0, (
                f"间隔 {minutes} 分钟的重做被当成了保持率证据"
            )

    def test_gap_at_threshold_is_included(self):
        """刚好达到最小间隔的对应被计入（边界不能反向排除）"""
        pairs = build_review_pairs([
            _record("card:a", 2, 5),
            _record("card:a", 2 - metrics.MIN_RETENTION_GAP_DAYS, 5),
        ])
        assert pairs[0]["gap_days"] == pytest.approx(metrics.MIN_RETENTION_GAP_DAYS)
        assert compute_retention_curve(pairs)["sample_size"] == 1

    def test_failed_previous_review_is_excluded(self):
        """前次没通过时谈不上"保持"（本来就没记住）"""
        pairs = [{
            "item_key": "card:a", "gap_days": 5.0,
            "prev_passed": False, "passed": True, "predicted": None,
        }]
        assert compute_retention_curve(pairs)["sample_size"] == 0

    def test_buckets_and_rate_are_correct(self):
        """构造充足数据后，分桶与保持率必须算对"""
        pairs = []
        # 3-7 天桶：4 个样本，3 个通过 → 75%
        for i in range(4):
            pairs.append({
                "item_key": f"card:{i}", "gap_days": 5.0,
                "prev_passed": True, "passed": i < 3, "predicted": None,
            })
        # 7-14 天桶：2 个样本，1 个通过 → 50%
        for i in range(2):
            pairs.append({
                "item_key": f"card:x{i}", "gap_days": 10.0,
                "prev_passed": True, "passed": i == 0, "predicted": None,
            })

        result = compute_retention_curve(pairs)
        by_label = {b["label"]: b for b in result["buckets"]}
        assert by_label["3-7天"]["retention"] == 75.0
        assert by_label["7-14天"]["retention"] == 50.0
        assert result["sample_size"] == 6
        assert result["insufficient_data"] is True, "6 < 20 应仍判为样本不足"

    def test_sufficient_sample_clears_the_flag(self):
        """样本达标后 insufficient_data 必须变 False（门槛不能是死的）"""
        pairs = [{
            "item_key": f"card:{i}", "gap_days": 5.0,
            "prev_passed": True, "passed": True, "predicted": None,
        } for i in range(MIN_SAMPLE)]
        result = compute_retention_curve(pairs)
        assert result["sample_size"] == MIN_SAMPLE
        assert result["insufficient_data"] is False

    def test_retention_decays_across_buckets(self):
        """真实的记忆数据应表现为"越久越记不住"

        这不是要求每个数据集都单调（小样本会抖），而是要求**函数确实
        按时间分桶**；这里构造严格递减的数据来验证分桶边界没串。
        """
        pairs = []
        for index, (gap, passed_count, total) in enumerate(
            [(2, 9, 10), (5, 7, 10), (10, 5, 10), (40, 1, 10)]
        ):
            for i in range(total):
                pairs.append({
                    "item_key": f"card:{index}-{i}", "gap_days": float(gap),
                    "prev_passed": True, "passed": i < passed_count, "predicted": None,
                })
        result = compute_retention_curve(pairs)
        rates = [b["retention"] for b in result["buckets"]]
        assert rates == sorted(rates, reverse=True), f"保持率未随间隔递减: {rates}"


class TestCalibrationCurve:

    def test_zero_self_ratings_reports_insufficient(self):
        """零自评样本：必须明确说不足（实测真实库正是零样本）"""
        records = [_record(f"card:{i}", i + 1, 5, self_rating=None) for i in range(30)]
        result = compute_calibration_curve(records)
        assert result["sample_size"] == 0
        assert result["insufficient_data"] is True
        assert result["tiers"] == []

    def test_calibration_needs_a_following_review(self):
        """只有自评、没有后续复习时无法校准（不知道自评准不准）"""
        records = [_record(f"card:{i}", 5, 5, self_rating=5) for i in range(30)]
        result = compute_calibration_curve(records)
        assert result["sample_size"] == 0

    def test_overconfident_self_rating_is_visible(self):
        """**核心价值**：自评说"轻松想起"但后续全错，必须体现为实际<预期

        这正是"校准"要抓的东西：用户以为自己记住了，实际上没有。
        """
        records = []
        for i in range(10):
            key = f"card:{i}"
            records.append({
                "item_key": key, "review_at": NOW - timedelta(days=10),
                "quality": 5, "self_rating": 5,
            })
            # 后续复习全部失败
            records.append({
                "item_key": key, "review_at": NOW - timedelta(days=1),
                "quality": 0, "self_rating": 0,
            })

        result = compute_calibration_curve(records)
        tier5 = next(t for t in result["tiers"] if t["self_rating"] == 5)
        assert tier5["predicted_accuracy"] == 100.0
        assert tier5["actual_accuracy"] == 0.0, (
            "自评 5 分后全错，实际正确率应为 0 —— 校准曲线没抓到过度自信"
        )
        assert result["insufficient_data"] is True  # 10 < 20

    def test_tiers_are_separated(self):
        """四档必须分开统计，不能合并"""
        records = []
        for tier, passed in ((0, False), (3, False), (4, True), (5, True)):
            key = f"card:t{tier}"
            records.append({
                "item_key": key, "review_at": NOW - timedelta(days=5),
                "quality": tier, "self_rating": tier,
            })
            records.append({
                "item_key": key, "review_at": NOW - timedelta(days=1),
                "quality": 5 if passed else 0, "self_rating": None,
            })
        result = compute_calibration_curve(records)
        assert {t["self_rating"] for t in result["tiers"]} == {0, 3, 4, 5}


class TestLapseDistribution:

    def test_current_streak_not_cumulative(self):
        """统计的是**当前连续**失败，不是累计失败

        用累计值会让 leech 列表只增不减：一张卡以前错过很多次、
        最近连续答对，仍会被永久标为顽固卡，用户很快就对它免疫。
        """
        records = [
            # 连续失败 9 次 → 应入选（阈值 8）
            *[_record("card:bad", 100 - i, 0) for i in range(9)],
            # 累计失败 10 次，但最后连续答对 2 次 → 不应入选
            *[_record("card:recovered", 200 - i, 0) for i in range(10)],
            _record("card:recovered", 10, 5),
            _record("card:recovered", 5, 5),
        ]
        result = compute_lapse_distribution(records)
        keys = [c["item_key"] for c in result["leech_candidates"]]
        assert "card:bad" in keys
        assert "card:recovered" not in keys, (
            "最近连续答对的卡片被永久标记为顽固卡 —— 用的是累计值而非当前连续值"
        )

    def test_threshold_is_configurable_constant(self):
        assert metrics.LEECH_LAPSE_THRESHOLD == 8


class TestReviewLoadAndForecast:

    def test_null_due_date_counts_as_due_now(self):
        """next_review_at 为 NULL 表示"立即可复习"（与 review_state_service 一致）"""
        load = compute_review_load([None, None], now=NOW)
        assert load["due_now"] == 2

    def test_windows_are_disjoint(self):
        dates = [
            NOW - timedelta(days=2),     # 逾期 -> due_now
            NOW + timedelta(hours=12),   # -> next_24h
            NOW + timedelta(days=3),     # -> next_7d
            NOW + timedelta(days=20),    # -> next_30d
            NOW + timedelta(days=100),   # -> beyond_30d
        ]
        load = compute_review_load(dates, now=NOW)
        assert load == {
            "due_now": 1, "next_24h": 1, "next_7d": 1,
            "next_30d": 1, "beyond_30d": 1,
        }

    def test_forecast_covers_30_days(self):
        forecast = summarize_forecast([NOW + timedelta(days=3)], now=NOW)
        assert len(forecast["daily"]) == 30
        day3 = forecast["daily"][2]
        assert day3["count"] == 1
        assert forecast["overdue"] == 0

    def test_forecast_puts_past_and_null_into_overdue(self):
        forecast = summarize_forecast(
            [NOW - timedelta(days=5), None, NOW + timedelta(days=400)], now=NOW
        )
        assert forecast["overdue"] == 2
        assert forecast["beyond_30d"] == 1


# ---------------------------------------------------------------------------
# 集成层：真实数据库 + HTTP
# ---------------------------------------------------------------------------

async def _make_user(session_factory) -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_card(session_factory, user_id: str) -> str:
    note_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(id=note_id, user_id=user_id, title="t", source_type=SourceType.pdf))
        await db.commit()
    card_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=card_id, user_id=user_id, note_id=note_id,
            card_type=CardType.concept, title="c", content="cc",
        ))
        await db.commit()
    return card_id


@pytest.mark.asyncio
class TestMetricsIntegration:

    async def test_empty_user_reports_no_data_with_guidance(self, test_db):
        """零数据用户：所有曲线都是 insufficient，且给出可读指引

        `notes` 面向用户，必须说清"缺什么、怎么才会有"，
        否则用户会以为功能坏了。
        """
        uid = await _make_user(test_db)
        async with test_db() as db:
            result = await metrics.get_learning_metrics(db, uid)

        assert result["retention"]["insufficient_data"] is True
        assert result["calibration"]["insufficient_data"] is True
        assert result["data_quality"]["total_reviews"] == 0
        assert result["data_quality"]["notes"], "零数据时必须给出说明文案"

    async def test_legacy_rows_are_reported_honestly(self, test_db):
        """**真实数据形态**：全 legacy + 无自评 → 两条曲线都无数据

        这条复刻了现场实测的 194 条记录形态。它同时是一条防回归测试：
        若将来有人把"无法归属的记录"混进配对，这里会失败。
        """
        uid = await _make_user(test_db)
        card_id = await _make_card(test_db, uid)
        async with test_db() as db:
            for index in range(30):
                db.add(ReviewLog(
                    user_id=uid, quiz_id=None, card_id=card_id, note_id=None,
                    user_answer="", is_correct=index % 2 == 0, quality=5 if index % 2 == 0 else 1,
                    self_rating=None, grading_method="legacy",
                    time_spent_ms=100, review_at=NOW - timedelta(days=30 - index),
                ))
            await db.commit()

        async with test_db() as db:
            result = await metrics.get_learning_metrics(db, uid)

        quality = result["data_quality"]
        assert quality["total_reviews"] == 30
        assert quality["self_rated_reviews"] == 0
        assert result["calibration"]["sample_size"] == 0
        # 30 条同一天的记录：相邻间隔为 1 天，应能形成配对
        assert quality["pairs_with_time_gap"] > 0, (
            "同一卡片不同天的记录没有形成配对 —— card_id 聚合失效"
        )
        assert result["calibration"]["insufficient_data"] is True
        assert any("校准" in n for n in quality["notes"])

    async def test_cards_are_aggregated_by_card_id(self, test_db):
        """多道题属于同一张卡时，应聚成一个学习项

        用户记住的是"这个概念"，不是"某一道题"。
        """
        uid = await _make_user(test_db)
        card_id = await _make_card(test_db, uid)
        async with test_db() as db:
            for index in range(4):
                quiz_id = str(uuid.uuid4())
                db.add(QuizItem(
                    id=quiz_id, user_id=uid, note_id=None, card_id=card_id,
                    question=f"q{index}", answer="a",
                    question_type=QuestionType.short_answer,
                ))
                for day in (10, 5):
                    db.add(ReviewLog(
                        user_id=uid, quiz_id=quiz_id, card_id=card_id, note_id=None,
                        user_answer="", is_correct=True, quality=5,
                        self_rating=5, grading_method="self_rating",
                        time_spent_ms=100, review_at=NOW - timedelta(days=day + index),
                    ))
            await db.commit()

        async with test_db() as db:
            records = await metrics._load_user_review_records(db, uid)
        assert {r["item_key"] for r in records} == {f"card:{card_id}"}, (
            "同一卡片的多道题没有被聚成一个学习项"
        )

    async def test_review_state_supplies_load_data(self, test_db):
        """复习负载来自 review_states，而不是旧的 quiz_items"""
        uid = await _make_user(test_db)
        card_id = await _make_card(test_db, uid)
        async with test_db() as db:
            db.add(ReviewState(
                user_id=uid, item_type=ITEM_TYPE_CARD, item_id=card_id,
                interval_days=1, repetition=0, easiness_factor=2.5,
                next_review_at=NOW - timedelta(days=1),
                state=ReviewStateKind.new,
            ))
            await db.commit()

        async with test_db() as db:
            result = await metrics.get_learning_metrics(db, uid)
        assert result["load"]["due_now"] >= 1
        assert result["data_quality"]["tracked_items"] >= 1


class TestMetricsAPI:
    """HTTP 契约

    每个用例用独立客户端 IP（限流按 IP 计数，共用会让后续用例收到 429）。
    """

    _ip_seq = 300

    def _client(self) -> TestClient:
        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{type(self)._ip_seq}", 9501))

    def _auth(self) -> dict:
        suffix = uuid.uuid4().hex[:8]
        resp = self._client().post("/api/auth/register", json={
            "email": f"m{suffix}@example.com",
            "username": f"m{suffix}",
            "password": "MetricsPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_requires_auth(self, test_db):
        assert self._client().get("/api/report/learning-metrics").status_code == 401

    def test_returns_full_shape_for_new_user(self, test_db):
        """新用户也必须有完整响应结构（前端无需分支处理缺失字段）"""
        headers = self._auth()
        resp = self._client().get("/api/report/learning-metrics", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        for key in ("retention", "calibration", "lapses", "load", "forecast", "data_quality"):
            assert key in body, f"响应缺少 {key}"

        assert body["retention"]["insufficient_data"] is True
        assert body["retention"]["buckets"] == []
        assert body["calibration"]["tiers"] == []
        assert body["load"] == {
            "due_now": 0, "next_24h": 0, "next_7d": 0, "next_30d": 0, "beyond_30d": 0,
        }
        assert len(body["forecast"]["daily"]) == 30
        assert body["data_quality"]["notes"]

    def test_response_is_json_serializable_and_typed(self, test_db):
        """日期字段必须是 ISO 字符串而不是 datetime 对象漏出去"""
        headers = self._auth()
        body = self._client().get("/api/report/learning-metrics", headers=headers).json()
        first_day = body["forecast"]["daily"][0]
        assert isinstance(first_day["date"], str)
        assert len(first_day["date"]) == 10  # YYYY-MM-DD

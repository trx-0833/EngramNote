"""阶段 4.6 收尾：提示词版本效果报表测试

## 这份测试要证明什么

4.6 把 `prompt_version` 写进了 `knowledge_cards` / `quiz_items`，但**没有人读**。
列写了没人用等于没做，所以这份测试盯的不是"函数能跑"，而是报表有没有真的
把那一列变成人能据以判断的东西：

| 骗法 | 后果 | 对应测试 |
|---|---|---|
| 报表不存在 / 接口没注册 | 数据依然埋在库里，缺陷原样保留 | `TestAPI::test_endpoint_registered_and_secured` |
| 接口不调用服务（返回罐头数据） | 报表永远"正常"，但从不反映真实数据 | `TestAPI::test_endpoint_calls_the_report_service` |
| NULL 被并进"版本 1" | 历史数据污染第一版的表现，比较结论反了 | `test_unknown_rows_are_a_separate_bucket` |
| 「已登记但无数据」不报 | 改了提示词却没有任何迹象表明"还没生效" | `test_registered_version_without_data_is_reported` |
| 漂移（数据有、登记表没有）不报 | 无法解释的分组被当成一个正常版本 | `test_unregistered_version_is_flagged` |
| 空库当成错误 | 新用户看到 500/空页面，以为系统坏了 | `test_empty_database_is_information_not_error` |
| 无复习被写成 0% | "没样本"被读成"这一版很差" | `test_no_reviews_is_null_not_zero` |
| 合计靠平均百分比 | 1 条内容与 100 条内容被同等加权 | `test_totals_are_recomputed_from_counts` |
| 没按用户过滤 | 看到/算进别人的内容表现 | `TestIsolation` |

## 与 `test_prompt_version.py` 的分工

那份管**写入**（列有没有被写、迁移有没有跑、版本号有没有跟提示词文本绑定）；
本份管**读取**（写进去的东西能不能回答"改了提示词到底有没有用"）。
两者合起来才闭环：写进去、读得出来。
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.services import prompt_version_report_service as pv
from app.services.llm.prompts import PROMPT_VERSIONS

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _now() -> datetime:
    """当前时刻（**不在模块层固化**）

    与 `test_llm_accounting.py` 里那条踩过坑的 `NOW` 常量同一个理由：
    把"现在"写死在模块层，用例就会在机器时钟走过某个点之后莫名其妙地变红，
    而失败原因与被测性质毫无关系。
    """
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 造数据
# ---------------------------------------------------------------------------

async def _make_user(session_factory, tag: str = "pv") -> str:
    from app.models.user import User

    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=f"{tag}{uid[:8]}@example.com", username=f"{tag}{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_card(
    session_factory,
    user_id: str,
    *,
    version: Optional[str] = None,
    created_at: Optional[datetime] = None,
    mastery: float = 0.0,
    title: str = "卡片",
) -> str:
    from app.models.knowledge_card import CardType, KnowledgeCard

    cid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=cid, user_id=user_id, note_id=None, card_type=CardType.concept,
            title=title, content="内容", prompt_version=version, mastery_level=mastery,
            created_at=created_at or _now(),
        ))
        await db.commit()
    return cid


async def _make_quiz(
    session_factory,
    user_id: str,
    card_id: str,
    *,
    version: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> str:
    from app.models.quiz_item import DifficultyLevel, QuestionType, QuizItem

    qid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(QuizItem(
            id=qid, user_id=user_id, card_id=card_id,
            question_type=QuestionType.choice, difficulty=DifficultyLevel.medium,
            question="题干？", answer="答案", prompt_version=version,
            created_at=created_at or _now(),
        ))
        await db.commit()
    return qid


async def _make_review(
    session_factory,
    user_id: str,
    *,
    card_id: Optional[str] = None,
    quiz_id: Optional[str] = None,
    quality: int = 5,
    review_at: Optional[datetime] = None,
) -> str:
    from app.models.review_log import ReviewLog

    rid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(ReviewLog(
            id=rid, user_id=user_id, card_id=card_id, quiz_id=quiz_id,
            user_answer="", is_correct=quality >= pv.PASS_QUALITY, quality=quality,
            grading_method="choice", review_at=review_at or _now(),
        ))
        await db.commit()
    return rid


async def _make_state(
    session_factory,
    user_id: str,
    *,
    item_type: str,
    item_id: str,
    lapses: int = 0,
    interval_days: int = 1,
    review_count: int = 1,
) -> None:
    from app.models.review_state import ReviewState, ReviewStateKind

    async with session_factory() as db:
        db.add(ReviewState(
            id=str(uuid.uuid4()), user_id=user_id, item_type=item_type, item_id=item_id,
            interval_days=interval_days, repetition=0, easiness_factor=2.5,
            lapses=lapses, state=ReviewStateKind.learning, review_count=review_count,
            last_reviewed_at=_now(),
        ))
        await db.commit()


async def _report(session_factory, user_id: str, **kwargs) -> Dict[str, Any]:
    async with session_factory() as db:
        return await pv.get_prompt_version_report(db, user_id=user_id, **kwargs)


def _bucket(report: Dict[str, Any], version: Optional[str]) -> Dict[str, Any]:
    """按版本取桶（取不到就让测试直接失败，而不是抛 KeyError 掩盖原因）"""
    for bucket in report["buckets"]:
        if bucket["prompt_version"] == version:
            return bucket
    raise AssertionError(
        f"报表里没有版本 {version!r} 的桶，实际有："
        f"{[b['prompt_version'] for b in report['buckets']]}"
    )


def _all_notes(report: Dict[str, Any]) -> str:
    return "\n".join(report["notes"])


# ---------------------------------------------------------------------------
# 分组
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGrouping:
    async def test_cards_are_grouped_by_prompt_version(self, test_db):
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version=None)
        await _make_card(test_db, uid, version=None)
        await _make_card(test_db, uid, version="1")
        await _make_card(test_db, uid, version="2")

        report = await _report(test_db, uid)

        assert len(report["buckets"]) == 3
        assert _bucket(report, None)["cards"]["content_total"] == 2
        assert _bucket(report, "1")["cards"]["content_total"] == 1
        assert _bucket(report, "2")["cards"]["content_total"] == 1
        assert report["totals"]["cards"]["content_total"] == 4

    async def test_unknown_bucket_sorts_last(self, test_db):
        """已知版本从新到旧，**未知永远排最后**（它不是"最小的版本"）"""
        uid = await _make_user(test_db)
        for version in (None, "1", "2"):
            await _make_card(test_db, uid, version=version)

        report = await _report(test_db, uid)

        assert [b["prompt_version"] for b in report["buckets"]] == ["2", "1", None]

    async def test_reviews_are_attributed_to_the_producing_version(self, test_db):
        """★ 表现必须挂到**产出这条内容的那一版**上，否则版本比较无从谈起"""
        uid = await _make_user(test_db)
        v1 = await _make_card(test_db, uid, version="1")
        unknown = await _make_card(test_db, uid, version=None)
        # v1：1 通过 1 未通过 → 50%
        await _make_review(test_db, uid, card_id=v1, quality=5)
        await _make_review(test_db, uid, card_id=v1, quality=1)
        # 未知：1 通过 → 100%
        await _make_review(test_db, uid, card_id=unknown, quality=4)

        report = await _report(test_db, uid)

        v1_stats = _bucket(report, "1")["cards"]
        assert (v1_stats["reviews"], v1_stats["passed"]) == (2, 1)
        assert v1_stats["pass_rate_percent"] == 50.0
        assert v1_stats["reviewed_content"] == 1

        unknown_stats = _bucket(report, None)["cards"]
        assert (unknown_stats["reviews"], unknown_stats["passed"]) == (1, 1)
        assert unknown_stats["pass_rate_percent"] == 100.0

    async def test_quiz_reviews_join_on_quiz_id(self, test_db):
        """题目的归属走 `review_logs.quiz_id`：题目被整批替换后卡片 id 仍在，
        但"这一版**题目**好不好"只能由题目自己回答"""
        uid = await _make_user(test_db)
        card = await _make_card(test_db, uid, version="1")
        quiz_v1 = await _make_quiz(test_db, uid, card, version="1")
        quiz_unknown = await _make_quiz(test_db, uid, card, version=None)
        await _make_review(test_db, uid, quiz_id=quiz_v1, card_id=card, quality=5)
        await _make_review(test_db, uid, quiz_id=quiz_unknown, card_id=card, quality=0)

        report = await _report(test_db, uid)

        assert _bucket(report, "1")["quizzes"]["pass_rate_percent"] == 100.0
        assert _bucket(report, None)["quizzes"]["pass_rate_percent"] == 0.0
        assert _bucket(report, None)["quizzes"]["reviews"] == 1

    async def test_lapses_and_interval_come_from_review_states(self, test_db):
        uid = await _make_user(test_db)
        card_a = await _make_card(test_db, uid, version="1")
        card_b = await _make_card(test_db, uid, version="1")
        await _make_state(test_db, uid, item_type="card", item_id=card_a,
                          lapses=1, interval_days=3)
        await _make_state(test_db, uid, item_type="card", item_id=card_b,
                          lapses=3, interval_days=7)

        report = await _report(test_db, uid)

        stats = _bucket(report, "1")["cards"]
        assert stats["tracked_items"] == 2
        assert stats["lapses"] == 4
        assert stats["lapses_per_item"] == 2.0
        # 间隔是**总和/条目数**，不是把两个均值再平均（这里恰好一样，
        # 但公式必须能处理条目数不同的情况 —— 见 totals 的用例）
        assert stats["avg_interval_days"] == 5.0

    async def test_mastery_average_counts_unreviewed_cards_as_zero(self, test_db):
        """★ 掌握度的口径必须显式：公式把"从未复习"算 0，
        因此均值会被拉低，`mastery_positive` 是配套的解药"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1", mastery=0.0)
        await _make_card(test_db, uid, version="1", mastery=60.0)
        await _make_card(test_db, uid, version="1", mastery=90.0)

        report = await _report(test_db, uid)

        stats = _bucket(report, "1")["cards"]
        assert stats["avg_mastery"] == 50.0
        assert stats["mastery_positive"] == 2

    async def test_quizzes_have_no_mastery_fields(self, test_db):
        """题目表没有掌握度字段 —— 报表不许凭空造一个（字段缺失比假数字诚实）"""
        uid = await _make_user(test_db)
        card = await _make_card(test_db, uid, version="1")
        await _make_quiz(test_db, uid, card, version="1")

        report = await _report(test_db, uid)

        assert "avg_mastery" not in _bucket(report, "1")["quizzes"]
        assert "avg_mastery" in _bucket(report, "1")["cards"]

    async def test_totals_are_recomputed_from_counts(self, test_db):
        """★ 合计不许把各版本的百分比直接平均

        1 条内容 0% 与 9 条内容 100%，正确合计是 90%，不是 50%。
        直接把百分比平均会让"只产出了一条内容"的版本与"产出了一百条"的
        版本被同等加权 —— 一个看起来很合理、实际完全错的数字。
        """
        uid = await _make_user(test_db)
        few = await _make_card(test_db, uid, version="1")
        await _make_review(test_db, uid, card_id=few, quality=0)
        for _ in range(9):
            card = await _make_card(test_db, uid, version="2")
            await _make_review(test_db, uid, card_id=card, quality=5)

        report = await _report(test_db, uid)

        assert _bucket(report, "1")["cards"]["pass_rate_percent"] == 0.0
        assert _bucket(report, "2")["cards"]["pass_rate_percent"] == 100.0
        assert report["totals"]["cards"]["reviews"] == 10
        assert report["totals"]["cards"]["pass_rate_percent"] == 90.0


# ---------------------------------------------------------------------------
# 三个状态
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestThreeStates:
    async def test_unknown_rows_are_a_separate_bucket(self, test_db):
        """★ NULL 必须自成一组，**不能**被并进"版本 1"（那会污染第一版的表现）"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version=None)

        report = await _report(test_db, uid)

        unknown = _bucket(report, None)
        assert unknown["state"] == "unknown"
        assert unknown["prompt_version"] is None
        assert "未知" in unknown["label"]
        assert unknown["registered_prompts"] == []
        assert unknown["producers"] == {"cards": [], "quizzes": []}
        # 版本 1 那一桶**不存在**（没有任何一行带版本号）
        assert "1" not in [b["prompt_version"] for b in report["buckets"]]

    async def test_registered_version_without_data_is_reported(self, test_db):
        """★ 状态一：已登记但库里没有这一版的内容 = 改动**尚未生效**"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version=None)
        await _make_quiz(test_db, uid, await _make_card(test_db, uid, version=None), version=None)

        report = await _report(test_db, uid)

        entries = {
            e["prompt_name"]: e
            for e in report["registry"]["registered_without_data"]
        }
        producers = {s.prompt_name for s in pv.CONTENT_SOURCES}
        assert set(entries) == producers, "每条内容生产者的「没有数据」都应当被报出来"

        card_entry = entries["understanding_session"]
        assert card_entry["version"] == PROMPT_VERSIONS["understanding_session"]
        assert card_entry["kind"] == "card"
        # 卡片表里有版本未知的行 → 不许断定"这一版没有效果"
        assert card_entry["unknown_version_rows_in_table"] == 2
        assert "可能" in card_entry["note"]

        quiz_entry = entries["question_session"]
        assert quiz_entry["kind"] == "quiz"
        # 题目表里同样有未知行
        assert quiz_entry["unknown_version_rows_in_table"] == 1

        assert "尚未生效" in _all_notes(report)

    async def test_registered_version_with_data_drops_out_of_without_data(self, test_db):
        """反过来：真的产出了带版本号的内容之后，就不该再说"还没生效" """
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version=PROMPT_VERSIONS["understanding_session"])

        report = await _report(test_db, uid)

        names = {e["prompt_name"] for e in report["registry"]["registered_without_data"]}
        assert "understanding_session" not in names
        assert _bucket(report, PROMPT_VERSIONS["understanding_session"])["state"] == "registered"

    async def test_registered_without_data_is_still_a_caveat_when_table_is_empty(self, test_db):
        """表里一行未知数据都没有时，措辞应当是"尚未生效"而不是"可能是它产出的" """
        uid = await _make_user(test_db)

        report = await _report(test_db, uid)

        for entry in report["registry"]["registered_without_data"]:
            assert entry["unknown_version_rows_in_table"] == 0
            assert "尚未生效" in entry["note"]

    async def test_unregistered_version_is_flagged(self, test_db):
        """★ 状态二：数据里有的版本号，登记表里没有 —— 漂移，不可解释"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="9")

        report = await _report(test_db, uid)

        bucket = _bucket(report, "9")
        assert bucket["state"] == "unregistered"
        assert "未登记" in bucket["label"]
        drift = report["registry"]["unregistered_versions_in_data"]
        assert [e["version"] for e in drift] == ["9"]
        assert drift[0]["cards_total"] == 1
        assert "漂移" in _all_notes(report)

    async def test_registered_bucket_lists_its_prompts_and_producers(self, test_db):
        """版本号→提示词是**多对一**的（11 个名字只有少数几个版本号），
        因此桶必须把"谁登记成这一版"与"谁可能产出这张表的内容"都列出来"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1")

        report = await _report(test_db, uid)

        bucket = _bucket(report, "1")
        assert bucket["state"] == "registered"
        expected = sorted(n for n, v in PROMPT_VERSIONS.items() if v == "1")
        assert bucket["registered_prompts"] == expected
        assert set(bucket["producers"]["cards"]) == {
            "understanding_session", "combined_analysis_session",
            "generate_extension_knowledge",
        }
        assert bucket["producers"]["quizzes"] == ["question_session"]
        # 多对一这件事必须说出来，否则会被读成"这一桶就是 understanding_session 的产出"
        assert "无法" in _all_notes(report) or "区分" in _all_notes(report)

    async def test_prompts_that_cannot_produce_content_are_declared(self, test_db):
        """不在卡片/题目表上写行的提示词，本报表**无法**评估其版本效果 ——
        这是限制，必须写出来，而不是留白让人以为"它们没有效果" """
        uid = await _make_user(test_db)

        report = await _report(test_db, uid)

        covered = {s.prompt_name for s in pv.CONTENT_SOURCES}
        uncovered = {e["prompt_name"] for e in report["registry"]["prompts_not_covered"]}
        assert uncovered == set(PROMPT_VERSIONS) - covered
        assert "rag_answer" in uncovered
        for entry in report["registry"]["prompts_not_covered"]:
            assert entry["version"] == PROMPT_VERSIONS[entry["prompt_name"]]
            assert "无法" in entry["reason"]


# ---------------------------------------------------------------------------
# 时间窗
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWindow:
    async def test_content_window_filters_on_created_at(self, test_db):
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1", created_at=_now() - timedelta(days=100))
        await _make_card(test_db, uid, version="1", created_at=_now() - timedelta(days=1))

        report = await _report(test_db, uid, since=_now() - timedelta(days=30), until=_now())

        stats = _bucket(report, "1")["cards"]
        assert stats["content_in_window"] == 1, "窗口外的旧内容被算成了窗口内新增"
        assert stats["content_total"] == 2, "全量计数必须保留（否则分不清「停产」与「从没有过」）"

    async def test_review_window_filters_on_review_at(self, test_db):
        uid = await _make_user(test_db)
        card = await _make_card(test_db, uid, version="1")
        await _make_review(test_db, uid, card_id=card, quality=5,
                           review_at=_now() - timedelta(days=100))
        await _make_review(test_db, uid, card_id=card, quality=0,
                           review_at=_now() - timedelta(days=1))

        report = await _report(test_db, uid, since=_now() - timedelta(days=30), until=_now())

        stats = _bucket(report, "1")["cards"]
        assert stats["reviews"] == 1
        assert stats["pass_rate_percent"] == 0.0

    async def test_window_does_not_hide_versions_that_produced_nothing_recently(self, test_db):
        """窗口把产出与复习都排除掉时，桶仍然在（`content_total` 是全量的）

        否则"这一版停产了"会看起来像"这一版不存在"，而两者完全不同。
        """
        uid = await _make_user(test_db)
        old = await _make_card(test_db, uid, version="1", created_at=_now() - timedelta(days=200))
        await _make_review(test_db, uid, card_id=old, quality=5,
                           review_at=_now() - timedelta(days=200))

        report = await _report(test_db, uid, since=_now() - timedelta(days=30), until=_now())

        stats = _bucket(report, "1")["cards"]
        assert stats["content_in_window"] == 0
        assert stats["content_total"] == 1
        assert stats["reviews"] == 0
        assert stats["pass_rate_percent"] is None

    async def test_window_is_half_open(self, test_db):
        """左闭右开：边界行不会被前后两个窗口同时算进去（与用量接口同一条约定）"""
        uid = await _make_user(test_db)
        created = _now() - timedelta(days=5)
        await _make_card(test_db, uid, version="1", created_at=created)

        inside = await _report(
            test_db, uid, since=created - timedelta(seconds=1), until=created + timedelta(seconds=1),
        )
        boundary = await _report(test_db, uid, since=created - timedelta(seconds=1), until=created)

        assert _bucket(inside, "1")["cards"]["content_in_window"] == 1
        assert _bucket(boundary, "1")["cards"]["content_in_window"] == 0


# ---------------------------------------------------------------------------
# 空库 / 诚实性
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHonesty:
    async def test_empty_database_is_information_not_error(self, test_db):
        """★ 空库不是错误：一个新用户看到 500 会以为系统坏了

        没有数据时返回**零桶 + 说明**，而不是抛异常，也不是一个看起来像
        "所有版本表现都是 0" 的报表。
        """
        uid = await _make_user(test_db)

        report = await _report(test_db, uid)

        assert report["buckets"] == []
        assert report["totals"]["cards"]["content_total"] == 0
        assert report["totals"]["cards"]["pass_rate_percent"] is None
        assert report["notes"], "空报表必须自己解释为什么是空的"
        assert "没有可比较的东西" in _all_notes(report)

    async def test_all_unknown_is_a_meaningful_answer(self, test_db):
        """★ 今天真库的状态：**全部内容都是 NULL**

        报表要把它渲染成"一桶 + 说清楚为什么"，并且明确不许把它读成第一版。
        """
        uid = await _make_user(test_db)
        card = await _make_card(test_db, uid, version=None)
        await _make_quiz(test_db, uid, card, version=None)
        await _make_review(test_db, uid, card_id=card, quality=5)

        report = await _report(test_db, uid)

        assert len(report["buckets"]) == 1, "全部未知时应当恰好只有一桶"
        bucket = report["buckets"][0]
        assert bucket["state"] == "unknown"
        assert bucket["prompt_version"] is None
        assert bucket["cards"]["content_total"] == 1
        assert bucket["quizzes"]["content_total"] == 1
        assert bucket["cards"]["reviews"] == 1

        notes = _all_notes(report)
        assert "未知不等于第一版" in notes
        assert "不是报表出错" in notes, "必须说明这是数据状态而不是故障"
        assert report["unknown_version_label"].startswith("版本未知")
        # 登记侧仍然照实报告"还没有带版本号的内容"
        assert report["registry"]["registered_without_data"]

    async def test_no_reviews_is_null_not_zero(self, test_db):
        """★ 没有复习 → `null`；写成 0% 会被读成"这一版内容很差" """
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1")

        report = await _report(test_db, uid)

        stats = _bucket(report, "1")["cards"]
        assert stats["reviews"] == 0
        assert stats["pass_rate_percent"] is None
        assert stats["lapses_per_item"] is None
        assert stats["avg_interval_days"] is None
        assert "不是 0%" in _all_notes(report)

    async def test_unattributable_reviews_are_declared(self, test_db):
        """既无 card_id 也无 quiz_id 的复习记录**进不了任何桶** ——
        不说明的话，各桶之和对不上总数只会被当成报表算错了"""
        uid = await _make_user(test_db)
        await _make_review(test_db, uid, card_id=None, quiz_id=None, quality=5)

        report = await _report(test_db, uid)

        assert report["data_quality"]["reviews_in_window"] == 1
        assert report["data_quality"]["reviews_unattributable"] == 1
        assert "差额" in _all_notes(report)

    async def test_overlapping_card_and_quiz_views_are_declared(self, test_db):
        """同一次答题在卡片视图与题目视图里各算一次 —— 必须显式说明不能相加"""
        uid = await _make_user(test_db)
        card = await _make_card(test_db, uid, version="1")
        quiz = await _make_quiz(test_db, uid, card, version="1")
        await _make_review(test_db, uid, card_id=card, quiz_id=quiz, quality=5)

        report = await _report(test_db, uid)

        assert report["data_quality"]["reviews_linked_to_both"] == 1
        assert _bucket(report, "1")["cards"]["reviews"] == 1
        assert _bucket(report, "1")["quizzes"]["reviews"] == 1
        assert "不能相加" in _all_notes(report)

    async def test_metric_notes_label_the_proxies_as_proxies(self, test_db):
        """代理指标必须自称代理指标（否则一个百分比会被当成结论）"""
        uid = await _make_user(test_db)

        report = await _report(test_db, uid)

        for field in ("pass_rate_percent", "lapses_per_item", "avg_interval_days", "avg_mastery"):
            assert "代理指标" in report["metric_notes"][field], f"{field} 没有声明自己是代理"
        assert "不是" in report["metric_notes"]["pass_rate_percent"]

    async def test_version_filter_can_select_the_unknown_bucket(self, test_db):
        """★ NULL 桶必须**能被单独查**：HTTP 查不了 NULL，所以要有 `unknown` 这个词"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1")
        await _make_card(test_db, uid, version=None)

        report = await _report(test_db, uid, version=pv.UNKNOWN_VERSION_TOKEN)

        assert [b["prompt_version"] for b in report["buckets"]] == [None]
        assert report["requested_version"] == pv.UNKNOWN_VERSION_TOKEN

    async def test_filtering_to_an_absent_version_is_empty_but_explained(self, test_db):
        """查一个没有数据的版本 → 空结果 + 说明，而不是错误"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1")

        report = await _report(test_db, uid, version="7")

        assert report["buckets"] == []
        assert "没有任何内容" in _all_notes(report)

    async def test_registry_sections_survive_a_version_filter(self, test_db):
        """登记表讲的是全局事实，不该因为"我在看 v1"就消失"""
        uid = await _make_user(test_db)
        await _make_card(test_db, uid, version="1")

        report = await _report(test_db, uid, version="1")

        assert report["registry"]["registered_without_data"]
        assert report["registry"]["prompts_not_covered"]


# ---------------------------------------------------------------------------
# 用户隔离
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestIsolation:
    async def test_other_users_content_and_reviews_are_not_counted(self, test_db):
        """★ 报表里出现别人的内容/表现是最严重的错误"""
        mine = await _make_user(test_db, tag="mine")
        other = await _make_user(test_db, tag="other")

        other_card = await _make_card(test_db, other, version="1")
        await _make_review(test_db, other, card_id=other_card, quality=5)
        await _make_state(test_db, other, item_type="card", item_id=other_card,
                          lapses=9, interval_days=99)

        my_card = await _make_card(test_db, mine, version="1")
        await _make_review(test_db, mine, card_id=my_card, quality=0)

        report = await _report(test_db, mine)

        stats = _bucket(report, "1")["cards"]
        assert stats["content_total"] == 1
        assert stats["reviews"] == 1
        assert stats["passed"] == 0
        assert stats["tracked_items"] == 0, "算进了别人的复习状态（lapses/间隔）"
        assert stats["lapses"] == 0
        assert report["totals"]["cards"]["content_total"] == 1
        assert report["data_quality"]["reviews_in_window"] == 1
        assert report["data_quality"]["unknown_version_cards"] == 0


# ---------------------------------------------------------------------------
# 覆盖范围：源码里的写入点必须与声明一致
# ---------------------------------------------------------------------------

#: 匹配真实写入点：`prompt_version=prompt_version("understanding_session")`
#:
#: 提示词名一律是 `[a-z][a-z0-9_]*`（见 prompts.py 的登记表）。这个形状限制是
#: **有意的**：它让文档/注释里为了举例而写的 `prompt_version("...")` 不会被
#: 当成一个真实写入点 —— 否则扫描器会把说明文字也算进来，然后逼着人去改注释
#: （本轮实测踩到：本文件的注释里写了一个带省略号的例子，扫描结果里就多了一个
#: `...`，测试因此变红）。
_WRITE_SITE_RE = re.compile(r'prompt_version\(\s*"([a-z][a-z0-9_]*)"\s*\)')


class TestContentSourceCoverage:
    """★ 防止报表**静默漏掉**新出现的写入点

    报表只能评估 `CONTENT_SOURCES` 里声明过的提示词。将来有人新增一条会写
    `knowledge_cards` 的提示词却忘了加进声明，那个版本就会永远不出现在报表里 ——
    没有人会收到任何提示。所以这里直接扫源码，把声明钉在真实调用点上。
    """

    def _write_site_names(self) -> set:
        found = set()
        for path in (BACKEND_DIR / "app").rglob("*.py"):
            found.update(_WRITE_SITE_RE.findall(path.read_text(encoding="utf-8")))
        return found

    def test_scanner_actually_finds_write_sites(self):
        """★ 先证明扫描器不是空转：一个都扫不到时，下面的断言会永远通过"""
        found = self._write_site_names()
        assert len(found) >= 4, f"只扫到 {found}，扫描器可能已经失效"

    def test_content_sources_cover_every_write_site(self):
        declared = {source.prompt_name for source in pv.CONTENT_SOURCES}
        assert self._write_site_names() == declared, (
            "源码里的 prompt_version(...) 写入点与 CONTENT_SOURCES 声明不一致 —— "
            "新增写入点时必须同时加进声明，否则它的版本不会出现在报表里"
        )

    def test_every_declared_source_is_registered(self):
        """声明了的提示词必须在登记表里有版本号，否则它写进列里的是 NULL"""
        for source in pv.CONTENT_SOURCES:
            assert source.prompt_name in PROMPT_VERSIONS
            assert source.kind in (pv.KIND_CARD, pv.KIND_QUIZ)
            assert source.what, "中文说明不能为空 —— 它会直接出现在报表里"


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------

class TestAPI:
    """接口只返回**当前用户**的版本报表

    ⚠️ 这个类**不加** `@pytest.mark.asyncio`：它的用例是同步的
    （TestClient + asyncio.run 播种），加了会得到一堆
    "marked with asyncio but it is not an async function" 噪音警告。
    """

    _ip_seq = 400

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"203.0.113.{type(self)._ip_seq}", 9502))

    def _auth(self) -> tuple[dict, str]:
        suffix = uuid.uuid4().hex[:8]
        resp = self._client().post("/api/auth/register", json={
            "email": f"pvr{suffix}@example.com",
            "username": f"pvr{suffix}",
            "password": "PvrPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        me = self._client().get("/api/auth/me", headers=headers).json()
        return headers, me["id"]

    # ---- 注册与鉴权 ----

    def test_requires_auth(self, test_db):
        assert self._client().get("/api/llm/prompt-versions").status_code == 401

    def test_endpoint_registered_and_secured(self, test_db):
        """★ 防"服务写好了但没人能访问" —— 报表不可达等于没做"""
        from app.main import app

        op = app.openapi()["paths"].get("/api/llm/prompt-versions", {}).get("get")
        assert op is not None, "GET /api/llm/prompt-versions 没有注册 —— 人根本够不到这张报表"
        assert op.get("security"), "这个接口会返回用户数据，必须声明 security"

    def test_rejects_malformed_version(self, test_db):
        headers, _ = self._auth()
        client = self._client()
        for bad in ("abc", "1a", "-1", "1.0", " "):
            resp = client.get(f"/api/llm/prompt-versions?version={bad}", headers=headers)
            assert resp.status_code == 400, f"version={bad!r} 应当被拒绝，实际 {resp.status_code}"

    def test_rejects_out_of_range_days(self, test_db):
        headers, _ = self._auth()
        assert self._client().get(
            "/api/llm/prompt-versions?days=0", headers=headers,
        ).status_code == 422

    # ---- 接线（反空转） ----

    def test_endpoint_calls_the_report_service(self, test_db, monkeypatch):
        """★ 接口必须真的调用服务，并把**当前用户**传下去

        这条盯的是"接口注册了、文档里也有，但返回的是一份罐头数据/别人的数据"。
        它同时断言 `user_id` 来自认证结果：隔离不是靠服务自己猜出来的。
        """
        from app.api import llm as llm_api

        seen: List[Dict[str, Any]] = []
        original = pv.get_prompt_version_report

        async def _spy(db, **kwargs):
            seen.append(kwargs)
            return await original(db, **kwargs)

        monkeypatch.setattr(llm_api.prompt_version_report_service, "get_prompt_version_report", _spy)

        headers, uid = self._auth()
        resp = self._client().get("/api/llm/prompt-versions?days=7", headers=headers)

        assert resp.status_code == 200, resp.text
        assert seen, "接口没有调用报表服务"
        assert seen[0]["user_id"] == uid
        assert seen[0]["version"] is None
        assert isinstance(seen[0]["since"], datetime)
        assert (seen[0]["until"] - seen[0]["since"]) == timedelta(days=7)

    def test_version_filter_reaches_the_service(self, test_db, monkeypatch):
        from app.api import llm as llm_api

        seen: List[Dict[str, Any]] = []
        original = pv.get_prompt_version_report

        async def _spy(db, **kwargs):
            seen.append(kwargs)
            return await original(db, **kwargs)

        monkeypatch.setattr(llm_api.prompt_version_report_service, "get_prompt_version_report", _spy)

        headers, _ = self._auth()
        self._client().get("/api/llm/prompt-versions?version=unknown", headers=headers)
        assert seen and seen[0]["version"] == "unknown"

    # ---- 内容与隔离 ----

    def test_empty_report_is_honest_not_an_error(self, test_db):
        headers, _ = self._auth()
        resp = self._client().get("/api/llm/prompt-versions", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["buckets"] == []
        assert body["totals"]["cards"]["pass_rate_percent"] is None
        assert body["notes"]

    def test_all_unknown_state_is_rendered(self, test_db):
        """★ 今天真库的形态：全部内容版本未知

        断言的是**渲染**：一桶、state=unknown、有中文说明、并没有被当成错误。
        """
        import asyncio

        headers, uid = self._auth()

        async def _seed():
            card = await _make_card(test_db, uid, version=None)
            await _make_review(test_db, uid, card_id=card, quality=5)

        asyncio.run(_seed())

        body = self._client().get("/api/llm/prompt-versions", headers=headers).json()

        assert len(body["buckets"]) == 1
        bucket = body["buckets"][0]
        assert bucket["state"] == "unknown"
        assert bucket["prompt_version"] is None
        assert bucket["cards"]["content_total"] == 1
        assert bucket["cards"]["reviews"] == 1
        assert any("未知不等于第一版" in note for note in body["notes"])
        assert body["data_quality"]["unknown_version_cards"] == 1
        assert body["registry"]["registered_without_data"]

    def test_only_returns_current_users_data(self, test_db):
        """★ 接口不许漏出别人的内容表现"""
        import asyncio

        headers_a, uid_a = self._auth()
        headers_b, uid_b = self._auth()

        async def _seed():
            await _make_card(test_db, uid_a, version=None)
            for _ in range(3):
                card = await _make_card(test_db, uid_b, version="1")
                await _make_review(test_db, uid_b, card_id=card, quality=5)

        asyncio.run(_seed())

        body_a = self._client().get("/api/llm/prompt-versions", headers=headers_a).json()
        assert body_a["totals"]["cards"]["content_total"] == 1, "看到了其他用户的卡片"
        assert body_a["totals"]["cards"]["reviews"] == 0
        assert [b["prompt_version"] for b in body_a["buckets"]] == [None]

        body_b = self._client().get("/api/llm/prompt-versions", headers=headers_b).json()
        assert body_b["totals"]["cards"]["content_total"] == 3
        assert body_b["totals"]["cards"]["pass_rate_percent"] == 100.0
        assert [b["prompt_version"] for b in body_b["buckets"]] == ["1"]

    def test_days_window_reaches_the_response(self, test_db):
        import asyncio

        headers, uid = self._auth()

        async def _seed():
            await _make_card(test_db, uid, version="1", created_at=_now() - timedelta(days=60))

        asyncio.run(_seed())

        body = self._client().get("/api/llm/prompt-versions?days=7", headers=headers).json()
        assert body["days"] == 7
        assert _bucket(body, "1")["cards"]["content_in_window"] == 0
        assert _bucket(body, "1")["cards"]["content_total"] == 1

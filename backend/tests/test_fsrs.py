"""阶段 3.6：FSRS-5 调度器测试

## 这份测试要证明什么

换调度算法是**最难事后纠错**的一类改动：间隔一旦写进库，用户按它安排复习，
错了也要几天后才显形，而且没有"回滚数据"这种操作（历史复习记录不可变，
见原则 P3）。因此这里不满足于"函数能跑通"，而是分四层证明：

| 层 | 问题 | 手段 |
|---|---|---|
| **公式** | 实现是否忠实于公开的 FSRS-5 | 用 `DEFAULT_W` **独立重写一遍**算式对拍；再验证 S 的定义（R(S,S)=0.9）与间隔反解（I(0.9,S)=S） |
| **性质** | 公式行为是否符合记忆科学 | 单调性：R 越小增长越多（间隔效应）、S 越大增长越少、D 越大增长越少 |
| **状态机** | 阶段迁移与间隔下限是否安全 | 逐条覆盖 new/learning/review/relearning × 四档评分；间隔恒 ≥1 |
| **接管** | 已有 SM-2 进度会不会被清零 | 旧行（stability=NULL）首次 FSRS 复习后间隔**不得**回到 1 天 |

## 为什么"独立重写一遍算式"而不是只断言几个魔数

断言魔数（比如"新卡答 Good 后 S 应为 5.869142"）只能证明实现没变，
不能证明它**对** —— 如果我在实现和测试里犯了同一个下标错误，两边会一起错。
独立重写时特意把 `w[i]` 展开成有名字的局部变量，下标错位会立刻对不上。
同时对 S 的定义做交叉验证（R(S,S)=0.9 与 I(0.9,S)=S 是两条独立的性质），
它们不依赖任何具体数值。
"""

import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.knowledge_card import CardType, KnowledgeCard
from app.models.note import Note, SourceType
from app.models.quiz_item import QuestionType, QuizItem
from app.models.review_log import ReviewLog
from app.models.review_state import (
    ITEM_TYPE_CARD,
    ITEM_TYPE_QUIZ,
    ReviewState,
    ReviewStateKind,
)
from app.models.user import User
from app.services import fsrs_service as F
from app.services import review_service, review_state_service, scheduler_service
from app.services.scheduler_service import (
    ALGORITHM_FSRS,
    ALGORITHM_SM2,
    ScheduleOutcome,
)

NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)

#: 使抖动偏移**恰好为 0** 的随机数
#:
#: `fuzz_interval` 把 [0,1) 均匀映射到 [-delta, +delta]：
#: `int(u * (2d+1)) - d`。u=0.5 时 `0.5*(2d+1) = d + 0.5` 是精确可表示的
#: 浮点数，取整得 d，偏移为 0。需要"真实配置 + 无抖动"时用它。
NO_FUZZ_OFFSET = 0.5


def plain_settings(*, fuzz: float = 0.0, due_hour: int = -1) -> SimpleNamespace:
    """配置桩：**关掉抖动与时段对齐**，用于断言"算法本身"给出的间隔

    阶段 3.7 之后 `advance` 会额外施加这两项调度策略（见
    `scheduler_service.apply_scheduling_policy`）。它们是**策略**而不是算法，
    把它们混进算法断言会让测试变成概率性的：同一个 S 算出的间隔可能是
    5/6/7 天。策略本身在 `tests/test_review_scheduling.py` 里单独测。
    """
    return SimpleNamespace(
        review_scheduler=ALGORITHM_FSRS,
        review_fuzz_ratio=fuzz,
        review_due_hour=due_hour,
        fsrs_request_retention=F.DEFAULT_REQUEST_RETENTION,
        fsrs_max_interval_days=F.MAX_INTERVAL_DAYS,
    )


# ---------------------------------------------------------------------------
# 第一层：公式忠实性
# ---------------------------------------------------------------------------

class TestPublishedFormulas:
    """逐条对照 open-spaced-repetition 的 FSRS-5 公式"""

    def test_weight_vector_shape(self):
        """FSRS-5 是 19 个参数；少一个就意味着某个 w_i 取了别的值"""
        assert len(F.DEFAULT_W) == 19

    def test_stability_is_the_90_percent_point(self):
        """S 的定义：R(t=S, S) == 0.9 —— 不依赖任何魔数的交叉验证"""
        for s in (0.5, 1, 3.173, 30, 365):
            assert F.retrievability(s, s) == pytest.approx(0.9, abs=1e-9)

    def test_interval_inverts_the_forgetting_curve(self):
        """I(r,S) 与 R(t,S) 互为反函数（r=0.9 时 I 恰为 S）"""
        for s in (1.0, 7.5, 100.0):
            assert F.interval_from_stability(s, 0.9) == pytest.approx(s, rel=1e-9)
            for r in (0.7, 0.8, 0.95):
                days = F.interval_from_stability(s, r)
                assert F.retrievability(days, s) == pytest.approx(r, rel=1e-9)

    def test_higher_retention_means_shorter_interval(self):
        s = 50.0
        assert (
            F.interval_from_stability(s, 0.95)
            < F.interval_from_stability(s, 0.9)
            < F.interval_from_stability(s, 0.8)
        )

    def test_initial_stability_is_direct_lookup(self):
        """S0(G) = w[G-1]"""
        for rating in F.RATINGS:
            assert F.initial_stability(rating) == F.DEFAULT_W[rating - 1]

    def test_initial_difficulty_matches_documented_anchor(self):
        """公开说明里写明 w4 = D0(1)（首次就忘记时的难度）

        这条断言专治下标错位：如果实现写成 `w4 - exp(w5*G) + 1`，
        D0(1) 就不会等于 w4。
        """
        assert F.initial_difficulty(1) == pytest.approx(F.DEFAULT_W[4])
        # 评分越高（越容易）难度越低，且严格单调
        ds = [F.initial_difficulty(r) for r in F.RATINGS]
        assert ds == sorted(ds, reverse=True)
        assert len(set(ds)) == len(ds)

    def test_difficulty_reference_implementation(self):
        """D0 的独立重写对拍"""
        w = F.DEFAULT_W
        for rating in F.RATINGS:
            expected = w[4] - math.exp(w[5] * (rating - 1)) + 1
            assert F.initial_difficulty(rating) == pytest.approx(expected)

    def test_next_difficulty_reference_implementation(self):
        """ΔD 线性阻尼 + 均值回归（目标 D0(4)）的独立重写对拍"""
        w = F.DEFAULT_W
        d0_easy = w[4] - math.exp(w[5] * 3) + 1
        for d in (1.0, 4.0, 5.2824, 9.0):
            for rating in F.RATINGS:
                damped = d + (-w[6] * (rating - 3)) * (10 - d) / 9
                expected = w[7] * d0_easy + (1 - w[7]) * damped
                assert F.next_difficulty(d, rating) == pytest.approx(
                    min(10.0, max(1.0, expected))
                )

    def test_difficulty_reverts_toward_d0_of_easy(self):
        """均值回归的目标是 D0(4) 而不是 D0(3)

        这是 FSRS-5 相对 FSRS-4.5 的改动之一，且强度很小（w7=0.0046），
        所以只能靠"多步收敛到哪里"来验证。rating=3 时 ΔD=0，
        此时 D 的演化**只剩**均值回归，收敛目标就是它。
        """
        target = F.initial_difficulty(F.RATING_EASY)
        d = 9.5
        for _ in range(3000):
            d = F.next_difficulty(d, F.RATING_GOOD)
        assert d == pytest.approx(target, abs=0.05)

    def test_recall_stability_reference_implementation(self):
        """成功复习的 S' 独立重写对拍（含 Hard 惩罚与 Easy 奖励的位置）"""
        w = F.DEFAULT_W
        for d, s, r, rating in (
            (5.0, 3.173, 0.9, F.RATING_GOOD),
            (5.0, 3.173, 0.9, F.RATING_HARD),
            (5.0, 3.173, 0.9, F.RATING_EASY),
            (2.0, 100.0, 0.75, F.RATING_EASY),
            (9.0, 0.4, 0.99, F.RATING_HARD),
        ):
            hard = w[15] if rating == F.RATING_HARD else 1.0
            easy = w[16] if rating == F.RATING_EASY else 1.0
            expected = s * (
                math.exp(w[8])
                * (11 - d)
                * s ** (-w[9])
                * (math.exp(w[10] * (1 - r)) - 1)
                * hard
                * easy
                + 1
            )
            assert F.stability_after_recall(d, s, r, rating) == pytest.approx(expected)

    def test_forget_stability_reference_implementation(self):
        """遗忘后 S' 独立重写对拍（注意是 (S+1)^w13 - 1 而不是 S^w13）"""
        w = F.DEFAULT_W
        for d, s, r in ((5.0, 3.173, 0.9), (2.0, 0.5, 0.99), (9.0, 60.0, 0.7)):
            expected = w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp(
                w[14] * (1 - r)
            )
            assert F.stability_after_forget(d, s, r) == pytest.approx(expected)

    def test_short_term_reference_implementation(self):
        """同日复习的 S' 独立重写对拍"""
        w = F.DEFAULT_W
        for rating, s in ((1, 6.0), (2, 6.0), (3, 6.0), (4, 6.0)):
            expected = s * math.exp(w[17] * (rating - 3 + w[18]))
            assert F.stability_short_term(s, rating) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 第二层：性质（记忆科学的三个方向）
# ---------------------------------------------------------------------------

class TestMemoryScienceProperties:
    """这些性质是换掉 SM-2 的**理由本身**，所以必须被测到

    SM-2 三条全不满足：它的间隔只由 repetition 与 EF 决定，
    与"隔了多久"（R）无关，也与"当时多费劲"（rating 强度）无关。
    """

    def test_spacing_effect_longer_delay_grows_stability_more(self):
        """间隔效应：拖得越久还答对，说明记得越牢 → S 增长越多"""
        gains = [
            F.stability_after_recall(5.0, 10.0, r, F.RATING_GOOD) / 10.0
            for r in (0.95, 0.9, 0.8, 0.6)
        ]
        assert gains == sorted(gains), "R 越小，S 增长应越大"

    def test_higher_stability_grows_slower(self):
        """已经记牢的卡，再巩固的边际收益递减"""
        gains = [
            F.stability_after_recall(5.0, s, 0.9, F.RATING_GOOD) / s
            for s in (1.0, 10.0, 100.0, 1000.0)
        ]
        assert gains == sorted(gains, reverse=True)

    def test_higher_difficulty_grows_slower(self):
        gains = [
            F.stability_after_recall(d, 10.0, 0.9, F.RATING_GOOD) / 10.0
            for d in (1.0, 4.0, 7.0, 10.0)
        ]
        assert gains == sorted(gains, reverse=True)

    def test_successful_review_never_shrinks_stability(self):
        """成功复习后 S 必须增长（否则间隔会倒退，用户会看到卡片越来越频繁）"""
        for d in (1.0, 5.0, 10.0):
            for s in (0.1, 1.0, 50.0):
                for r in (0.5, 0.9, 1.0):
                    for rating in (F.RATING_HARD, F.RATING_GOOD, F.RATING_EASY):
                        new = F.stability_after_recall(d, s, r, rating)
                        assert new >= s, f"D={d} S={s} R={r} G={rating} → {new}"

    def test_easy_grows_faster_than_good_than_hard(self):
        hard = F.stability_after_recall(5.0, 10.0, 0.9, F.RATING_HARD)
        good = F.stability_after_recall(5.0, 10.0, 0.9, F.RATING_GOOD)
        easy = F.stability_after_recall(5.0, 10.0, 0.9, F.RATING_EASY)
        assert hard < good < easy

    def test_rating_strength_changes_the_interval(self):
        """SM-2 的核心缺陷回归测试：不同评分必须给出不同间隔

        旧实现里 quality=3 与 quality=5 得到**完全相同**的间隔
        （`calculate_sm2` 的 `quality >= 3` 分支只用 quality 更新 EF）。
        """
        intervals = {
            r: F._interval_days(
                F.stability_after_recall(5.0, 10.0, 0.9, r), 0.9,
            )
            for r in (F.RATING_HARD, F.RATING_GOOD, F.RATING_EASY)
        }
        assert intervals[F.RATING_HARD] < intervals[F.RATING_GOOD] < intervals[F.RATING_EASY]

    def test_forgetting_keeps_residual_strength(self):
        """遗忘不是回到零：答错后 S 下降，但**保留与旧 S 相关的残余**

        这不是可选的实现细节 —— 它决定了"打回 1 天重学"之后
        下次答对时是从零爬还是从残余爬。SM-2 是彻底清零（repetition=0）。
        """
        old = F.stability_after_forget(5.0, 60.0, 0.9)
        fresh = F.stability_after_forget(5.0, 1.0, 0.9)
        assert old > fresh, "曾经更牢的卡，遗忘后的残余强度应更高"
        assert old < 60.0, "遗忘后 S 必须下降"

    def test_retrievability_monotone_and_bounded(self):
        values = [F.retrievability(t, 20.0) for t in (0, 1, 5, 20, 60, 365)]
        assert values == sorted(values, reverse=True)
        assert values[0] == pytest.approx(1.0)
        assert values[3] == pytest.approx(0.9)      # t == S 是 0.9 的定义点
        # ⚠️ FSRS 用的是一条**幂律**遗忘曲线（尾部很厚），不是指数衰减：
        # S=20 的卡放一年（18 倍 S）R 仍有 0.44，而指数模型
        # （mastery_service.compute_retrievability 的 2^(-t/S)）只给 0.03。
        # 这不是实现问题，但它意味着阶段 3.9 不能把两个公式混用 ——
        # 掌握度与"到期预测"若引用不同的曲线，用户会看到互相矛盾的数字。
        assert values[-1] == pytest.approx(0.435, abs=0.01)
        assert values[-1] > 0.4

    def test_guard_rails_on_degenerate_input(self):
        """数值护栏：0/负数/NaN/极大输入不得产生 NaN 或负数

        ⚠️ NaN 这一条是**真抓到过 bug 的**：原先各处写的是
        `max(stability, MIN_STABILITY)`，而 Python 的 `max(nan, 0.01)`
        返回 nan（`0.01 > nan` 恒为 False），NaN 会一路穿到
        `S ** -w9` 再扩散进库。改用 `safe_stability` 后才有下面这些性质。
        """
        assert F.retrievability(10, 0) > 0          # S=0 不得除零
        assert F.retrievability(-5, 10) == pytest.approx(1.0)  # 时钟回拨
        assert F.clamp(float("nan"), 1.0, 10.0) == 1.0
        assert not math.isnan(F.retrievability(10, float("nan")))
        assert not math.isnan(F.stability_after_recall(float("nan"), 5.0, 0.9, 3))
        assert not math.isnan(F.stability_after_forget(float("nan"), float("nan"), 0.9))
        assert not math.isnan(F.stability_short_term(float("nan"), 3))
        assert not math.isnan(F.next_difficulty(float("nan"), 3))
        assert F._interval_days(0.0, 0.9) >= 1
        # S 溢出成无穷 → 取上限而不是 1 天（否则"无限牢"的卡会明天到期）
        assert F._interval_days(float("inf"), 0.9) == F.MAX_INTERVAL_DAYS
        assert F._interval_days(float("nan"), 0.9) == 1
        assert F._interval_days(1e9, 0.9) == F.MAX_INTERVAL_DAYS


# ---------------------------------------------------------------------------
# 第三层：状态机
# ---------------------------------------------------------------------------

def _sched(rating, state, *, s=None, d=None, elapsed=0.0, interval=1, ef=2.5):
    return F.schedule(
        rating=rating, state=state, stability=s, difficulty=d,
        elapsed_days=elapsed, interval_days=interval, easiness_factor=ef, now=NOW,
    )


class TestStateMachine:
    """阶段迁移与间隔下限

    ⚠️ 学习步策略（新卡答对先给 1 天）**不是** FSRS 的规定部分，
    而是本项目的选择（见 `fsrs_service._next_state_and_interval`）。
    它是被测对象，不是被测前提 —— 改它就要改这里，并说明理由。
    """

    def test_new_card_passing_enters_learning_for_one_day(self):
        for rating in (F.RATING_AGAIN, F.RATING_HARD, F.RATING_GOOD):
            r = _sched(rating, ReviewStateKind.new)
            assert r.state == ReviewStateKind.learning
            assert r.interval_days == 1

    def test_new_card_easy_graduates_immediately(self):
        r = _sched(F.RATING_EASY, ReviewStateKind.new)
        assert r.state == ReviewStateKind.review
        assert r.interval_days == round(F.initial_stability(F.RATING_EASY))

    def test_learning_graduates_on_pass(self):
        r = _sched(F.RATING_GOOD, ReviewStateKind.learning, s=3.173, d=5.28, elapsed=1.0)
        assert r.state == ReviewStateKind.review
        assert r.interval_days == round(F.interval_from_stability(r.stability))

    def test_learning_again_stays_in_learning(self):
        r = _sched(F.RATING_AGAIN, ReviewStateKind.learning, s=3.173, d=5.28, elapsed=1.0)
        assert r.state in (ReviewStateKind.learning, ReviewStateKind.relearning)
        assert r.interval_days == 1

    def test_review_lapse_enters_relearning(self):
        r = _sched(F.RATING_AGAIN, ReviewStateKind.review, s=20.0, d=5.0, elapsed=20.0)
        assert r.state == ReviewStateKind.relearning
        assert r.interval_days == 1
        assert r.stability < 20.0

    def test_review_pass_stays_in_review_and_grows(self):
        r = _sched(F.RATING_GOOD, ReviewStateKind.review, s=20.0, d=5.0, elapsed=20.0)
        assert r.state == ReviewStateKind.review
        assert r.stability > 20.0
        assert r.interval_days > 20

    def test_same_day_review_uses_short_term_formula(self):
        """同日复习必须走短时公式

        回归测试：若把 `elapsed < 1` 分支去掉，t=0 时成功公式给出的 S'
        恰好等于 S（因为 `exp(w10*(1-R))-1 = 0`），于是"当天再看一遍"
        对记忆状态毫无影响 —— 这显然是错的。
        """
        same_day = _sched(
            F.RATING_GOOD, ReviewStateKind.relearning, s=6.0, d=5.0, elapsed=0.0,
        )
        assert same_day.stability != pytest.approx(6.0)
        assert same_day.stability == pytest.approx(
            F.stability_short_term(6.0, F.RATING_GOOD)
        )

    def test_same_day_again_reduces_stability(self):
        r = _sched(F.RATING_AGAIN, ReviewStateKind.relearning, s=6.0, d=5.0, elapsed=0.0)
        assert r.stability < 6.0

    def test_interval_always_at_least_one_day(self):
        """间隔下限 1 天：0 天会让卡片立刻再次到期，形成死循环"""
        for rating in F.RATINGS:
            for kind in ReviewStateKind:
                r = _sched(rating, kind, s=0.01, d=10.0, elapsed=0.0)
                assert r.interval_days >= 1

    def test_interval_capped_by_max(self):
        r = F.schedule(
            rating=F.RATING_EASY, state=ReviewStateKind.review,
            stability=1e6, difficulty=1.0, elapsed_days=1.0,
            now=NOW, max_interval_days=30,
        )
        assert r.interval_days == 30

    def test_next_review_at_is_now_plus_interval(self):
        r = _sched(F.RATING_GOOD, ReviewStateKind.review, s=10.0, d=5.0, elapsed=10.0)
        assert r.next_review_at == NOW + timedelta(days=r.interval_days)

    def test_interval_tracks_request_retention(self):
        """目标保持率越高，同一张卡的间隔越短（配置项真的接上了）"""
        base = dict(
            rating=F.RATING_GOOD, state=ReviewStateKind.review,
            stability=100.0, difficulty=5.0, elapsed_days=50.0, now=NOW,
        )
        low = F.schedule(**base, request_retention=0.8).interval_days
        mid = F.schedule(**base, request_retention=0.9).interval_days
        high = F.schedule(**base, request_retention=0.95).interval_days
        assert high < mid < low


# ---------------------------------------------------------------------------
# 第四层：从 SM-2 接管（旧进度不得清零）
# ---------------------------------------------------------------------------

class TestAdoptLegacyState:
    """真库里有 2241 行 SM-2 时期的状态，它们的 stability 都是 NULL

    这一层测的是 overhaul-plan 症状 D-3 的同型风险：换算法把学习历史清零。
    """

    def test_stability_adopted_from_interval(self):
        """S := interval_days（FSRS 对 S 的定义直接给出的换算，不是近似）"""
        s, _ = F.adopt_legacy_state(
            state=ReviewStateKind.review, interval_days=17,
            easiness_factor=2.5,
        )
        assert s == pytest.approx(17.0)
        # 换算的自洽性：接管后的 S 解出的间隔就是原来那个间隔
        assert F.interval_from_stability(s, 0.9) == pytest.approx(17.0)

    def test_difficulty_adopted_from_easiness(self):
        _, d = F.adopt_legacy_state(
            state=ReviewStateKind.review, interval_days=10, easiness_factor=2.5,
        )
        assert d == pytest.approx(5.0), "中性 EF=2.5 应对应中性难度 D=5"
        _, harder = F.adopt_legacy_state(
            state=ReviewStateKind.review, interval_days=10, easiness_factor=1.5,
        )
        assert harder > d, "EF 低（更难）应换算成更高的难度"

    def test_new_card_is_not_adopted_from_interval(self):
        """新卡用 FSRS 自己的先验（Good 档初值），而不是 EF 反解出的中性难度

        一张从未复习过的卡上的 EF 必然是默认值 2.5，不含关于这张卡难度的
        任何信息；而 D0(Good)=5.28 是 FSRS 对"刚学会的卡"的先验。
        """
        s, d = F.adopt_legacy_state(
            state=ReviewStateKind.new, interval_days=1, easiness_factor=2.5,
        )
        assert s == pytest.approx(F.initial_stability(F.RATING_GOOD))
        assert d == pytest.approx(F.initial_difficulty(F.RATING_GOOD))

    def test_legacy_progress_is_not_reset(self):
        """★ 关键回归：一张复习了 3 次、间隔 15 天的 SM-2 卡，首次 FSRS 复习后
        间隔**不得**变成 1 天

        若把 stability=None 当成"新卡"，这张卡会被打回 1 天 ——
        用户攒了两周的进度一次复习就归零，而这在界面上完全看不出来。
        """
        r = _sched(
            F.RATING_GOOD, ReviewStateKind.review,
            s=None, d=None, elapsed=15.0, interval=15, ef=2.5,
        )
        assert r.interval_days > 15, f"接管后间隔反而缩短到 {r.interval_days} 天"
        assert r.state == ReviewStateKind.review
        assert r.retrievability == pytest.approx(0.9, abs=0.01)

    def test_easiness_bridge_is_invertible_on_the_representable_range(self):
        """在 EF 能表示的范围内（D ∈ [3,10]）D↔EF 是一一映射"""
        for d in (3.0, 5.0, 7.0, 10.0):
            assert F.difficulty_from_easiness(F.easiness_from_difficulty(d)) == pytest.approx(d)
        ef = [F.easiness_from_difficulty(d) for d in (1.0, 4.0, 7.0, 10.0)]
        assert ef == sorted(ef, reverse=True)
        assert all(F.EF_MIN <= v <= F.EF_MAX for v in ef)
        assert F.easiness_from_difficulty(5.0) == pytest.approx(2.5), (
            "两个算法的默认值（D=5 / EF=2.5）必须落在同一点，否则接管时难度会凭空跳变"
        )

    def test_easiness_bridge_saturates_below_d3(self):
        """D < 3 在 EF 一侧被夹住 —— 这是**有损**的，必须被记录下来

        真库里的 EF 本来就在 [1.3, 2.8] 内（SM-2 自己夹的），所以"取回来"
        不会比原来更糟。而 D<3 只可能由 FSRS 自己产生，那时
        `review_states.stability/difficulty` 已非 NULL，走直通分支，
        根本不会经过这个桥。
        """
        assert F.easiness_from_difficulty(1.0) == pytest.approx(F.EF_MAX)
        assert F.difficulty_from_easiness(F.EF_MAX) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# quality → rating 口径
# ---------------------------------------------------------------------------

class TestRatingMapping:
    """quality 是 SM-2 的 0-5，rating 是 FSRS 的 1-4；换算取决于**谁给的这一分**"""

    def test_self_rating_four_buttons(self):
        """前端四档按钮的取值恰好是 0/3/4/5 → Again/Hard/Good/Easy"""
        assert scheduler_service.rating_from_quality(0, "self_rating") == F.RATING_AGAIN
        assert scheduler_service.rating_from_quality(3, "self_rating") == F.RATING_HARD
        assert scheduler_service.rating_from_quality(4, "self_rating") == F.RATING_GOOD
        assert scheduler_service.rating_from_quality(5, "self_rating") == F.RATING_EASY

    def test_machine_grade_never_yields_easy(self):
        """★ 机器判分封顶 Good

        选择题答对一律给 quality=5，但"选对了"没有"毫不费力"这层信息。
        若照搬成 Easy，新卡第一次答对就排到两周后（S0(Easy)=15.7 天），
        而用户只见了这张卡一次。
        """
        for method in ("choice", "fill_blank", "semantic", "ungraded", "legacy", ""):
            assert scheduler_service.rating_from_quality(5, method) == F.RATING_GOOD

    def test_failure_is_again_on_both_paths(self):
        for q in (0, 1, 2):
            for method in ("self_rating", "choice", "semantic"):
                assert scheduler_service.rating_from_quality(q, method) == F.RATING_AGAIN

    def test_out_of_range_is_clamped(self):
        assert scheduler_service.rating_from_quality(-5, "self_rating") == F.RATING_AGAIN
        assert scheduler_service.rating_from_quality(99, "choice") == F.RATING_GOOD

    def test_machine_correct_gets_shorter_interval_than_self_easy(self):
        """口径差异必须真的体现在间隔上，而不只是常量表里的差别"""
        machine = _sched(F.RATING_GOOD, ReviewStateKind.new)
        self_easy = _sched(F.RATING_EASY, ReviewStateKind.new)
        assert machine.interval_days < self_easy.interval_days

    def test_derive_kind(self):
        assert scheduler_service.derive_kind(0, None) == ReviewStateKind.new
        assert scheduler_service.derive_kind(0, 1) == ReviewStateKind.learning
        assert scheduler_service.derive_kind(2, 5) == ReviewStateKind.review
        assert scheduler_service.derive_kind(2, 1) == ReviewStateKind.relearning


# ---------------------------------------------------------------------------
# 端到端：落库、事件流、回退开关
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


async def _make_card(session_factory, user_id: str) -> tuple[str, str]:
    note_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(id=note_id, user_id=user_id, title="t", source_type=SourceType.pdf))
        await db.commit()
    card_id = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(KnowledgeCard(
            id=card_id, user_id=user_id, note_id=note_id,
            card_type=CardType.concept, title="机器学习", content="让计算机从数据中学习",
        ))
        await db.commit()
    return card_id, note_id


async def _make_quiz(
    session_factory, user_id: str, card_id: str, note_id: str, *,
    question_type=QuestionType.short_answer, interval: int = 1,
    repetition: int = 0, ef: float = 2.5,
    last_reviewed_days_ago: float | None = None,
) -> str:
    quiz_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    last = (
        now - timedelta(days=last_reviewed_days_ago)
        if last_reviewed_days_ago is not None else None
    )
    async with session_factory() as db:
        db.add(QuizItem(
            id=quiz_id, user_id=user_id, note_id=note_id, card_id=card_id,
            question="什么是机器学习", answer="机器学习",
            question_type=question_type,
            interval=interval, repetition=repetition, easiness_factor=ef,
            last_reviewed_at=last, next_review_at=None,
        ))
        await db.commit()
    return quiz_id


async def _state_of(session_factory, user_id: str, item_type: str, item_id: str):
    async with session_factory() as db:
        return (await db.execute(
            select(ReviewState).where(
                ReviewState.user_id == user_id,
                ReviewState.item_type == item_type,
                ReviewState.item_id == item_id,
            )
        )).scalars().first()


async def _logs_of(session_factory, user_id: str):
    async with session_factory() as db:
        return list((await db.execute(
            select(ReviewLog).where(ReviewLog.user_id == user_id)
            .order_by(ReviewLog.review_at)
        )).scalars().all())


@pytest.mark.asyncio
class TestPersistence:
    """FSRS 状态真的写进库了吗（模型加了列 ≠ 路径写了列）"""

    async def test_submit_writes_stability_and_difficulty(self, test_db):
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=4,
            )

        state = await _state_of(test_db, uid, ITEM_TYPE_QUIZ, quiz_id)
        assert state is not None
        assert state.stability is not None, "FSRS 的 S 没有落库"
        assert state.difficulty is not None, "FSRS 的 D 没有落库"
        assert state.stability == pytest.approx(F.initial_stability(F.RATING_GOOD))
        assert state.state == ReviewStateKind.learning
        assert state.interval_days == 1

    async def test_submit_writes_event_stream_fields(self, test_db):
        """阶段 3.2：rating / predicted_retention / item_type"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=5,
            )

        logs = await _logs_of(test_db, uid)
        assert len(logs) == 1
        log = logs[0]
        assert log.rating == F.RATING_EASY, "自评『轻松想起』应记为 Easy"
        assert log.item_type == "quiz"
        # 首评的 R 恒为 1.0：从未复习过，模型认为"此刻必然想得起来"
        assert log.predicted_retention == pytest.approx(1.0)

    async def test_legacy_fields_are_mirrored(self, test_db):
        """`quiz_items` 旧字段仍是到期队列的读取来源，必须是权威调度的镜像"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=4,
            )

        async with test_db() as db:
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()
            state = (await db.execute(
                select(ReviewState).where(
                    ReviewState.item_type == ITEM_TYPE_QUIZ,
                    ReviewState.item_id == quiz_id,
                )
            )).scalars().first()

        assert quiz.interval == state.interval_days
        assert quiz.repetition == state.repetition
        assert quiz.next_review_at.replace(tzinfo=None) == state.next_review_at.replace(tzinfo=None)
        assert quiz.easiness_factor == pytest.approx(state.easiness_factor)

    async def test_predicted_retention_is_pre_review_value(self, test_db):
        """★ predicted_retention 必须是**复习前**的 R

        回归测试：若在 `apply_schedule_result` 之后才计算 R，得到的是
        "用复习后的 S 算出来的事后数字"，它总在 1.0 附近，
        校准曲线（预测 vs 实际）随即变成一句永远乐观的空话。
        这里让第二次复习间隔 10 天，预测值必须明显低于 1。
        """
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(
            test_db, uid, card_id, note_id,
            interval=10, repetition=2, last_reviewed_days_ago=10,
        )

        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "机器学习", 0, db, self_rating=4,
            )

        log = (await _logs_of(test_db, uid))[-1]
        # 接管换算 S=10 天，隔了 10 天复习 → R 应恰为 0.9
        assert log.predicted_retention == pytest.approx(0.9, abs=0.01)
        assert log.rating == F.RATING_GOOD

    async def test_placeholder_submission_does_not_touch_schedule(self, test_db):
        """简答题占位提交（未自评）：不推进调度，也不写 rating/预测值"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            result = await review_service.submit_answer(quiz_id, uid, "随便写", 0, db)

        assert result["needs_self_assessment"] is True
        # 占位提交不推进调度 ⇒ 连 review_states 行都不该存在。
        # 这比"行存在但 stability 为 NULL"更强：占位提交没有制造任何调度痕迹。
        state = await _state_of(test_db, uid, ITEM_TYPE_QUIZ, quiz_id)
        assert state is None, "占位提交不该创建/修改调度状态"

        log = (await _logs_of(test_db, uid))[-1]
        assert log.rating is None
        assert log.predicted_retention is None
        assert log.item_type == "quiz"

    async def test_self_rating_completes_placeholder_and_advances(self, test_db):
        """两阶段流程：占位 → 自评补完，调度由自评分驱动"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(test_db, uid, card_id, note_id)

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, "随便写", 0, db)
        async with test_db() as db:
            await review_service.submit_answer(
                quiz_id, uid, "随便写", 0, db, self_rating=0,
            )

        state = await _state_of(test_db, uid, ITEM_TYPE_QUIZ, quiz_id)
        assert state.stability is not None
        assert state.interval_days == 1
        assert state.lapses == 1
        # 首次复习就答错 → FSRS 判定为 learning 而不是 relearning：
        # 这张卡**还没学会**，谈不上"重学"（Anki 同此语义）。
        # lapses 仍然 +1（"被遗忘过一次"是事实），两者并不矛盾。
        assert state.state == ReviewStateKind.learning

    async def test_choice_correct_is_good_not_easy(self, test_db):
        """端到端验证机器判分的封顶（新卡答对不应被排到两周后）"""
        uid = await _make_user(test_db)
        card_id, note_id = await _make_card(test_db, uid)
        quiz_id = await _make_quiz(
            test_db, uid, card_id, note_id, question_type=QuestionType.choice,
        )
        async with test_db() as db:
            quiz = (await db.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()
            quiz.answer = "A. 机器学习"
            quiz.options = '["A. 机器学习", "B. 数据库"]'
            await db.commit()

        async with test_db() as db:
            await review_service.submit_answer(quiz_id, uid, "A", 0, db)

        log = (await _logs_of(test_db, uid))[-1]
        assert log.quality == 5, "选择题答对应给 SM-2 的 5 分"
        assert log.rating == F.RATING_GOOD, "但它不得被当成 Easy"
        state = await _state_of(test_db, uid, ITEM_TYPE_QUIZ, quiz_id)
        assert state.interval_days == 1


class TestAlgorithmSelection:
    """算法选择与回退开关（纯计算部分）"""

    def test_sm2_outcome_has_no_fsrs_state(self):
        state = ReviewState(
            user_id="u", item_type=ITEM_TYPE_CARD, item_id="c",
            interval_days=6, repetition=2, easiness_factor=2.5,
            state=ReviewStateKind.review,
        )
        outcome = scheduler_service.advance(
            state, 5, method="self_rating", now=NOW,
            algorithm=ALGORITHM_SM2, settings=plain_settings(),
        )
        assert outcome.algorithm == ALGORITHM_SM2
        assert outcome.stability is None
        assert outcome.rating is None
        assert outcome.predicted_retention is None
        # 与旧 SM-2 行为逐位一致（6 天 × EF 2.6）
        assert outcome.interval_days == 16

    def test_unknown_algorithm_falls_back_to_fsrs(self):
        """配置写错时按 FSRS 处理（而不是静默用一个谁也说不清的算法）"""
        state = ReviewState(
            user_id="u", item_type=ITEM_TYPE_CARD, item_id="c",
            interval_days=1, repetition=0, easiness_factor=2.5,
            state=ReviewStateKind.new,
        )
        outcome = scheduler_service.advance(
            state, 4, method="self_rating", now=NOW, algorithm="sm3",
        )
        assert outcome.algorithm == ALGORITHM_FSRS

    def test_env_switch_selects_sm2(self, monkeypatch):
        """配置项真的接上了（而不是写了个没人读的字段）

        这里**必须**用真实配置（才能验证 env 被读到），所以不能靠
        `plain_settings()` 关抖动；改用 `rand=0.5` —— 它使抖动偏移恰为 0
        （`int(0.5*(2d+1)) - d == d - d`），从而只关掉抖动、不动其它路径。
        """
        from app.config import get_settings

        state = ReviewState(
            user_id="u", item_type=ITEM_TYPE_CARD, item_id="c",
            interval_days=6, repetition=2, easiness_factor=2.5,
            state=ReviewStateKind.review,
        )
        monkeypatch.setenv("REVIEW_SCHEDULER", "sm2")
        get_settings.cache_clear()
        try:
            outcome = scheduler_service.advance(
                state, 5, method="self_rating", now=NOW, rand=NO_FUZZ_OFFSET,
            )
        finally:
            monkeypatch.delenv("REVIEW_SCHEDULER", raising=False)
            get_settings.cache_clear()
        assert outcome.algorithm == ALGORITHM_SM2
        assert outcome.interval_days == 16


@pytest.mark.asyncio
class TestRollbackSwitch:
    """回退到 SM-2 时，FSRS 的状态必须被清空"""

    async def test_switch_clears_fsrs_state(self, test_db):
        """★ 切回 SM-2 后，FSRS 的 S/D 必须被**清空**而不是留着过期值

        留着过期的 S 有两种坏结果：之后切回 FSRS 会拿它与 SM-2 刚写的
        interval 互相矛盾地当输入；而 NULL 与有值在 `schedule()` 里走的是
        两条不同分支。清空让"NULL ⟺ 当前不由 FSRS 调度"重新成立。
        """
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        # 先造出一条"已被 FSRS 调度过"的状态
        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            fsrs_outcome = scheduler_service.advance(
                state, 4, method="self_rating", now=NOW,
                algorithm=ALGORITHM_FSRS, settings=plain_settings(),
            )
            await review_state_service.apply_schedule_result(
                db, uid, ITEM_TYPE_CARD, card_id,
                outcome=fsrs_outcome, quality=4, now=NOW,
            )
            await db.commit()
        state = await _state_of(test_db, uid, ITEM_TYPE_CARD, card_id)
        assert state.stability is not None

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            sm2_outcome = scheduler_service.advance(
                state, 4, method="self_rating", now=NOW,
                algorithm=ALGORITHM_SM2, settings=plain_settings(),
            )
            await review_state_service.apply_schedule_result(
                db, uid, ITEM_TYPE_CARD, card_id,
                outcome=sm2_outcome, quality=4, now=NOW,
            )
            await db.commit()

        state = await _state_of(test_db, uid, ITEM_TYPE_CARD, card_id)
        assert state.stability is None
        assert state.difficulty is None
        # SM-2 的路径仍然工作：间隔 1 → 6（第 2 次成功）
        assert state.interval_days == 6
        assert state.state == ReviewStateKind.review

    async def test_unknown_algorithm_name_is_not_fatal(self, test_db):
        """配置写错时按 FSRS 处理（而不是静默用一个谁也说不清的算法）"""
        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            state = await review_state_service.get_state(db, uid, ITEM_TYPE_CARD, card_id)
            outcome = scheduler_service.advance(
                state, 4, method="self_rating", now=NOW, algorithm="sm3",
            )
        assert outcome.algorithm == ALGORITHM_FSRS


@pytest.mark.asyncio
class TestCardReviewUsesSameScheduler:
    """卡片复习与答题复习必须走同一个调度入口

    改造前 `api/review.py::submit_card_review` 里有一份**手抄的 SM-2 调用**。
    换算法时如果只改了答题那条路径，卡片复习会继续按 SM-2 排期，
    而两条路径写的是同一批 review_states 行 —— 同一张卡在两条路径间
    来回切换会得到互相矛盾的间隔。
    """

    async def test_card_review_writes_fsrs_state(self, test_db):
        from app.api.review import submit_card_review
        from app.schemas.review import CardReviewSubmitRequest

        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)

        async with test_db() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalars().first()
            resp = await submit_card_review(
                card_id, CardReviewSubmitRequest(self_rating=4), user, db,
            )

        state = await _state_of(test_db, uid, ITEM_TYPE_CARD, card_id)
        assert state.stability is not None, "卡片复习没有走 FSRS"
        assert state.stability == pytest.approx(F.initial_stability(F.RATING_GOOD))
        assert resp.interval_days == state.interval_days == 1
        # 响应要把"为什么给这个间隔"的依据一并给出：只回一个天数
        # 等于要求用户盲信调度器（S=记忆强度，R=复习前的可回忆概率）
        assert resp.stability == pytest.approx(state.stability)
        assert resp.difficulty == pytest.approx(state.difficulty)
        assert resp.predicted_retention == pytest.approx(1.0)

        log = (await _logs_of(test_db, uid))[-1]
        assert log.item_type == "card"
        assert log.rating == F.RATING_GOOD
        assert log.predicted_retention == pytest.approx(1.0)

    async def test_both_paths_agree_on_the_same_state(self, test_db):
        """同一份 review_states 行，两条路径给出的算法必须一致"""
        from app.api.review import submit_card_review
        from app.schemas.review import CardReviewSubmitRequest

        uid = await _make_user(test_db)
        card_id, _ = await _make_card(test_db, uid)
        async with test_db() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalars().first()
            await submit_card_review(
                card_id, CardReviewSubmitRequest(self_rating=4), user, db,
            )

        state = await _state_of(test_db, uid, ITEM_TYPE_CARD, card_id)
        assert state.state == ReviewStateKind.learning, (
            "卡片复习路径的阶段迁移与答题路径不一致（预计是新卡答对 → learning）"
        )


class TestScheduleOutcomeContract:
    """`ScheduleOutcome` 是落库层唯一依赖的口径"""

    def test_fields_present(self):
        outcome = ScheduleOutcome(
            interval_days=1, repetition=0, easiness_factor=2.5,
            next_review_at=NOW, state=ReviewStateKind.new,
            algorithm=ALGORITHM_FSRS,
        )
        for name in (
            "interval_days", "repetition", "easiness_factor", "next_review_at",
            "state", "algorithm", "rating", "predicted_retention",
            "stability", "difficulty",
        ):
            assert hasattr(outcome, name)

    def test_elapsed_business_days_handles_naive_datetimes(self):
        """SQLite 不存时区，历史行取出来可能是 naive 的（不得因此抛异常）"""
        naive = datetime(2026, 9, 1, 9, 0)
        assert scheduler_service.elapsed_business_days(naive, NOW) == 10

    def test_elapsed_business_days_never_negative(self):
        """时钟回拨（或未来时间戳）不得产生负的 elapsed"""
        future = NOW + timedelta(days=5)
        assert scheduler_service.elapsed_business_days(future, NOW) == 0
        assert scheduler_service.elapsed_business_days(None, NOW) == 0

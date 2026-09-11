"""FSRS-5 间隔重复算法（overhaul-plan 阶段 3.6）

## 为什么换掉 SM-2

SM-2 是 1987 年的算法，它只有一个状态变量（`interval`）和一个难度旋钮
（`EF`），并且**没有"记忆强度随时间衰减"的模型**。三个直接后果：

1. **`quality=3` 与 `quality=5` 得到完全相同的间隔**
   （`sm2_service.calculate_sm2` 的 `quality >= 3` 分支里 `quality` 只用于
   更新 EF，不影响本次间隔）—— "勉强想起"和"脱口而出"被当成同一件事。
2. **没有时间维度**：一张卡"当前能想起的概率"无法计算，所以掌握度只能
   用活动量（答对过几次）近似，而不是记忆状态（见 `mastery_service` 的说明）。
3. **间隔只由本次评分决定**，与"实际隔了多久才复习"无关。而记忆科学里
   最重要的效应之一恰恰是**间隔效应**：隔得越久还答对，说明记得越牢。

FSRS（Free Spaced Repetition Scheduler）用 DSR 三参数模型修掉这三点：

    D (Difficulty)      题目对该用户的难度，∈[1,10]
    S (Stability)       记忆强度；定义为"回忆概率降到 90% 所需的天数"
    R (Retrievability)  当前可回忆概率，由 S 与距上次复习的天数算出

`R` 就是 overhaul-plan 阶段 3.9 需要的"当前可回忆概率"，`S` 则是
"同等保持率下更少的复习量"的来源 —— 因为间隔由 S 直接解出，
而不是靠 EF 连乘。

## 本实现的选择：自实现而非引入 `fsrs` 包

- 公式与默认参数是**公开且固定**的（见下），实现是纯算术，不需要拟合器；
- 项目其余部分（`_levenshtein`、`to_bigrams`、拆分块器）同样是自实现，
  依赖越少越好在离线环境里越明显（当前 mineru_env 里没有 `fsrs`）；
- 真正需要外部工具的是**参数拟合**（用累计复习日志拟合个人化 w），
  那是阶段 3.14 的事，且拟合出来的仍然只是一组 19 个浮点数，
  本模块的 `DEFAULT_W` 已经是可直接替换的插槽。

## 参数来源（不要凭记忆改这些数字）

FSRS-5 默认参数（19 个），取自 open-spaced-repetition 的算法说明：

    https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm

换算成"同等保持率下复习量下降 20-30%"这一结论的数据集与代码都在该组织下。
**这些数字是拟合结果，不是调参旋钮** —— 手改它们等于宣称自己比公开数据集
更懂用户的记忆。要个人化就应该走拟合（3.14），而不是猜。

## 本模块负责什么、不负责什么

负责：**记忆状态的更新**（S/D 怎么变）与**间隔的求解**（由 S 与目标保持率
解出天数）。这两者是 FSRS 的规定部分，有公开公式可比对。

不负责：**学习步（learning step）策略**。FSRS 本身不规定"新卡第一次答对
之后隔多久"，那是调度器（Anki 的 learning steps）的事。本项目的做法见
`_next_state_and_interval`：新卡先给 1 天的学习步，第二次答对才按 FSRS
解出的间隔进入长期复习。这一段是**本项目的策略**，不是 FSRS 的公式。
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# 学习阶段直接复用 `review_states.state` 的枚举，不另立一套字符串常量 ——
# 两套名字一旦漂移，写进库的阶段值就会有一部分谁都认不出来。
from ..models.review_state import ReviewStateKind

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: FSRS-5 默认参数（19 个）。顺序敏感，下标即公式里的 w_i。
DEFAULT_W: tuple[float, ...] = (
    0.40255,    # w0  S0(Again)
    1.18385,    # w1  S0(Hard)
    3.173,      # w2  S0(Good)
    15.69105,   # w3  S0(Easy)
    7.1949,     # w4  D0(Again)
    0.5345,     # w5  D0 的指数系数
    1.4604,     # w6  ΔD 系数
    0.0046,     # w7  难度均值回归强度（目标 D0(4)）
    1.54575,    # w8  成功复习 S 增长的基准
    0.1192,     # w9  S 的幂衰减（S 越大增长越慢）
    1.01925,    # w10 可回忆度对 S 增长的放大（间隔效应）
    1.9395,     # w11 遗忘后 S 的基准
    0.11,       # w12 难度对遗忘后 S 的影响
    0.29605,    # w13 遗忘前 S 的幂（(S+1)^w13 - 1）
    2.2698,     # w14 遗忘时可回忆度的影响
    0.2315,     # w15 Hard 惩罚（rating=2 时乘上）
    2.9898,     # w16 Easy 奖励（rating=4 时乘上）
    0.51655,    # w17 同日复习的 S 增长
    0.6621,     # w18 同日复习的偏移
)

#: 遗忘曲线的形状。FSRS-4.5 起固定为这两个值，
#: 使 R(t=S, S) == 0.9（即 S 的定义）成立。
DECAY = -0.5
FACTOR = 19.0 / 81.0

#: 评分档位（FSRS 的 G）
RATING_AGAIN = 1
RATING_HARD = 2
RATING_GOOD = 3
RATING_EASY = 4
RATINGS = (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY)

#: 默认目标保持率。Anki 默认同为 0.9 —— 它同时是 S 的定义点，
#: 因此 R_req=0.9 时解出的间隔恰好等于 S（便于人工核对）。
DEFAULT_REQUEST_RETENTION = 0.9

#: 间隔上限（天）。10 年。
#:
#: 这不是 FSRS 的规定（Anki 默认 36500 天），而是**产品判断**：
#: 本项目的间隔上限还会影响到期队列与提醒，而"22 年后复习这张卡"
#: 对用户没有任何可操作意义。取 10 年已经远超任何真实使用周期，
#: 只作为数值护栏存在（防止 S 异常放大后写出荒谬的 next_review_at）。
MAX_INTERVAL_DAYS = 3650

#: S 的下限。0 会让 `S ** -w9` 变成 inf，进而污染整条状态。
MIN_STABILITY = 0.01

#: 学习步（天）。见模块末尾 `_next_state_and_interval` 的说明。
LEARNING_STEP_DAYS = 1

#: 间隔抖动（fuzz）比例与生效下限 —— 阶段 3.7
#:
#: ## 抖动解决的是什么问题
#:
#: 同一次导入产生的卡片**初始状态完全相同**（同一批 `S0(rating)`、同一批
#: `D0(rating)`），于是它们此后永远在同一天到期。一篇笔记 20 张卡，
#: 用户就会在"0 张"和"20 张"之间反复横跳 —— 这就是复习雪崩。
#: 抖动把同批卡片摊开到几天里，而**不改变任何一张卡的平均间隔**。
#:
#: ## 为什么低于 3 天不抖
#:
#: 本项目最小调度粒度是 1 天，所以 3 天间隔上最小可能的抖动也是 ±1 天
#: （±33%）。在这个量级上抖动不再是"摊开负载"，而是**改写学习节奏**：
#: 一张 3 天的卡变成 4 天，与"答对后涨到 4 天"在界面上完全无法区分。
#: 短间隔本来也不会雪崩 —— 它们全都该到期。
#:
#: ## 为什么抖动放在调度层而不是 `schedule()` 里
#:
#: FSRS 的公开公式不包含抖动（Anki 也是在 FSRS 之外施加的）。把它留在
#: `schedule()` 里会让"公式对拍"测试失去意义：同输入不再同输出，
#: 而公式正确性恰恰是这个模块最需要被证明的东西。
#: 因此 `schedule()` 保持确定，抖动由 `scheduler_service` 施加 ——
#: 那里本来就是"把间隔变成到期日"的策略层，且 SM-2 回退路径同样需要它。
FUZZ_RATIO = 0.05
FUZZ_MIN_INTERVAL_DAYS = 3


# ---------------------------------------------------------------------------
# 基础公式（纯函数，可逐条对照公开公式）
# ---------------------------------------------------------------------------

def clamp(value: float, low: float, high: float) -> float:
    """把 value 夹到 [low, high]；NaN 归到 low

    NaN 必须显式处理：`max(low, min(high, nan))` 在 Python 里返回 nan
    （比较全部为 False），然后 nan 会一路穿过乘加写进数据库。
    """
    if math.isnan(value):
        return low
    return max(low, min(high, value))


def safe_stability(stability: Optional[float]) -> float:
    """S 的数值护栏：NaN/None → 下限，其余夹到 ≥ MIN_STABILITY

    ## 为什么不能只写 `max(stability, MIN_STABILITY)`

    Python 的 `max(nan, 0.01)` 返回 **nan**：`max` 先假设第一个参数最大，
    之后每个候选靠 `>` 比较决定是否替换，而 `0.01 > nan` 恒为 False。
    于是 NaN 会原样穿过去，`S ** -w9` 再把它扩散到整条状态，
    最后写进数据库变成一个谁都解释不了的 `next_review_at`。
    本轮实测正是靠性质测试（`test_guard_rails_on_degenerate_input`）抓到。
    """
    if stability is None:
        return MIN_STABILITY
    return clamp(float(stability), MIN_STABILITY, float("inf"))


def retrievability(elapsed_days: float, stability: float) -> float:
    """R(t, S)：距上次复习 t 天后的可回忆概率

    R(t,S) = (1 + FACTOR * t/S) ** DECAY，且 R(S,S) == 0.9。
    """
    stability = safe_stability(stability)
    t = max(0.0, elapsed_days)
    return (1.0 + FACTOR * t / stability) ** DECAY


def interval_from_stability(
    stability: float, request_retention: float = DEFAULT_REQUEST_RETENTION,
) -> float:
    """I(r, S)：要达到目标保持率 r，间隔应为多少天

    由 R(t,S)=r 反解 t：
        I = S / FACTOR * (r ** (1/DECAY) - 1)
    r=0.9 时 I == S（这正是 S 的定义，可当作实现是否写错的第一个自检）。
    """
    stability = safe_stability(stability)
    r = clamp(request_retention, 0.1, 0.99)
    return stability / FACTOR * (r ** (1.0 / DECAY) - 1.0)


def initial_stability(rating: int) -> float:
    """S0(G) = w[G-1]"""
    return DEFAULT_W[rating - 1]


def initial_difficulty(rating: int) -> float:
    """D0(G) = w4 - exp(w5 * (G-1)) + 1，夹到 [1, 10]

    G=1（Again）时 exp(0)=1，D0=w4 —— 即 w4 的语义就是"首次就忘记的难度"。
    """
    return clamp(DEFAULT_W[4] - math.exp(DEFAULT_W[5] * (rating - 1)) + 1.0, 1.0, 10.0)


def next_difficulty(difficulty: float, rating: int) -> float:
    """难度更新（含线性阻尼与均值回归）

        ΔD(G) = -w6 * (G - 3)
        D'    = D + ΔD * (10 - D) / 9      ← 线性阻尼：越接近 10 越难再变难
        D''   = w7 * D0(4) + (1 - w7) * D'  ← 均值回归，防"难度雪崩"

    均值回归的目标在 FSRS-5 里是 **D0(4)**（早期版本是 D0(3)）。
    """
    delta = -DEFAULT_W[6] * (rating - 3)
    damped = difficulty + delta * (10.0 - difficulty) / 9.0
    reverted = DEFAULT_W[7] * initial_difficulty(RATING_EASY) + (1.0 - DEFAULT_W[7]) * damped
    return clamp(reverted, 1.0, 10.0)


def stability_after_recall(
    difficulty: float, stability: float, r: float, rating: int,
) -> float:
    """答对（Hard/Good/Easy）后的新 S

        S' = S * (exp(w8) * (11-D) * S^(-w9) * (exp(w10*(1-R)) - 1)
                  * (w15 if G=2) * (w16 if G=4) + 1)

    三个方向都是记忆科学的既有结论，也是 SM-2 完全没有的部分：
      1. D 越大（越难）增长越少；
      2. S 越大增长越少（已经记牢了，再巩固的边际收益递减）；
      3. R 越小（拖得越久才复习）增长越多 —— **间隔效应**。
    """
    stability = safe_stability(stability)
    difficulty = clamp(difficulty, 1.0, 10.0)
    hard_penalty = DEFAULT_W[15] if rating == RATING_HARD else 1.0
    easy_bonus = DEFAULT_W[16] if rating == RATING_EASY else 1.0
    gain = (
        math.exp(DEFAULT_W[8])
        * (11.0 - difficulty)
        * stability ** (-DEFAULT_W[9])
        * (math.exp(DEFAULT_W[10] * (1.0 - r)) - 1.0)
        * hard_penalty
        * easy_bonus
    )
    return safe_stability(stability * (gain + 1.0))


def stability_after_forget(difficulty: float, stability: float, r: float) -> float:
    """答错（Again）后的新 S

        S' = w11 * D^(-w12) * ((S+1)^w13 - 1) * exp(w14 * (1-R))

    `(S+1)^w13 - 1` 而不是 `S^w13`：保证 S=0 时结果为 0 而不是发散，
    同时让"忘掉一张很牢的卡"留下的残余强度高于"忘掉一张新卡"。
    """
    stability = safe_stability(stability)
    difficulty = clamp(difficulty, 1.0, 10.0)
    return safe_stability(
        DEFAULT_W[11]
        * difficulty ** (-DEFAULT_W[12])
        * ((stability + 1.0) ** DEFAULT_W[13] - 1.0)
        * math.exp(DEFAULT_W[14] * (1.0 - r))
    )


def stability_short_term(stability: float, rating: int) -> float:
    """同一天内再次复习后的新 S：S' = S * exp(w17 * (G - 3 + w18))

    ## 为什么必须有这个分支

    同日复习时 t=0 → R=1 → `exp(w10*(1-R)) - 1 = 0`，于是成功复习公式
    给出的 S' 恰好等于 S：**同一天内怎么答都不改变记忆状态**。
    这对"当天没想起来、当天再看一遍"是完全错误的反馈，
    所以 FSRS-5 单独给了这条短时公式。

    注意它**不区分答对答错**，只用 rating 的差值调整增长幅度
    （rating=1 时指数为负 → S 下降）。
    """
    stability = safe_stability(stability)
    return safe_stability(
        stability * math.exp(DEFAULT_W[17] * (rating - 3 + DEFAULT_W[18]))
    )


# ---------------------------------------------------------------------------
# 调度结果
# ---------------------------------------------------------------------------

@dataclass
class FSRSResult:
    """一次复习后的完整调度结果

    Attributes:
        interval_days: 距下次复习的天数（已按 max_interval_days 夹紧、取整、≥1）
        stability: 新的记忆强度 S
        difficulty: 新的难度 D
        retrievability: **复习前**的可回忆概率 R（校准曲线的预测值）
        rating: 本次评分档位 1-4
        state: 复习后的学习阶段
        next_review_at: 下次到期时间
    """
    interval_days: int
    stability: float
    difficulty: float
    retrievability: float
    rating: int
    state: ReviewStateKind
    next_review_at: datetime


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

def _next_state_and_interval(
    state: ReviewStateKind, rating: int, stability: float,
    request_retention: float, max_interval_days: int = MAX_INTERVAL_DAYS,
) -> tuple[ReviewStateKind, int]:
    """由当前学习阶段与评分决定（新阶段, 间隔天数）

    ⚠️ 这一段是**本项目的策略**，不是 FSRS 的公式。FSRS 规定了 S/D/R 怎么
    更新，但不规定"新卡第一次答对之后隔多久"——那是调度器的事。

    规则（三档，理由逐条）：

    1. **新卡答对（Hard/Good）→ 学习阶段，1 天**。
       不直接用 FSRS 解出的间隔（Good 时约 3 天），是为了与 SM-2 时期
       "首次答对隔 1 天"保持一致的用户体验：新学的东西当天记住、
       第二天再确认一次，比隔三天更符合直觉，也让"学完立刻复习"这条
       最常用的路径不因换算法而突变。
    2. **学习/重学阶段答对（≥Hard）→ 进入长期复习**，间隔由 FSRS 解出。
       这次"隔了 1 天还答对"正是 FSRS 需要的第一个真实间隔信号。
    3. **任何阶段答错（Again）→ 重学，1 天**。
       答错必须把间隔打回最短，这是 SRS 的底线；FSRS 已经通过
       `stability_after_forget` 把"残余强度"记在 S 里，
       所以打回 1 天不会丢失"这张卡曾经很牢"的信息 ——
       下次答对时 S 会从那个残余值继续往上走，而不是从零开始。
    4. **只有"新卡 + Easy"直接进长期复习**：用户主动说"太简单了"，
       还塞一个 1 天的学习步就是无视他的判断。

    Easy 在长期复习阶段的额外奖励已经由 `w16` 体现在 S 上，
    这里不再另设"简单加成倍数"—— 两处同时加成会让 Easy 的间隔失控。
    """
    if state == ReviewStateKind.new:
        if rating == RATING_EASY:
            return (
                ReviewStateKind.review,
                _interval_days(
                    initial_stability(RATING_EASY), request_retention, max_interval_days,
                ),
            )
        return ReviewStateKind.learning, LEARNING_STEP_DAYS

    if state in (ReviewStateKind.learning, ReviewStateKind.relearning):
        if rating == RATING_AGAIN:
            return state, LEARNING_STEP_DAYS
        return ReviewStateKind.review, _interval_days(
            stability, request_retention, max_interval_days,
        )

    # review
    if rating == RATING_AGAIN:
        return ReviewStateKind.relearning, LEARNING_STEP_DAYS
    return ReviewStateKind.review, _interval_days(
        stability, request_retention, max_interval_days,
    )


def _interval_days(
    stability: float, request_retention: float, max_interval_days: int = MAX_INTERVAL_DAYS,
) -> int:
    """S → 间隔天数（取整、下限 1 天、上限 max_interval_days）

    下限 1 而不是 0：本项目的最小调度粒度是"天"，0 天会让卡片立刻再次到期
    而陷入死循环（到期 → 复习 → 仍到期）。
    """
    raw = interval_from_stability(stability, request_retention)
    if math.isnan(raw):
        return 1
    if math.isinf(raw):
        # S 溢出成无穷（数值退化）时取上限而不是 1 天：语义上"无限牢"的卡
        # 该被排到最远，而不是变成明天就到期 —— 后者会让它进入死循环。
        return max_interval_days
    return int(clamp(round(raw), 1, max_interval_days))


def schedule(
    *,
    rating: int,
    state: ReviewStateKind,
    stability: Optional[float],
    difficulty: Optional[float],
    elapsed_days: float,
    interval_days: int = 1,
    easiness_factor: float = 2.5,
    now: Optional[datetime] = None,
    request_retention: float = DEFAULT_REQUEST_RETENTION,
    max_interval_days: int = MAX_INTERVAL_DAYS,
) -> FSRSResult:
    """跑一次 FSRS 调度（纯函数）

    Args:
        rating: 本次评分 1-4（Again/Hard/Good/Easy）
        state: 复习前所处阶段（new/learning/review/relearning）
        stability: 复习前的 S；**None 表示该行从未被 FSRS 调度过**
        difficulty: 复习前的 D；None 同义
        elapsed_days: 距上次复习的天数（同日复习传 0）
        interval_days / easiness_factor: 旧 SM-2 字段，仅在
            `stability is None` 时用于**接管换算**（见 `adopt_legacy_state`）
        now: 本次复习时间；默认取当前 UTC 时间
        request_retention: 目标保持率

    Returns:
        FSRSResult

    ## 为什么 stability=None 要"接管"而不是"当作新卡"

    升级到 FSRS 时，库里的卡片已经有 SM-2 攒下的 `interval` 和 `EF`
    （真库 2241 行 review_states、1183 张卡）。把它们的 S 当作 0 会让
    **所有历史进度归零** —— 这正是 overhaul-plan 症状 D-3 的形态，
    只是换成了换算法来触发。因此按 FSRS 对 S 的定义（"R 降到 90% 的
    天数"）把现有间隔直接当作 S，是用**同一套语义**完成交接：

        I(0.9, S) == S   ⟹   S := 当前 interval_days

    难度则由 EF 反解（`difficulty_from_easiness`），两者都是可逆的一一映射，
    所以这次接管不丢信息，也不需要一次性数据迁移。
    """
    rating = int(clamp(rating, RATING_AGAIN, RATING_EASY))
    now = now or datetime.now(timezone.utc)
    elapsed = max(0.0, float(elapsed_days or 0.0))

    s, d = stability, difficulty
    if s is None or d is None:
        s, d = adopt_legacy_state(
            state=state, interval_days=interval_days,
            easiness_factor=easiness_factor, stability=s, difficulty=d,
        )

    # R 必须用**复习前**的 S 算：它是"复习发生之前，模型认为此刻能想起来的
    # 概率"。用更新后的 S 会得到一个事后才成立的数字，
    # 校准曲线（预测 vs 实际）就失去意义了。
    r = retrievability(elapsed, s)

    if state == ReviewStateKind.new:
        new_s = initial_stability(rating)
        new_d = initial_difficulty(rating)
    else:
        new_d = next_difficulty(d, rating)
        if elapsed < 1.0:
            # 同日复习：见 stability_short_term 的说明
            new_s = stability_short_term(s, rating)
        elif rating == RATING_AGAIN:
            new_s = stability_after_forget(d, s, r)
        else:
            new_s = stability_after_recall(d, s, r, rating)

    new_state, interval = _next_state_and_interval(
        state, rating, new_s, request_retention, max_interval_days,
    )
    interval = int(clamp(interval, 1, max_interval_days))

    return FSRSResult(
        interval_days=interval,
        stability=round(new_s, 6),
        difficulty=round(new_d, 6),
        retrievability=round(r, 6),
        rating=rating,
        state=new_state,
        next_review_at=now + timedelta(days=interval),
    )


def fuzz_interval(
    interval_days: int,
    *,
    rand: Optional[float] = None,
    ratio: float = FUZZ_RATIO,
    min_interval_days: int = FUZZ_MIN_INTERVAL_DAYS,
    max_interval_days: int = MAX_INTERVAL_DAYS,
) -> int:
    """给间隔加一个随机抖动，返回抖动后的天数（纯函数）

    Args:
        interval_days: 原始间隔
        rand: [0, 1) 的随机数；None 时取 `random.random()`。
            **显式传入是为了可测**：抖动的正确性（范围、边界、分布）
            必须能被断言，而"调 1000 次看统计量"既慢又不稳定。
        ratio: 抖动比例（相对间隔）；0 表示关闭
        min_interval_days: 低于这个间隔不抖动（理由见 `FUZZ_RATIO` 的说明）
        max_interval_days: 结果上限

    Returns:
        抖动后的间隔，∈ [1, max_interval_days]

    ## 抖动幅度 = max(1, round(interval * ratio))

    两个部分都不能少：
    - `interval * ratio` 让长间隔摊得更开（100 天 ±5 天，才可能把
      同批卡片摊到不同周）；
    - `max(1, ...)` 保证短间隔也真的动起来 —— 只按比例算的话，
      3 天的 5% 是 0.15，四舍五入成 0，抖动等于没做。
      而"同批卡片全挤在 3 天后"恰恰是最常见的雪崩形态（新导入的笔记）。
    """
    interval = max(1, int(interval_days))
    if ratio <= 0 or interval < min_interval_days:
        return min(interval, max_interval_days)
    delta = max(1, round(interval * ratio))
    # 把 [0,1) 均匀映射到整数偏移 [-delta, +delta]：
    # u * (2delta + 1) 取整后落在 0..2delta，减去 delta 即得。
    u = random.random() if rand is None else float(rand)
    if math.isnan(u):
        u = 0.0
    u = clamp(u, 0.0, 0.999999)
    offset = int(u * (2 * delta + 1)) - delta
    return int(clamp(interval + offset, 1, max_interval_days))


def adopt_legacy_state(
    *,
    state: ReviewStateKind,
    interval_days: int,
    easiness_factor: float,
    stability: Optional[float] = None,
    difficulty: Optional[float] = None,
) -> tuple[float, float]:
    """把 SM-2 的 (interval, EF) 换算成 FSRS 的 (S, D)

    - 已经是 FSRS 状态（两个值都有）时原样返回；
    - 两个值都缺时按新卡处理（S0、D0 需要 rating，这里给 Good 的初值：
      调用方在 `state == new` 分支里会立刻用 rating 重新初始化，
      所以这里的值只影响"非 new 但两个值都缺"这种异常行）。

    ## S 的换算：S := interval_days

    这是 FSRS 对 S 的定义直接给出的（`interval_from_stability(0.9, S) == S`），
    不是近似。SM-2 的 `interval` 语义是"下次复习间隔"，而 SM-2 隐含的
    目标正是"到那时还记得的概率约 90%"（EF 的调整规则就是围绕"及格"转的），
    所以两者指的是同一件事。

    ## D 的换算：EF 的反函数

    见 `difficulty_from_easiness`。

    ⚠️ 两个值都缺且**不是**新卡时（异常行，例如迁移脚本漏了一行），
    S 取当前间隔、D 取 EF 反解，两者都来自这一行自己的旧字段。
    新卡则用 Good 档的初值 —— 一张从未复习过的卡上的 EF 必然是默认值
    （2.5），不含任何关于这张卡难度的信息，反解出来只是中性值，
    不如用 FSRS 自己对"刚学会的卡"的先验。
    """
    if stability is not None and difficulty is not None:
        return safe_stability(stability), clamp(difficulty, 1.0, 10.0)

    if stability is None:
        if state == ReviewStateKind.new:
            stability = initial_stability(RATING_GOOD)
        else:
            stability = float(interval_days or 1)
    if difficulty is None:
        difficulty = (
            initial_difficulty(RATING_GOOD) if state == ReviewStateKind.new
            else difficulty_from_easiness(easiness_factor)
        )
    return safe_stability(stability), clamp(difficulty, 1.0, 10.0)


# ---------------------------------------------------------------------------
# 与 SM-2 字段的桥接
# ---------------------------------------------------------------------------

#: SM-2 的 EF 取值范围（`sm2_service.calculate_sm2` 里 EF 下限就是 1.3）
EF_MIN = 1.3
EF_MAX = 2.8
#: EF 与 D 的桥接斜率：D 每降低 1（更容易），EF 上升 0.15
EF_PER_DIFFICULTY = 0.15
#: 桥接的中性点：FSRS 难度区间 [1,10] 的中点是 5.5，但 D0(Good)=5.28、
#: SM-2 的默认 EF=2.5 对应的是"中等难度"。取 D=5 ↔ EF=2.5，
#: 两个都是各自的默认值，映射在原地不动。
EF_NEUTRAL_DIFFICULTY = 5.0
EF_NEUTRAL = 2.5


def easiness_from_difficulty(difficulty: float) -> float:
    """FSRS 难度 D → SM-2 的 EF（单调递减，夹到 [1.3, 2.8]）

    ## 为什么需要这个桥，而不是把 EF 冻在最后一次 SM-2 的值

    换算法后 `quiz_items.easiness_factor` / `review_states.easiness_factor`
    仍然被读取（掌握度、卡片复习列表、以及回滚后的 SM-2 路径），
    而它们**不再被 SM-2 更新**。留着一个不再更新的字段有两种坏结果：
    一是它看起来仍是最新值却其实已经过期（无法与"正确"区分），
    二是回滚到 SM-2 后带着一个几年前的 EF 继续跑。

    桥接保持了这一列的**语义**（越大越容易、范围 [1.3, 2.8]）而不是让它
    变成"难度"的同义词 —— 后者会让列名说谎。代价是 D↔EF 不是 FSRS 的
    规定部分，属于本项目为兼容旧字段做的适配，因此这里显式标注。

    单调性：D 越大（越难）→ EF 越小，与两边的直觉一致。

    ⚠️ **这个映射不是满射**：D ∈ [1, 10] 映射到 EF 后会被夹到
    [1.3, 2.8]，其中 D < 3 的部分全部落到 EF=2.8。反向换算因此是
    **有损**的（D=3.0 与 D=1.0 都得到 EF=2.8，回推只能得到 3.0）。
    这是可接受的：真库里 EF 本来就被 SM-2 夹在这一区间内，
    所以"取回来"时不会比原来更糟；而 D<3 只可能由 FSRS 自己产生，
    那时 `review_states.stability/difficulty` 已经非 NULL，走的是直通分支，
    根本不会经过这个桥。
    """
    return clamp(
        EF_NEUTRAL + (EF_NEUTRAL_DIFFICULTY - difficulty) * EF_PER_DIFFICULTY,
        EF_MIN, EF_MAX,
    )


def difficulty_from_easiness(easiness_factor: Optional[float]) -> float:
    """SM-2 的 EF → FSRS 难度 D（`easiness_from_difficulty` 的反函数）

    EF 缺失或非正时返回中性难度（D=5），与 EF=2.5 对应。

    可逆范围：EF ∈ [1.3, 2.8] ⟺ D ∈ [3.0, 10.0]。
    更低的 D 在 EF 一侧被夹住，回推只会得到 3.0（见
    `easiness_from_difficulty` 关于"不是满射"的说明）。
    """
    ef = float(easiness_factor or EF_NEUTRAL)
    if ef <= 0:
        ef = EF_NEUTRAL
    return clamp(
        EF_NEUTRAL_DIFFICULTY - (ef - EF_NEUTRAL) / EF_PER_DIFFICULTY,
        1.0, 10.0,
    )


__all__ = [
    "DEFAULT_W",
    "DECAY",
    "FACTOR",
    "DEFAULT_REQUEST_RETENTION",
    "MAX_INTERVAL_DAYS",
    "MIN_STABILITY",
    "LEARNING_STEP_DAYS",
    "FUZZ_RATIO",
    "FUZZ_MIN_INTERVAL_DAYS",
    "RATING_AGAIN",
    "RATING_HARD",
    "RATING_GOOD",
    "RATING_EASY",
    "RATINGS",
    "FSRSResult",
    "clamp",
    "safe_stability",
    "retrievability",
    "interval_from_stability",
    "initial_stability",
    "initial_difficulty",
    "next_difficulty",
    "stability_after_recall",
    "stability_after_forget",
    "stability_short_term",
    "schedule",
    "fuzz_interval",
    "adopt_legacy_state",
    "easiness_from_difficulty",
    "difficulty_from_easiness",
]

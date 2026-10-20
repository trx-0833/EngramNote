/**
 * @file 复习答题页面
 * @description 基于间隔重复算法的复习答题界面，支持选择题、填空题和简答题。
 * 用户逐题作答，提交后即时显示正误判断和解析，SM-2 算法自动更新复习间隔。
 *
 * ## 会话逻辑住在 `hooks/useQuizSession`
 *
 * 本页只保留**这一页独有**的东西：取数（到期题目 + 统计）、每日额度用尽时的
 * 分流、以及完成页/错误页的排版。答题会话的状态机（当前题、前后翻、题号条、
 * 提交锁、自评接线、计时）三页共用一份实现 —— 理由见那个 hook 的文件头。
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getDueQuizzes,
  submitAnswer,
  getReviewStats,
  DueQuiz,
  SubmitAnswerResponse,
  ReviewStats,
} from '../api/client';
// 共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard';
// 与卡片复习页共用的进度条与回车键约定（5.12）
import ReviewProgress from '../components/quiz/ReviewProgress';
// 题号导航条（想法 8）：可前后翻、可点题号跳转
import QuizNavigator, { type QuizNavItem } from '../components/quiz/QuizNavigator';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变
import PageHeader from '../components/PageHeader';
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard';
import { useQuizSession } from '../hooks/useQuizSession';
import { useTranslation } from '../i18n';

/**
 * 读取异常对象上的后端 error_code（阶段 0.11）
 *
 * 用**结构化读字段**而不是 `instanceof ApiError`：本页依赖的 `../api/client`
 * 在测试里被整体 mock（`vi.mock('../api/client', ...)`），从被 mock 的模块
 * import 进来的类会是 undefined，那时 `e instanceof undefined` 直接抛
 * TypeError。字段读取对模块替换免疫。
 */
function errorCodeOf(e: unknown): string | null {
  if (typeof e !== 'object' || e === null) return null;
  const code = (e as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : null;
}

export default function Review() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  /**
   * 提交作答
   *
   * 这里**只做转发**：错误分流交给下面的 `onSubmitError`。
   * 为什么不在这一层 `catch`：hook 需要看到异常才能弹 toast，
   * 而页面需要看到 `error_code` 才能判断"是限额还是真错误" ——
   * 两边都要看，所以用回调而不是在这里把异常吃掉。
   */
  const submit = useCallback(
    (
      quiz: DueQuiz,
      userAnswer: string,
      timeSpentMs: number,
      selfRating: number | undefined,
      semanticGrading?: boolean,
    ): Promise<SubmitAnswerResponse> =>
      submitAnswer(quiz.id, userAnswer, timeSpentMs, selfRating, semanticGrading),
    [],
  );

  /** 每次提交后刷新统计（今日已完成数变了） */
  const refreshStats = useCallback(() => {
    void getReviewStats()
      .then(setStats)
      .catch(() => {
        /* 统计刷新失败不影响答题 */
      });
  }, []);

  /**
   * 提交失败：按后端**错误码**分流，不匹配中文文案（阶段 0.11 / F-19）
   *
   * 后端 `POST /api/review/submit` 在额度用尽时返回
   * `error_code="DAILY_REVIEW_LIMIT_REACHED"`（文案中的数字会变，码不会）。
   * 额度用尽**不是错误**，是"今天做完了"—— 切到完成页，并刷新统计，
   * 否则完成页上的"今日已完成"显示的是旧值。
   *
   * ⚠️ 用 ref 拿 `setCompleted` 而不是直接从 `session` 解构：那会形成
   * **循环引用**（`onSubmitError` → `session` → `onSubmitError`），
   * `tsc` 会报 TS7022/TS2448。ref 在首次渲染后就填好，而回调只在
   * 用户交互时才跑，所以不存在"还没填上"的窗口。
   */
  const setCompletedRef = useRef<((v: boolean) => void) | null>(null);
  const onSubmitError = useCallback(
    (e: unknown) => {
      if (errorCodeOf(e) === 'DAILY_REVIEW_LIMIT_REACHED') {
        refreshStats();
        setCompletedRef.current?.(true);
      }
    },
    [refreshStats],
  );

  const session = useQuizSession<DueQuiz>({
    // 进度持久化：刷新后回到离开时的那一题（想法 8 阶段 3）
    scope: 'review',
    submit,
    // 每次提交成功都刷新统计：今日已完成数变了
    onSubmitted: refreshStats,
    // 自评也会推进调度并写入今日完成数
    onRated: refreshStats,
    onSubmitError,
  });
  const {
    quizzes,
    currentIndex,
    current,
    sessionCorrect,
    sessionTotal,
    completed,
    semanticGrading,
    submitting,
    isRated,
    ratingSubmitting,
    skipRating,
    replaceAll,
    clear,
    goTo,
    handlePrev,
    handleNext,
    handleSubmit,
    handleSelfRate,
    handleEnter,
    setAnswer,
    setSemanticGrading,
    setCompleted,
  } = session;
  // 供 onSubmitError 使用，见那里的说明。
  // ⚠️ 在 effect 里赋值而不是渲染期直接写：渲染期写 ref 被
  // react-hooks/refs 规则判为 error。回调只在用户交互时跑，那时早已填好。
  useEffect(() => {
    setCompletedRef.current = setCompleted;
  }, [setCompleted]);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [dueData, statsData] = await Promise.all([getDueQuizzes(50), getReviewStats()]);
      setStats(statsData);
      if (dueData.items.length === 0) {
        clear();
        setCompleted(true);
      } else {
        replaceAll(dueData.items);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('review.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t, clear, replaceAll, setCompleted]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadData();
    // `t` 进依赖：语言切换后重新加载时，错误文案要是新语言的
  }, [t, loadData]);

  /** 题号条的数据：序号 + 是否已答 + 是否答对 */
  const navItems: QuizNavItem[] = quizzes.map((q, i) => ({
    index: i,
    submitted: q.submitted,
    isCorrect: q.result?.is_correct,
  }));

  /**
   * 今日额度是否已用尽（想法 8 阶段 4）
   *
   * 判据与后端**同一口径**：额度按"已结算"的题数算（`review_service.py`
   * 的 `_count_settled_today` 排除了 `grading_method='ungraded'` 的占位记录），
   * 而 `stats.today_done` 就是这个数（`/review/stats` 直接给的），
   * 所以这里不必自己重算。
   *
   * 为什么要有它：`/review/due` 一次装载 50 道，而每日只能结算 10 道。
   * 没有它的话，用户做到第 11 道点提交才拿到 429，
   * 而页面上还剩 40 道**看起来完全能点**的题 —— 一道道撞墙。
   */
  const dailyLimit = stats?.daily_limit ?? 10;
  const quotaExhausted = (stats?.today_done ?? 0) >= dailyLimit;

  // 焦点刻意不收回容器：这一页的焦点应当留在填空/简答输入框里（见 hook 的说明）
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter: handleEnter });

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>{t('review.loading')}</div>
    );
  }

  // 完成页面
  if (completed) {
    // 每日限额从后端 /review/stats 读取（单一来源），见 docs/decisions.md#F-12。
    // `dailyLimit` 已在组件级算过，这里只需要今日已完成数。
    const todayDone = stats?.today_done ?? 0;
    return (
      <div className="page-enter" style={{ margin: '0 auto' }}>
        <h2>{t('review.done')}</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>{t('review.sessionStats')}</h3>
          <p>{t('review.answerCount', { count: sessionTotal })}</p>
          <p>{t('review.correctCount', { count: sessionCorrect })}</p>
          <p>
            {t('review.accuracy', {
              percent: sessionTotal > 0 ? Math.round((sessionCorrect / sessionTotal) * 100) : 0,
            })}
          </p>
        </div>

        {stats && (
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <h3>{t('review.overallStats')}</h3>
            <p>{t('review.todayProgress', { done: todayDone, limit: dailyLimit })}</p>
            <p>{t('review.todayAccuracy', { percent: stats.today_accuracy })}</p>
            <p>{t('review.dueCount', { count: stats.due_count })}</p>
            <p>{t('review.totalReviews', { count: stats.total_reviews })}</p>
            <p>{t('review.totalAccuracy', { percent: stats.total_accuracy })}</p>
            <p>{t('review.totalQuizzes', { count: stats.total_quizzes })}</p>
          </div>
        )}

        {quotaExhausted ? (
          <div
            className="card card-accent-warning"
            style={{ textAlign: 'center', marginBottom: 'var(--space-md)' }}
          >
            {/* 原值 `#ff9800`（白底 2.16:1）与 F-36 的「中」徽章同色值，
                换成 `--color-warning` 的取值（#936408，白底 5.16:1）。 */}
            <p style={{ fontWeight: 600, color: 'var(--color-warning)' }}>
              {t('review.dailyLimitReached', { limit: dailyLimit })}
            </p>
            <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
              {t('review.comeBackTomorrow')}
            </p>
          </div>
        ) : (
          <button className="btn btn-primary" onClick={loadData}>
            {t('review.continueReview')}
          </button>
        )}
      </div>
    );
  }

  if (error && quizzes.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
        <p style={{ color: 'var(--color-error)' }}>{error}</p>
        <button className="btn btn-primary" onClick={loadData}>
          {t('review.retry')}
        </button>
      </div>
    );
  }

  if (!current) return null;

  const quiz = current.quiz;

  return (
    <div
      className="page-enter"
      ref={containerRef}
      onKeyDown={handleKeyDown}
      style={{ margin: '0 auto' }}
    >
      {/* 页面标题（a11y-audit **F-32**：这一页此前 `<h1>`~`<h6>` 数量为 **0**，
          axe 判 page-has-heading-one）。
          ⚠️ 补 h1 时要一起看**层级**：这一页在答题态下除此之外**没有任何标题**
          （`QuizAnswerCard` 里全是 p/span/button），所以补上 h1 就是完整的
          大纲，不存在上一轮踩到的 `h1 → h3` 跳级（§8.4 第 1 条）。
          为什么写在页面里而不是用 `ReviewProgress` 的 `title`：那个组件的文件头
          写明了两种排版的分工 ——"**答题复习侧：页面标题在别处**（或没有），
          进度条与两侧计数排在同一行"。也就是说标题本来就该长在这一页上，
          只是一直没写；传 `title` 会把进度条挤到第二行，那是改版式，
          不是修可访问性。
          标题用词取自项目里已有的说法：`CardReview.tsx` 里指到这一页的链接
          文案就是「答题复习」（`ReviewProgress` 的文件头也这么称呼它）。
          批次 C1：量尺交给 `<PageHeader>`（字号本就 1.5rem，观感不变），
          上面那条"标题长在页面上、不借 `ReviewProgress` 的 `title`"的结论不变。 */}
      <PageHeader title={t('review.title')} />

      {/* 进度条（与卡片复习页共用） */}
      <ReviewProgress
        index={currentIndex}
        total={quizzes.length}
        done={current.submitted}
        label={
          <>
            {currentIndex + 1} / {quizzes.length}
          </>
        }
        trailing={
          <>
            {t('review.headerProgress', {
              correct: sessionCorrect,
              total: sessionTotal,
              done: stats?.today_done ?? 0,
            })}
            {stats?.daily_limit ?? 10}
          </>
        }
      />

      {/* 题号导航条（想法 8）：可点题号跳转，四态可视 */}
      <QuizNavigator items={navItems} currentIndex={currentIndex} onJump={goTo} />

      {/* 额度用尽：明确告知，而不是让用户一道道去撞 429。
          已提交的题不受影响 —— 回看它们正是本功能的另一半诉求。 */}
      {quotaExhausted && !current.submitted && (
        <div
          className="card card-accent-warning"
          style={{ marginBottom: 'var(--space-md)', textAlign: 'center' }}
        >
          <p style={{ fontWeight: 600, color: 'var(--color-warning)' }}>
            {t('quizNav.quotaExhaustedBanner', {
              done: stats?.today_done ?? 0,
              limit: dailyLimit,
            })}
          </p>
        </div>
      )}
      {/* 题目卡片（共享 QuizAnswerCard；提交竞态锁由 useQuizSession 的 submittingRef 承担，见 docs/decisions.md#F-23） */}
      <QuizAnswerCard
        quiz={quiz}
        userAnswer={current.userAnswer}
        submitted={current.submitted}
        result={current.result}
        submitting={submitting}
        showSm2Info
        showReviewMeta
        isLast={currentIndex >= quizzes.length - 1}
        selfRated={isRated(quiz.id)}
        selfRatingSubmitting={ratingSubmitting}
        semanticGrading={semanticGrading}
        onToggleSemanticGrading={setSemanticGrading}
        onSelectAnswer={setAnswer}
        onSubmit={handleSubmit}
        onSelfRate={handleSelfRate}
        onSkipSelfRate={() => skipRating(quiz.id)}
        onNext={handleNext}
        onPrev={handlePrev}
        hasPrev={currentIndex > 0}
        quotaExhausted={quotaExhausted}
      />
    </div>
  );
}

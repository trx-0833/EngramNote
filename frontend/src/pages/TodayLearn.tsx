/**
 * @file 今日学习页面
 * @description 整合待复习题目、今日学习报告和薄弱点的入口页面。
 * 用户可以在此查看今日学习任务、开始答题、查看进度。
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  getDueQuizzes,
  submitAnswer,
  getReviewStats,
  getDailyReport,
  getWeakPoints,
  getDailyPlan,
  type DueQuiz,
  type SubmitAnswerResponse,
  type ReviewStats,
  type DailyReport,
  type WeakPoint,
  type DailyPlanResponse,
  type RecommendedTask,
} from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
// 共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard';
// 与卡片复习页共用的进度条与回车键约定（5.12）
import ReviewProgress from '../components/quiz/ReviewProgress';
// 题号导航条（想法 8）：可前后翻、可点题号跳转
import QuizNavigator, { type QuizNavItem } from '../components/quiz/QuizNavigator';
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard';
import { useQuizSession } from '../hooks/useQuizSession';
// 统计卡片（overhaul-plan 5.6 第三批）：与 Dashboard 共用的 `.stat-card*` 已抽成
// 组件、样式进模块 —— 本页不再需要 `dashboard.css` 里的全局类名
import StatCard from '../components/StatCard';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变
import PageHeader from '../components/PageHeader';
import { useLabels, weakPointBadge } from '../utils/labels';
import { useTranslation, type MessageKey } from '../i18n';

/**
 * 读取异常对象上的后端 error_code（阶段 0.11）
 *
 * 用**结构化读字段**而不是 `instanceof ApiError`：本页依赖的 `../api/client`
 * 在测试里被整体 mock，从被 mock 的模块 import 进来的类会是 undefined，
 * 那时 `e instanceof undefined` 直接抛 TypeError。字段读取对模块替换免疫。
 */
function errorCodeOf(e: unknown): string | null {
  if (typeof e !== 'object' || e === null) return null;
  const code = (e as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : null;
}

export default function TodayLearn() {
  const { t } = useTranslation();
  const { cardTypeLabels } = useLabels();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [dailyReport, setDailyReport] = useState<DailyReport | null>(null);
  const [weakPoints, setWeakPoints] = useState<WeakPoint[]>([]);
  /** 今日每日推荐任务 */
  const [dailyPlan, setDailyPlan] = useState<DailyPlanResponse | null>(null);

  /**
   * 提交作答：本页用普通复习入口
   *
   * 只做转发；错误分流交给下面的 `onSubmitError`（理由见 `Review.tsx` 同名处）。
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

  /** 刷新复习统计（今日已完成数变了） */
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
   * ⚠️ 刻意**不** import `client.ts` 的 `ApiError` 做 instanceof：本模块的
   * API 客户端在测试里被整体 mock（`vi.mock('../api/client')`），
   * instanceof 拿到的是 undefined，会直接抛 TypeError。结构化读字段
   * 对这种替换是免疫的。
   *
   * 用 ref 拿 `setCompleted` 是为了避免与 `session` 形成循环引用 —— 见
   * `Review.tsx` 同名处的详细说明。
   */
  const setCompletedRef = useRef<((v: boolean) => void) | null>(null);
  const onSubmitError = useCallback(
    (e: unknown) => {
      if (errorCodeOf(e) === 'DAILY_REVIEW_LIMIT_REACHED') {
        refreshStats();
        setCompletedRef.current?.(true);
      } else {
        setError(e instanceof Error ? e.message : t('todayLearn.submitFailed'));
      }
    },
    [refreshStats, t],
  );

  const session = useQuizSession<DueQuiz>({
    submit,
    onSubmitted: refreshStats,
    onRated: refreshStats,
    // 本页把自评失败也写进页面级错误（`Review` 只弹 toast）
    onRateError: (message) => setError(message),
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

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadData() {
    setLoading(true);
    setError('');
    try {
      const [statsData, reportRes, weakRes, planRes] = await Promise.all([
        getReviewStats().catch(() => null),
        getDailyReport().catch(() => null),
        getWeakPoints(5).catch(() => null),
        getDailyPlan().catch(() => null),
      ]);
      if (statsData) setStats(statsData);
      if (reportRes) setDailyReport(reportRes);
      if (weakRes) setWeakPoints(weakRes.items);
      if (planRes) setDailyPlan(planRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('todayLearn.loadFailed'));
    } finally {
      setLoading(false);
    }
  }

  async function startReview() {
    try {
      const dueData = await getDueQuizzes(50);
      if (dueData.items.length === 0) return;
      replaceAll(dueData.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('todayLearn.loadQuestionsFailed'));
    }
  }

  /** 题号条的数据：序号 + 是否已答 + 是否答对 */
  const navItems: QuizNavItem[] = quizzes.map((q, i) => ({
    index: i,
    submitted: q.submitted,
    isCorrect: q.result?.is_correct,
  }));

  // 焦点刻意不收回容器：这一页的焦点应当留在填空/简答输入框里（见 hook 的说明）
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter: handleEnter });

  function formatTime(ms: number): string {
    if (ms < 60000) return t('dashboard.durationSeconds', { count: Math.round(ms / 1000) });
    if (ms < 3600000) return t('dashboard.durationMinutes', { count: Math.round(ms / 60000) });
    return t('dashboard.durationHours', { count: (ms / 3600000).toFixed(1) });
  }

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text={t('todayLearn.loading')} />;

  // --- 错误 ---
  if (error && !stats) return <ErrorDisplay message={error} onRetry={loadData} />;

  // --- 答题完成 ---
  if (completed) {
    // 每日限额从后端 /review/stats 读取（单一来源），见 docs/decisions.md#F-12
    const dailyLimit = stats?.daily_limit ?? 10;
    const todayDone = stats?.today_done ?? 0;
    return (
      <div className="page-enter" style={{ margin: '0 auto' }}>
        <h2 style={{ marginBottom: 'var(--space-lg)' }}>{t('todayLearn.done')}</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>{t('todayLearn.sessionStats')}</h3>
          <p>{t('todayLearn.answerCount', { count: sessionTotal })}</p>
          <p>{t('todayLearn.correctCount', { count: sessionCorrect })}</p>
          <p>
            {t('todayLearn.accuracy', {
              percent: sessionTotal > 0 ? Math.round((sessionCorrect / sessionTotal) * 100) : 0,
            })}
          </p>
        </div>
        {stats && (
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <h3>{t('todayLearn.todayOverall')}</h3>
            <p>{t('todayLearn.todayProgress', { done: todayDone, limit: dailyLimit })}</p>
            <p>{t('todayLearn.todayAccuracy', { percent: stats.today_accuracy })}</p>
            <p>{t('todayLearn.dueCount', { count: stats.due_count })}</p>
          </div>
        )}
        {todayDone < dailyLimit ? (
          <button className="btn btn-primary" onClick={startReview}>
            {t('todayLearn.continueReview')}
          </button>
        ) : (
          // 原值 `#ff9800` 白底只有 2.16:1（0.9rem 文字要求 4.5:1），
          // 与 F-36 的「中」优先级徽章是同一个色值 —— 换成 `--color-warning`
          // 的取值（#936408，白底 5.16:1），不再各处写一份橙。
          <p style={{ color: 'var(--color-warning)', fontWeight: 600 }}>
            {t('todayLearn.dailyLimitReached', { limit: dailyLimit })}
          </p>
        )}
      </div>
    );
  }

  // --- 答题中 ---
  if (quizzes.length > 0 && current) {
    const quiz = current.quiz;

    return (
      <div
        className="page-enter"
        ref={containerRef}
        onKeyDown={handleKeyDown}
        style={{ margin: '0 auto' }}
      >
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
            <>{t('todayLearn.headerProgress', { correct: sessionCorrect, total: sessionTotal })}</>
          }
        />

        {/* 题号导航条（想法 8）：可点题号跳转，四态可视 */}
        <QuizNavigator items={navItems} currentIndex={currentIndex} onJump={goTo} />

        {/* 题目卡片（共享 QuizAnswerCard） */}
        <QuizAnswerCard
          quiz={quiz}
          userAnswer={current.userAnswer}
          submitted={current.submitted}
          result={current.result}
          submitting={submitting}
          showSm2Info={false}
          semanticGrading={semanticGrading}
          onToggleSemanticGrading={setSemanticGrading}
          fillAutoFocus
          isLast={currentIndex >= quizzes.length - 1}
          selfRated={isRated(quiz.id)}
          selfRatingSubmitting={ratingSubmitting}
          onSelectAnswer={setAnswer}
          onSubmit={handleSubmit}
          onSelfRate={handleSelfRate}
          onSkipSelfRate={() => skipRating(quiz.id)}
          onNext={handleNext}
          onPrev={handlePrev}
          hasPrev={currentIndex > 0}
        />
      </div>
    );
  }

  // --- 入口页面 ---
  const dueCount = stats?.due_count ?? 0;
  const todayDone = stats?.today_done ?? 0;

  return (
    <div className="page-enter">
      {/* 页面标题（批次 C1：统一进 <PageHeader>，字号本就 1.5rem） */}
      <PageHeader title={t('todayLearn.title')} />

      {error && <ErrorDisplay message={error} onRetry={loadData} />}

      {/* 每日推荐任务（位于页面顶部，按类别分组展示） */}
      {dailyPlan && dailyPlan.total_count > 0 && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
            {t('todayLearn.dailyPlan')}
          </h2>
          <p
            style={{
              fontSize: '0.85rem',
              color: 'var(--color-text-secondary)',
              marginBottom: 'var(--space-md)',
            }}
          >
            {t('todayLearn.dailyPlanProgress', {
              date: dailyPlan.plan_date,
              done: dailyPlan.completed_count,
              total: dailyPlan.total_count,
            })}
          </p>
          <DailyPlanSection plan={dailyPlan} navigate={navigate} />
        </section>
      )}

      {/* 今日学习报告摘要 */}
      {dailyReport && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>
            {t('todayLearn.todayReport', { date: dailyReport.date })}
          </h2>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
              gap: 'var(--space-md)',
            }}
          >
            <StatCard
              variant="blue"
              value={dailyReport.new_mastered}
              label={t('todayLearn.statNewMastered')}
            />
            <StatCard
              variant="green"
              value={dailyReport.total_reviews}
              label={t('todayLearn.statTotalReviews')}
            />
            <StatCard
              variant="gold"
              value={`${dailyReport.today_accuracy}%`}
              label={t('todayLearn.statAccuracy')}
            />
            <StatCard
              variant="purple"
              value={formatTime(dailyReport.total_review_time_ms)}
              label={t('todayLearn.statReviewTime')}
            />
          </div>
        </section>
      )}

      {/* 待复习任务。
          ⚠️ 这里**没有** role="button"、容器也不再可聚焦 —— 与仪表盘那张卡片
          （F-09，`Dashboard.tsx`）同形：原来外层是 `div[role="button"][tabIndex=0]`
          而里面又有一个真 `<button>开始复习</button>`，axe 判 nested-interactive
          （F-34），且外层只监听 Enter、不监听 Space，与 role="button" 的约定不符。
          改法就是审计建议的字面做法：**卡片只是盒子，行为落在真有名字的按钮上**。
          键盘结果更好：Tab 只停一次，且按钮原生支持 Space。 */}
      <section style={{ marginBottom: 'var(--space-xl)' }}>
        <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>
          {t('todayLearn.dueTasks')}
        </h2>
        {dueCount > 0 ? (
          <div className="card card-accent-left">
            {/* 窄屏换行：标题与"开始复习"并排会被压扁（.page-header-row） */}
            <div
              className="page-header-row"
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
            >
              <div>
                <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
                  {t('todayLearn.dueToday', { count: dueCount })}
                </h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                  {t('todayLearn.todayDone', {
                    count: todayDone,
                    accuracy: stats?.today_accuracy ?? 0,
                  })}
                </p>
              </div>
              <button className="btn btn-primary" onClick={startReview}>
                {t('todayLearn.startReview')}
              </button>
            </div>
            {/* 进度条 */}
            <div className="progress-bar" style={{ marginTop: 'var(--space-md)' }}>
              <div
                className="progress-bar-fill"
                style={{
                  width: `${Math.min((todayDone / (stats?.daily_limit ?? 10)) * 100, 100)}%`,
                }}
              />
            </div>
            <p
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
                marginTop: 'var(--space-xs)',
              }}
            >
              {t('todayLearn.dailyGoal', {
                limit: stats?.daily_limit ?? 10,
                done: todayDone,
              })}
            </p>
          </div>
        ) : (
          <EmptyState message={t('todayLearn.noDue')} description={t('todayLearn.noDueHint')} />
        )}
      </section>

      {/* 薄弱点 */}
      {weakPoints.length > 0 && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>
            {t('todayLearn.weakPoints')}
          </h2>
          <div className="card card-accent-error">
            {/* 每一行是**真链接**：原来外层是 `div[onClick]`（没有 role/tabIndex），
                键盘到不了 —— 而"跳到那张卡片"是这一行唯一的行为。
                与仪表盘上那一份（同形 JSX）同一次改法。 */}
            {weakPoints.map((wp) => (
              <Link
                key={wp.card_id}
                to={`/cards/${wp.card_id}`}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: 'var(--space-xs) 0',
                  borderBottom: '1px solid var(--color-border)',
                  color: 'inherit',
                  textDecoration: 'none',
                }}
              >
                <div>
                  <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>{wp.card_title}</span>
                  <span
                    style={{
                      fontSize: '0.75rem',
                      marginLeft: 'var(--space-sm)',
                      padding: '1px 6px',
                      borderRadius: 3,
                      // 与仪表盘上那份**共用同一个常量**：这段徽章在两页上写过两遍，
                      // 而 F-35 只在一页上报出来（另一页被桩的字段名藏住了），
                      // 所以修法与取值都收在 utils/labels.ts 的 weakPointBadge 里。
                      background: weakPointBadge.background,
                      color: weakPointBadge.color,
                    }}
                  >
                    {cardTypeLabels[wp.card_type] || wp.card_type}
                  </span>
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                  {t('todayLearn.errorCount', { count: wp.error_count, accuracy: wp.accuracy })}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/**
 * 每日推荐任务展示组件
 * 按任务类型分组（薄弱点 / 复习 / 新资料），
 * 每个任务展示标题与优先级徽章，含 quiz_id/note_id 的任务可点击跳转
 */
interface DailyPlanSectionProps {
  plan: DailyPlanResponse;
  navigate: (path: string) => void;
}

/**
 * 任务类型 → 文案**键名**（不是文案本身）
 *
 * ⚠️ 模块级常量在加载时就固化了，存译文则切语言不变 —— 这是本项目
 * 反复踩到的坑（`Sidebar.NAV_SECTIONS` / `Upload.BACKEND_OPTIONS` /
 * `Dashboard.taskTypeLabelKeys` / `DailyMaterials.STATUS_FILTERS`）。
 */
const dailyTaskTypeLabelKeys: Record<string, MessageKey> = {
  weak_point: 'todayLearn.weakPoints',
  review: 'todayLearn.taskReview',
  new_material: 'todayLearn.taskNewMaterial',
};

/**
 * 优先级到颜色与中文标签的映射
 *
 * ── 为什么三个色值全换了（a11y-audit **F-36**）──
 *
 * 这三个徽章是**白字压色块**（第 501 行 `color: '#fff'`），而原值
 * `#f44336` / `#ff9800` / `#10b981` 与白色的对比度分别是
 * **3.68 / 2.16 / 2.54:1**，11.2px 常规字重要求 4.5:1 —— **三个全部不达标**。
 *
 * ⚠️ **axe 永远报不出这三条**，所以它们没有登记项、也没有上限，
 * 唯一的信号是 `e2e/a11y.spec.ts` 里那条 `measureContrast` 断言。
 * 机制（读 axe-core 4.13 源码确认，`colorContrastEvaluate`）：
 * `shortTextContent = visibleText.length === 1` ——
 * 文本只有一个字符、且对比度不足时它**不下结论**，节点进 `incomplete`，
 * 永远不会变成 violation。「高 / 中 / 低」正好各一个字。
 *
 * 取值：**压深一档、色相不变**（与 F-06 的 `--color-success`、
 * F-19 的金绿两档同一个做法），并尽量落在项目已有的语义色上：
 *
 * | 优先级 | 原值 | 白字 | 新值 | 白字 | 说明 |
 * |---|---|---|---|---|---|
 * | 1 高 | `#f44336` | 3.68:1 | `#c0392b` | **5.44:1** | = `--color-error` |
 * | 2 中 | `#ff9800` | 2.16:1 | `#936408` | **5.16:1** | = `--color-warning` |
 * | 3 低 | `#10b981` | 2.54:1 | `#25714a` | **5.93:1** | = `--color-success` |
 *
 * 「中」直接用 `--color-warning` 的取值：两处本来就是同一个语义
 * （"需要注意但不是错误"），共用色值才不会又出现"改了令牌漏了字面量"。
 * 与该令牌一样，这是 `#ff9800` 加深后的琥珀色，色相没换。
 */
const priorityMeta: Record<number, { color: string; labelKey: MessageKey }> = {
  1: { color: '#c0392b', labelKey: 'todayLearn.difficultyHigh' },
  2: { color: '#936408', labelKey: 'todayLearn.difficultyMedium' },
  3: { color: '#25714a', labelKey: 'todayLearn.difficultyLow' },
};

function DailyPlanSection({ plan, navigate }: DailyPlanSectionProps) {
  const { t } = useTranslation();
  // 兼容 recommended_tasks 的两种结构：数组形式 / 对象分组形式
  const tasks: RecommendedTask[] = (() => {
    const raw = plan.recommended_tasks as unknown;
    if (Array.isArray(raw)) {
      return raw as RecommendedTask[];
    }
    if (raw && typeof raw === 'object') {
      const obj = raw as Record<string, RecommendedTask[]>;
      const merged: RecommendedTask[] = [];
      for (const key of Object.keys(obj)) {
        const arr = obj[key];
        if (Array.isArray(arr)) {
          merged.push(...arr);
        }
      }
      return merged;
    }
    return [];
  })();

  // 按任务类型分组
  const grouped: Record<string, RecommendedTask[]> = {};
  for (const t of tasks) {
    if (!grouped[t.task_type]) grouped[t.task_type] = [];
    grouped[t.task_type].push(t);
  }

  // 三类任务的展示顺序
  const categories: Array<'weak_point' | 'review' | 'new_material'> = [
    'weak_point',
    'review',
    'new_material',
  ];

  return (
    <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
      {categories.map((cat) => {
        const arr = grouped[cat];
        if (!arr || arr.length === 0) return null;
        // 同类任务按优先级升序排序（1=最高优先）
        const sorted = [...arr].sort((a, b) => a.priority - b.priority);
        return (
          <div key={cat} className="card" style={{ padding: 'var(--space-md)' }}>
            <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-sm)', fontSize: '1rem' }}>
              {t(dailyTaskTypeLabelKeys[cat]) || cat}
              <span
                style={{
                  marginLeft: 'var(--space-sm)',
                  fontSize: '0.8rem',
                  color: 'var(--color-text-secondary)',
                  fontWeight: 400,
                }}
              >
                {t('todayLearn.itemCount', { count: arr.length })}
              </span>
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-xs)' }}>
              {sorted.map((task, idx) => {
                // 是否可点击跳转
                const clickable = !!task.quiz_id || !!task.note_id;
                const prio = priorityMeta[task.priority] || {
                  color: 'var(--color-text-secondary)',
                  label: String(task.priority),
                };
                const go = () => {
                  if (task.quiz_id) navigate('/review');
                  else if (task.note_id) navigate(`/notes/${task.note_id}`);
                };
                const rowStyle: React.CSSProperties = {
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  width: '100%',
                  padding: 'var(--space-xs) var(--space-sm)',
                  borderRadius: 4,
                  background: clickable ? 'var(--color-surface)' : 'transparent',
                  border: clickable ? '1px solid var(--color-border)' : '1px solid transparent',
                  textAlign: 'left',
                };
                const inner = (
                  <>
                    <span style={{ fontSize: '0.9rem', flex: 1 }}>{task.title}</span>
                    {/* 优先级徽章 */}
                    <span
                      style={{
                        fontSize: '0.7rem',
                        padding: '1px 6px',
                        borderRadius: 3,
                        color: '#fff',
                        background: prio.color,
                        marginLeft: 'var(--space-sm)',
                      }}
                    >
                      {t(prio.labelKey)}
                    </span>
                  </>
                );
                // ⚠️ 这里原来无论可不可点都渲染 `div[role="button"][tabIndex=0]`，
                // 而且只监听 `Enter`、**不监听 `Space`** —— 与 F-09 / F-30 / F-34
                // 是同一个洞的又一处入口：Tab 停在一个"不是按钮的按钮"上，
                // 而且它的键位约定与 `role="button"` 不符。axe 报不出来（里面
                // 没有可聚焦后代），只有这里把它换成**真 `<button>`** 才算修好：
                // 原生的 Enter **与** Space 都生效，读屏也会念"按钮"。
                // 不可点的任务保持普通 div（渲染一个按不动的按钮是另一种误导）。
                // 字族/行高/颜色显式继承：按钮的 UA 样式会把它们换掉，
                // 不复位就是一次改版（字号本来就在内层 span 上写着）。
                return clickable ? (
                  <button
                    key={idx}
                    type="button"
                    onClick={go}
                    style={{
                      ...rowStyle,
                      fontFamily: 'inherit',
                      lineHeight: 'inherit',
                      color: 'inherit',
                      cursor: 'pointer',
                    }}
                  >
                    {inner}
                  </button>
                ) : (
                  <div key={idx} style={rowStyle}>
                    {inner}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

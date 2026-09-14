/**
 * @file 今日学习页面
 * @description 整合待复习题目、今日学习报告和薄弱点的入口页面。
 * 用户可以在此查看今日学习任务、开始答题、查看进度。
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getDueQuizzes, submitAnswer, getReviewStats, getDailyReport, getWeakPoints, getDailyPlan,
  type DueQuiz, type SubmitAnswerResponse, type ReviewStats, type DailyReport, type WeakPoint,
  type DailyPlanResponse, type RecommendedTask,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
// 共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard'
// 与卡片复习页共用的进度条与回车键约定（5.12）
import ReviewProgress from '../components/quiz/ReviewProgress'
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard'
import { useSelfRating } from '../hooks/useSelfRating'
import { useToast } from '../components/Toast'
// 统计卡片（overhaul-plan 5.6 第三批）：与 Dashboard 共用的 `.stat-card*` 已抽成
// 组件、样式进模块 —— 本页不再需要 `dashboard.css` 里的全局类名
import StatCard from '../components/StatCard'
import { cardTypeLabels, weakPointBadge } from '../utils/labels'

/** 单题答题状态 */
interface QuizState {
  quiz: DueQuiz
  userAnswer: string
  submitted: boolean
  result: SubmitAnswerResponse | null
  startTime: number
}

export default function TodayLearn() {
  const navigate = useNavigate()
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [dailyReport, setDailyReport] = useState<DailyReport | null>(null)
  const [weakPoints, setWeakPoints] = useState<WeakPoint[]>([])
  /** 今日每日推荐任务 */
  const [dailyPlan, setDailyPlan] = useState<DailyPlanResponse | null>(null)

  // 答题状态
  const [quizzes, setQuizzes] = useState<QuizState[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [sessionCorrect, setSessionCorrect] = useState(0)
  const [sessionTotal, setSessionTotal] = useState(0)
  const [completed, setCompleted] = useState(false)
  // 语义判分开关（阶段 3.5）：状态由页面持有，回车提交才能用上它
  const [semanticGrading, setSemanticGrading] = useState(false)
  /** 提交 in-flight 锁（防双击重复提交），见 docs/decisions.md#F-23 */
  const submittingRef = useRef(false)

  // 四档自评：补完简答题的占位记录并推进 SM-2 调度
  const onRated = useCallback((result: SubmitAnswerResponse) => {
    setQuizzes(prev => prev.map(q =>
      q.quiz.id === result.quiz_id ? { ...q, result } : q,
    ))
    if (result.is_correct) setSessionCorrect(prev => prev + 1)
    setSessionTotal(prev => prev + 1)
    getReviewStats().then(setStats).catch(() => { /* 统计刷新失败不影响答题 */ })
    toast.success('自评已记录，复习进度已更新')
  }, [toast])
  const onRateError = useCallback((message: string) => {
    setError(message)
    toast.error(message)
  }, [toast])
  const { submitRating, submitting: ratingSubmitting, isRated, skipRating } = useSelfRating({
    submit: submitAnswer,
    onRated,
    onError: onRateError,
  })

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [statsData, reportRes, weakRes, planRes] = await Promise.all([
        getReviewStats().catch(() => null),
        getDailyReport().catch(() => null),
        getWeakPoints(5).catch(() => null),
        getDailyPlan().catch(() => null),
      ])
      if (statsData) setStats(statsData)
      if (reportRes) setDailyReport(reportRes)
      if (weakRes) setWeakPoints(weakRes.items)
      if (planRes) setDailyPlan(planRes)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function startReview() {
    try {
      const dueData = await getDueQuizzes(50)
      if (dueData.items.length === 0) return
      setQuizzes(dueData.items.map(q => ({
        quiz: q, userAnswer: '', submitted: false, result: null, startTime: Date.now(),
      })))
      setCurrentIndex(0)
      setSessionCorrect(0)
      setSessionTotal(0)
      setCompleted(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载题目失败')
    }
  }

  async function handleSubmit() {
    // in-flight 锁，防止双击/连按回车重复提交，见 docs/decisions.md#F-23
    if (submittingRef.current) return
    const current = quizzes[currentIndex]
    if (!current || current.submitted || !current.userAnswer.trim()) return

    submittingRef.current = true
    const timeSpent = Date.now() - current.startTime
    try {
      // 语义判分只在首次提交时按用户的勾选请求（带自评的那次后端会直接跳过 LLM）
      const result = await submitAnswer(
        current.quiz.id, current.userAnswer, timeSpent, undefined, semanticGrading,
      )
      const newQuizzes = [...quizzes]
      newQuizzes[currentIndex] = { ...current, submitted: true, result }
      setQuizzes(newQuizzes)
      // 只有真正推进了调度的提交才计入本次统计；简答题的占位提交
      // （grading_method='ungraded'，尚未自评）不算一次"已答"。
      if (result.grading_method !== 'ungraded') {
        if (result.is_correct) setSessionCorrect(prev => prev + 1)
        setSessionTotal(prev => prev + 1)
      }
      const newStats = await getReviewStats()
      setStats(newStats)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : ''
      if (message.includes('每日上限')) {
        setCompleted(true)
        const newStats = await getReviewStats()
        setStats(newStats)
      } else {
        setError(message || '提交失败')
      }
    } finally {
      submittingRef.current = false
    }
  }

  /** 四档自评：补完占位记录并推进 SM-2 调度 */
  const handleSelfRate = useCallback(
    async (quality: number) => {
      const current = quizzes[currentIndex]
      if (!current || isRated(current.quiz.id)) return
      const timeSpent = Date.now() - current.startTime
      await submitRating(current.quiz.id, current.userAnswer, timeSpent, quality)
    },
    [quizzes, currentIndex, isRated, submitRating],
  )

  function handleNext() {
    const current = quizzes[currentIndex]
    // 等待自评时不允许跳到下一题：此刻调度尚未推进，
    // 放行会让这道题永远停在"答了但结不了账"的状态。
    if (current?.result?.needs_self_assessment && !isRated(current.quiz.id)) return
    if (currentIndex < quizzes.length - 1) {
      const nextIndex = currentIndex + 1
      setCurrentIndex(nextIndex)
      const newQuizzes = [...quizzes]
      newQuizzes[nextIndex] = { ...newQuizzes[nextIndex], startTime: Date.now() }
      setQuizzes(newQuizzes)
    } else {
      setCompleted(true)
    }
  }

  /** 回车 = 推进当前这一步：已提交则下一题，否则提交答案（与卡片复习页同一约定） */
  function handleEnter() {
    const current = quizzes[currentIndex]
    if (current?.submitted) handleNext()
    else void handleSubmit()
  }

  // 焦点刻意不收回容器：这一页的焦点应当留在填空/简答输入框里（见 hook 的说明）
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter: handleEnter })

  function formatTime(ms: number): string {
    if (ms < 60000) return `${Math.round(ms / 1000)}秒`
    if (ms < 3600000) return `${Math.round(ms / 60000)}分钟`
    return `${(ms / 3600000).toFixed(1)}小时`
  }

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text="加载今日学习数据..." />

  // --- 错误 ---
  if (error && !stats) return <ErrorDisplay message={error} onRetry={loadData} />

  // --- 答题完成 ---
  if (completed) {
    // 每日限额从后端 /review/stats 读取（单一来源），见 docs/decisions.md#F-12
    const dailyLimit = stats?.daily_limit ?? 10
    const todayDone = stats?.today_done ?? 0
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 'var(--space-lg)' }}>今日复习完成</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>本次统计</h3>
          <p>答题数: {sessionTotal}</p>
          <p>正确数: {sessionCorrect}</p>
          <p>正确率: {sessionTotal > 0 ? Math.round(sessionCorrect / sessionTotal * 100) : 0}%</p>
        </div>
        {stats && (
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <h3>今日总体</h3>
            <p>已完成: {todayDone} / {dailyLimit}</p>
            <p>正确率: {stats.today_accuracy}%</p>
            <p>待复习: {stats.due_count}</p>
          </div>
        )}
        {todayDone < dailyLimit ? (
          <button className="btn btn-primary" onClick={startReview}>继续复习</button>
        ) : (
          // 原值 `#ff9800` 白底只有 2.16:1（0.9rem 文字要求 4.5:1），
          // 与 F-36 的「中」优先级徽章是同一个色值 —— 换成 `--color-warning`
          // 的取值（#936408，白底 5.16:1），不再各处写一份橙。
          <p style={{ color: 'var(--color-warning)', fontWeight: 600 }}>今日已完成 {dailyLimit} 道题，休息一下吧！</p>
        )}
      </div>
    )
  }

  // --- 答题中 ---
  if (quizzes.length > 0) {
    const current = quizzes[currentIndex]
    const quiz = current.quiz

    return (
      <div
        className="page-enter"
        ref={containerRef}
        onKeyDown={handleKeyDown}
        style={{ maxWidth: 700, margin: '0 auto' }}
      >
        {/* 进度条（与卡片复习页共用） */}
        <ReviewProgress
          index={currentIndex}
          total={quizzes.length}
          done={current.submitted}
          label={<>{currentIndex + 1} / {quizzes.length}</>}
          trailing={<>{sessionCorrect}/{sessionTotal} 正确</>}
        />

        {/* 题目卡片（共享 QuizAnswerCard） */}
        <QuizAnswerCard
          quiz={quiz}
          userAnswer={current.userAnswer}
          submitted={current.submitted}
          result={current.result}
          submitting={submittingRef.current}
          showSm2Info={false}
          semanticGrading={semanticGrading}
          onToggleSemanticGrading={setSemanticGrading}
          fillAutoFocus
          isLast={currentIndex >= quizzes.length - 1}
          selfRated={isRated(quiz.id)}
          selfRatingSubmitting={ratingSubmitting}
          onSelectAnswer={(answer) => {
            const newQuizzes = [...quizzes]
            newQuizzes[currentIndex] = { ...current, userAnswer: answer }
            setQuizzes(newQuizzes)
          }}
          onSubmit={handleSubmit}
          onSelfRate={handleSelfRate}
          onSkipSelfRate={() => skipRating(quiz.id)}
          onNext={handleNext}
        />
      </div>
    )
  }

  // --- 入口页面 ---
  const dueCount = stats?.due_count ?? 0
  const todayDone = stats?.today_done ?? 0

  return (
    <div className="page-enter">
      <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem', marginBottom: 'var(--space-lg)' }}>今日学习</h1>

      {error && <ErrorDisplay message={error} onRetry={loadData} />}

      {/* 每日推荐任务（位于页面顶部，按类别分组展示） */}
      {dailyPlan && dailyPlan.total_count > 0 && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
            每日推荐任务
          </h2>
          <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-md)' }}>
            {dailyPlan.plan_date} · 已完成 {dailyPlan.completed_count} / {dailyPlan.total_count}
          </p>
          <DailyPlanSection plan={dailyPlan} navigate={navigate} />
        </section>
      )}

      {/* 今日学习报告摘要 */}
      {dailyReport && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>
            今日报告 ({dailyReport.date})
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 'var(--space-md)' }}>
            <StatCard variant="blue" value={dailyReport.new_mastered} label="新掌握" />
            <StatCard variant="green" value={dailyReport.total_reviews} label="复习次数" />
            <StatCard variant="gold" value={`${dailyReport.today_accuracy}%`} label="正确率" />
            <StatCard variant="purple" value={formatTime(dailyReport.total_review_time_ms)} label="复习时长" />
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
        <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>待复习任务</h2>
        {dueCount > 0 ? (
          <div className="card card-accent-left">
            {/* 窄屏换行：标题与"开始复习"并排会被压扁（.page-header-row） */}
            <div className="page-header-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>今日待复习: {dueCount} 题</h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                  今日已完成 {todayDone} 题 | 正确率 {stats?.today_accuracy ?? 0}%
                </p>
              </div>
              <button className="btn btn-primary" onClick={startReview}>开始复习</button>
            </div>
            {/* 进度条 */}
            <div className="progress-bar" style={{ marginTop: 'var(--space-md)' }}>
              <div className="progress-bar-fill" style={{
                width: `${Math.min((todayDone / (stats?.daily_limit ?? 10)) * 100, 100)}%`,
              }} />
            </div>
            <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
              每日目标 {stats?.daily_limit ?? 10} 题，已完成 {todayDone} 题
            </p>
          </div>
        ) : (
          <EmptyState message="今日没有待复习的题目" description="所有题目都已复习完毕，明天再来" />
        )}
      </section>

      {/* 薄弱点 */}
      {weakPoints.length > 0 && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>薄弱点</h2>
          <div className="card card-accent-error">
            {weakPoints.map(wp => (
              <div
                key={wp.card_id}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-xs) 0', borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                onClick={() => navigate(`/cards/${wp.card_id}`)}
              >
                <div>
                  <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>{wp.card_title}</span>
                  <span style={{
                    fontSize: '0.75rem', marginLeft: 'var(--space-sm)', padding: '1px 6px', borderRadius: 3,
                    // 与仪表盘上那份**共用同一个常量**：这段徽章在两页上写过两遍，
                    // 而 F-35 只在一页上报出来（另一页被桩的字段名藏住了），
                    // 所以修法与取值都收在 utils/labels.ts 的 weakPointBadge 里。
                    background: weakPointBadge.background, color: weakPointBadge.color,
                  }}>
                    {cardTypeLabels[wp.card_type] || wp.card_type}
                  </span>
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                  错{wp.error_count}次 | {wp.accuracy}%
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

/**
 * 每日推荐任务展示组件
 * 按任务类型分组（薄弱点 / 复习 / 新资料），
 * 每个任务展示标题与优先级徽章，含 quiz_id/note_id 的任务可点击跳转
 */
interface DailyPlanSectionProps {
  plan: DailyPlanResponse
  navigate: (path: string) => void
}

/** 任务类型到中文标签的映射 */
const dailyTaskTypeLabels: Record<string, string> = {
  weak_point: '薄弱点',
  review: '复习',
  new_material: '新资料',
}

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
const priorityMeta: Record<number, { color: string; label: string }> = {
  1: { color: '#c0392b', label: '高' },
  2: { color: '#936408', label: '中' },
  3: { color: '#25714a', label: '低' },
}

function DailyPlanSection({ plan, navigate }: DailyPlanSectionProps) {
  // 兼容 recommended_tasks 的两种结构：数组形式 / 对象分组形式
  const tasks: RecommendedTask[] = (() => {
    const raw = plan.recommended_tasks as unknown
    if (Array.isArray(raw)) {
      return raw as RecommendedTask[]
    }
    if (raw && typeof raw === 'object') {
      const obj = raw as Record<string, RecommendedTask[]>
      const merged: RecommendedTask[] = []
      for (const key of Object.keys(obj)) {
        const arr = obj[key]
        if (Array.isArray(arr)) {
          merged.push(...arr)
        }
      }
      return merged
    }
    return []
  })()

  // 按任务类型分组
  const grouped: Record<string, RecommendedTask[]> = {}
  for (const t of tasks) {
    if (!grouped[t.task_type]) grouped[t.task_type] = []
    grouped[t.task_type].push(t)
  }

  // 三类任务的展示顺序
  const categories: Array<'weak_point' | 'review' | 'new_material'> = ['weak_point', 'review', 'new_material']

  return (
    <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
      {categories.map(cat => {
        const arr = grouped[cat]
        if (!arr || arr.length === 0) return null
        // 同类任务按优先级升序排序（1=最高优先）
        const sorted = [...arr].sort((a, b) => a.priority - b.priority)
        return (
          <div key={cat} className="card" style={{ padding: 'var(--space-md)' }}>
            <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-sm)', fontSize: '1rem' }}>
              {dailyTaskTypeLabels[cat] || cat}
              <span style={{ marginLeft: 'var(--space-sm)', fontSize: '0.8rem', color: 'var(--color-text-secondary)', fontWeight: 400 }}>
                共 {arr.length} 项
              </span>
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-xs)' }}>
              {sorted.map((task, idx) => {
                // 是否可点击跳转
                const clickable = !!task.quiz_id || !!task.note_id
                const prio = priorityMeta[task.priority] || { color: 'var(--color-text-secondary)', label: String(task.priority) }
                return (
                  <div
                    key={idx}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: 'var(--space-xs) var(--space-sm)',
                      borderRadius: 4,
                      background: clickable ? 'var(--color-surface)' : 'transparent',
                      cursor: clickable ? 'pointer' : 'default',
                      border: clickable ? '1px solid var(--color-border)' : '1px solid transparent',
                    }}
                    onClick={() => {
                      if (task.quiz_id) navigate('/review')
                      else if (task.note_id) navigate(`/notes/${task.note_id}`)
                    }}
                    role={clickable ? 'button' : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    onKeyDown={(e) => {
                      if (!clickable) return
                      if (e.key === 'Enter') {
                        if (task.quiz_id) navigate('/review')
                        else if (task.note_id) navigate(`/notes/${task.note_id}`)
                      }
                    }}
                  >
                    <span style={{ fontSize: '0.9rem', flex: 1 }}>{task.title}</span>
                    {/* 优先级徽章 */}
                    <span style={{
                      fontSize: '0.7rem',
                      padding: '1px 6px',
                      borderRadius: 3,
                      color: '#fff',
                      background: prio.color,
                      marginLeft: 'var(--space-sm)',
                    }}>
                      {prio.label}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

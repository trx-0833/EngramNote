/**
 * @file 今日学习页面
 * @description 整合待复习题目、今日学习报告和薄弱点的入口页面。
 * 用户可以在此查看今日学习任务、开始答题、查看进度。
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getDueQuizzes, submitAnswer, getReviewStats, getDailyReport, getWeakPoints, getDailyPlan,
  type DueQuiz, type SubmitAnswerResponse, type ReviewStats, type DailyReport, type WeakPoint,
  type DailyPlanResponse, type RecommendedTask,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
// F-33：共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard'
import { cardTypeLabels } from '../utils/labels'

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
  /** F-23：提交 in-flight 锁（防双击重复提交） */
  const submittingRef = useRef(false)

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
    // F-23：in-flight 锁，防止双击/连按回车重复提交
    if (submittingRef.current) return
    const current = quizzes[currentIndex]
    if (!current || current.submitted || !current.userAnswer.trim()) return

    submittingRef.current = true
    const timeSpent = Date.now() - current.startTime
    try {
      const result = await submitAnswer(current.quiz.id, current.userAnswer, timeSpent)
      const newQuizzes = [...quizzes]
      newQuizzes[currentIndex] = { ...current, submitted: true, result }
      setQuizzes(newQuizzes)
      if (result.is_correct) setSessionCorrect(prev => prev + 1)
      setSessionTotal(prev => prev + 1)
      const newStats = await getReviewStats()
      setStats(newStats)
    } catch (e: any) {
      if (e.message?.includes('每日上限')) {
        setCompleted(true)
        const newStats = await getReviewStats()
        setStats(newStats)
      } else {
        setError(e.message || '提交失败')
      }
    } finally {
      submittingRef.current = false
    }
  }

  function handleNext() {
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

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const current = quizzes[currentIndex]
      if (current?.submitted) handleNext()
      else handleSubmit()
    }
  }

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
    // F-12：每日限额从后端 /review/stats 读取（单一来源）
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
          <p style={{ color: '#ff9800', fontWeight: 600 }}>今日已完成 {dailyLimit} 道题，休息一下吧！</p>
        )}
      </div>
    )
  }

  // --- 答题中 ---
  if (quizzes.length > 0) {
    const current = quizzes[currentIndex]
    const quiz = current.quiz

    return (
      <div className="page-enter" style={{ maxWidth: 700, margin: '0 auto' }} onKeyDown={handleKeyDown}>
        {/* 进度条 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginBottom: 'var(--space-lg)' }}>
          <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
            {currentIndex + 1} / {quizzes.length}
          </span>
          <div className="progress-bar" style={{ flex: 1 }}>
            <div className="progress-bar-fill" style={{
              width: `${((currentIndex + (current.submitted ? 1 : 0)) / quizzes.length) * 100}%`,
            }} />
          </div>
          <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
            {sessionCorrect}/{sessionTotal} 正确
          </span>
        </div>

        {/* 题目卡片（F-33：共享 QuizAnswerCard） */}
        <QuizAnswerCard
          quiz={quiz}
          userAnswer={current.userAnswer}
          submitted={current.submitted}
          result={current.result}
          submitting={submittingRef.current}
          showSm2Info={false}
          fillAutoFocus
          isLast={currentIndex >= quizzes.length - 1}
          onSelectAnswer={(answer) => {
            const newQuizzes = [...quizzes]
            newQuizzes[currentIndex] = { ...current, userAnswer: answer }
            setQuizzes(newQuizzes)
          }}
          onSubmit={handleSubmit}
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
            <div className="stat-card stat-card-blue">
              <div className="stat-number">{dailyReport.new_mastered}</div>
              <div className="stat-label">新掌握</div>
            </div>
            <div className="stat-card stat-card-green">
              <div className="stat-number">{dailyReport.total_reviews}</div>
              <div className="stat-label">复习次数</div>
            </div>
            <div className="stat-card stat-card-gold">
              <div className="stat-number">{dailyReport.today_accuracy}%</div>
              <div className="stat-label">正确率</div>
            </div>
            <div className="stat-card stat-card-purple">
              <div className="stat-number">{formatTime(dailyReport.total_review_time_ms)}</div>
              <div className="stat-label">复习时长</div>
            </div>
          </div>
        </section>
      )}

      {/* 待复习任务 */}
      <section style={{ marginBottom: 'var(--space-xl)' }}>
        <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>待复习任务</h2>
        {dueCount > 0 ? (
          <div
            className="card card-accent-left"
            style={{ cursor: 'pointer' }}
            onClick={startReview}
            role="button"
            tabIndex={0}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>今日待复习: {dueCount} 题</h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                  今日已完成 {todayDone} 题 | 正确率 {stats?.today_accuracy ?? 0}%
                </p>
              </div>
              <button className="btn btn-primary">开始复习</button>
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
                    background: '#f4433620', color: '#f44336',
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

/** 优先级到颜色与中文标签的映射 */
const priorityMeta: Record<number, { color: string; label: string }> = {
  1: { color: '#f44336', label: '高' },
  2: { color: '#ff9800', label: '中' },
  3: { color: '#10b981', label: '低' },
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

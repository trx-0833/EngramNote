/**
 * @file 今日学习页面
 * @description 整合待复习题目、今日学习报告和薄弱点的入口页面。
 * 用户可以在此查看今日学习任务、开始答题、查看进度。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getDueQuizzes, submitAnswer, getReviewStats, getDailyReport, getWeakPoints,
  type DueQuiz, type SubmitAnswerResponse, type ReviewStats, type DailyReport, type WeakPoint,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { cardTypeLabels, questionTypeLabels, difficultyLabels } from '../utils/labels'

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

  // 答题状态
  const [quizzes, setQuizzes] = useState<QuizState[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [sessionCorrect, setSessionCorrect] = useState(0)
  const [sessionTotal, setSessionTotal] = useState(0)
  const [completed, setCompleted] = useState(false)

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [statsData, reportRes, weakRes] = await Promise.all([
        getReviewStats().catch(() => null),
        getDailyReport().catch(() => null),
        getWeakPoints(5).catch(() => null),
      ])
      if (statsData) setStats(statsData)
      if (reportRes) setDailyReport(reportRes)
      if (weakRes) setWeakPoints(weakRes.items)
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
    const current = quizzes[currentIndex]
    if (!current || current.submitted || !current.userAnswer.trim()) return

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

  function parseOptions(optionsStr: string | null): string[] {
    if (!optionsStr) return []
    try {
      const parsed = JSON.parse(optionsStr)
      return Array.isArray(parsed) ? parsed : []
    } catch { return [] }
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
    const dailyLimit = 50
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
    const options = parseOptions(quiz.options)

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

        {/* 题目卡片 */}
        <div className="card">
          <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
            <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: '0.8rem', background: 'var(--color-primary)', color: '#fff' }}>
              {questionTypeLabels[quiz.question_type] || quiz.question_type}
            </span>
            <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: '0.8rem', background: difficultyLabels[quiz.difficulty] === '简单' ? '#10b981' : difficultyLabels[quiz.difficulty] === '中等' ? '#ff9800' : '#f44336', color: '#fff' }}>
              {difficultyLabels[quiz.difficulty] || quiz.difficulty}
            </span>
          </div>

          <p style={{ fontSize: '1.1rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>{quiz.question}</p>

          {!current.submitted ? (
            <>
              {quiz.question_type === 'choice' && options.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                  {options.map((opt, i) => (
                    <button key={i} className={`quiz-option${current.userAnswer === opt ? ' quiz-option-selected' : ''}`} onClick={() => {
                      const newQuizzes = [...quizzes]
                      newQuizzes[currentIndex] = { ...current, userAnswer: opt }
                      setQuizzes(newQuizzes)
                    }}>{opt}</button>
                  ))}
                </div>
              )}
              {quiz.question_type === 'fill_blank' && (
                <input type="text" value={current.userAnswer} onChange={e => {
                  const newQuizzes = [...quizzes]
                  newQuizzes[currentIndex] = { ...current, userAnswer: e.target.value }
                  setQuizzes(newQuizzes)
                }} placeholder="请输入答案..." style={{ width: '100%', padding: 'var(--space-sm) var(--space-md)', border: '1px solid var(--color-border)', borderRadius: 6, fontSize: '1rem' }} autoFocus />
              )}
              {quiz.question_type === 'short_answer' && (
                <textarea value={current.userAnswer} onChange={e => {
                  const newQuizzes = [...quizzes]
                  newQuizzes[currentIndex] = { ...current, userAnswer: e.target.value }
                  setQuizzes(newQuizzes)
                }} placeholder="请输入你的回答..." rows={4} style={{ width: '100%', padding: 'var(--space-sm) var(--space-md)', border: '1px solid var(--color-border)', borderRadius: 6, fontSize: '1rem', resize: 'vertical' }} />
              )}
              <div style={{ marginTop: 'var(--space-md)', textAlign: 'right' }}>
                <button className="btn btn-primary" onClick={handleSubmit} disabled={!current.userAnswer.trim()}>提交答案</button>
              </div>
            </>
          ) : (
            <>
              <div className={current.result?.is_correct ? 'feedback-correct' : 'feedback-incorrect'} style={{ marginBottom: 'var(--space-md)' }}>
                <p style={{ fontWeight: 600, color: current.result?.is_correct ? '#4caf50' : '#f44336' }}>
                  {current.result?.is_correct ? '回答正确!' : '回答错误'}
                </p>
                {!current.result?.is_correct && <p style={{ marginTop: 'var(--space-xs)' }}><strong>正确答案:</strong> {current.result?.correct_answer}</p>}
                {current.result?.explanation && <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}><strong>解析:</strong> {current.result.explanation}</p>}
              </div>
              <div style={{ textAlign: 'right' }}>
                <button className="btn btn-primary" onClick={handleNext}>
                  {currentIndex < quizzes.length - 1 ? '下一题' : '完成复习'}
                </button>
              </div>
            </>
          )}
        </div>
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
                width: `${Math.min((todayDone / 50) * 100, 100)}%`,
              }} />
            </div>
            <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
              每日目标 50 题，已完成 {todayDone} 题
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

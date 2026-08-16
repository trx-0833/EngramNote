/**
 * @file 复习答题页面
 * @description 基于间隔重复算法的复习答题界面，支持选择题、填空题和简答题。
 * 用户逐题作答，提交后即时显示正误判断和解析，SM-2 算法自动更新复习间隔。
 */
import { useState, useEffect, useRef } from 'react'
import { getDueQuizzes, submitAnswer, getReviewStats, DueQuiz, SubmitAnswerResponse, ReviewStats } from '../api/client'
// F-33：共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard'

/** 单题答题状态 */
interface QuizState {
  quiz: DueQuiz
  userAnswer: string
  submitted: boolean
  result: SubmitAnswerResponse | null
  startTime: number
}

export default function Review() {
  const [quizzes, setQuizzes] = useState<QuizState[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [stats, setStats] = useState<ReviewStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [completed, setCompleted] = useState(false)
  const [sessionCorrect, setSessionCorrect] = useState(0)
  const [sessionTotal, setSessionTotal] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  /** F-23：提交 in-flight 锁（防双击重复提交） */
  const submittingRef = useRef(false)

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    try {
      setLoading(true)
      const [dueData, statsData] = await Promise.all([
        getDueQuizzes(50),
        getReviewStats(),
      ])
      setQuizzes(dueData.items.map(q => ({
        quiz: q,
        userAnswer: '',
        submitted: false,
        result: null,
        startTime: Date.now(),
      })))
      setStats(statsData)
      if (dueData.items.length === 0) {
        setCompleted(true)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSubmit() {
    // F-23：in-flight 锁，防止双击/连按回车重复提交（重复 ReviewLog + SM-2 叠加）
    if (submittingRef.current) return
    const current = quizzes[currentIndex]
    if (!current || current.submitted) return
    if (!current.userAnswer.trim()) return

    submittingRef.current = true
    const timeSpent = Date.now() - current.startTime

    try {
      const result = await submitAnswer(current.quiz.id, current.userAnswer, timeSpent)
      const newQuizzes = [...quizzes]
      newQuizzes[currentIndex] = { ...current, submitted: true, result }
      setQuizzes(newQuizzes)

      if (result.is_correct) {
        setSessionCorrect(prev => prev + 1)
      }
      setSessionTotal(prev => prev + 1)

      // 刷新统计（更新今日已完成数）
      const newStats = await getReviewStats()
      setStats(newStats)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '提交失败'
      if (msg.includes('每日上限')) {
        // 达到每日限额，跳到完成页面
        setCompleted(true)
        const newStats = await getReviewStats()
        setStats(newStats)
      } else {
        setError(msg)
      }
    } finally {
      submittingRef.current = false
    }
  }

  function handleNext() {
    if (currentIndex < quizzes.length - 1) {
      const nextIndex = currentIndex + 1
      setCurrentIndex(nextIndex)
      // 重置下一题的开始时间
      const newQuizzes = [...quizzes]
      newQuizzes[nextIndex] = { ...newQuizzes[nextIndex], startTime: Date.now() }
      setQuizzes(newQuizzes)
      // 聚焦输入框
      setTimeout(() => {
        const quiz = quizzes[nextIndex]?.quiz
        if (quiz?.question_type === 'fill_blank') inputRef.current?.focus()
        else if (quiz?.question_type === 'short_answer') textareaRef.current?.focus()
      }, 100)
    } else {
      setCompleted(true)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const current = quizzes[currentIndex]
      if (current?.submitted) {
        handleNext()
      } else {
        handleSubmit()
      }
    }
  }

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>加载复习题目中...</div>
  }

  // 完成页面
  if (completed) {
    // F-12：每日限额从后端 /review/stats 读取（单一来源），不再硬编码 50
    const dailyLimit = stats?.daily_limit ?? 10
    const todayDone = stats?.today_done ?? 0
    const reachedDailyLimit = todayDone >= dailyLimit

    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2>复习完成</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>本次复习统计</h3>
          <p>答题数: {sessionTotal}</p>
          <p>正确数: {sessionCorrect}</p>
          <p>正确率: {sessionTotal > 0 ? Math.round(sessionCorrect / sessionTotal * 100) : 0}%</p>
        </div>

        {stats && (
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <h3>总体统计</h3>
            <p>今日已完成: {todayDone} / {dailyLimit}</p>
            <p>今日正确率: {stats.today_accuracy}%</p>
            <p>待复习题目: {stats.due_count}</p>
            <p>累计复习次数: {stats.total_reviews}</p>
            <p>累计正确率: {stats.total_accuracy}%</p>
            <p>总题目数: {stats.total_quizzes}</p>
          </div>
        )}

        {reachedDailyLimit ? (
          <div className="card card-accent-warning" style={{ textAlign: 'center', marginBottom: 'var(--space-md)' }}>
            <p style={{ fontWeight: 600, color: '#ff9800' }}>今日已完成 {dailyLimit} 道题，休息一下吧！</p>
            <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>明天再来继续复习</p>
          </div>
        ) : (
          <button className="btn btn-primary" onClick={loadData}>
            继续复习
          </button>
        )}
      </div>
    )
  }

  if (error && quizzes.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
        <p style={{ color: 'var(--color-error)' }}>{error}</p>
        <button className="btn btn-primary" onClick={loadData}>重试</button>
      </div>
    )
  }

  const current = quizzes[currentIndex]
  if (!current) return null

  const quiz = current.quiz

  return (
    <div className="page-enter" style={{ maxWidth: 700, margin: '0 auto' }} onKeyDown={handleKeyDown}>
      {/* 进度条 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-sm)',
        marginBottom: 'var(--space-lg)',
      }}>
        <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
          {currentIndex + 1} / {quizzes.length}
        </span>
        <div className="progress-bar" style={{ flex: 1 }}>
          <div className="progress-bar-fill" style={{
            width: `${((currentIndex + (current.submitted ? 1 : 0)) / quizzes.length) * 100}%`,
          }} />
        </div>
        <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
          {sessionCorrect}/{sessionTotal} 正确 | 今日 {stats?.today_done ?? 0}/{stats?.daily_limit ?? 10}
        </span>
      </div>

      {/* 题目卡片（F-33：共享 QuizAnswerCard；提交竞态锁由 handleSubmit 的 submittingRef 承担） */}
      <QuizAnswerCard
        quiz={quiz}
        userAnswer={current.userAnswer}
        submitted={current.submitted}
        result={current.result}
        submitting={submittingRef.current}
        showSm2Info
        showReviewMeta
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

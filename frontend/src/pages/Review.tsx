/**
 * @file 复习答题页面
 * @description 基于间隔重复算法的复习答题界面，支持选择题、填空题和简答题。
 * 用户逐题作答，提交后即时显示正误判断和解析，SM-2 算法自动更新复习间隔。
 */
import { useState, useEffect, useRef } from 'react'
import { getDueQuizzes, submitAnswer, getReviewStats, DueQuiz, SubmitAnswerResponse, ReviewStats } from '../api/client'

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
    } catch (e: any) {
      setError(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSubmit() {
    const current = quizzes[currentIndex]
    if (!current || current.submitted) return
    if (!current.userAnswer.trim()) return

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
    } catch (e: any) {
      if (e.message?.includes('每日上限')) {
        // 达到每日限额，跳到完成页面
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

  /** 解析选择题选项 */
  function parseOptions(optionsStr: string | null): string[] {
    if (!optionsStr) return []
    try {
      const parsed = JSON.parse(optionsStr)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }

  /** 难度标签颜色 */
  function difficultyColor(d: string) {
    switch (d) {
      case 'easy': return '#4caf50'
      case 'medium': return '#ff9800'
      case 'hard': return '#f44336'
      default: return '#999'
    }
  }

  /** 题目类型中文 */
  function questionTypeLabel(t: string) {
    switch (t) {
      case 'choice': return '选择题'
      case 'fill_blank': return '填空题'
      case 'short_answer': return '简答题'
      default: return t
    }
  }

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>加载复习题目中...</div>
  }

  // 完成页面
  if (completed) {
    const dailyLimit = 50
    const todayDone = stats?.today_done ?? 0
    const reachedDailyLimit = todayDone >= dailyLimit

    return (
      <div style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2>复习完成</h2>
        <div style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 8,
          padding: 'var(--space-lg)',
          marginBottom: 'var(--space-lg)',
        }}>
          <h3>本次复习统计</h3>
          <p>答题数: {sessionTotal}</p>
          <p>正确数: {sessionCorrect}</p>
          <p>正确率: {sessionTotal > 0 ? Math.round(sessionCorrect / sessionTotal * 100) : 0}%</p>
        </div>

        {stats && (
          <div style={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 8,
            padding: 'var(--space-lg)',
            marginBottom: 'var(--space-lg)',
          }}>
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
          <div style={{
            background: 'rgba(255, 152, 0, 0.1)',
            border: '1px solid #ff9800',
            borderRadius: 8,
            padding: 'var(--space-md)',
            marginBottom: 'var(--space-md)',
            textAlign: 'center',
          }}>
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
  const options = parseOptions(quiz.options)

  return (
    <div style={{ maxWidth: 700, margin: '0 auto' }} onKeyDown={handleKeyDown}>
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
        <div style={{
          flex: 1,
          height: 6,
          background: 'var(--color-border)',
          borderRadius: 3,
          overflow: 'hidden',
        }}>
          <div style={{
            width: `${((currentIndex + (current.submitted ? 1 : 0)) / quizzes.length) * 100}%`,
            height: '100%',
            background: 'var(--color-primary)',
            borderRadius: 3,
            transition: 'width 0.3s ease',
          }} />
        </div>
        <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
          {sessionCorrect}/{sessionTotal} 正确 | 今日 {stats?.today_done ?? 0}/50
        </span>
      </div>

      {/* 题目卡片 */}
      <div style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 8,
        padding: 'var(--space-lg)',
        marginBottom: 'var(--space-lg)',
      }}>
        {/* 题目头部 */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-md)',
        }}>
          <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
            <span style={{
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: '0.8rem',
              background: 'var(--color-primary)',
              color: '#fff',
            }}>
              {questionTypeLabel(quiz.question_type)}
            </span>
            <span style={{
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: '0.8rem',
              background: difficultyColor(quiz.difficulty),
              color: '#fff',
            }}>
              {quiz.difficulty === 'easy' ? '简单' : quiz.difficulty === 'medium' ? '中等' : '困难'}
            </span>
          </div>
          {quiz.review_count > 0 && (
            <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              已复习 {quiz.review_count} 次 | 间隔 {quiz.interval} 天
            </span>
          )}
        </div>

        {/* 题目内容 */}
        <p style={{ fontSize: '1.1rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>
          {quiz.question}
        </p>

        {/* 答题区域 */}
        {!current.submitted ? (
          <>
            {/* 选择题 */}
            {quiz.question_type === 'choice' && options.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                {options.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      const newQuizzes = [...quizzes]
                      newQuizzes[currentIndex] = { ...current, userAnswer: opt }
                      setQuizzes(newQuizzes)
                    }}
                    style={{
                      padding: 'var(--space-sm) var(--space-md)',
                      textAlign: 'left',
                      border: `2px solid ${current.userAnswer === opt ? 'var(--color-primary)' : 'var(--color-border)'}`,
                      borderRadius: 6,
                      background: current.userAnswer === opt ? 'rgba(25, 118, 210, 0.08)' : 'var(--color-surface)',
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                    }}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}

            {/* 填空题 */}
            {quiz.question_type === 'fill_blank' && (
              <input
                ref={inputRef}
                type="text"
                value={current.userAnswer}
                onChange={e => {
                  const newQuizzes = [...quizzes]
                  newQuizzes[currentIndex] = { ...current, userAnswer: e.target.value }
                  setQuizzes(newQuizzes)
                }}
                placeholder="请输入答案..."
                style={{
                  width: '100%',
                  padding: 'var(--space-sm) var(--space-md)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 6,
                  fontSize: '1rem',
                }}
              />
            )}

            {/* 简答题 */}
            {quiz.question_type === 'short_answer' && (
              <textarea
                ref={textareaRef}
                value={current.userAnswer}
                onChange={e => {
                  const newQuizzes = [...quizzes]
                  newQuizzes[currentIndex] = { ...current, userAnswer: e.target.value }
                  setQuizzes(newQuizzes)
                }}
                placeholder="请输入你的回答..."
                rows={4}
                style={{
                  width: '100%',
                  padding: 'var(--space-sm) var(--space-md)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 6,
                  fontSize: '1rem',
                  resize: 'vertical',
                }}
              />
            )}

            {/* 提交按钮 */}
            <div style={{ marginTop: 'var(--space-md)', textAlign: 'right' }}>
              <button
                className="btn btn-primary"
                onClick={handleSubmit}
                disabled={!current.userAnswer.trim()}
              >
                提交答案
              </button>
            </div>
          </>
        ) : (
          <>
            {/* 判分结果 */}
            <div style={{
              padding: 'var(--space-md)',
              borderRadius: 6,
              marginBottom: 'var(--space-md)',
              background: current.result?.is_correct ? 'rgba(76, 175, 80, 0.1)' : 'rgba(244, 67, 54, 0.1)',
              border: `1px solid ${current.result?.is_correct ? '#4caf50' : '#f44336'}`,
            }}>
              <p style={{ fontWeight: 600, color: current.result?.is_correct ? '#4caf50' : '#f44336' }}>
                {current.result?.is_correct ? '回答正确!' : '回答错误'}
              </p>
              {!current.result?.is_correct && (
                <p style={{ marginTop: 'var(--space-xs)' }}>
                  <strong>正确答案:</strong> {current.result?.correct_answer}
                </p>
              )}
              {current.result?.explanation && (
                <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                  <strong>解析:</strong> {current.result.explanation}
                </p>
              )}
            </div>

            {/* SM-2 信息 */}
            {current.result?.sm2 && (
              <div style={{
                fontSize: '0.85rem',
                color: 'var(--color-text-secondary)',
                padding: 'var(--space-sm)',
                background: 'var(--color-bg)',
                borderRadius: 4,
                marginBottom: 'var(--space-md)',
              }}>
                下次复习: {current.result.sm2.interval} 天后 | EF: {current.result.sm2.easiness_factor} | 评分: {current.result.quality}
              </div>
            )}

            {/* 下一题按钮 */}
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

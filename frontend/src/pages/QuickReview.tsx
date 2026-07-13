/**
 * @file 快速复习页面
 * @description 按笔记维度快速复习，用户上传笔记并完成理解后，
 * 可立即复习该笔记关联的所有题目。支持选择题、填空题和简答题，
 * 逐题展示，提交后显示判分结果和解析，最终汇总统计。
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  getQuickReview, submitQuickReviewAnswer,
  type QuickQuiz, type SubmitAnswerResponse,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { questionTypeLabels, difficultyLabels } from '../utils/labels'

/** 单题答题状态 */
interface QuizState {
  quiz: QuickQuiz
  userAnswer: string
  submitted: boolean
  result: SubmitAnswerResponse | null
  startTime: number
}

export default function QuickReview() {
  const { noteId } = useParams<{ noteId: string }>()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 答题状态
  const [quizzes, setQuizzes] = useState<QuizState[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [sessionCorrect, setSessionCorrect] = useState(0)
  const [sessionTotal, setSessionTotal] = useState(0)
  const [completed, setCompleted] = useState(false)

  useEffect(() => {
    loadQuizzes()
  }, [noteId])

  async function loadQuizzes() {
    if (!noteId) return
    setLoading(true)
    setError('')
    try {
      const data = await getQuickReview(noteId)
      if (data.items.length === 0) {
        setQuizzes([])
      } else {
        setQuizzes(data.items.map(q => ({
          quiz: q, userAnswer: '', submitted: false, result: null, startTime: Date.now(),
        })))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载题目失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSubmit() {
    const current = quizzes[currentIndex]
    if (!current || current.submitted || !current.userAnswer.trim()) return

    const timeSpent = Date.now() - current.startTime
    try {
      const result = await submitQuickReviewAnswer(noteId, current.quiz.id, current.userAnswer, timeSpent)
      const newQuizzes = [...quizzes]
      newQuizzes[currentIndex] = { ...current, submitted: true, result }
      setQuizzes(newQuizzes)
      if (result.is_correct) setSessionCorrect(prev => prev + 1)
      setSessionTotal(prev => prev + 1)
    } catch (e: any) {
      setError(e.message || '提交失败')
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

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text="加载复习题目..." />

  // --- 错误 ---
  if (error && quizzes.length === 0) return <ErrorDisplay message={error} onRetry={loadQuizzes} />

  // --- 无题目 ---
  if (!loading && quizzes.length === 0) {
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <EmptyState
          message="暂无复习题目"
          description="该笔记还没有生成题目，请先完成理解流程"
          action={
            <button className="btn btn-secondary" onClick={() => navigate(`/notes/${noteId}`)}>
              返回笔记
            </button>
          }
        />
      </div>
    )
  }

  // --- 答题完成汇总 ---
  if (completed) {
    const accuracy = sessionTotal > 0 ? Math.round(sessionCorrect / sessionTotal * 100) : 0
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 'var(--space-lg)' }}>复习完成</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>本次统计</h3>
          <p>答题数: {sessionTotal}</p>
          <p>正确数: {sessionCorrect}</p>
          <p>正确率: {accuracy}%</p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button className="btn btn-primary" onClick={() => navigate(`/notes/${noteId}`)}>
            返回笔记
          </button>
          <button className="btn btn-secondary" onClick={async () => {
            // 重新从 API 获取题目，避免重复走 SM-2 调度
            if (!noteId) return
            setLoading(true)
            try {
              const data = await getQuickReview(noteId)
              if (data.items.length === 0) {
                setQuizzes([])
              } else {
                setQuizzes(data.items.map(q => ({
                  quiz: q, userAnswer: '', submitted: false, result: null, startTime: Date.now(),
                })))
                setCurrentIndex(0)
                setSessionCorrect(0)
                setSessionTotal(0)
                setCompleted(false)
              }
            } catch (err) {
              setError(err instanceof Error ? err.message : '加载题目失败')
            } finally {
              setLoading(false)
            }
          }}>
            再来一次
          </button>
        </div>
      </div>
    )
  }

  // --- 答题中 ---
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

      {/* 返回笔记按钮 */}
      <div style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
        <button className="btn btn-secondary" onClick={() => navigate(`/notes/${noteId}`)}>返回笔记</button>
      </div>
    </div>
  )
}

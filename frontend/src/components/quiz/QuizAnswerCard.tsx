/**
 * @file 共享答题卡片组件（F-33）
 * @description 抽取 Review / QuickReview / TodayLearn 三页重复的题目卡片：
 * 类型/难度标签、题目内容、选择题/填空题/简答题作答区、提交按钮、
 * 判分反馈、SM-2 信息（可选）、下一题按钮。
 * 行为差异通过 props 参数化；提交/下一题的竞态锁由页面 handleSubmit 内实现
 * （F-23 submittingRef），本组件仅透传 submitting 禁用状态。
 */
import type { ReactNode } from 'react'
import type { SubmitAnswerResponse } from '../../api/client'
import { questionTypeLabels, difficultyLabels, difficultyColors } from '../../utils/labels'

/** 答题卡片所需的最小题目结构 */
export interface QuizCardQuestion {
  question_type: string
  question: string
  difficulty?: string
  options?: string | string[] | null
  /** 复习元信息（Review 页展示） */
  review_count?: number
  interval?: number
}

interface QuizAnswerCardProps {
  quiz: QuizCardQuestion
  userAnswer: string
  submitted: boolean
  result: SubmitAnswerResponse | null
  /** 是否正在提交（F-23 锁，禁用按钮） */
  submitting?: boolean
  /** 是否展示 SM-2 调度信息（Review 有，快速复习无） */
  showSm2Info?: boolean
  /** 是否展示复习次数/间隔（Review 有） */
  showReviewMeta?: boolean
  /** 填空题是否自动聚焦 */
  fillAutoFocus?: boolean
  /** 头部额外信息（如统计） */
  headerExtra?: ReactNode
  /** 是否最后一题（决定"下一题/完成"文案） */
  isLast: boolean
  /** 下一题按钮文案（默认 下一题/完成复习） */
  nextButtonText?: string
  onSelectAnswer: (answer: string) => void
  onSubmit: () => void
  onNext: () => void
}

export default function QuizAnswerCard({
  quiz,
  userAnswer,
  submitted,
  result,
  submitting = false,
  showSm2Info = true,
  showReviewMeta = false,
  fillAutoFocus = false,
  headerExtra,
  isLast,
  nextButtonText,
  onSelectAnswer,
  onSubmit,
  onNext,
}: QuizAnswerCardProps) {
  // 解析选择题选项
  let options: string[] = []
  if (Array.isArray(quiz.options)) {
    options = quiz.options
  } else if (typeof quiz.options === 'string' && quiz.options) {
    try {
      const parsed = JSON.parse(quiz.options)
      options = Array.isArray(parsed) ? parsed : []
    } catch {
      options = []
    }
  }

  const typeLabel = questionTypeLabels[quiz.question_type] || quiz.question_type
  const diffLabel = difficultyLabels[quiz.difficulty || ''] || quiz.difficulty || ''

  return (
    <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
      {/* 题目头部 */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 'var(--space-md)',
      }}>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <span style={{
            padding: '2px 8px', borderRadius: 4, fontSize: '0.8rem',
            background: 'var(--color-primary)', color: '#fff',
          }}>
            {typeLabel}
          </span>
          {diffLabel && (
            <span style={{
              padding: '2px 8px', borderRadius: 4, fontSize: '0.8rem',
              background: difficultyColors[quiz.difficulty || ''] || '#999', color: '#fff',
            }}>
              {diffLabel}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
          {showReviewMeta && quiz.review_count != null && quiz.review_count > 0 && (
            <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              已复习 {quiz.review_count} 次 | 间隔 {quiz.interval ?? 0} 天
            </span>
          )}
          {headerExtra}
        </div>
      </div>

      {/* 题目内容 */}
      <p style={{ fontSize: '1.1rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>
        {quiz.question}
      </p>

      {/* 答题区域 */}
      {!submitted ? (
        <>
          {/* 选择题 */}
          {quiz.question_type === 'choice' && options.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
              {options.map((opt, i) => (
                <button
                  key={i}
                  className={`quiz-option${userAnswer === opt ? ' quiz-option-selected' : ''}`}
                  onClick={() => onSelectAnswer(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}

          {/* 填空题 */}
          {quiz.question_type === 'fill_blank' && (
            <input
              type="text"
              value={userAnswer}
              onChange={e => onSelectAnswer(e.target.value)}
              placeholder="请输入答案..."
              autoFocus={fillAutoFocus}
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
              value={userAnswer}
              onChange={e => onSelectAnswer(e.target.value)}
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

          {/* 提交按钮（F-23：submitting 时禁用） */}
          <div style={{ marginTop: 'var(--space-md)', textAlign: 'right' }}>
            <button
              className="btn btn-primary"
              onClick={onSubmit}
              disabled={!userAnswer.trim() || submitting}
            >
              提交答案
            </button>
          </div>
        </>
      ) : (
        <>
          {/* 判分结果 */}
          <div
            className={result?.is_correct ? 'feedback-correct' : 'feedback-incorrect'}
            style={{ marginBottom: 'var(--space-md)' }}
          >
            <p style={{ fontWeight: 600, color: result?.is_correct ? '#4caf50' : '#f44336' }}>
              {result?.is_correct ? '回答正确!' : '回答错误'}
            </p>
            {!result?.is_correct && (
              <p style={{ marginTop: 'var(--space-xs)' }}>
                <strong>正确答案:</strong> {result?.correct_answer}
              </p>
            )}
            {result?.explanation && (
              <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                <strong>解析:</strong> {result.explanation}
              </p>
            )}
          </div>

          {/* SM-2 信息（Review 页展示） */}
          {showSm2Info && result?.sm2 && (
            <div style={{
              fontSize: '0.85rem',
              color: 'var(--color-text-secondary)',
              padding: 'var(--space-sm)',
              background: 'var(--color-bg)',
              borderRadius: 4,
              marginBottom: 'var(--space-md)',
            }}>
              下次复习: {result.sm2.interval} 天后 | EF: {result.sm2.easiness_factor} | 评分: {result.quality}
            </div>
          )}

          {/* 下一题按钮 */}
          <div style={{ textAlign: 'right' }}>
            <button className="btn btn-primary" onClick={onNext}>
              {nextButtonText ?? (isLast ? '完成复习' : '下一题')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

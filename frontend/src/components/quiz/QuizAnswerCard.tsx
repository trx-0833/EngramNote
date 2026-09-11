/**
 * @file 共享答题卡片组件
 * @description 抽取 Review / QuickReview / TodayLearn 三页重复的题目卡片：
 * 类型/难度标签、题目内容、选择题/填空题/简答题作答区、提交按钮、
 * 判分反馈、SM-2 信息（可选）、下一题按钮。
 * 行为差异通过 props 参数化；提交/下一题的竞态锁由页面 handleSubmit 内实现
 * （submittingRef），本组件仅透传 submitting 禁用状态，见 docs/decisions.md#F-23。
 */
import type { ReactNode } from 'react'
import type { SubmitAnswerResponse } from '../../api/client'
import {
  questionTypeLabels,
  difficultyLabels,
  difficultyColors,
  selfRatingOptions,
  gradingMethodLabels,
} from '../../utils/labels'

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
  /** 是否正在提交（提交中禁用按钮，见 docs/decisions.md#F-23） */
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
  /** 本次是否已提交自评（提交后禁用四档按钮，防重复自评） */
  selfRated?: boolean
  /** 是否正在提交自评 */
  selfRatingSubmitting?: boolean
  onSelectAnswer: (answer: string) => void
  onSubmit: () => void
  /** 用户点击四档自评之一；quality 为 SM-2 分值 0/3/4/5 */
  onSelfRate?: (quality: number) => void
  /** 跳过自评（逃生口，见 hooks/useSelfRating.ts 的 skipRating 说明） */
  onSkipSelfRate?: () => void
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
  selfRated = false,
  selfRatingSubmitting = false,
  onSelectAnswer,
  onSubmit,
  onSelfRate,
  onSkipSelfRate,
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

  // 是否仍需要用户自评：后端明确要求，且本次会话尚未给出自评。
  // 注意不要用 result.is_correct 之类推断——简答题的占位判分恒为"错误"。
  const needsSelfAssessment = !!result?.needs_self_assessment && !selfRated

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

          {/* 提交按钮（submitting 时禁用，防连点重复提交，见 docs/decisions.md#F-23） */}
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
          {/* 判分结果
              等待自评时（needs_self_assessment）**不显示对错**：此时后端给的
              quality 只是占位值（简答题固定 1 = "错误"），照常渲染会把每个
              还没自评的简答题都标成"回答错误"，是明确的误导。 */}
          {needsSelfAssessment ? (
            <div
              className="feedback-pending"
              style={{
                marginBottom: 'var(--space-md)',
                padding: 'var(--space-sm) var(--space-md)',
                background: 'var(--color-bg)',
                borderLeft: '3px solid var(--color-primary)',
                borderRadius: 4,
              }}
            >
              <p style={{ fontWeight: 600 }}>请对照答案，给自己的回忆程度打分</p>
              <p style={{ marginTop: 'var(--space-xs)', fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                简答题无法自动判分，需要你自评后才会计入复习进度。
              </p>
            </div>
          ) : (
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
            </div>
          )}

          {/* 参考答案：等待自评时必须可见，否则用户无从对照打分 */}
          {needsSelfAssessment && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <p>
                <strong>参考答案:</strong> {result?.correct_answer}
              </p>
              {result?.explanation && (
                <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                  <strong>解析:</strong> {result.explanation}
                </p>
              )}
            </div>
          )}

          {/* 四档自评：仅在后端明确请求自评且本次尚未自评时展示 */}
          {needsSelfAssessment && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: 'var(--space-sm)',
                }}
              >
                {selfRatingOptions.map(opt => (
                  <button
                    key={opt.quality}
                    className="btn self-rating-btn"
                    onClick={() => onSelfRate?.(opt.quality)}
                    disabled={selfRatingSubmitting}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'flex-start',
                      gap: 2,
                      padding: 'var(--space-sm) var(--space-md)',
                      border: `1px solid ${opt.color}`,
                      borderLeft: `4px solid ${opt.color}`,
                      borderRadius: 6,
                      background: 'transparent',
                      color: 'inherit',
                      cursor: selfRatingSubmitting ? 'wait' : 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <span style={{ fontWeight: 600, color: opt.color }}>{opt.label}</span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      {opt.hint}
                    </span>
                  </button>
                ))}
              </div>
              {selfRatingSubmitting && (
                <p style={{ marginTop: 'var(--space-xs)', fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                  正在记录自评...
                </p>
              )}
              {onSkipSelfRate && !selfRatingSubmitting && (
                <p style={{ marginTop: 'var(--space-xs)', textAlign: 'right' }}>
                  <button
                    className="btn btn-link"
                    onClick={onSkipSelfRate}
                    style={{
                      background: 'none', border: 'none', padding: 0,
                      color: 'var(--color-text-secondary)', cursor: 'pointer',
                      fontSize: '0.85rem', textDecoration: 'underline',
                    }}
                  >
                    跳过自评，先做下一题
                  </button>
                </p>
              )}
            </div>
          )}

          {/* 自评结果确认：让用户看到自己的打分确实生效了 */}
          {selfRated && result?.self_rating != null && (
            <div
              style={{
                fontSize: '0.9rem',
                padding: 'var(--space-sm)',
                background: 'var(--color-bg)',
                borderRadius: 4,
                marginBottom: 'var(--space-md)',
              }}
            >
              已按自评「
              {selfRatingOptions.find(o => o.quality === result.self_rating)?.label
                ?? `quality=${result.self_rating}`}
              」记录，复习进度已更新。
            </div>
          )}

          {/* SM-2 信息（Review 页展示）
              等待自评时 next_review_at 为 null、调度未推进，显示"下次复习"是假信息，
              故此时只在已给出自评后才展示。 */}
          {showSm2Info && result?.sm2 && !needsSelfAssessment && (
            <div style={{
              fontSize: '0.85rem',
              color: 'var(--color-text-secondary)',
              padding: 'var(--space-sm)',
              background: 'var(--color-bg)',
              borderRadius: 4,
              marginBottom: 'var(--space-md)',
            }}>
              下次复习: {result.sm2.interval} 天后 | EF: {result.sm2.easiness_factor} | 评分: {result.quality}
              {result.grading_method && (
                <> | 判分方式: {gradingMethodLabels[result.grading_method] ?? result.grading_method}</>
              )}
            </div>
          )}

          {/* 下一题按钮
              等待自评时禁用：此刻调度尚未推进，直接进入下一题会让这道题
              永远停在未完成状态（见 docs/overhaul-plan.md 阶段 1.4）。 */}
          <div style={{ textAlign: 'right' }}>
            <button
              className="btn btn-primary"
              onClick={onNext}
              disabled={needsSelfAssessment}
              title={needsSelfAssessment ? '请先完成自评' : undefined}
            >
              {nextButtonText ?? (isLast ? '完成复习' : '下一题')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

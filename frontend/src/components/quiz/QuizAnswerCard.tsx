/**
 * @file 共享答题卡片组件
 * @description 抽取 Review / QuickReview / TodayLearn 三页重复的题目卡片：
 * 类型/难度标签、题目内容、选择题/填空题/简答题作答区、提交按钮、
 * 判分反馈、语义判分明细、调度依据、原文语境、下一题按钮。
 * 行为差异通过 props 参数化；提交/下一题的竞态锁由页面 handleSubmit 内实现
 * （submittingRef），本组件仅透传 submitting 禁用状态，见 docs/decisions.md#F-23。
 */
import { type ReactNode } from 'react'
import type { SubmitAnswerResponse } from '../../api/client'
import SourceContext from './SourceContext'
// 四档自评控件与卡片复习页共用（5.12：两条复习流程的统一件）
import SelfRatingButtons, { selfRatingLabel } from './SelfRatingButtons'
import {
  questionTypeLabels,
  difficultyLabels,
  difficultyColors,
  gradingMethodLabels,
  ratingLabels,
  verdictLabels,
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
  /** 卡片与笔记 ID：用于"原文语境"（阶段 3.13） */
  card_id?: string
  note_id?: string
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
  /**
   * 语义判分开关的当前值（阶段 3.5，仅简答题有意义）
   *
   * ⚠️ 状态**必须由页面持有**，不能藏在本组件里：回车提交的处理函数在页面上
   * （`onKeyDown` → `handleSubmit`），若开关只在卡片内部，用户勾了框再按回车
   * 就会静默按"不判分"提交 —— "选了但没生效"是最难被发现的一类不一致。
   */
  semanticGrading?: boolean
  onToggleSemanticGrading?: (enabled: boolean) => void
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
  semanticGrading = false,
  onToggleSemanticGrading,
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

  // 语义判分明细：null 表示"本次没有判分"，与"判分过但没发现问题"是两回事
  const detail = result?.grading_detail ?? null
  const missingPoints = detail?.missing_points ?? []
  const misconceptions = detail?.misconceptions ?? []
  const verdictMeta = detail ? verdictLabels[detail.verdict] : undefined
  const verdictLabel = verdictMeta?.label ?? '已判分'
  const verdictColor = verdictMeta?.color ?? 'var(--color-primary)'

  const ratingLabel = result?.sm2?.rating ? ratingLabels[result.sm2.rating] : ''
  // 首次复习的 predicted_retention 恒为 1（"从未复习过，必然想得起来"），
  // 显示成"预测还能想起 100%"没有信息量，反而像在敷衍。只在 <1 时展示。
  const rawRetention = result?.sm2?.predicted_retention
  const showRetention = typeof rawRetention === 'number' && rawRetention < 0.999

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

          {/* 语义判分开关（阶段 3.5 的前端入口；仅简答题有意义）
              默认关闭的理由见后端 SubmitAnswerRequest.use_semantic_grading：
              判分在提交的同步路径上调外部 LLM，会给每次提交叠加一次往返延迟，
              而两阶段流程本来就以用户自评为主评分来源。
              这里把选择权交给用户，而不是替他决定"慢一点但更准"。 */}
          {onToggleSemanticGrading && quiz.question_type === 'short_answer' && (
            <label
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 'var(--space-xs)',
                marginTop: 'var(--space-sm)',
                fontSize: '0.85rem',
                color: 'var(--color-text-secondary)',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={semanticGrading}
                onChange={e => onToggleSemanticGrading(e.target.checked)}
                disabled={submitting}
                style={{ marginTop: 3 }}
              />
              <span>
                让 AI 先判一次，指出遗漏与误解
                <br />
                <span style={{ fontSize: '0.8rem' }}>
                  需要联网调用模型，提交会慢几秒；不勾选则直接由你自评。
                </span>
              </span>
            </label>
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

          {/* 语义判分明细（阶段 3.5 的落地处）
              这里展示的是"缺了哪一点 / 误解了哪一点"，而不是一个分数 ——
              分数无法校准、也没有指导价值。`grading_detail` 为 null 表示
              本次**没有**判分（未请求/判分失败/已自评），此时整块不渲染；
              绝不能渲染成"没有发现问题"。 */}
          {detail && (
            <div
              style={{
                marginBottom: 'var(--space-md)',
                padding: 'var(--space-sm) var(--space-md)',
                background: 'var(--color-bg)',
                borderLeft: `3px solid ${verdictColor}`,
                borderRadius: 4,
                fontSize: '0.9rem',
              }}
            >
              <p style={{ fontWeight: 600, color: verdictColor }}>
                AI 判分：{verdictLabel}
              </p>
              {detail.reason && (
                <p style={{ marginTop: 'var(--space-xs)' }}>{detail.reason}</p>
              )}
              {missingPoints.length > 0 && (
                <p style={{ marginTop: 'var(--space-xs)' }}>
                  <strong>遗漏：</strong>
                  <ul style={{ margin: '4px 0 0 1.2em', padding: 0 }}>
                    {missingPoints.map((point, i) => <li key={i}>{point}</li>)}
                  </ul>
                </p>
              )}
              {misconceptions.length > 0 && (
                <p style={{ marginTop: 'var(--space-xs)' }}>
                  <strong>误解：</strong>
                  <ul style={{ margin: '4px 0 0 1.2em', padding: 0 }}>
                    {misconceptions.map((point, i) => <li key={i}>{point}</li>)}
                  </ul>
                </p>
              )}
              {missingPoints.length === 0 && misconceptions.length === 0 && (
                <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                  没有发现遗漏或误解。
                </p>
              )}
            </div>
          )}

          {/* 原文语境（阶段 3.13）
              答错之后用户最想做的就是回原文看一眼，而改造前这里只有
              「正确答案」四个字。
              **不在 `needsSelfAssessment` 时隐藏**：等待自评时用户正在
              对照参考答案回忆，原文段落恰恰是最有用的对照材料。 */}
          <SourceContext cardId={quiz.card_id} noteId={quiz.note_id} />

          {/* 四档自评：仅在后端明确请求自评且本次尚未自评时展示。
              控件本身与卡片复习页共用（SelfRatingButtons），差异只有
              "跳过自评"这个逃生口：答题复习有（这里是两阶段提交的第二阶段，
              库中已有占位记录），卡片复习没有（那里的自评就是提交本身）。 */}
          {needsSelfAssessment && (
            <SelfRatingButtons
              onRate={quality => onSelfRate?.(quality)}
              submitting={selfRatingSubmitting}
              onSkip={onSkipSelfRate}
            />
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
              已按自评「{selfRatingLabel(result.self_rating)}」记录，复习进度已更新。
            </div>
          )}

          {/* 调度依据（阶段 3.6 / 3.9）
              改造前这里写着 `下次复习: 6 天后 | EF: 2.5 | 评分: 4` ——
              `EF` 是 SM-2 的旋钮，用户看不懂，而且换 FSRS 之后它已经
              不再参与调度（现在由难度 D 桥接而来）。改为展示真正决定
              间隔的两个量：**复习前预测的回忆概率**与**评分档位**。 */}
          {showSm2Info && result?.sm2 && !needsSelfAssessment && (
            <div style={{
              fontSize: '0.85rem',
              color: 'var(--color-text-secondary)',
              padding: 'var(--space-sm)',
              background: 'var(--color-bg)',
              borderRadius: 4,
              marginBottom: 'var(--space-md)',
            }}>
              下次复习: {result.sm2.interval} 天后 | 评分: {result.quality}
              {ratingLabel && <> | 档位: {ratingLabel}</>}
              {showRetention && (
                <>
                  {' | '}
                  复习前预测还能想起: {Math.round((result.sm2.predicted_retention ?? 0) * 100)}%
                </>
              )}
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

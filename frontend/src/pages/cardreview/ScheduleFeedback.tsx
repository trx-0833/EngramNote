/**
 * @file 卡片复习的调度依据面板
 *
 * ## 为什么单独成一个模块
 *
 * 这一段是贯穿阶段 3.6 / 3.7 / 3.9 的那条线的用户可见面：**只给一个
 * "6 天后"等于要求用户盲信调度器**。所以自评之后要把"凭什么排到 N 天后"
 * 摆出来：复习前预测的回忆概率 R、记忆强度 S、难度 D，以及掌握度的变化。
 * 它是卡片复习页里信息密度最高、也最容易被误改的一块，独立出来便于测试。
 *
 * ## 与答题复习侧的"调度依据"为什么不是同一个组件
 *
 * 答题复习展示的是另一个响应结构（`SubmitAnswerResponse.sm2`：间隔 / 评分档位 /
 * 判分方式），而这里展示的是 `CardReviewSubmitResponse` 独有的 S / D 与掌握度，
 * 两边没有可直接共用的字段集合。硬做成一个组件只能靠一堆可选字段，
 * 反而会让"哪条流程显示什么"变得看不出。共用的部分（四档自评控件本身）
 * 已经收在 `components/quiz/SelfRatingButtons.tsx`。
 */
import type { CardReviewSubmitResponse } from '../../api/review'
import { selfRatingLabel } from '../../components/quiz/SelfRatingButtons'

interface ScheduleFeedbackProps {
  result: CardReviewSubmitResponse
  /** 复习**前**的掌握度（0-100）：面板要展示 "掌握度 57 → 63" 的变化 */
  previousMastery: number
}

/** 格式化到"月日"（本地时区）；空值与非法值都给占位，不显示 NaN */
function formatDue(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

export default function ScheduleFeedback({ result, previousMastery }: ScheduleFeedbackProps) {
  return (
    <div
      style={{
        padding: 'var(--space-md)',
        background: 'var(--color-bg)',
        borderLeft: `3px solid ${result.is_correct ? '#2d8a56' : '#c0392b'}`,
        borderRadius: 4,
        marginBottom: 'var(--space-md)',
        fontSize: '0.9rem',
      }}
    >
      {/* 措辞与答题复习侧的"已按自评「…」记录"统一：同一个事件，
          用户在两条路径上不该读到两句不同的话。 */}
      <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
        已按自评「{selfRatingLabel(result.quality)}」记录
      </p>
      <p>
        下次复习: <strong>{result.interval_days} 天后</strong>
        {result.next_review_at && <> （{formatDue(result.next_review_at)}）</>}
      </p>
      {typeof result.predicted_retention === 'number'
        && result.predicted_retention < 0.999 && (
        <p>
          复习前模型认为你还能想起:
          {' '}<strong>{Math.round(result.predicted_retention * 100)}%</strong>
          {' '}—— 越接近遗忘，这次答对后间隔涨得越多
        </p>
      )}
      {(result.stability != null || result.difficulty != null) && (
        <p style={{ color: 'var(--color-text-secondary)' }}>
          记忆强度 {result.stability != null ? `${result.stability.toFixed(1)} 天` : '—'}
          {' · '}
          难度 {result.difficulty != null ? result.difficulty.toFixed(1) : '—'}
          {' · '}
          掌握度 {previousMastery.toFixed(0)} → {result.mastery_level.toFixed(0)}
        </p>
      )}
    </div>
  )
}

/**
 * @file 四档自评控件（5.12：两条复习流程的统一件）
 *
 * ## 为什么要有它
 *
 * 答题复习（`QuizAnswerCard`）与卡片复习（`CardReview`）此前各写了一份
 * **逐行相同**的四档按钮：同一份 `selfRatingOptions`、同一个
 * `self-rating-btn` 类名、同一套内联样式。两份并行实现必然漂移 ——
 * 本项目的 `statusClass` 就是这么漂移的（overhaul-plan §2.8 F-13）：
 * 一页改了档位说明，另一页不会跟着改。
 *
 * ## 两条流程里真正一致的是什么
 *
 * 档位语义（0/3/4/5）、按钮外观、"提交中禁用 + 正在记录自评"的反馈、
 * 自评达成后的措辞：这是**同一个交互**。用户在两条路径上做的自评
 * 会写进同一套调度，所以看到的也必须是同一件东西。
 *
 * ## 刻意保留的差异（不要顺手抹平）
 *
 * 1. `onSkip` 只有答题复习会传。那里的自评是两阶段提交的**第二阶段**
 *    （库中已有一条 `grading_method='ungraded'` 的占位记录），跳过只是
 *    不推进调度，是"自评请求持续失败时把人从这一题放出去"的逃生口
 *    （见 `hooks/useSelfRating.ts` 的 `skipRating`）。卡片复习是**单阶段**
 *    提交，自评就是提交本身："跳过"等于什么都没提交，卡片会永远停在
 *    未复习状态，而界面上看不出来 —— 所以那里没有这个入口。
 * 2. `prompt` 只有卡片复习会传（"刚才想得起来吗？"）。答题复习的同类
 *    提示是判分横幅里的"请对照答案，给自己的回忆程度打分"，因为它同时
 *    承担"简答题无法自动判分"的解释，与这里的一行提示不是同一件事。
 */
import type { ReactNode } from 'react'
import { selfRatingOptions } from '../../utils/labels'

/**
 * 把 SM-2 quality 分翻译成用户看到的档位名
 *
 * 两条流程都要在"自评生效"的反馈里回显档位名。各写一遍
 * `selfRatingOptions.find(...) ?? `quality=${q}`` 的话，回退文案
 * 迟早会有一页忘记写。
 */
export function selfRatingLabel(quality: number): string {
  return selfRatingOptions.find(o => o.quality === quality)?.label ?? `quality=${quality}`
}

interface SelfRatingButtonsProps {
  /** 用户点选四档之一；quality 为 SM-2 分值 0/3/4/5 */
  onRate: (quality: number) => void
  /** 提交中：禁用全部按钮，防连点重复推进调度 */
  submitting?: boolean
  /** 四档上方的提示语（可选，见文件头"刻意保留的差异"） */
  prompt?: ReactNode
  /** 跳过自评的逃生口（可选，只有答题复习能传，见文件头） */
  onSkip?: () => void
}

export default function SelfRatingButtons({
  onRate,
  submitting = false,
  prompt,
  onSkip,
}: SelfRatingButtonsProps) {
  return (
    <div style={{ marginBottom: 'var(--space-md)' }}>
      {prompt && (
        <p style={{ fontWeight: 600, marginBottom: 'var(--space-sm)' }}>{prompt}</p>
      )}

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
        gap: 'var(--space-sm)',
      }}>
        {selfRatingOptions.map(opt => (
          <button
            key={opt.quality}
            className="btn self-rating-btn"
            onClick={() => onRate(opt.quality)}
            disabled={submitting}
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
              cursor: submitting ? 'wait' : 'pointer',
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

      {submitting && (
        <p style={{ marginTop: 'var(--space-xs)', fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
          正在记录自评...
        </p>
      )}

      {onSkip && !submitting && (
        <p style={{ marginTop: 'var(--space-xs)', textAlign: 'right' }}>
          <button
            className="btn btn-link"
            onClick={onSkip}
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
  )
}

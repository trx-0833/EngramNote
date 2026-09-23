/**
 * @file 四档自评控件（5.12：两条复习流程的统一件）
 *
 * ## 为什么要有它
 *
 * 答题复习（`QuizAnswerCard`）与卡片复习（`CardReview`）此前各写了一份
 * **逐行相同**的四档按钮：同一份 `selfRatingOptions`、同一个
 * `self-rating-btn` 类名（现已随本组件迁入 `SelfRatingButtons.module.css`
 * 并哈希化，见 overhaul-plan 5.6）、同一套内联样式。
 * 两份并行实现必然漂移 ——
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
 *
 * ## 批次 E6 的改动：四档固定底部横排
 *
 * 原先这一块的布局与外观**全在一组内联样式里**（`display: grid` +
 * `repeat(auto-fit, minmax(140px, 1fr))`），也就是说宽度一变，"几档一行"
 * 就跟着变 —— 1250px 宽时四档一行，窄一点就掉成三档、两档。
 * 复习时每张卡都要做一次自评，档位位置**跳动**会直接变成误点。
 * 现在改成：桌面恒为四档等宽一横排，窄屏才两行（见模块文件末尾的两档），
 * 整块用 `position: sticky; bottom: 0` 钉在视口底部。
 *
 * 只有 `border` / `borderLeft` / 档位文字色**仍然内联**：它们的值逐档取自
 * `selfRatingOptions[].color`，那是 `utils/labels.ts` 里的字面量（不是 CSS
 * 变量，理由见那边的注释）。搬进样式表只会变成"同一套色在两个地方各写一遍"。
 */
import type { ReactNode } from 'react';
import { selfRatingOptions } from '../../utils/labels';
import styles from './SelfRatingButtons.module.css';

/**
 * 把 SM-2 quality 分翻译成用户看到的档位名
 *
 * 两条流程都要在"自评生效"的反馈里回显档位名。各写一遍
 * `selfRatingOptions.find(...) ?? `quality=${q}`` 的话，回退文案
 * 迟早会有一页忘记写。
 */
export function selfRatingLabel(quality: number): string {
  return selfRatingOptions.find((o) => o.quality === quality)?.label ?? `quality=${quality}`;
}

interface SelfRatingButtonsProps {
  /** 用户点选四档之一；quality 为 SM-2 分值 0/3/4/5 */
  onRate: (quality: number) => void;
  /** 提交中：禁用全部按钮，防连点重复推进调度 */
  submitting?: boolean;
  /** 四档上方的提示语（可选，见文件头"刻意保留的差异"） */
  prompt?: ReactNode;
  /** 跳过自评的逃生口（可选，只有答题复习能传，见文件头） */
  onSkip?: () => void;
}

export default function SelfRatingButtons({
  onRate,
  submitting = false,
  prompt,
  onSkip,
}: SelfRatingButtonsProps) {
  return (
    <div className={styles.selfRatingDock}>
      {prompt && <p className={styles.selfRatingPrompt}>{prompt}</p>}

      <div className={styles.selfRatingRow}>
        {selfRatingOptions.map((opt) => (
          <button
            key={opt.quality}
            className={`btn ${styles.selfRatingBtn}`}
            onClick={() => onRate(opt.quality)}
            disabled={submitting}
            // 逐档的边框色与左竖条：值来自 utils/labels.ts 的单一数据源，
            // 刻意留在 tsx（见文件头最后一段）
            style={{
              border: `1px solid ${opt.color}`,
              borderLeft: `4px solid ${opt.color}`,
            }}
          >
            <span className={styles.selfRatingLabel} style={{ color: opt.color }}>
              {opt.label}
            </span>
            <span className={styles.selfRatingHint}>{opt.hint}</span>
          </button>
        ))}
      </div>

      {submitting && <p className={styles.selfRatingPending}>正在记录自评...</p>}

      {onSkip && !submitting && (
        <p className={styles.selfRatingSkip}>
          <button className={`btn btn-link ${styles.selfRatingSkipBtn}`} onClick={onSkip}>
            跳过自评，先做下一题
          </button>
        </p>
      )}
    </div>
  );
}

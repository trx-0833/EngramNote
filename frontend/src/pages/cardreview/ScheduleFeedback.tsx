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
import type { CardReviewSubmitResponse } from '../../api/review';
import { useSelfRatingLabel } from '../../components/quiz/SelfRatingButtons';
import { useTranslation, type MessageKey } from '../../i18n';

interface ScheduleFeedbackProps {
  result: CardReviewSubmitResponse;
  /** 复习**前**的掌握度（0-100）：面板要展示 "掌握度 57 → 63" 的变化 */
  previousMastery: number;
}

/**
 * 格式化到"月日"（本地时区）；空值与非法值都给占位，不显示 NaN
 *
 * ⚠️ 模块级函数调不了 hook，所以 `t` 由调用方传进来（i18n 阶段 3）。
 */
function formatDue(
  value: string | null | undefined,
  t: (key: MessageKey, params?: Record<string, string | number>) => string,
): string {
  if (!value) return t('scheduleFeedback.empty');
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return t('scheduleFeedback.empty');
  return t('scheduleFeedback.monthDay', { month: d.getMonth() + 1, day: d.getDate() });
}

export default function ScheduleFeedback({ result, previousMastery }: ScheduleFeedbackProps) {
  const { t } = useTranslation();
  const labelOfSelfRating = useSelfRatingLabel();
  return (
    <div
      style={{
        padding: 'var(--space-md)',
        background: 'var(--color-bg)',
        // A4：改用令牌。原来这里的绿是 a11y 压深**之前**的 #2d8a56，
        // 与 --color-success(#25714a) 并不是同一个颜色 —— 而这条竖条
        // 与卡片里的成功文案表达的是同一件事，不该有两个绿。
        borderLeft: `3px solid var(${result.is_correct ? '--color-success' : '--color-error'})`,
        borderRadius: 4,
        marginBottom: 'var(--space-md)',
        fontSize: '0.9rem',
      }}
    >
      {/* 措辞与答题复习侧的"已按自评「…」记录"统一：同一个事件，
          用户在两条路径上不该读到两句不同的话。 */}
      <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
        {t('scheduleFeedback.recorded', { rating: labelOfSelfRating(result.quality) })}
      </p>
      <p>
        {t('scheduleFeedback.nextReview')}
        <strong>{t('scheduleFeedback.daysLater', { count: result.interval_days })}</strong>
        {result.next_review_at && <> （{formatDue(result.next_review_at, t)}）</>}
      </p>
      {typeof result.predicted_retention === 'number' && result.predicted_retention < 0.999 && (
        <p>
          {t('scheduleFeedback.predictedRetention')}
          <strong>{Math.round(result.predicted_retention * 100)}%</strong>{' '}
          {t('scheduleFeedback.retentionNote')}
        </p>
      )}
      {(result.stability != null || result.difficulty != null) && (
        <p style={{ color: 'var(--color-text-secondary)' }}>
          {t('scheduleFeedback.stability', {
            value:
              result.stability != null ? result.stability.toFixed(1) : t('scheduleFeedback.empty'),
          })}
          {' · '}
          {t('scheduleFeedback.difficulty', {
            value:
              result.difficulty != null
                ? result.difficulty.toFixed(1)
                : t('scheduleFeedback.empty'),
          })}
          {' · '}
          {t('scheduleFeedback.mastery', {
            from: previousMastery.toFixed(0),
            to: result.mastery_level.toFixed(0),
          })}
        </p>
      )}
    </div>
  );
}

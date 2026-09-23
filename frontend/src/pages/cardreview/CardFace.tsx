/**
 * @file 卡片复习的正面/背面（提示与正文）
 *
 * ## 为什么把"翻面"单独成一个模块
 *
 * 这是两条复习流程**必须不同**的地方。答题复习里题目就是提示、答案是提交后
 * 由后端给出的 `correct_answer`；卡片复习没有可判分的答案，正文既是答案也是
 * 用户要回忆的对象。所以：
 *
 * - 正文默认隐藏，点"显示答案"才展开（先回忆再核对，否则自评数据是假的）；
 * - 展开后自评按钮才出现（`CardReview` 负责这一步）。
 *
 * 把它独立出来，是为了让"默认隐藏"这条约束有一个明确的归属和测试落点 ——
 * 它是这一页存在意义的全部，而它只是 `revealed` 一个布尔值。
 */
import type { DueCard } from '../../api/review';
import { cardTypeLabels } from '../../utils/labels';

interface CardFaceProps {
  card: DueCard;
  /** 是否已翻面；false 时只渲染提示语与"显示答案" */
  revealed: boolean;
  onReveal: () => void;
}

export default function CardFace({ card, revealed, onReveal }: CardFaceProps) {
  const typeLabel = cardTypeLabels[card.card_type] || card.card_type;

  return (
    <>
      {/* 卡片头部：类型 / 章节 / 复习元信息 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-md)',
          gap: 'var(--space-sm)',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
          <span
            style={{
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: '0.8rem',
              background: 'var(--color-primary)',
              color: '#fff',
            }}
          >
            {typeLabel}
          </span>
          {card.chapter_title && (
            <span style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
              {card.chapter_title}
            </span>
          )}
        </div>
        <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
          已复习 {card.review_count} 次
          {card.review_count > 0 && <> · 间隔 {card.interval_days} 天</>}
          {card.lapses > 0 && <> · 遗忘 {card.lapses} 次</>}
        </span>
      </div>

      {/* 正面：标题（提示）。
          `h2` 而不是 `h3`：这一页的页面标题是 `ReviewProgress` 渲染的 h1
          （"卡片复习"），中间不存在任何层级 —— h1 → h3 是跳级，
          axe 判 `heading-order`。字号写在下面（1.15rem），
          标题级别与视觉大小本来就是两件事，改级别**一个像素都不动**。 */}
      <h2 style={{ fontSize: '1.15rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>
        {card.title}
      </h2>

      {!revealed ? (
        <>
          <p
            style={{
              color: 'var(--color-text-secondary)',
              fontSize: '0.9rem',
              marginBottom: 'var(--space-md)',
            }}
          >
            先在心里把这张卡的内容讲一遍，再看答案 —— 直接翻面等于看答案， 这次自评就不准了。
          </p>
          <div style={{ textAlign: 'right' }}>
            <button className="btn btn-primary" onClick={onReveal}>
              显示答案
            </button>
          </div>
        </>
      ) : (
        <>
          {/* 背面：正文 + 摘要 */}
          <div
            style={{
              padding: 'var(--space-md)',
              background: 'var(--color-bg)',
              borderRadius: 6,
              lineHeight: 1.8,
              whiteSpace: 'pre-wrap',
              marginBottom: 'var(--space-md)',
            }}
          >
            {card.content}
          </div>
          {card.summary && (
            <p
              style={{
                fontSize: '0.9rem',
                color: 'var(--color-text-secondary)',
                marginBottom: 'var(--space-md)',
              }}
            >
              <strong>摘要:</strong> {card.summary}
            </p>
          )}
        </>
      )}
    </>
  );
}

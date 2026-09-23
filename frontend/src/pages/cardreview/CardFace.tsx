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
 *
 * ## 批次 E3：卡面有了"正面 / 背面"两种外观
 *
 * 借鉴 `docs/visual-symbol-research.md` §C3 的「知识卡片」行（Heptabase 卡片正面）：
 * **正面白底细边框、背面转金色淡底**；明确**不抄 3D 翻转**。
 * 两种外观由一个模块类切换（`.cardFace` / `.cardFaceBack`），翻面动效是
 * 一次不超过 200ms 的淡入，时长走 `--duration-fast` 令牌，并且在
 * `prefers-reduced-motion: reduce` 下整条关掉 —— 细节与理由写在
 * `CardFace.module.css` 的文件头，样式值不在 tsx 里重复一遍。
 */
import type { DueCard } from '../../api/review';
import { cardTypeLabels } from '../../utils/labels';
// 类名由 CSS Modules 哈希化后从 styles 取（overhaul-plan 5.6 的约定）：
// 不再写全局类名字面量，否则搬迁到模块的规则会因为选择器对不上而静默失效
import styles from './CardFace.module.css';

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
      {/* 卡片头部：类型 / 章节 / 复习元信息（在卡面之外，它是"关于这张卡"的台账） */}
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

      {/*
        卡面（批次 E3）：正面白底细边框、背面金色淡底。

        `key` 是翻面动效的开关：正反两面的 JSX 都是同位置的 `<div>`，
        没有 key 的话 React 会**复用同一个 DOM 节点**、只换 class，
        `cardFaceReveal` 这条动画就只在首次挂载时播一次（CSS 动画只在
        元素被插入时启动）。换 key 就是重新挂载，于是每次翻面都播一遍，
        而且"两张面"在 React 眼里也确实是两个不同的东西。
      */}
      <div
        key={revealed ? 'back' : 'front'}
        className={revealed ? `${styles.cardFace} ${styles.cardFaceBack}` : styles.cardFace}
      >
        {/* 正面：标题（提示）。
            `h2` 而不是 `h3`：这一页的页面标题是 `ReviewProgress` 渲染的 h1
            （"卡片复习"），中间不存在任何层级 —— h1 → h3 是跳级，
            axe 判 `heading-order`。字号写在下面（1.15rem），
            标题级别与视觉大小本来就是两件事，改级别**一个像素都不动**。
            批次 E3 只把它挪进卡面，级别与字号一个字没改。 */}
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
            {/* 背面：正文 + 摘要。
                正文块**不再自带底色**（批次 E3 删掉了 `background: var(--color-bg)`）：
                整张面这一侧已经转成金色淡底，块内再叠一层米白会把它压成灰调。
                行高 1.8 与 `whiteSpace: pre-wrap`（保留正文里的换行）**逐字保留**，
                只是跟着这条规则一起搬进了模块（`.cardFaceContent`）。 */}
            <div className={styles.cardFaceContent}>{card.content}</div>
            {card.summary && (
              /* `marginTop` 而不是 `marginBottom`：摘要是卡面里的最后一段，
                 底下由卡面的内边距留白即可 —— 再挂一条下外边距等于把
                 "卡面"与"卡面外的按钮"之间的间距算了两遍。 */
              <p
                style={{
                  fontSize: '0.9rem',
                  color: 'var(--color-text-secondary)',
                  marginTop: 'var(--space-md)',
                }}
              >
                <strong>摘要:</strong> {card.summary}
              </p>
            )}
          </>
        )}
      </div>
    </>
  );
}

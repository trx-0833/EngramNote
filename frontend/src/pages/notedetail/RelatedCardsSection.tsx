/**
 * @file 关联知识卡片区域
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 最多展示 6 张、超出时给"查看全部"入口，卡片类型颜色/标签继续从
 * `utils/labels.ts` 读取（见 docs/decisions.md#F-28）。
 * 显示条件（`relatedCards.length > 0`）由页面侧判断。
 */
import type { KnowledgeCard } from '../../api/client'
import { cardTypeColors, cardTypeLabels } from '../../utils/labels'

/** 首页最多展示的卡片数，超出时提供"查看全部"入口 */
const MAX_VISIBLE_CARDS = 6

interface RelatedCardsSectionProps {
  /** 关联知识卡片 */
  relatedCards: KnowledgeCard[]
  /** 笔记 ID（"查看全部"跳转用） */
  noteId: string | undefined
  /** 跳转路由 */
  navigate: (path: string) => void
}

/** 关联知识卡片区域 */
export default function RelatedCardsSection({
  relatedCards,
  noteId,
  navigate,
}: RelatedCardsSectionProps) {
  return (
    <div style={{ marginTop: 'var(--space-lg)' }}>
      <h2 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>关联知识卡片 ({relatedCards.length})</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--space-md)' }}>
        {relatedCards.slice(0, MAX_VISIBLE_CARDS).map(card => (
          <div
            key={card.id}
            className="card card-hover"
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/cards/${card.id}`)}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-xs)' }}>
              <strong style={{ fontSize: '0.9rem' }}>{card.title}</strong>
              <span style={{
                fontSize: '0.7rem', padding: '1px 6px', borderRadius: '9999px',
                // 卡片类型颜色/标签统一从 utils/labels.ts 读取，见 docs/decisions.md#F-28
                background: cardTypeColors[card.card_type] || '#6b7280',
                color: 'white', whiteSpace: 'nowrap',
              }}>
                {cardTypeLabels[card.card_type] || card.card_type}
              </span>
            </div>
            <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
              {card.content}
            </p>
          </div>
        ))}
      </div>
      {relatedCards.length > MAX_VISIBLE_CARDS && (
        <button
          className="btn btn-secondary"
          style={{ marginTop: 'var(--space-sm)', fontSize: '0.85rem' }}
          onClick={() => navigate(`/cards?note_id=${noteId}`)}
        >
          查看全部 {relatedCards.length} 张卡片
        </button>
      )}
    </div>
  )
}

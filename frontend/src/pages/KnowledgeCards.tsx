/**
 * @file 知识卡片列表页面
 * @description 展示当前用户所有知识卡片，按所属笔记分组，每组可折叠/展开
 */
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getKnowledgeCards, type KnowledgeCard } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { cardTypeLabels, cardTypeColors } from '../utils/labels'

interface NoteGroup {
  note_id: string
  note_title: string
  cards: KnowledgeCard[]
}

export default function KnowledgeCards() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [groups, setGroups] = useState<NoteGroup[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set())
  const noteId = searchParams.get('note_id') || undefined

  useEffect(() => {
    fetchCards()
  }, [noteId])

  async function fetchCards() {
    setLoading(true)
    try {
      const data = await getKnowledgeCards(1, 999, noteId)
      const grouped = groupByNote(data.items)
      setGroups(grouped)
      setTotal(data.total)
      setExpandedNotes(new Set(grouped.map(g => g.note_id)))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  function groupByNote(cards: KnowledgeCard[]): NoteGroup[] {
    const map = new Map<string, KnowledgeCard[]>()
    for (const card of cards) {
      const list = map.get(card.note_id) || []
      list.push(card)
      map.set(card.note_id, list)
    }
    return Array.from(map.entries())
      .map(([noteId, cards]) => ({
        note_id: noteId,
        note_title: cards[0].note_title || '未命名笔记',
        cards,
      }))
      .sort((a, b) => {
        const aTime = a.cards[0]?.created_at ?? ''
        const bTime = b.cards[0]?.created_at ?? ''
        return bTime.localeCompare(aTime)
      })
  }

  function toggleGroup(noteId: string) {
    setExpandedNotes(prev => {
      const next = new Set(prev)
      if (next.has(noteId)) {
        next.delete(noteId)
      } else {
        next.add(noteId)
      }
      return next
    })
  }

  return (
    <div style={{ padding: 'var(--space-lg) 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>知识卡片</h1>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>共 {total} 张卡片</span>
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchCards} />
      ) : groups.length === 0 ? (
        <EmptyState message="暂无知识卡片" description="请先上传笔记并触发理解管道" />
      ) : (
        <div>
          {groups.map(group => (
            <div key={group.note_id} style={{ marginBottom: 'var(--space-md)' }}>
              <div
                onClick={() => toggleGroup(group.note_id)}
                style={{
                  cursor: 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: 'var(--space-sm) var(--space-md)',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '8px',
                  marginBottom: expandedNotes.has(group.note_id) ? 'var(--space-sm)' : 0,
                  transition: 'margin-bottom 0.15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                  <span style={{ fontSize: '0.8rem', userSelect: 'none' }}>
                    {expandedNotes.has(group.note_id) ? '▼' : '▶'}
                  </span>
                  <strong>{group.note_title}</strong>
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    ({group.cards.length} 张卡片)
                  </span>
                </div>
              </div>

              {expandedNotes.has(group.note_id) && (
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
                  gap: 'var(--space-md)',
                }}>
                  {group.cards.map(card => (
                    <div
                      key={card.id}
                      className="card"
                      style={{ cursor: 'pointer', transition: 'box-shadow 0.2s' }}
                      onClick={() => navigate(`/cards/${card.id}`)}
                      onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--shadow-md)')}
                      onMouseLeave={e => (e.currentTarget.style.boxShadow = 'var(--shadow-sm)')}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-sm)' }}>
                        <h3 style={{ fontSize: '1rem', fontWeight: 600, flex: 1, marginRight: 'var(--space-sm)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {card.title}
                        </h3>
                        <span
                          style={{
                            fontSize: '0.75rem',
                            padding: '2px 8px',
                            borderRadius: '9999px',
                            background: cardTypeColors[card.card_type] || '#6b7280',
                            color: 'white',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {cardTypeLabels[card.card_type] || card.card_type}
                        </span>
                      </div>
                      <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)', lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {card.content}
                      </p>
                      {card.chapter_title && (
                        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-sm)' }}>
                          章节: {card.chapter_title}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
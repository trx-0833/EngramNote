/**
 * @file 知识卡片详情页面
 * @description 展示单张知识卡片的完整内容、原始出处和关联题目，支持编辑和删除
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getKnowledgeCard, updateKnowledgeCard, deleteKnowledgeCard, getQuestions, type KnowledgeCard, type QuizItem } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorDisplay from '../components/ErrorDisplay'
import { cardTypeLabels, difficultyLabels, questionTypeLabels } from '../utils/labels'

export default function CardDetail() {
  const { cardId } = useParams<{ cardId: string }>()
  const navigate = useNavigate()
  const [card, setCard] = useState<KnowledgeCard | null>(null)
  const [questions, setQuestions] = useState<QuizItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showAnswer, setShowAnswer] = useState<Record<string, boolean>>({})

  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')

  useEffect(() => {
    if (!cardId) return
    fetchCard()
  }, [cardId])

  async function fetchCard() {
    setLoading(true)
    try {
      const data = await getKnowledgeCard(cardId!)
      setCard(data)
      setEditTitle(data.title)
      setEditContent(data.content)
      const qData = await getQuestions(1, 20, data.note_id)
      const related = qData.items.filter(q => q.card_id === cardId)
      setQuestions(related)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSave() {
    if (!card) return
    try {
      const updated = await updateKnowledgeCard(card.id, { title: editTitle, content: editContent })
      setCard(updated)
      setEditing(false)
    } catch (err) {
      alert(err instanceof Error ? err.message : '保存失败')
    }
  }

  async function handleDelete() {
    if (!card || !confirm('确定删除此知识卡片？关联的题目也将被删除。')) return
    try {
      await deleteKnowledgeCard(card.id)
      navigate('/cards')
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  function handleCancelEdit() {
    if (!card) return
    setEditTitle(card.title)
    setEditContent(card.content)
    setEditing(false)
  }

  if (loading) return <LoadingSpinner />
  if (error || !card) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorDisplay message={error || '卡片不存在'} />
        <button className="btn btn-secondary" onClick={() => navigate('/cards')}>返回列表</button>
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--space-lg) 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-lg)' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>{card.title}</h1>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          {editing ? (
            <>
              <button className="btn btn-primary" onClick={handleSave}>保存</button>
              <button className="btn btn-secondary" onClick={handleCancelEdit}>取消</button>
            </>
          ) : (
            <>
              <button className="btn btn-secondary" onClick={() => setEditing(true)}>编辑</button>
              <button className="btn btn-danger" onClick={handleDelete}>删除</button>
            </>
          )}
          <button className="btn btn-secondary" onClick={() => navigate('/cards')}>返回</button>
        </div>
      </div>

      {/* 来源笔记链接 */}
      {card.note_id && (
        <p style={{ marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>
          来源笔记：
          <span style={{ color: 'var(--color-primary)', cursor: 'pointer' }} onClick={() => navigate(`/notes/${card.note_id}`)}>
            {card.note_title || '查看笔记'}
          </span>
        </p>
      )}

      {/* 卡片信息 */}
      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          <span className="badge">{cardTypeLabels[card.card_type] || card.card_type}</span>
          {card.chapter_title && <span>章节: {card.chapter_title}</span>}
        </div>
        {editing ? (
          <div>
            <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', display: 'block', marginBottom: 'var(--space-xs)' }}>标题</label>
            <input
              className="input"
              value={editTitle}
              onChange={e => setEditTitle(e.target.value)}
              style={{ width: '100%', marginBottom: 'var(--space-md)' }}
            />
            <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', display: 'block', marginBottom: 'var(--space-xs)' }}>内容</label>
            <textarea
              className="input"
              value={editContent}
              onChange={e => setEditContent(e.target.value)}
              style={{ width: '100%', minHeight: '200px', resize: 'vertical' }}
            />
          </div>
        ) : (
          <div style={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{card.content}</div>
        )}
      </div>

      {/* 章节摘要 */}
      {card.summary && !editing && (
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>章节摘要</h3>
          <p style={{ color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>{card.summary}</p>
        </div>
      )}

      {/* 原始出处 */}
      {card.source_text && !editing && (
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>原始出处</h3>
          <blockquote style={{ borderLeft: '3px solid var(--color-primary)', paddingLeft: 'var(--space-md)', color: 'var(--color-text-secondary)', lineHeight: 1.6, margin: 0 }}>
            {card.source_text}
          </blockquote>
        </div>
      )}

      {/* 关联题目 */}
      {questions.length > 0 && !editing && (
        <div className="card">
          <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>关联题目 ({questions.length})</h3>
          {questions.map((q, idx) => (
            <div key={q.id} style={{ padding: 'var(--space-md)', borderBottom: idx < questions.length - 1 ? '1px solid var(--color-border)' : 'none' }}>
              <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-sm)', fontSize: '0.75rem' }}>
                <span className="badge">{questionTypeLabels[q.question_type] || q.question_type}</span>
                <span className="badge">{difficultyLabels[q.difficulty] || q.difficulty}</span>
              </div>
              <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>{idx + 1}. {q.question}</p>
              {q.options && (
                <div style={{ marginBottom: 'var(--space-sm)', paddingLeft: 'var(--space-md)' }}>
                  {JSON.parse(q.options).map((opt: string, i: number) => (
                    <p key={i} style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>{opt}</p>
                  ))}
                </div>
              )}
              <button
                className="btn btn-secondary"
                style={{ fontSize: '0.8rem' }}
                onClick={() => setShowAnswer(prev => ({ ...prev, [q.id]: !prev[q.id] }))}
              >
                {showAnswer[q.id] ? '隐藏答案' : '显示答案'}
              </button>
              {showAnswer[q.id] && (
                <div style={{ marginTop: 'var(--space-sm)', padding: 'var(--space-sm)', background: 'var(--color-surface)', borderRadius: '4px' }}>
                  <p style={{ color: 'var(--color-success)', fontWeight: 500 }}>答案: {q.answer}</p>
                  {q.explanation && <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>解析: {q.explanation}</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
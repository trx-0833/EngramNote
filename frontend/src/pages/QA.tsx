/**
 * @file 智能问答页面
 * @description 基于 RAG 的智能问答，支持跨笔记检索和引用来源展示
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { askQuestion, type AnswerSource } from '../api/client'
import EmptyState from '../components/EmptyState'

interface QARecord {
  question: string;
  answer: string;
  sources: AnswerSource[];
  provider: string;
}

export default function QA() {
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState<QARecord[]>([])

  async function handleAsk() {
    if (!question.trim()) return
    setLoading(true)
    setError('')
    try {
      const result = await askQuestion(question.trim())
      setHistory(prev => [{ question: result.question, answer: result.answer, sources: result.sources, provider: result.provider }, ...prev])
      setQuestion('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '问答失败')
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleAsk()
    }
  }

  return (
    <div className="page-enter" style={{ maxWidth: '800px', margin: '0 auto' }}>
      <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem', marginBottom: 'var(--space-lg)' }}>智能问答</h1>

      {/* 输入区域 */}
      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <input
            type="text"
            className="input"
            placeholder="输入你的问题，AI 将基于你的笔记内容回答..."
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            style={{ flex: 1 }}
          />
          <button className="btn btn-primary" onClick={handleAsk} disabled={loading || !question.trim()}>
            {loading ? '思考中...' : '提问'}
          </button>
        </div>
        {error && <p style={{ color: 'var(--color-error)', marginTop: 'var(--space-sm)', fontSize: '0.875rem' }}>{error}</p>}
      </div>

      {/* 问答历史 */}
      {history.length === 0 ? (
        <EmptyState message="输入问题开始问答" description="AI 将基于你所有笔记的内容进行回答" />
      ) : (
        history.map((record) => (
          <div key={record.question + record.answer.slice(0, 20)} style={{ marginBottom: 'var(--space-md)' }}>
            {/* 问题 */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 'var(--space-sm)' }}>
              <div className="qa-user-bubble">
                {record.question}
              </div>
            </div>
            {/* 回答 */}
            <div className="card qa-ai-card">
              <div style={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{record.answer}</div>
              {/* 引用来源 */}
              {record.sources.length > 0 && (
                <div style={{ marginTop: 'var(--space-md)', paddingTop: 'var(--space-sm)', borderTop: '1px solid var(--color-border)' }}>
                  <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-xs)' }}>引用来源:</p>
                  {record.sources.map((source, sIdx) => (
                    <div
                      key={sIdx}
                      style={{ fontSize: '0.8rem', color: 'var(--color-primary)', cursor: 'pointer', marginBottom: '2px' }}
                      onClick={() => navigate(`/notes/${source.note_id}`)}
                    >
                      📄 {source.note_title}
                      {source.chapter_title && ` > ${source.chapter_title}`}
                    </div>
                  ))}
                </div>
              )}
              {record.provider && (
                <p style={{ fontSize: '0.7rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)', textAlign: 'right' }}>
                  由 {record.provider === 'glm' ? 'GLM' : 'DeepSeek'} 提供支持
                </p>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}

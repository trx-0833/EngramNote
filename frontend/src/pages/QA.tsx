/**
 * @file 智能问答页面
 * @description 基于 RAG 的智能问答，支持跨笔记检索和引用来源展示
 * 使用 SSE 流式响应实现实时答案展示，首字到达前显示"AI 正在思考..."
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { askQuestionStream, type AnswerSource } from '../api/client'
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
    const currentQuestion = question.trim()

    // 创建一个临时记录，答案会逐步填充
    const tempRecord: QARecord = { question: currentQuestion, answer: '', sources: [], provider: '' }
    setHistory(prev => [tempRecord, ...prev])
    setQuestion('')

    // 标记是否已收到首个 token，用于切换"思考中"与"流式渲染"状态
    let firstTokenReceived = false

    try {
      const stream = await askQuestionStream(currentQuestion)
      // askQuestionStream 返回 ReadableStream，直接获取读取器
      const reader = stream.getReader()
      if (!reader) {
        throw new Error('无法读取流式响应')
      }

      const decoder = new TextDecoder()
      // 缓冲区，用于处理跨 chunk 的不完整行
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        // stream: true 表示可能还有后续 chunk，避免多字节字符被截断
        buffer += decoder.decode(value, { stream: true })

        // 按双换行分割事件（SSE 协议中空行分隔事件）
        const events = buffer.split('\n\n')
        // 最后一个可能不完整，保留到下一次循环处理
        buffer = events.pop() || ''

        for (const eventBlock of events) {
          const lines = eventBlock.split('\n')
          let eventType = ''
          let dataStr = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7)
            else if (line.startsWith('data: ')) dataStr = line.slice(6)
          }
          if (!eventType || !dataStr) continue
          const data = JSON.parse(dataStr)

          if (eventType === 'token') {
            // 首个 token 到达时，切换出"思考中"状态
            if (!firstTokenReceived) {
              firstTokenReceived = true
              setLoading(false)
            }
            // 追加 token 到当前答案（最新一条历史记录）
            setHistory(prev => {
              if (prev.length === 0) return prev
              const updated = [...prev]
              updated[0] = { ...updated[0], answer: updated[0].answer + (data.content || '') }
              return updated
            })
          } else if (eventType === 'sources') {
            // 保存当前答案的引用来源与提供商
            setHistory(prev => {
              if (prev.length === 0) return prev
              const updated = [...prev]
              updated[0] = { ...updated[0], sources: data.sources || [], provider: data.provider || '' }
              return updated
            })
          } else if (eventType === 'done') {
            // 流式响应结束
            return
          } else if (eventType === 'error') {
            throw new Error(data.message || '流式响应错误')
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '问答失败')
      // 如果首字未到，移除空白的临时记录，避免列表中出现空白气泡
      if (!firstTokenReceived) {
        setHistory(prev => (prev.length > 0 ? prev.slice(1) : prev))
      }
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
        history.map((record, idx) => {
          // 第一条记录且处于"思考中"阶段（loading=true 且尚未收到任何 token）
          const isThinking = idx === 0 && loading && record.answer === ''
          return (
            <div key={record.question + idx + record.answer.slice(0, 20)} style={{ marginBottom: 'var(--space-md)' }}>
              {/* 问题 */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 'var(--space-sm)' }}>
                <div className="qa-user-bubble">
                  {record.question}
                </div>
              </div>
              {/* 回答 */}
              <div className="card qa-ai-card">
                {isThinking ? (
                  // 思考中状态：首字到达前显示
                  <div style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic', lineHeight: 1.8 }}>
                    AI 正在思考...
                  </div>
                ) : record.answer ? (
                  // 流式渲染：答案实时增长
                  <div style={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{record.answer}</div>
                ) : (
                  <div style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic', lineHeight: 1.8 }}>
                    AI 正在思考...
                  </div>
                )}
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
          )
        })
      )}
    </div>
  )
}

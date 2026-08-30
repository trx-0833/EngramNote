/**
 * @file 智能问答页面
 * @description 基于 RAG 的智能问答，支持跨笔记检索和引用来源展示
 * 使用 SSE 流式响应实现实时答案展示，首字到达前显示"AI 正在思考..."
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { askQuestionStream, type AnswerSource } from '../api/client'
import EmptyState from '../components/EmptyState'

interface QARecord {
  question: string;
  answer: string;
  sources: AnswerSource[];
  provider: string;
  retrievalStatus?: string;
}

export default function QA() {
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState<QARecord[]>([])

  // 持有当前流式请求的 AbortController 与 reader，用于新问题中止旧流、停止生成与卸载清理
  const abortRef = useRef<AbortController | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)

  // 组件卸载时中止进行中的流式请求并取消读取
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      const reader = readerRef.current
      readerRef.current = null
      abortRef.current = null
      if (reader) {
        void reader.cancel().catch(() => {})
      }
    }
  }, [])

  /** 中止当前流式请求（新问题发起前 / 点击停止生成时调用） */
  function stopActiveStream() {
    abortRef.current?.abort()
    const reader = readerRef.current
    readerRef.current = null
    abortRef.current = null
    if (reader) {
      void reader.cancel().catch(() => {})
    }
  }

  function handleStop() {
    stopActiveStream()
  }

  async function handleAsk() {
    if (!question.trim()) return
    // 新问题发起前中止上一个未完成的流，避免旧 token 污染新答案
    stopActiveStream()

    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setStreaming(true)
    setError('')
    const currentQuestion = question.trim()

    // 创建一个临时记录，答案会逐步填充
    const tempRecord: QARecord = { question: currentQuestion, answer: '', sources: [], provider: '' }
    setHistory(prev => [tempRecord, ...prev])
    setQuestion('')

    // 标记是否已收到首个 token，用于切换"思考中"与"流式渲染"状态
    let firstTokenReceived = false
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null

    try {
      const stream = await askQuestionStream(currentQuestion, controller.signal)
      // askQuestionStream 返回 ReadableStream，直接获取读取器
      const r = stream.getReader()
      if (!r) {
        throw new Error('无法读取流式响应')
      }
      reader = r
      readerRef.current = reader

      const decoder = new TextDecoder()
      // 缓冲区，用于处理跨 chunk 的不完整行
      let buffer = ''

      while (true) {
        const { done, value } = await r.read()
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

          if (eventType === 'meta') {
            // 首事件：记录检索降级状态，供渲染降级提示
            setHistory(prev => {
              if (prev.length === 0) return prev
              const updated = [...prev]
              updated[0] = { ...updated[0], retrievalStatus: data.retrieval_status || '' }
              return updated
            })
          } else if (eventType === 'token') {
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
      if (controller.signal.aborted) {
        // 用户主动停止：保留已生成文本；若尚无任何文本则移除空气泡
        if (!firstTokenReceived) {
          setHistory(prev => (prev.length > 0 ? prev.slice(1) : prev))
        }
      } else {
        setError(err instanceof Error ? err.message : '问答失败')
        // 如果首字未到，移除空白的临时记录，避免列表中出现空白气泡
        if (!firstTokenReceived) {
          setHistory(prev => (prev.length > 0 ? prev.slice(1) : prev))
        }
      }
    } finally {
      // 仅当 ref 仍指向当前请求时才清理，避免误清新流
      if (abortRef.current === controller) abortRef.current = null
      if (readerRef.current === reader) readerRef.current = null
      setLoading(false)
      setStreaming(false)
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
            disabled={streaming}
            style={{ flex: 1 }}
          />
          {streaming ? (
            <button className="btn btn-danger" onClick={handleStop}>停止生成</button>
          ) : (
            <button className="btn btn-primary" onClick={handleAsk} disabled={!question.trim()}>提问</button>
          )}
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
                {/* 检索降级提示：向量服务不可用时走关键词检索 */}
                {record.retrievalStatus === 'bm25_only' && (
                  <p style={{ fontSize: '0.75rem', color: 'var(--color-warning)', marginTop: 'var(--space-sm)' }}>
                    已降级为关键词检索（向量服务不可用）
                  </p>
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

/**
 * @file 智能问答页面
 * @description 基于 RAG 的智能问答，支持跨笔记检索和引用来源展示
 * 使用 SSE 流式响应实现实时答案展示，首字到达前显示"AI 正在思考..."
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { askQuestionStream, type AnswerSource } from '../api/client'
import { parseSSEStream } from '../utils/sse'
import { useThrottledStream } from '../hooks/useStreamAnswer'
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

  // 流式 token 的节流累积器：把每个 token 的 setState 合并为按间隔批量刷新，
  // 避免长回答时"每个 token 全量重算 Markdown"造成的 O(n²) 卡顿（§2.8 F-10）
  const { push: pushDelta, flush: flushDelta, reset: resetDelta } = useThrottledStream()

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
    // 清空上一轮可能残留的节流缓冲
    resetDelta()

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

      // 用共享的规范 SSE 解析器替换原先内联的手写解析：
      // 手写版只保留最后一行 data:、不识别 \r\n、且 JSON.parse 无保护
      // （一个截断分片就会终结整段回答）。见 utils/sse.ts 的说明。
      await parseSSEStream(
        stream,
        {
          onEvent: (eventType, data) => {
            const payload = (data ?? {}) as Record<string, unknown>

            if (eventType === 'meta') {
              // 首事件：记录检索降级状态，供渲染降级提示
              setHistory(prev => {
                if (prev.length === 0) return prev
                const updated = [...prev]
                updated[0] = { ...updated[0], retrievalStatus: String(payload.retrieval_status || '') }
                return updated
              })
              return
            }

            if (eventType === 'token') {
              // 首个 token 到达时，切换出"思考中"状态
              if (!firstTokenReceived) {
                firstTokenReceived = true
                setLoading(false)
              }
              // 节流提交：把 token 累积到缓冲区，按间隔批量刷新。
              // 原先每个 token 一次 setState + 一次全文 Markdown 重算，
              // 长回答时是 O(n²)（见 §2.8 F-10）。
              pushDelta(String(payload.content || ''), delta => {
                setHistory(prev => {
                  if (prev.length === 0) return prev
                  const updated = [...prev]
                  updated[0] = { ...updated[0], answer: updated[0].answer + delta }
                  return updated
                })
              })
              return
            }

            if (eventType === 'sources') {
              setHistory(prev => {
                if (prev.length === 0) return prev
                const updated = [...prev]
                updated[0] = {
                  ...updated[0],
                  sources: (payload.sources as AnswerSource[]) || [],
                  provider: String(payload.provider || ''),
                }
                return updated
              })
              return
            }

            if (eventType === 'done') {
              flushDelta(delta => {
                setHistory(prev => {
                  if (prev.length === 0) return prev
                  const updated = [...prev]
                  updated[0] = { ...updated[0], answer: updated[0].answer + delta }
                  return updated
                })
              })
              return
            }

            if (eventType === 'error') {
              throw new Error(String(payload.message || '流式响应错误'))
            }
          },
          onParseError: (raw, err) => {
            // 坏事件跳过而非中断整段回答
            console.warn('[QA] 无法解析的 SSE 事件已跳过:', raw.slice(0, 120), err)
          },
        },
        controller.signal,
      )
      // 部分实现不发 done 事件，流自然结束也要提交剩余文本
      flushDelta(delta => {
        setHistory(prev => {
          if (prev.length === 0) return prev
          const updated = [...prev]
          updated[0] = { ...updated[0], answer: updated[0].answer + delta }
          return updated
        })
      })
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
            // key 必须**稳定**：原实现把 answer 的前 20 字符拼进 key，
            // 于是答案每增长约 20 字符 key 就变化一次，React 会把整条记录
            // （问题气泡 + AI 卡片 + 引用列表）卸载重建 —— 焦点、文本选区、
            // 滚动位置全部丢失，并成倍放大流式渲染开销（§2.8 F-10）。
            // question + idx 在本列表内已唯一且不随流式内容变化。
            <div key={`${record.question}#${idx}`} style={{ marginBottom: 'var(--space-md)' }}>
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
                    {record.sources.map((source, sIdx) => {
                      // 阶段 2.7：带定位信息才能跳到原文那一段。
                      // 缺 char_start/char_end 时**不提供跳转** —— 跳到笔记开头
                      // 会让用户以为"引用就是开头那段"，比不给跳转更容易误导。
                      const canJump =
                        typeof source.char_start === 'number' &&
                        typeof source.char_end === 'number' &&
                        source.char_end > source.char_start
                      const params = new URLSearchParams()
                      if (canJump) {
                        // view=clean：chunk 偏移是基于 clean 副本算的，
                        // 若页面显示 original 副本，偏移对不上，会高亮错位置
                        params.set('view', 'clean')
                        params.set('cs', String(source.char_start))
                        params.set('ce', String(source.char_end))
                      }
                      const href = params.toString()
                        ? `/notes/${source.note_id}?${params}`
                        : `/notes/${source.note_id}`
                      // 引用编号与回答里的 [N] 对应（后端保证同一次遍历产出）
                      return (
                        <div
                          key={source.chunk_id || sIdx}
                          style={{
                            fontSize: '0.8rem',
                            color: canJump ? 'var(--color-primary)' : 'var(--color-text-secondary)',
                            cursor: 'pointer',
                            marginBottom: '2px',
                          }}
                          title={canJump ? '点击跳到原文该段落' : '该引用缺少定位信息，只能打开笔记'}
                          onClick={() => navigate(href)}
                        >
                          [{sIdx + 1}] 📄 {source.note_title}
                          {source.heading_path
                            ? ` > ${source.heading_path}`
                            : source.chapter_title
                              ? ` > ${source.chapter_title}`
                              : ''}
                          {!canJump && <span style={{ fontSize: '0.7rem' }}>（无定位）</span>}
                        </div>
                      )
                    })}
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

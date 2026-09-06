/**
 * @file AI 提问浮层组件
 * @description 在笔记阅读页选中文本后弹出的 AI 提问面板：
 * 1. 输入模式：可编辑问题输入框（预填选中文本），确认后提交
 * 2. 回答模式：SSE 流式展示 AI 回答，支持停止生成 / 重新提问 / 关闭
 *
 * 数据流：
 * 选中文本 + 选区前后上下文 → POST /api/notes/{noteId}/ask/stream
 * （SSE 事件：meta(provider) → token... → done / error）
 */
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react'
import { askNoteQuestionStream } from '../api/notes'
import { renderMarkdown } from '../utils/markdown'

interface NoteAskPanelProps {
  noteId: string
  noteTitle: string
  initialText: string
  contextBefore: string
  contextAfter: string
  viewMode: 'original' | 'clean'
  pos: { x: number; y: number }
  onClose: () => void
}

const PANEL_WIDTH = 480

export default function NoteAskPanel({
  noteId,
  initialText,
  contextBefore,
  contextAfter,
  viewMode,
  pos,
  onClose,
}: NoteAskPanelProps) {
  const [question, setQuestion] = useState(initialText)
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [provider, setProvider] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [mode, setMode] = useState<'input' | 'answer'>('input')
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  /** 窗口当前位置（初始为选区旁，拖拽后可自由移动） */
  const [panelPos, setPanelPos] = useState(pos)
  /** 窗口相对锚点的方位（上方/下方），拖拽过程中保持恒定避免跳动 */
  const [panelAbove, setPanelAbove] = useState(pos.y > 180)

  // 打开即聚焦输入框并全选选中文本，方便直接覆盖为自定义问题
  useEffect(() => {
    textAreaRef.current?.focus()
    textAreaRef.current?.select()
  }, [])

  // 组件卸载时中止未完成的流式请求
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      const reader = readerRef.current
      readerRef.current = null
      if (reader) {
        void reader.cancel().catch(() => {})
      }
    }
  }, [])

  /** 中止当前流式请求 */
  function stopActiveStream() {
    abortRef.current?.abort()
    const reader = readerRef.current
    readerRef.current = null
    abortRef.current = null
    if (reader) {
      void reader.cancel().catch(() => {})
    }
  }

  /** 提交问题并发起流式请求 */
  async function handleAsk() {
    const q = question.trim()
    if (!q) return
    // 新请求前中止上一个未完成的流，避免旧 token 污染新答案
    stopActiveStream()

    const controller = new AbortController()
    abortRef.current = controller
    setSubmittedQuestion(q)
    setAnswer('')
    setProvider('')
    setError('')
    setLoading(true)
    setStreaming(true)
    setMode('answer')

    // 标记是否已收到首个 token，用于切换"思考中"与"流式渲染"状态
    let firstTokenReceived = false
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null

    try {
      const stream = await askNoteQuestionStream(
        noteId,
        {
          question: q,
          selected_text: initialText,
          context_before: contextBefore,
          context_after: contextAfter,
          view_mode: viewMode,
        },
        controller.signal
      )
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
        buffer += decoder.decode(value, { stream: true })

        // 按双换行分割事件（SSE 协议中空行分隔事件）
        const events = buffer.split('\n\n')
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
            // 首事件：记录 LLM 提供商标识
            setProvider(data.provider || '')
          } else if (eventType === 'token') {
            // 首个 token 到达时，切换出"思考中"状态
            if (!firstTokenReceived) {
              firstTokenReceived = true
              setLoading(false)
            }
            // 追加 token 到当前答案
            setAnswer(prev => prev + (data.content || ''))
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
        // 用户主动停止：保留已生成文本
      } else {
        setError(err instanceof Error ? err.message : '问答失败')
        // 如果首字未到，清空答案，避免展示空白回答
        if (!firstTokenReceived) {
          setAnswer('')
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

  /** 重新提问：回到输入模式 */
  function handleReask() {
    setMode('input')
    setAnswer('')
    setError('')
    setQuestion(submittedQuestion)
    // 下一次渲染后再聚焦并全选
    requestAnimationFrame(() => {
      textAreaRef.current?.focus()
      textAreaRef.current?.select()
    })
  }

  /** 关闭面板：中止流并通知父组件 */
  function handleClose() {
    stopActiveStream()
    onClose()
  }

  /** 标题栏拖拽：自由移动窗口（fixed 定位，滚动页面时窗口保持视口内） */
  function handleDragStart(e: ReactMouseEvent) {
    if (e.button !== 0) return
    e.preventDefault()
    setPanelAbove(panelPos.y > 180)
    const startX = e.clientX
    const startY = e.clientY
    const origX = panelPos.x
    const origY = panelPos.y

    function onMove(ev: MouseEvent) {
      setPanelPos({ x: origX + (ev.clientX - startX), y: origY + (ev.clientY - startY) })
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
    }
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // 定位：默认显示在选区上方，贴近顶部时显示在下方；拖拽后按用户摆放位置固定
  const panelStyle: CSSProperties = {
    position: 'fixed',
    left: Math.min(Math.max(panelPos.x, PANEL_WIDTH / 2 + 8), window.innerWidth - PANEL_WIDTH / 2 - 8),
    top: panelPos.y,
    transform: panelAbove ? 'translate(-50%, calc(-100% - 10px))' : 'translate(-50%, 12px)',
    width: PANEL_WIDTH,
    maxWidth: 'calc(100vw - 24px)',
    maxHeight: '60vh',
    overflowY: 'auto',
    zIndex: 1100,
  }

  const selectedTextPreview = initialText.length > 60 ? `${initialText.slice(0, 60)}...` : initialText

  return (
    <div className="ask-ai-panel" style={panelStyle}>
      <div className="ask-ai-header" onMouseDown={handleDragStart} title="按住拖动窗口">
        <span className="ask-ai-title">AI 提问</span>
        <button className="ask-ai-close" onClick={handleClose} title="关闭">✕</button>
      </div>
      <div className="ask-ai-body">
        {mode === 'input' ? (
          <>
            <textarea
              ref={textAreaRef}
              className="ask-ai-input"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              placeholder="输入你的问题..."
              rows={3}
            />
            <p className="ask-ai-selected-hint">基于当前笔记选中文本：「{selectedTextPreview}」</p>
            <div className="ask-ai-actions">
              <button className="btn btn-secondary" onClick={handleClose}>取消</button>
              <button className="btn btn-primary" onClick={handleAsk} disabled={!question.trim()}>提问</button>
            </div>
          </>
        ) : (
          <>
            <div className="ask-ai-question">{submittedQuestion}</div>
            {loading && <div className="ask-ai-thinking">AI 正在思考...</div>}
            {answer && (
              <div
                className="ask-ai-answer markdown-body"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(answer) }}
              />
            )}
            {error && <p className="ask-ai-error">{error}</p>}
            <div className="ask-ai-actions">
              {streaming ? (
                <button className="btn btn-danger" onClick={stopActiveStream}>停止生成</button>
              ) : (
                <>
                  <button className="btn btn-secondary" onClick={handleReask}>重新提问</button>
                  <button className="btn btn-primary" onClick={handleClose}>关闭</button>
                </>
              )}
            </div>
            {provider && (
              <p className="ask-ai-provider">由 {provider === 'glm' ? 'GLM' : 'DeepSeek'} 提供支持</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
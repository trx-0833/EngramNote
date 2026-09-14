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
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react'
import { askNoteQuestionStream } from '../api/notes'
import type { AnswerSource } from '../api/client'
import { renderMarkdown } from '../utils/markdown'
import { highlightCitation } from '../utils/citationJump'
import { parseSSEStream } from '../utils/sse'
import { useThrottledStream } from '../hooks/useStreamAnswer'

interface NoteAskPanelProps {
  noteId: string
  noteTitle: string
  initialText: string
  contextBefore: string
  contextAfter: string
  viewMode: 'original' | 'clean'
  /**
   * 当前显示的 Markdown 源文（阶段 2.7）
   *
   * 引用跳转要用它在渲染后的正文里定位段落 —— 只有容器 DOM 是不够的，
   * 因为后端给的是**源文**里的字符下标，必须用同一份源文切片才能得到
   * 可搜索的指纹。
   */
  markdown: string
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
  markdown,
  pos,
  onClose,
}: NoteAskPanelProps) {
  const [question, setQuestion] = useState(initialText)
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [provider, setProvider] = useState('')
  const [sources, setSources] = useState<AnswerSource[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [mode, setMode] = useState<'input' | 'answer'>('input')
  const textAreaRef = useRef<HTMLTextAreaElement>(null)

  // 流式 token 的节流累积器（见 §2.8 F-10：原实现为每 token 一次
  // setState + 渲染层全文重跑 Markdown，长回答时为 O(n²)）
  const { push: pushDelta, flush: flushDelta, reset: resetDelta } = useThrottledStream()

  // Markdown 只在 answer 真正变化时重算一次。
  // 原先 renderMarkdown(answer) 直接写在 JSX 里，任何一次重渲染
  // （包括滚动、hover、父组件更新）都会重跑完整的 marked + DOMPurify + KaTeX 管道。
  const answerHtml = useMemo(() => (answer ? renderMarkdown(answer) : ''), [answer])
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
    // 清空上一轮可能残留的节流缓冲
    resetDelta()

    const controller = new AbortController()
    abortRef.current = controller
    setSubmittedQuestion(q)
    setAnswer('')
    setProvider('')
    setSources([])
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

      // 共享的规范 SSE 解析器（原内联实现只保留最后一行 data:、不识别 \r\n、
      // JSON.parse 无保护；两份副本还已漂移，见 utils/sse.ts）
      await parseSSEStream(
        stream,
        {
          onEvent: (eventType, data) => {
            const payload = (data ?? {}) as Record<string, unknown>

            if (eventType === 'meta') {
              // 首事件：记录 LLM 提供商标识
              setProvider(String(payload.provider || ''))
              return
            }
            if (eventType === 'token') {
              // 首个 token 到达时，切换出"思考中"状态
              if (!firstTokenReceived) {
                firstTokenReceived = true
                setLoading(false)
              }
              // 节流提交：原先每个 token 一次 setState，且渲染层对全文重跑
              // renderMarkdown（含 KaTeX），长回答时为 O(n²)（§2.8 F-10）
              pushDelta(String(payload.content || ''), delta => {
                setAnswer(prev => prev + delta)
              })
              return
            }
            if (eventType === 'sources') {
              // 与 QA 页保持一致：选段提问同样会返回引用来源
              setSources((payload.sources as AnswerSource[]) || [])
              return
            }
            if (eventType === 'done') {
              flushDelta(delta => setAnswer(prev => prev + delta))
              return
            }
            if (eventType === 'error') {
              throw new Error(String(payload.message || '流式响应错误'))
            }
          },
          onParseError: (raw, err) => {
            console.warn('[NoteAskPanel] 无法解析的 SSE 事件已跳过:', raw.slice(0, 120), err)
          },
        },
        controller.signal,
      )
      // 流自然结束也要提交剩余文本
      flushDelta(delta => setAnswer(prev => prev + delta))
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
  //
  // 夹取区间必须用**面板的实际宽度**算，不能用常量 PANEL_WIDTH：
  // 窄屏（例如 375px）上面板宽度被 maxWidth 压到 351px，若仍按 480 算，
  // 右边界 = 375 - 240 - 8 = 127 小于左边界 248，再叠加 translate(-50%)
  // 就把整个浮层推出屏幕左侧 —— 选段提问在手机上完全够不着
  // （overhaul-plan §2.8 F-16）。宽屏下 panelWidth 恒为 480，行为与改动前一致。
  const panelWidth = Math.min(PANEL_WIDTH, Math.max(window.innerWidth - 24, 0))
  const halfWidth = panelWidth / 2
  const minLeft = halfWidth + 8
  const maxLeft = window.innerWidth - halfWidth - 8
  const panelLeft = maxLeft > minLeft
    ? Math.min(Math.max(panelPos.x, minLeft), maxLeft)
    : window.innerWidth / 2 // 视口比面板还窄：居中，由 maxWidth 保证不溢出

  const panelStyle: CSSProperties = {
    position: 'fixed',
    left: panelLeft,
    top: panelPos.y,
    transform: panelAbove ? 'translate(-50%, calc(-100% - 10px))' : 'translate(-50%, 12px)',
    width: panelWidth,
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
                dangerouslySetInnerHTML={{ __html: answerHtml }}
              />
            )}
            {sources.length > 0 && (
              <div className="ask-ai-sources">
                {sources.map((s, i) => {
                  // 阶段 2.7：面板本来就贴在正文上，所以直接在当前页面里
                  // 定位并高亮，不必跳转路由。定位信息缺失时不提供跳转 ——
                  // 滚到笔记开头会让用户以为"引用就是开头那段"。
                  const canJump =
                    typeof s.char_start === 'number' &&
                    typeof s.char_end === 'number' &&
                    s.char_end > s.char_start
                  return (
                    <span
                      key={s.chunk_id || `${s.note_id}-${i}`}
                      className="ask-ai-source-item"
                      style={{ cursor: canJump ? 'pointer' : 'default' }}
                      title={canJump ? '点击跳到原文该段落' : '该引用缺少定位信息'}
                      onClick={() => {
                        if (!canJump) return
                        // 面板浮在正文上，跳到别的笔记没有意义 → 只处理当前笔记
                        if (s.note_id !== noteId) return
                        const result = highlightCitation(
                          document.querySelector('.markdown-body') as HTMLElement | null,
                          markdown,
                          s.char_start,
                          s.char_end,
                        )
                        if (!result.highlighted) {
                          setError('未能定位到引用段落（正文可能尚未加载或排版有差异）')
                        }
                      }}
                    >
                      [{i + 1}] 📄 {s.note_title}
                      {s.heading_path
                        ? ` > ${s.heading_path}`
                        : s.chapter_title
                          ? ` > ${s.chapter_title}`
                          : ''}
                    </span>
                  )
                })}
              </div>
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
import { useCallback, useRef } from 'react'
import { parseSSEStream } from '../utils/sse'

/**
 * 流式回答的节流累积器
 *
 * 背景（见 docs/overhaul-plan.md §2.8 F-10）：
 * 此前每个 `token` 事件都直接 `setState` 追加，并在 render 里对**全文**
 * 调一次 `renderMarkdown`（marked + DOMPurify + 遍历文本节点做 KaTeX）。
 * 于是第 k 个 token 要重新解析长度约 k 的全文，整体是 **O(n²)**：
 * 长回答（含公式/代码块）时页面明显卡顿、输入框失去响应。
 *
 * 本 hook 把 token 累积在 ref 里，按固定间隔（默认 80ms）批量提交一次 state，
 * 并在结束时强制 flush，保证不漏最后一段文本。
 * 渲染层配合 `useMemo` 后，Markdown 只在提交点重算一次。
 */
export function useThrottledStream(flushIntervalMs = 80) {
  const bufferRef = useRef('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** 追加文本，并按节流间隔提交 */
  const push = useCallback(
    (chunk: string, commit: (text: string) => void) => {
      bufferRef.current += chunk
      if (timerRef.current !== null) return
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        if (bufferRef.current) commit(bufferRef.current)
      }, flushIntervalMs)
    },
    [flushIntervalMs],
  )

  /** 立即提交剩余文本（流结束、出错、或组件卸载前调用） */
  const flush = useCallback((commit: (text: string) => void) => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (bufferRef.current) commit(bufferRef.current)
  }, [])

  /** 清空缓冲（开始新一轮回答时调用） */
  const reset = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    bufferRef.current = ''
  }, [])

  return { push, flush, reset }
}

export interface StreamAnswerOptions {
  /** 接口地址 */
  url: string
  /** 请求体 */
  body: unknown
  /** 请求头（通常含 Authorization） */
  headers?: Record<string, string>
  /** 每批新增文本（已节流） */
  onDelta: (text: string) => void
  /** meta 事件：检索降级状态 / provider */
  onMeta?: (data: { retrieval_status?: string; provider?: string }) => void
  /** sources 事件 */
  onSources?: (data: { sources?: unknown[]; provider?: string }) => void
  /** 完成 */
  onDone?: () => void
  /** 出错（网络或后端 error 事件） */
  onError?: (err: Error) => void
  /** 中止信号 */
  signal?: AbortSignal
}

/**
 * 发起一次 SSE 流式问答请求并消费事件
 *
 * 统一了 QA 页面与选段提问浮层的流式处理，避免两份实现再次漂移。
 */
export async function streamAnswer(
  opts: StreamAnswerOptions,
  pushDelta: (chunk: string, commit: (text: string) => void) => void,
  flush: (commit: (text: string) => void) => void,
): Promise<void> {
  try {
    const resp = await fetch(opts.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
      },
      body: JSON.stringify(opts.body),
      signal: opts.signal,
    })

    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      throw new Error(text || `请求失败: ${resp.status}`)
    }
    if (!resp.body) throw new Error('响应体为空，无法读取流')

    await parseSSEStream(
      resp.body,
      {
        onEvent: (eventType, data) => {
          const payload = (data ?? {}) as Record<string, unknown>
          switch (eventType) {
            case 'meta':
              opts.onMeta?.(payload as { retrieval_status?: string; provider?: string })
              break
            case 'token':
              pushDelta(String(payload.content || ''), opts.onDelta)
              break
            case 'sources':
              opts.onSources?.(payload as { sources?: unknown[]; provider?: string })
              break
            case 'done':
              flush(opts.onDelta)
              opts.onDone?.()
              break
            case 'error':
              throw new Error(String(payload.message || '流式响应错误'))
            default:
              break
          }
        },
        onParseError: (raw, error) => {
          // 坏事件跳过而非中断整段回答
          console.warn('[sse] 无法解析的事件已跳过:', raw.slice(0, 120), error)
        },
      },
      opts.signal,
    )

    // 部分实现不发 done 事件，流自然结束也要提交剩余文本
    flush(opts.onDelta)
    opts.onDone?.()
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') {
      flush(opts.onDelta)
      return
    }
    flush(opts.onDelta)
    opts.onError?.(err instanceof Error ? err : new Error(String(err)))
  }
}

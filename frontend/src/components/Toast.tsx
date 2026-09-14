import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

/**
 * 全局提示层（toast）
 *
 * 背景（见 docs/overhaul-plan.md §2.8 F-8）：
 * 全站有 **49 处 `alert()`、10 处 `confirm()`**，且约 20 处失败被静默吞掉。
 * 后果：
 * 1. `alert` 阻塞主线程、样式不可定制、移动端体验差，同类错误在不同页面
 *    形态不一（有的用 ErrorDisplay、有的用 alert、有的只 console.error）
 * 2. 静默失败最伤用户 —— 例如仪表盘的邮件提醒开关：请求失败后复选框弹回，
 *    没有任何提示，用户以为开关坏了而反复点击
 *
 * 本模块提供统一的 `toast.success/error/info/warning`，并支持一个全局
 * 错误上报入口，供 ErrorBoundary 与请求层调用。
 */

export type ToastKind = 'success' | 'error' | 'info' | 'warning'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
  /** 可选详情（例如后端 error_code / request_id），折叠展示 */
  detail?: string
}

interface ToastContextValue {
  show: (kind: ToastKind, message: string, detail?: string) => void
  success: (message: string, detail?: string) => void
  error: (message: string, detail?: string) => void
  info: (message: string, detail?: string) => void
  warning: (message: string, detail?: string) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const DEFAULT_DURATION_MS = 4000
/** 错误停留更久，便于阅读 */
const ERROR_DURATION_MS = 7000

/**
 * 每种提示的外观。
 *
 * ⚠️ 括号里的是 CSS 变量的**兜底字面量**：正常路径上 `base.css` 一定定义了
 * 这些变量，所以兜底值永远不生效 —— 正因如此它们很容易在改令牌时被漏掉
 * （a11y 修复轮就把 `--color-success` 改成了 `#25714a`、`--color-warning`
 * 改成了 `#936408`）。这里一并跟上，免得哪天变量真的缺失时得到一对
 * 与令牌不一致的旧色值。`border` 只用于 3px 的图标边框（非文字），
 * 不参与 4.5:1 的判据，所以只保证"与令牌一致"，不额外挑值。
 */
const KIND_STYLE: Record<ToastKind, { bg: string; border: string; icon: string }> = {
  success: { bg: 'var(--color-success-bg, #eaf7ef)', border: 'var(--color-success, #25714a)', icon: '✓' },
  error: { bg: 'var(--color-error-bg, #fdecea)', border: 'var(--color-error, #c0392b)', icon: '✕' },
  info: { bg: 'var(--color-bg-subtle, #f5f6f8)', border: 'var(--color-primary, #0f3460)', icon: 'i' },
  warning: { bg: 'var(--color-warning-bg, #fdf6e3)', border: 'var(--color-warning, #936408)', icon: '!' },
}

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    const timer = timersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
    setItems(prev => prev.filter(t => t.id !== id))
  }, [])

  const show = useCallback(
    (kind: ToastKind, message: string, detail?: string) => {
      const id = nextId++
      // 相同内容的消息合并，避免批量失败时刷屏
      setItems(prev => {
        if (prev.some(t => t.kind === kind && t.message === message)) return prev
        return [...prev, { id, kind, message, detail }]
      })
      const duration = kind === 'error' ? ERROR_DURATION_MS : DEFAULT_DURATION_MS
      const timer = setTimeout(() => dismiss(id), duration)
      timersRef.current.set(id, timer)
    },
    [dismiss],
  )

  // 卸载时清理全部计时器，避免对已卸载组件 setState
  useEffect(() => {
    const timers = timersRef.current
    return () => {
      timers.forEach(t => clearTimeout(t))
      timers.clear()
    }
  }, [])

  const value = useMemo<ToastContextValue>(
    () => ({
      show,
      success: (m, d) => show('success', m, d),
      error: (m, d) => show('error', m, d),
      info: (m, d) => show('info', m, d),
      warning: (m, d) => show('warning', m, d),
      dismiss,
    }),
    [show, dismiss],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* 提示容器：固定右上角，不拦截页面交互 */}
      <div
        aria-live="polite"
        aria-atomic="false"
        style={{
          position: 'fixed',
          top: 16,
          right: 16,
          zIndex: 3000,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          maxWidth: 'min(380px, calc(100vw - 32px))',
          pointerEvents: 'none',
        }}
      >
        {items.map(item => {
          const style = KIND_STYLE[item.kind]
          return (
            <div
              key={item.id}
              role={item.kind === 'error' ? 'alert' : 'status'}
              style={{
                pointerEvents: 'auto',
                display: 'flex',
                gap: 10,
                alignItems: 'flex-start',
                padding: '10px 12px',
                borderRadius: 10,
                borderLeft: `4px solid ${style.border}`,
                background: style.bg,
                boxShadow: '0 4px 16px rgba(15, 52, 96, 0.12)',
                fontSize: '0.875rem',
                lineHeight: 1.55,
                color: 'var(--color-text, #1a1a1a)',
              }}
            >
              <span aria-hidden="true" style={{ flexShrink: 0, fontWeight: 700, color: style.border }}>
                {style.icon}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ wordBreak: 'break-word' }}>{item.message}</div>
                {item.detail && (
                  <div
                    style={{
                      marginTop: 4,
                      fontSize: '0.75rem',
                      color: 'var(--color-text-tertiary, #777)',
                      wordBreak: 'break-word',
                    }}
                  >
                    {item.detail}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                aria-label="关闭提示"
                style={{
                  flexShrink: 0,
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: '1rem',
                  lineHeight: 1,
                  color: 'var(--color-text-tertiary, #777)',
                  padding: 2,
                }}
              >
                ×
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

/**
 * 读取 toast API
 *
 * 在 ToastProvider 之外调用时返回一个**降级实现**（打到 console），
 * 这样组件不必担心挂载顺序，也不会因为缺少 Provider 而抛错崩溃 ——
 * 对一个"用来报告错误"的工具来说，自身抛错是最糟的行为。
 */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (ctx) return ctx
  const fallback = (kind: ToastKind) => (message: string, detail?: string) => {
    const line = `[toast:${kind}] ${message}${detail ? ` — ${detail}` : ''}`
    if (kind === 'error') console.error(line)
    else console.warn(line)
  }
  return {
    show: (kind, message, detail) => fallback(kind)(message, detail),
    success: fallback('success'),
    error: fallback('error'),
    info: fallback('info'),
    warning: fallback('warning'),
    dismiss: () => {},
  }
}

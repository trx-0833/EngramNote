import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * 全局错误边界
 *
 * 背景（见 docs/overhaul-plan.md §2.8 F-2）：
 * 本项目此前**没有任何 ErrorBoundary**（全仓 grep 0 命中），而 Markdown 渲染
 * （marked + DOMPurify + KaTeX）是在**渲染阶段同步执行**的重逻辑。
 * 一份含畸形 HTML/LaTeX 的笔记、或一条被截断的 SSE 分片，都会让 React 卸载
 * 整棵树 —— 表现为**整个应用白屏**，而且刷新后回到同一份笔记会再次白屏，
 * 用户无法自救（换页面也没用，因为错误来自渲染而不是路由）。
 *
 * 本组件解决两件事：
 * 1. 捕获子树渲染异常，展示可读的兜底界面而不是白屏
 * 2. 提供 `resetKey`：路由/数据变化时自动清空错误状态，
 *    避免"一旦某条数据触发异常，该页面永久不可用"
 */

interface Props {
  children: ReactNode
  /** 变化时自动重置错误状态（通常传 location.pathname） */
  resetKey?: string
  /** 自定义兜底 UI；不传则用默认界面 */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  error: Error | null
  errorInfo: ErrorInfo | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo })
    // 保留完整堆栈到控制台：兜底 UI 只给用户看摘要，排查仍需堆栈
    console.error('[ErrorBoundary] 渲染异常已捕获:', error, errorInfo)
  }

  componentDidUpdate(prevProps: Props): void {
    // 路由变化或外部要求重置时清空错误 —— 让"坏数据卡死整页"变成一次性故障
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.reset()
    }
  }

  reset = (): void => {
    this.setState({ error: null, errorInfo: null })
  }

  render(): ReactNode {
    const { error, errorInfo } = this.state
    if (!error) return this.props.children

    if (this.props.fallback) return this.props.fallback(error, this.reset)

    return (
      <div
        role="alert"
        style={{
          maxWidth: 720,
          margin: '48px auto',
          padding: '24px 28px',
          border: '1px solid var(--color-border, #e5e5e5)',
          borderRadius: 12,
          background: 'var(--color-surface, #fff)',
          color: 'var(--color-text, #1a1a1a)',
          lineHeight: 1.7,
        }}
      >
        <h2 style={{ margin: '0 0 8px', fontSize: '1.25rem' }}>这个页面出错了</h2>
        <p style={{ margin: '0 0 16px', color: 'var(--color-text-secondary, #666)' }}>
          页面渲染时发生异常，已被错误边界拦截（应用其余部分仍可正常使用）。
          常见原因是笔记内容里的 Markdown/公式语法异常。
        </p>

        <pre
          style={{
            margin: '0 0 16px',
            padding: '12px 14px',
            background: 'var(--color-bg-subtle, #f7f7f8)',
            borderRadius: 8,
            fontSize: '0.8rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 220,
            overflow: 'auto',
          }}
        >
          {error.message || String(error)}
          {errorInfo?.componentStack ? `\n${errorInfo.componentStack.trim()}` : ''}
        </pre>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={this.reset}
            style={{
              padding: '8px 18px',
              borderRadius: 8,
              border: '1px solid var(--color-border, #ddd)',
              background: 'var(--color-surface, #fff)',
              cursor: 'pointer',
              fontSize: '0.9rem',
            }}
          >
            重试
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              padding: '8px 18px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--color-primary, #0f3460)',
              color: '#fff',
              cursor: 'pointer',
              fontSize: '0.9rem',
            }}
          >
            刷新页面
          </button>
        </div>
      </div>
    )
  }
}

export default ErrorBoundary

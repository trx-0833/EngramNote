import { Component, type ErrorInfo, type ReactNode } from 'react';

import { useTranslation } from '../i18n';

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
  children: ReactNode;
  /** 变化时自动重置错误状态（通常传 location.pathname） */
  resetKey?: string;
  /** 自定义兜底 UI；不传则用默认界面 */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * 默认兜底界面 —— **单独抽成函数组件**，而不是写在 `ErrorBoundary.render` 里
 *
 * ## 为什么必须抽出来（i18n 阶段 1）
 *
 * `ErrorBoundary` 是**类组件**（React 只支持类组件做错误边界），而
 * `useTranslation()` 是 hook —— 类组件的 `render` 里调不了。
 *
 * 抽出来之后有两件事同时成立：
 * 1. 它是函数组件，能用 hook 取 `t()`；
 * 2. 语言切换会**重渲染它**（它订阅了 context），于是"出错时切语言"
 *    文案也跟着变 —— 若在类组件里直接读 `getCurrentLocale()`，
 *    拿到的是一个不会触发重渲染的静态值。
 *
 * 样式**逐字未改**（全是内联 style，带 var() 兜底值），只换了文案来源。
 */
function ErrorFallback({
  error,
  errorInfo,
  onReset,
}: {
  error: Error;
  errorInfo: ErrorInfo | null;
  onReset: () => void;
}) {
  const { t } = useTranslation();

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
      <h2 style={{ margin: '0 0 8px', fontSize: '1.25rem' }}>{t('errorBoundary.title')}</h2>
      <p style={{ margin: '0 0 16px', color: 'var(--color-text-secondary, #666)' }}>
        {t('errorBoundary.description')} {t('errorBoundary.hint')}
      </p>

      {/* 技术细节**不翻译**：它是异常消息与组件栈，翻译它只会让排查更难。
          这正是"用户可见内容"与"诊断信息"的边界。 */}
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
          onClick={onReset}
          style={{
            padding: '8px 18px',
            borderRadius: 8,
            border: '1px solid var(--color-border, #ddd)',
            background: 'var(--color-surface, #fff)',
            cursor: 'pointer',
            fontSize: '0.9rem',
          }}
        >
          {t('errorBoundary.retry')}
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
          {t('errorBoundary.reload')}
        </button>
      </div>
    </div>
  );
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo });
    // 保留完整堆栈到控制台：兜底 UI 只给用户看摘要，排查仍需堆栈
    console.error('[ErrorBoundary] 渲染异常已捕获:', error, errorInfo);
  }

  componentDidUpdate(prevProps: Props): void {
    // 路由变化或外部要求重置时清空错误 —— 让"坏数据卡死整页"变成一次性故障
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.reset();
    }
  }

  reset = (): void => {
    this.setState({ error: null, errorInfo: null });
  };

  render(): ReactNode {
    const { error, errorInfo } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return <ErrorFallback error={error} errorInfo={errorInfo} onReset={this.reset} />;
  }
}

export default ErrorBoundary;

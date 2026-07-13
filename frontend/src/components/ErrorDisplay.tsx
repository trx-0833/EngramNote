/**
 * @file 统一错误展示组件
 * @description SVG 图标 + 抖动动画 + 渐变重试按钮
 */
interface ErrorDisplayProps {
  message: string
  onRetry?: () => void
}

export default function ErrorDisplay({ message, onRetry }: ErrorDisplayProps) {
  return (
    <div className="card state-container" style={{ animation: 'shake 0.4s ease, fadeIn 0.4s var(--ease-out-expo)' }}>
      <div className="state-icon">
        <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="32" cy="32" r="20" stroke="var(--color-error)" strokeWidth="2" fill="var(--color-error-light)" />
          <path d="M32 22v12" stroke="var(--color-error)" strokeWidth="2.5" strokeLinecap="round" />
          <circle cx="32" cy="42" r="2" fill="var(--color-error)" />
        </svg>
      </div>
      <p role="alert" style={{ color: 'var(--color-error)', marginBottom: onRetry ? 'var(--space-md)' : 0 }}>
        {message}
      </p>
      {onRetry && (
        <button className="btn btn-primary" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  )
}

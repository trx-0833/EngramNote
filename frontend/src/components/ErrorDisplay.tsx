/**
 * @file 统一错误展示组件
 * @description 全局统一的错误状态展示，带错误信息和可选重试按钮
 */
interface ErrorDisplayProps {
  message: string
  onRetry?: () => void
}

export default function ErrorDisplay({ message, onRetry }: ErrorDisplayProps) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
      <div style={{ fontSize: '2rem', marginBottom: 'var(--space-sm)', opacity: 0.5 }}>⚠️</div>
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

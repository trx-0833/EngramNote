/**
 * @file 统一空数据状态组件
 * @description 全局统一的空数据展示，带提示文字和可选操作按钮
 */
import type { ReactNode } from 'react'

interface EmptyStateProps {
  message: string
  description?: string
  action?: ReactNode
}

export default function EmptyState({ message, description, action }: EmptyStateProps) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
      <div style={{ fontSize: '2rem', marginBottom: 'var(--space-sm)', opacity: 0.5 }}>📭</div>
      <p style={{ color: 'var(--color-text-secondary)', marginBottom: description ? 'var(--space-xs)' : action ? 'var(--space-md)' : 0 }}>
        {message}
      </p>
      {description && (
        <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem', marginBottom: action ? 'var(--space-md)' : 0 }}>
          {description}
        </p>
      )}
      {action}
    </div>
  )
}

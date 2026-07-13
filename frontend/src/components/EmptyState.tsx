/**
 * @file 统一空数据状态组件
 * @description SVG 插画风格图标 + 淡入动画
 */
import type { ReactNode } from 'react'

interface EmptyStateProps {
  message: string
  description?: string
  action?: ReactNode
}

export default function EmptyState({ message, description, action }: EmptyStateProps) {
  return (
    <div className="card state-container">
      <div className="state-icon">
        <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="12" y="16" width="40" height="36" rx="4" stroke="var(--color-border)" strokeWidth="2" fill="var(--color-bg)" />
          <path d="M12 24h40" stroke="var(--color-border)" strokeWidth="2" />
          <circle cx="20" cy="20" r="2" fill="var(--color-accent)" />
          <circle cx="26" cy="20" r="2" fill="var(--color-primary)" opacity="0.5" />
          <circle cx="32" cy="20" r="2" fill="var(--color-primary)" opacity="0.3" />
          <rect x="18" y="30" width="28" height="2" rx="1" fill="var(--color-border)" />
          <rect x="18" y="36" width="20" height="2" rx="1" fill="var(--color-border)" />
          <rect x="18" y="42" width="24" height="2" rx="1" fill="var(--color-border)" />
        </svg>
      </div>
      <p className="state-message">{message}</p>
      {description && <p className="state-description">{description}</p>}
      {action}
    </div>
  )
}

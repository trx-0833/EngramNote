/**
 * @file 统一加载中组件
 * @description 全局统一的加载状态展示，带旋转动画和可选提示文字
 */
export default function LoadingSpinner({ text = '加载中...' }: { text?: string }) {
  return (
    <div style={{ textAlign: 'center', padding: 'var(--space-xl)', color: 'var(--color-text-secondary)' }}>
      <div style={{
        display: 'inline-block',
        width: 24,
        height: 24,
        border: '3px solid var(--color-border)',
        borderTopColor: 'var(--color-primary)',
        borderRadius: '50%',
        animation: 'spin 0.8s linear infinite',
        marginRight: 'var(--space-sm)',
        verticalAlign: 'middle',
      }} />
      <span>{text}</span>
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}

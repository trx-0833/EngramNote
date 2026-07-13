/**
 * @file 统一加载中组件
 * @description 优雅的渐变脉冲圆环动画
 */
export default function LoadingSpinner({ text = '加载中...' }: { text?: string }) {
  return (
    <div className="state-container">
      <div className="spinner" />
      <p className="state-message" style={{ marginTop: 'var(--space-md)' }}>{text}</p>
    </div>
  )
}

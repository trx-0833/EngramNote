/**
 * @file 项目页的可关闭错误提示条
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 加载/重命名/删除/扫描/移出笔记的失败都报在这一条上，文案由调用方原样传入。
 */
interface ProjectsErrorBannerProps {
  error: string
  /** 点 ✕ 关闭（清空 error，页面骨架不动） */
  onDismiss: () => void
}

export default function ProjectsErrorBanner({ error, onDismiss }: ProjectsErrorBannerProps) {
  return (
    <div
      className="card"
      style={{
        background: 'var(--color-error-light)',
        border: '1px solid var(--color-error)',
        color: 'var(--color-error)',
        padding: '12px 16px',
        marginBottom: 16,
        fontSize: '0.875rem',
      }}
    >
      {error}
      <button
        style={{ float: 'right', color: 'var(--color-error)', fontSize: '0.8rem' }}
        onClick={onDismiss}
      >
        ✕
      </button>
    </div>
  )
}

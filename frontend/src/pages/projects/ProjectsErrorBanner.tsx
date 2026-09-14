/**
 * @file 项目页的可关闭错误提示条
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 加载/重命名/删除/扫描/移出笔记的失败都报在这一条上，文案由调用方原样传入。
 * 组件对"报的是哪一类失败"无感，因此同一形状可以同时出现两条（页面级失败 / 重命名校验），
 * 各自独立关闭 —— 这正是原来共用一个 error 槽位时做不到的（BB.8 第 5 条）。
 */
interface ProjectsErrorBannerProps {
  error: string
  /** 点 ✕ 关闭（清空 error，页面骨架不动） */
  onDismiss: () => void
  /**
   * 关闭按钮的无障碍名。默认就是可见的 ✕ —— 只有页面上同时出现两条提示
   * （页面级失败 + 重命名校验）时才需要区分，否则读屏与测试都分不清点的是哪一条。
   */
  dismissLabel?: string
}

export default function ProjectsErrorBanner({ error, onDismiss, dismissLabel }: ProjectsErrorBannerProps) {
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
        aria-label={dismissLabel}
        style={{ float: 'right', color: 'var(--color-error)', fontSize: '0.8rem' }}
        onClick={onDismiss}
      >
        ✕
      </button>
    </div>
  )
}

/**
 * @file diff 对比视图面板
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 加载中 / 有数据 / 无法加载 三种分支的文案与结构均与拆分前一致。
 */
import DiffView from '../../components/DiffView'
import type { CleaningDiffResponse } from '../../api/client'

interface DiffPanelProps {
  /** diff 数据（null 表示未加载成功） */
  diffData: CleaningDiffResponse | null
  /** 是否正在加载 diff 数据 */
  diffLoading: boolean
}

/** diff 对比视图 */
export default function DiffPanel({ diffData, diffLoading }: DiffPanelProps) {
  if (diffLoading) {
    return (
      <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
        <p style={{ color: 'var(--color-text-secondary)' }}>加载对比数据...</p>
      </div>
    )
  }

  if (diffData) {
    return (
      <DiffView
        blocks={diffData.blocks}
        originalLines={diffData.original_lines}
        cleanLines={diffData.clean_lines}
      />
    )
  }

  return (
    <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
      <p style={{ color: 'var(--color-text-secondary)' }}>无法加载对比数据</p>
    </div>
  )
}

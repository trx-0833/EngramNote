/**
 * @file 项目卡片里的「添加笔记」面板
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：搜索框、候选列表（带状态徽章）、勾选、`添加（N）` 按钮的
 * 禁用条件（`adding || selectedNoteIds.length === 0`）与文案、取消/✕ 关闭均逐字保留。
 *
 * 候选过滤里的 `(n.title ?? '')` 是白屏护栏：`title` 可能为 null（后端/历史数据），
 * 原来直接 `null.toLowerCase()` 一输入搜索词就崩。缺失标题按空串处理 ——
 * 只在搜索关键词为空时留在候选里，其余情况不参与匹配。
 */
import type { Note } from '../../api/client'
import { statusClass } from '../../utils/labels'

interface AddNotesPanelProps {
  /** 未过滤的候选笔记（打开面板时已剔除已归属本项目的） */
  candidates: Note[]
  search: string
  /** 加载/添加失败的提示（面板内展示，不静默吞掉） */
  error: string
  adding: boolean
  selectedNoteIds: string[]
  onChangeSearch: (value: string) => void
  onToggleSelect: (id: string) => void
  onConfirm: () => void
  onClose: () => void
}

export default function AddNotesPanel({
  candidates,
  search,
  error,
  adding,
  selectedNoteIds,
  onChangeSearch,
  onToggleSelect,
  onConfirm,
  onClose,
}: AddNotesPanelProps) {
  const addKeyword = search.trim().toLowerCase()
  const filteredCandidates = candidates.filter(
    // title 可能为 null（后端/历史数据）：原来直接 `null.toLowerCase()` 一输入搜索词就崩，
    // 缺失标题按空串处理 —— 只在搜索关键词为空时留在候选里，其余情况不参与匹配。
    (n) => !addKeyword || (n.title ?? '').toLowerCase().includes(addKeyword)
  )

  return (
    <div
      style={{
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border-light)',
        borderRadius: 'var(--radius-sm)',
        padding: 10,
        fontSize: '0.8rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>添加笔记</span>
        <button style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary)' }} onClick={onClose}>
          ✕
        </button>
      </div>
      <input
        value={search}
        onChange={(e) => onChangeSearch(e.target.value)}
        placeholder="按标题搜索候选笔记…"
        style={{ width: '100%', marginBottom: 8, fontSize: '0.8rem' }}
      />
      {error && <div style={{ color: 'var(--color-error)', fontSize: '0.8rem', marginBottom: 8 }}>{error}</div>}
      <div
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          marginBottom: 8,
        }}
      >
        {filteredCandidates.length === 0 ? (
          <div style={{ color: 'var(--color-text-tertiary)', textAlign: 'center', padding: '12px 0' }}>
            暂无可添加的笔记
          </div>
        ) : (
          filteredCandidates.map((n) => (
            <label key={n.id} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={selectedNoteIds.includes(n.id)}
                onChange={() => onToggleSelect(n.id)}
                // 覆盖全局 input{width:100%}，否则 checkbox 会撑满整行导致标题被挤成 0 宽
                style={{ width: 'auto', margin: 0, padding: 0, flexShrink: 0 }}
              />
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={n.title}
              >
                {n.title}
              </span>
              <span className={statusClass(n.status)} style={{ fontSize: '0.7rem', flexShrink: 0 }}>
                {n.status}
              </span>
            </label>
          ))
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          className="btn btn-primary"
          style={{ fontSize: '0.8rem', padding: '4px 12px' }}
          onClick={onConfirm}
          disabled={adding || selectedNoteIds.length === 0}
        >
          {adding ? '添加中…' : `添加（${selectedNoteIds.length}）`}
        </button>
        <button className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '4px 12px' }} onClick={onClose}>
          取消
        </button>
      </div>
    </div>
  )
}

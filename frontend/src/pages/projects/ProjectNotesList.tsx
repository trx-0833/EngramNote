/**
 * @file 项目卡片里展开后的笔记列表
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：空列表时说明"可把文件放入收件箱 source/ 后点击扫描导入"、
 * 每行显示来源徽章/标题/状态/大小，**点整行跳转笔记详情**，
 * 而「移出」按钮 `stopPropagation`（否则用户想去掉归属却被弹到笔记页）。
 */
import { useNavigate } from 'react-router-dom'
import type { NoteInFolder } from '../../api/client'
import { statusClass } from '../../utils/labels'
import { TYPE_BADGE, formatSize } from './helpers'

interface ProjectNotesListProps {
  notes: NoteInFolder[]
  /** 移出笔记（调用方负责二次确认与刷新） */
  onRemoveNote: (note: NoteInFolder) => void
}

export default function ProjectNotesList({ notes, onRemoveNote }: ProjectNotesListProps) {
  const navigate = useNavigate()

  return (
    <div style={{ borderTop: '1px solid var(--color-border-light)', paddingTop: 10 }}>
      {notes.length === 0 ? (
        <div style={{ fontSize: '0.85rem', color: 'var(--color-text-tertiary)', textAlign: 'center', padding: '16px 0' }}>
          项目暂无笔记。可把文件放入收件箱{' '}
          <code style={{ color: 'var(--color-primary)' }}>source/</code>{' '}
          后点击「扫描导入」。
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {notes.map((n) => (
            <div
              key={n.id}
              className="note-select-card"
              style={{ marginBottom: 0, cursor: 'pointer' }}
              onClick={() => navigate(`/notes/${n.id}`)}
            >
              <span
                className={`badge ${TYPE_BADGE[n.source_type] ?? 'badge-markdown'}`}
                style={{ flexShrink: 0, width: 56, justifyContent: 'center' }}
              >
                {n.source_type}
              </span>
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontWeight: 500,
                }}
              >
                {n.title}
              </span>
              <span className={statusClass(n.status)} style={{ fontSize: '0.75rem', flexShrink: 0 }}>
                {n.status}
              </span>
              <span style={{ fontSize: '0.7rem', color: 'var(--color-text-tertiary)', flexShrink: 0 }}>
                {formatSize(n.file_size)}
              </span>
              <button
                className="btn btn-ghost"
                title="将笔记移出该项目"
                style={{ fontSize: '0.7rem', padding: '2px 8px', flexShrink: 0 }}
                onClick={(e) => {
                  e.stopPropagation() // 避免触发整行跳转到笔记详情
                  onRemoveNote(n)
                }}
              >
                移出
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

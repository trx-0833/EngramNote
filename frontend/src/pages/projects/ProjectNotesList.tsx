/**
 * @file 项目卡片里展开后的笔记列表
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：空列表时说明"可把文件放入收件箱 source/ 后点击扫描导入"、
 * 每行显示来源徽章/标题/状态/大小，**点标题跳转笔记详情**，
 * 而「移出」按钮 `stopPropagation`（否则用户想去掉归属却被弹到笔记页）。
 *
 * a11y-audit 的 Part A：**"点整行"改成了"点标题"** —— 原来外层是
 * `div[onClick]`（没有 role/tabIndex），键盘到不了"打开这篇笔记"
 * （详见行内注释）。
 */
import { Link } from 'react-router-dom'
import type { NoteInFolder } from '../../api/client'
import { statusClass } from '../../utils/labels'
import { TYPE_BADGE, formatSize } from './helpers'

interface ProjectNotesListProps {
  notes: NoteInFolder[]
  /** 移出笔记（调用方负责二次确认与刷新） */
  onRemoveNote: (note: NoteInFolder) => void
}

export default function ProjectNotesList({ notes, onRemoveNote }: ProjectNotesListProps) {
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
            /* ⚠️ 这一行原来是 `div.note-select-card[onClick]`（没有 role/tabIndex）——
               键盘**到不了**"打开这篇笔记"，而那是这一行唯一的行为。
               改法与 F-17（笔记列表卡片）/ Dashboard 的笔记卡片同形：
               **卡片是盒子，控件在标题上**（标题是真 `<Link>`，可右键、可新标签页）。
               「移出」保持独立按钮，是它的**兄弟**（`stopPropagation` 留着：
               外层已经没有 onClick 了，但那个 handler 同时对"点空白处"这类调用有意义）。 */
            <div
              key={n.id}
              className="note-select-card"
              style={{ marginBottom: 0 }}
            >
              <span
                className={`badge ${TYPE_BADGE[n.source_type] ?? 'badge-markdown'}`}
                style={{ flexShrink: 0, width: 56, justifyContent: 'center' }}
              >
                {n.source_type}
              </span>
              <Link
                to={`/notes/${n.id}`}
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontWeight: 500,
                  // 整块可点的标题链接：下划线显式关掉（与其它卡片标题链接一致），
                  // 颜色继承（原来是普通 span）
                  color: 'inherit',
                  textDecoration: 'none',
                }}
              >
                {n.title}
              </Link>
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

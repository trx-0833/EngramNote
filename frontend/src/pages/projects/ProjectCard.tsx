/**
 * @file 单个项目卡片（项目管理是破坏性操作最集中的地方）
 * @description 自 `pages/Projects.tsx` 的 `renderCard(p, index)` 拆分（overhaul-plan 5.5），
 * **只搬不改**：卡片类名（含 `stagger-${(index % 5) + 1}` 入场动画）、重命名态与展示态的
 * 互斥、`note_count ?? 0`（缺字段显示 0 而不是字面量 undefined）、重命名时隐藏描述、
 * 展开按钮的箭头与文案、以及"移出笔记"的确认与不跳转全部逐字保留。
 *
 * 卡片自身不持有状态：草稿（重命名/展开详情/扫描结果/候选笔记）都在 `pages/projects/`
 * 的 hooks 里，与拆分前同源。
 */
import type { Note, NoteInFolder, Project, ProjectDetail, ScanImportResponse } from '../../api/client'
import AddNotesPanel from './AddNotesPanel'
import ProjectNotesList from './ProjectNotesList'
import ProjectRenameForm from './ProjectRenameForm'
import ScanResultPanel from './ScanResultPanel'
import { unwrapProjectNotes } from './helpers'

interface ProjectCardProps {
  project: Project
  /** 列表下标，仅用于入场动画的 stagger 类 */
  index: number
  /** 行内重命名草稿（存在即处于重命名态） */
  rename: { name: string; description: string } | undefined
  /** 展开后的项目详情（未展开为 undefined） */
  detail: ProjectDetail | null | undefined
  isScanning: boolean
  scanResult: ScanImportResponse | null | undefined
  /** 添加笔记面板是否开在本卡片上（同时只开一个） */
  addPanelOpen: boolean
  candidateNotes: Note[]
  addSearch: string
  addError: string
  adding: boolean
  selectedNoteIds: string[]
  onStartRename: () => void
  onChangeRenameName: (value: string) => void
  onChangeRenameDescription: (value: string) => void
  onSaveRename: () => void
  onCancelRename: () => void
  onDelete: () => void
  onScan: () => void
  onToggleExpand: () => void
  onOpenAddPanel: () => void
  onChangeAddSearch: (value: string) => void
  onToggleSelectNote: (id: string) => void
  onConfirmAdd: () => void
  onCloseAddPanel: () => void
  onRemoveNote: (note: NoteInFolder) => void
}

export default function ProjectCard({
  project: p,
  index,
  rename,
  detail,
  isScanning,
  scanResult,
  addPanelOpen,
  candidateNotes,
  addSearch,
  addError,
  adding,
  selectedNoteIds,
  onStartRename,
  onChangeRenameName,
  onChangeRenameDescription,
  onSaveRename,
  onCancelRename,
  onDelete,
  onScan,
  onToggleExpand,
  onOpenAddPanel,
  onChangeAddSearch,
  onToggleSelectNote,
  onConfirmAdd,
  onCloseAddPanel,
  onRemoveNote,
}: ProjectCardProps) {
  const isRenaming = !!rename
  const isExpanded = !!detail
  // 详情里的 notes 与 `/projects` 走同一道拆包判据：缺字段/包装对象都不该让 `notes.map` 白屏
  const notes: NoteInFolder[] = unwrapProjectNotes(detail?.notes)

  return (
    <div
      className={`card card-hover fade-in stagger-${(index % 5) + 1}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        padding: 20,
        borderTop: '3px solid var(--color-primary)',
      }}
    >
      {/* 项目头：名称 + 笔记数 */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {isRenaming ? (
            <input
              value={rename.name}
              onChange={(e) => onChangeRenameName(e.target.value)}
              placeholder="项目名称"
              style={{ width: '100%', fontWeight: 600 }}
              autoFocus
            />
          ) : (
            <h3
              style={{
                fontSize: '1.05rem',
                fontWeight: 700,
                margin: 0,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {p.name}
            </h3>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, fontSize: '0.75rem', color: 'var(--color-text-tertiary)' }}>
            <span className="badge" style={{ background: 'var(--color-primary-light)', color: 'var(--color-primary)' }}>
              {p.note_count ?? 0} 篇笔记
            </span>
          </div>
        </div>
        {/* 操作按钮 */}
        <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
          <button
            className="btn btn-ghost"
            title="从已有笔记中选择并添加到本项目"
            onClick={onOpenAddPanel}
            style={{ fontSize: '0.8rem', padding: '4px 10px' }}
          >
            添加笔记
          </button>
          <button
            className="btn btn-ghost"
            title="扫描导入 source/ 目录中的新文件"
            onClick={onScan}
            disabled={isScanning}
            style={{ fontSize: '0.8rem', padding: '4px 10px' }}
          >
            {isScanning ? '扫描中…' : '扫描导入'}
          </button>
        </div>
      </div>

      {/* 描述 */}
      {!isRenaming && p.description && (
        <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', margin: 0, lineHeight: 1.6 }}>
          {p.description}
        </p>
      )}

      {/* 重命名编辑区 */}
      {isRenaming && (
        <ProjectRenameForm
          name={rename.name}
          description={rename.description}
          onChangeName={onChangeRenameName}
          onChangeDescription={onChangeRenameDescription}
          onSave={onSaveRename}
          onCancel={onCancelRename}
        />
      )}

      {/* 扫描结果 */}
      {scanResult && <ScanResultPanel result={scanResult} />}

      {/* 添加笔记面板 */}
      {addPanelOpen && (
        <AddNotesPanel
          candidates={candidateNotes}
          search={addSearch}
          error={addError}
          adding={adding}
          selectedNoteIds={selectedNoteIds}
          onChangeSearch={onChangeAddSearch}
          onToggleSelect={onToggleSelectNote}
          onConfirm={onConfirmAdd}
          onClose={onCloseAddPanel}
        />
      )}

      {/* 底部操作行 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, borderTop: '1px solid var(--color-border-light)', paddingTop: 10, marginTop: 'auto' }}>
        <button
          className="btn btn-ghost"
          style={{ fontSize: '0.8rem', padding: '4px 8px' }}
          onClick={onToggleExpand}
        >
          <span className={`collapse-arrow ${isExpanded ? 'collapse-arrow-open' : ''}`}>▶</span>
          {isExpanded ? '收起笔记' : `查看笔记（${p.note_count ?? 0}）`}
        </button>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.8rem', padding: '4px 8px' }}
            onClick={onStartRename}
          >
            重命名
          </button>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.8rem', padding: '4px 8px', color: 'var(--color-error)' }}
            onClick={onDelete}
          >
            删除
          </button>
        </div>
      </div>

      {/* 笔记列表 */}
      {isExpanded && <ProjectNotesList notes={notes} onRemoveNote={onRemoveNote} />}
    </div>
  )
}

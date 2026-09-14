/**
 * @file 笔记详情页头部：标题与操作按钮、元信息标签、错误提示、视图切换
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 标签顺序、禁用/显示条件、`title` 提示文案、inline 样式均与拆分前一致。
 */
import type { CSSProperties } from 'react'
import { updateNoteRole, type NoteDetail } from '../../api/client'
import { useToast } from '../../components/Toast'
import { formatDateTime } from '../../utils/datetime'
import { statusClass, statusLabels } from '../../utils/labels'
import RetryConvertButton from './RetryConvertButton'
import ViewModeTabs from './ViewModeTabs'
// 「笔记角色」下拉框的样式（含 axe 看不见的焦点环）—— 见模块文件头
import styles from './NoteDetailHeader.module.css'
import type { EditMode, ViewMode } from './types'

/** 处理中（不可编辑）的笔记状态 */
const PROCESSING_STATUSES = ['uploading', 'converting', 'cleaning', 'learning']

interface NoteDetailHeaderProps {
  /** 笔记详情 */
  note: NoteDetail
  /** 笔记 ID（重试转换用；与 note.id 同源） */
  noteId: string | undefined
  /** 当前视图模式 */
  viewMode: ViewMode
  /** 当前编辑模式（编辑中隐藏"编辑"按钮） */
  editMode: EditMode
  /** 是否有可立即复习的题目 */
  hasQuizItems: boolean
  /** 是否可切换到清洗版 */
  canShowClean: boolean
  /** 是否可切换到对比视图 */
  canShowDiff: boolean
  /** 切换视图模式 */
  onViewModeChange: (mode: ViewMode) => void
  /** 进入编辑模式 */
  onEnterEdit: () => void
  /** 触发 AI 预处理（开始学习） */
  onStartLearning: () => void
  /** 归档 / 取消归档 */
  onArchive: () => void
  /** 打开删除确认弹窗 */
  onDelete: () => void
  /** 打开版本历史面板 */
  onOpenVersionHistory: () => void
  /** 打开关联资料管理弹窗 */
  onManageLinks: () => void
  /** 局部合并角色更新结果 */
  onRoleUpdated: (noteRole: string | undefined) => void
  /** 局部合并重试转换结果 */
  onRetryConverted: (next: { status: string; error_message: string | null }) => void
  /** 跳转路由 */
  navigate: (path: string) => void
}

/** 笔记详情页头部区域 */
export default function NoteDetailHeader({
  note,
  noteId,
  viewMode,
  editMode,
  hasQuizItems,
  canShowClean,
  canShowDiff,
  onViewModeChange,
  onEnterEdit,
  onStartLearning,
  onArchive,
  onDelete,
  onOpenVersionHistory,
  onManageLinks,
  onRoleUpdated,
  onRetryConverted,
  navigate,
}: NoteDetailHeaderProps) {
  const toast = useToast()
  const isProcessing = PROCESSING_STATUSES.includes(note.status)

  return (
    <header style={{ marginBottom: 'var(--space-lg)' }}>
      {/* 标题 + 操作按钮组：窄屏下改为上下排列、按钮换行（见 responsive.css 的 768px 断点） */}
      <div className="note-detail-header">
        <h1 className="heading-serif" style={{ fontSize: '1.5rem' }}>{note.title}</h1>
        <div className="note-detail-actions">
          {editMode === 'view' && (
            <button
              className="btn btn-secondary"
              onClick={onEnterEdit}
              disabled={viewMode === 'original' || isProcessing}
              title={
                viewMode === 'original'
                  ? '原始版不可编辑，请在清洗版中编辑'
                  : (isProcessing ? '处理中，暂不可编辑' : '编辑笔记内容')
              }
            >
              编辑
            </button>
          )}
          <button
            className="btn btn-secondary"
            onClick={onOpenVersionHistory}
            disabled={['uploading', 'converting'].includes(note.status)}
            title="查看版本历史"
          >
            版本历史
          </button>
          {(note.status === 'archived' || note.status === 'learning') && hasQuizItems && (
            <button className="btn btn-primary" onClick={() => navigate(`/review/quick/${note.id}`)}>立即复习</button>
          )}
          {(note.note_role === 'material' || !note.note_role) && (
            <button className="btn btn-secondary" onClick={() => navigate(`/assessment?noteId=${note.id}`)}>学习评估</button>
          )}
          {note.note_role === 'personal_note' && (
            <button className="btn btn-secondary" onClick={onManageLinks}>管理关联资料</button>
          )}
          {(note.status === 'cleaned' || note.status === 'learning_failed' || note.status === 'archived') && (
            <button className="btn btn-primary" onClick={onStartLearning}>AI预处理</button>
          )}
          {(note.status === 'cleaned' || note.status === 'learning_failed' || note.status === 'converted' || note.status === 'archived') && (
            <button className="btn btn-secondary" onClick={onArchive}>
              {note.status === 'archived' ? '取消审阅' : '审阅'}
            </button>
          )}
          <button className="btn btn-danger" onClick={onDelete}>删除</button>
          <button className="btn btn-secondary" onClick={() => navigate('/notes')}>返回</button>
        </div>
      </div>

      {/* 笔记元信息标签行 */}
      <div style={{ display: 'flex', gap: 'var(--space-md)', alignItems: 'center', flexWrap: 'wrap', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
        <span className={`badge badge-${note.source_type}`}>{note.source_type.toUpperCase()}</span>
        {/* 所属项目标签：色值与 NotesList 的同一枚标签保持一致
            （`--color-primary-soft` 在本项目未定义，实际落到兜底 #eef2ff；
            而兜底前景 #2563eb 与它只有 4.62:1，12px 小字压在门槛线上 ——
            改用同色系的 #1b4fbf，5.49:1。两处必须一起改，否则同一枚标签
            在两个页面上是两个颜色）。 */}
        {note.project_names?.map((name) => (
          <span key={name} className="badge" style={{ backgroundColor: 'var(--color-primary-soft, #eef2ff)', color: '#1b4fbf' }}>
            {name}
          </span>
        ))}
        <span className={statusClass(note.status)}>{statusLabels[note.status] || note.status}</span>
        {/* 笔记角色：本页唯一会写数据的原生控件。
            - 可访问名用 `aria-label`（这一行是 flex 排布的元信息标签，
              插一个可见 <label> 会改变排版）；
            - 外观搬进 NoteDetailHeader.module.css：内联样式的权重高于任何
              选择器，`outline: 'none'` 留在 tsx 里的话，样式表中的
              `:focus-visible` 焦点环**永远不会生效**（文件头有完整说明）；
            - 底色随角色变，通过 `--note-role-bg` 传进去，两个色值仍在 tsx 里可见。 */}
        <select
          className={styles.roleSelect}
          aria-label="笔记角色"
          value={note.note_role || 'material'}
          onChange={async (e) => {
            try {
              const updated = await updateNoteRole(note.id, e.target.value)
              onRoleUpdated(updated.note_role)
            } catch (err) {
              toast.error(err instanceof Error ? err.message : '更新角色失败')
            }
          }}
          style={{
            // #316fd8 白底白字 4.78:1、#6d28d9 为 7.10:1（原先 #3b82f6 只有 3.68:1）
            '--note-role-bg': note.note_role === 'personal_note' ? '#6d28d9' : '#316fd8',
          } as CSSProperties}
        >
          <option value="material">学习资料</option>
          <option value="personal_note">我的笔记</option>
        </select>
        {note.page_count && <span>{note.page_count} 页</span>}
        <span>{(note.file_size / 1024).toFixed(0)} KB</span>
        <span>创建于 {formatDateTime(note.created_at)}</span>
      </div>

      {/* 错误信息提示 + 重试按钮 */}
      {note.error_message && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginTop: 'var(--space-sm)' }}>
          <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem', margin: 0 }}>
            错误: {note.error_message}
          </p>
          {note.status === 'failed' && (
            <RetryConvertButton noteId={noteId} onRetried={onRetryConverted} />
          )}
        </div>
      )}

      {/* 视图模式切换按钮 */}
      <ViewModeTabs
        viewMode={viewMode}
        onViewModeChange={onViewModeChange}
        canShowClean={canShowClean}
        canShowDiff={canShowDiff}
      />
    </header>
  )
}

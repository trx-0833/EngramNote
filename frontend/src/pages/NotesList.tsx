/**
 * @file 笔记列表页面
 * @description 展示用户所有笔记的列表页面，支持：
 * 1. 按标题关键词搜索
 * 2. 分页浏览（每页 20 条）
 * 3. 删除笔记（带确认提示）
 * 4. 点击笔记卡片跳转到详情页
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getNotes, getArchivedNotes, deleteNote, retryConvert, type Note } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { sourceTypeLabels, statusLabels } from '../utils/labels'

/**
 * 笔记列表页面组件
 *
 * 数据流：
 * 1. 组件挂载或 page/keyword 变化时，调用 getNotes() 获取笔记列表
 * 2. 展示笔记卡片列表，支持搜索和分页
 * 3. 删除操作：确认后调用 deleteNote()，成功后从本地状态中移除该笔记
 *
 * 状态管理：
 * - notes: 当前页的笔记列表
 * - total: 笔记总数，用于计算分页
 * - page: 当前页码
 * - keyword: 搜索关键词，输入时自动重置到第 1 页
 * - loading: 数据加载状态
 */
export default function NotesList() {
  const navigate = useNavigate()
  /** 当前页的笔记列表 */
  const [notes, setNotes] = useState<Note[]>([])
  /** 笔记总数，用于计算总页数 */
  const [total, setTotal] = useState(0)
  /** 当前页码（从 1 开始） */
  const [page, setPage] = useState(1)
  /** 搜索关键词 */
  const [keyword, setKeyword] = useState('')
  /** 当前笔记角色筛选：material=学习资料，personal_note=我的笔记 */
  const [noteRole, setNoteRole] = useState<'material' | 'personal_note'>('material')
  /** 各角色下是否只显示已审阅笔记，保持两个 Tab 下已审阅筛选独立 */
  const [archivedByRole, setArchivedByRole] = useState<Record<string, boolean>>({
    material: false,
    personal_note: false,
  })
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true)
  /** 错误信息 */
  const [error, setError] = useState('')

  /** 每页显示条数，固定为 20 */
  const pageSize = 20

  // 当页码或关键词或筛选条件变化时重新获取笔记列表
  async function fetchNotes() {
    setLoading(true)
    setError('')
    try {
      const showArchived = archivedByRole[noteRole]
      const res = showArchived
        ? await getArchivedNotes(page, pageSize, noteRole)
        : await getNotes(page, pageSize, keyword || undefined, noteRole)
      setNotes(res.items)
      setTotal(res.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchNotes()
  }, [page, keyword, noteRole, archivedByRole[noteRole]])

  /**
   * 处理删除笔记
   * 弹出确认对话框，确认后调用 API 删除，成功后从本地状态中移除。
   *
   * @param noteId - 要删除的笔记 ID
   * @param e - 鼠标事件，阻止事件冒泡以免触发卡片的点击导航
   */
  async function handleDelete(noteId: string, e: React.MouseEvent) {
    e.stopPropagation() // 阻止冒泡，避免触发卡片 onClick 导航
    // 删除操作不可恢复，需要用户二次确认
    if (!confirm('确定删除此笔记？将同时删除关联的知识卡片、练习题目、复习记录和图谱关系。此操作不可恢复。')) return

    try {
      await deleteNote(noteId)
      // 乐观更新：从本地状态中移除已删除的笔记，无需重新请求列表
      setNotes((prev) => prev.filter((n) => n.id !== noteId))
      setTotal((prev) => prev - 1)
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  /**
   * 处理重试转换失败的笔记
   * 调用 retryConvert API，成功后更新本地状态
   */
  async function handleRetry(noteId: string, e: React.MouseEvent) {
    e.stopPropagation()
    try {
      const result = await retryConvert(noteId)
      setNotes((prev) =>
        prev.map((n) =>
          n.id === noteId
            ? { ...n, status: result.status, error_message: result.error_message }
            : n
        )
      )
    } catch (err) {
      alert(err instanceof Error ? err.message : '重试失败')
    }
  }

  /** 计算总页数，用于分页控件 */
  const totalPages = Math.ceil(total / pageSize)

  return (
    <div className="page-enter">
      {/* 搜索栏：输入关键词即时搜索，同时重置到第 1 页 */}
      <div style={{ display: 'flex', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)', alignItems: 'center' }}>
        <div className="search-input-wrapper">
          <svg className="search-input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="search"
            placeholder="搜索笔记标题..."
            value={keyword}
            onChange={(e) => { setKeyword(e.target.value); setPage(1) }}
            aria-label="搜索笔记"
          />
        </div>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
          共 {total} 条
        </span>
      </div>

      {/* 笔记角色 Tab 切换：学习资料 / 我的笔记 */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)', flexWrap: 'wrap' }}>
        <button
          className={`filter-pill ${noteRole === 'material' ? 'filter-pill-active' : ''}`}
          onClick={() => { setNoteRole('material'); setPage(1) }}
        >
          学习资料
        </button>
        <button
          className={`filter-pill ${noteRole === 'personal_note' ? 'filter-pill-active' : ''}`}
          onClick={() => { setNoteRole('personal_note'); setPage(1) }}
        >
          我的笔记
        </button>
      </div>

      {/* 筛选标签：全部 / 已审阅 */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
        <button
          className={`filter-pill ${!archivedByRole[noteRole] ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setArchivedByRole(prev => ({ ...prev, [noteRole]: false }))
            setPage(1)
          }}
        >
          全部
        </button>
        <button
          className={`filter-pill ${archivedByRole[noteRole] ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setArchivedByRole(prev => ({ ...prev, [noteRole]: true }))
            setPage(1)
          }}
        >
          已审阅
        </button>
      </div>

      {/* 笔记列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchNotes} />
      ) : notes.length === 0 ? (
        /* 空状态：根据是否有搜索关键词显示不同提示 */
        <EmptyState
          message={keyword ? '没有找到匹配的笔记' : '还没有笔记'}
          description={keyword ? undefined : '上传你的第一份学习资料'}
          action={!keyword ? <button className="btn btn-primary" onClick={() => navigate('/upload')}>上传资料</button> : undefined}
        />
      ) : (
        /* 笔记卡片列表 */
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {notes.map((note) => (
            <article
              key={note.id}
              className="card card-hover"
              style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              onClick={() => navigate(`/notes/${note.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/notes/${note.id}`) }} // 支持键盘 Enter 键触发导航
            >
              <div style={{ flex: 1 }}>
                <h3 style={{ fontWeight: 500, marginBottom: 'var(--space-xs)' }}>{note.title}</h3>
                <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center', flexWrap: 'wrap' }}>
                  {/* 来源类型标签 */}
                  <span className={`badge badge-${note.source_type}`}>
                    {sourceTypeLabels[note.source_type] || note.source_type}
                  </span>
                  {/* 处理状态标签 */}
                  <span className={`status-${note.status}`} style={{ fontSize: '0.8rem' }}>
                    {statusLabels[note.status] || note.status}
                  </span>
                  {/* 页数信息，仅 PDF/Office 文档有值 */}
                  {note.page_count && (
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      {note.page_count} 页
                    </span>
                  )}
                  {/* 文件大小，从字节转换为 KB 显示 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {(note.file_size / 1024).toFixed(0)} KB
                  </span>
                  {/* 创建日期 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {new Date(note.created_at).toLocaleDateString('zh-CN')}
                  </span>
                </div>
                {/* 错误信息，仅 status 为 failed 时显示 */}
                {note.error_message && (
                  <p style={{ color: 'var(--color-error)', fontSize: '0.8rem', marginTop: 'var(--space-xs)' }}>
                    {note.error_message}
                  </p>
                )}
              </div>
              {/* 操作按钮区域 */}
              <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
                {/* 重试按钮，仅 failed 状态显示 */}
                {note.status === 'failed' && (
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                    onClick={(e) => handleRetry(note.id, e)}
                    aria-label={`重试 ${note.title}`}
                  >
                    重试
                  </button>
                )}
                {/* 删除按钮，需要阻止事件冒泡 */}
                <button
                  className="btn btn-danger"
                  style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                  onClick={(e) => handleDelete(note.id, e)}
                  aria-label={`删除 ${note.title}`}
                >
                  删除
                </button>
                <span style={{ color: 'var(--color-text-secondary)', alignSelf: 'center' }} aria-hidden="true">→</span>
              </div>
            </article>
          ))}
        </div>
      )}

      {/* 分页控件：仅在总页数大于 1 时显示 */}
      {totalPages > 1 && (
        <nav style={{ display: 'flex', justifyContent: 'center', gap: 'var(--space-sm)', marginTop: 'var(--space-lg)' }} aria-label="分页">
          <button
            className="btn btn-secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </button>
          <span style={{ alignSelf: 'center', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
            {page} / {totalPages}
          </span>
          <button
            className="btn btn-secondary"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </nav>
      )}
    </div>
  )
}

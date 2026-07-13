/**
 * @file 今日资料页面
 * @description 按日期组织学习资料的文件夹视图，支持：
 * 1. 新建文件夹（默认以今天日期命名）
 * 2. 浏览最近 7 天的文件夹列表
 * 3. 展开文件夹查看内部文件
 * 4. 在文件夹内上传新文件
 * 5. 按状态筛选文件
 */
import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getFolders,
  getFolderDetail,
  createFolder,
  deleteFolder,
  updateFolder,
  uploadFileToFolder,
  getUploadStatus,
  type Folder,
  type FolderDetail,
  type NoteInFolder,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { sourceTypeLabels, statusLabels } from '../utils/labels'

/** 允许上传的文件扩展名列表 */
const ALLOWED_EXTENSIONS = [
  '.pdf', '.png', '.jpg', '.jpeg', '.docx', '.pptx', '.xlsx',
  '.mp4', '.mp3', '.wav', '.m4a', '.md',
]

/** 状态筛选选项 */
const STATUS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'processing', label: '处理中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
] as const

/**
 * 格式化文件大小
 * @param bytes - 文件大小（字节）
 * @returns 格式化后的文件大小字符串
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * 格式化日期为中文格式
 * @param dateStr - ISO 日期字符串
 * @returns 格式化后的日期字符串
 */
function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  if (date.toDateString() === today.toDateString()) return '今天'
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
}

/**
 * 判断笔记状态属于哪个筛选类别
 * @param status - 笔记状态
 * @returns 筛选类别 key
 */
function getStatusCategory(status: string): string {
  if (['uploading', 'converting', 'cleaning', 'learning'].includes(status)) return 'processing'
  if (['converted', 'cleaned', 'archived'].includes(status)) return 'completed'
  if (['failed', 'cleaning_failed', 'learning_failed'].includes(status)) return 'failed'
  return 'all'
}

/**
 * 今日资料页面组件
 *
 * 数据流：
 * 1. 组件挂载时调用 getFolders() 获取最近 7 天的文件夹列表
 * 2. 点击文件夹展开详情，调用 getFolderDetail() 获取笔记列表
 * 3. 新建文件夹调用 createFolder()，默认以今天日期命名
 * 4. 在文件夹内上传文件调用 uploadFileToFolder()
 *
 * 状态管理：
 * - folders: 文件夹列表
 * - expandedFolderId: 当前展开的文件夹 ID
 * - folderDetail: 当前展开文件夹的详情（含笔记列表）
 * - statusFilter: 笔记状态筛选
 * - uploading: 是否正在上传
 */
export default function DailyMaterials() {
  const navigate = useNavigate()
  /** 文件夹列表 */
  const [folders, setFolders] = useState<Folder[]>([])
  /** 当前展开的文件夹 ID */
  const [expandedFolderId, setExpandedFolderId] = useState<string | null>(null)
  /** 当前展开文件夹的详情 */
  const [folderDetail, setFolderDetail] = useState<FolderDetail | null>(null)
  /** 笔记状态筛选 */
  const [statusFilter, setStatusFilter] = useState<string>('all')
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true)
  /** 详情加载状态 */
  const [detailLoading, setDetailLoading] = useState(false)
  /** 错误信息 */
  const [error, setError] = useState('')
  /** 详情错误信息 */
  const [detailError, setDetailError] = useState('')
  /** 是否正在创建文件夹 */
  const [creating, setCreating] = useState(false)
  /** 是否正在上传文件 */
  const [uploading, setUploading] = useState(false)
  /** 上传状态文本 */
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  /** 隐藏的文件输入框引用 */
  const fileInputRef = useRef<HTMLInputElement>(null)
  /** 当前正在重命名的文件夹 ID */
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null)
  /** 重命名输入框的当前值 */
  const [editingName, setEditingName] = useState('')
  /** 是否正在保存重命名 */
  const [renaming, setRenaming] = useState(false)
  /** 重命名输入框引用 */
  const renameInputRef = useRef<HTMLInputElement>(null)

  /**
   * 获取文件夹列表
   */
  async function fetchFolders() {
    setLoading(true)
    setError('')
    try {
      const data = await getFolders(7)
      setFolders(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchFolders()
  }, [])

  /**
   * 展开/折叠文件夹
   * 点击已展开的文件夹则折叠，点击新的文件夹则加载其详情。
   *
   * @param folderId - 文件夹 ID
   */
  async function toggleFolder(folderId: string) {
    if (expandedFolderId === folderId) {
      setExpandedFolderId(null)
      setFolderDetail(null)
      return
    }

    setExpandedFolderId(folderId)
    setDetailLoading(true)
    setDetailError('')
    setStatusFilter('all')

    try {
      const detail = await getFolderDetail(folderId)
      setFolderDetail(detail)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '加载详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  /**
   * 创建新文件夹
   * 默认以今天日期命名，如 "2024-01-15 学习资料"
   */
  async function handleCreateFolder() {
    setCreating(true)
    try {
      const today = new Date().toISOString().split('T')[0]
      const folder = await createFolder(`${today} 学习资料`)
      setFolders((prev) => [folder, ...prev])
    } catch (err) {
      alert(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  /**
   * 删除空文件夹
   * @param folderId - 文件夹 ID
   * @param e - 鼠标事件，阻止冒泡
   */
  async function handleDeleteFolder(folderId: string, e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirm('确定删除此文件夹？')) return

    try {
      await deleteFolder(folderId)
      setFolders((prev) => prev.filter((f) => f.id !== folderId))
      if (expandedFolderId === folderId) {
        setExpandedFolderId(null)
        setFolderDetail(null)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  /**
   * 进入文件夹重命名模式
   * @param folder - 要重命名的文件夹
   * @param e - 鼠标事件，阻止冒泡触发折叠/展开
   */
  function startRenameFolder(folder: Folder, e: React.MouseEvent) {
    e.stopPropagation()
    setEditingFolderId(folder.id)
    setEditingName(folder.name)
    // 输入框渲染后自动聚焦并选中文本
    setTimeout(() => {
      const input = renameInputRef.current
      if (input) {
        input.focus()
        input.select()
      }
    }, 0)
  }

  /**
   * 取消重命名
   * @param e - 事件，阻止冒泡
   */
  function cancelRenameFolder(e?: React.SyntheticEvent) {
    e?.stopPropagation()
    setEditingFolderId(null)
    setEditingName('')
    setRenaming(false)
  }

  /**
   * 保存重命名
   * @param folderId - 文件夹 ID
   * @param e - 事件，阻止冒泡
   */
  async function saveRenameFolder(folderId: string, e?: React.SyntheticEvent) {
    e?.stopPropagation()
    const trimmed = editingName.trim()
    if (!trimmed) {
      alert('文件夹名称不能为空')
      return
    }

    setRenaming(true)
    try {
      const updated = await updateFolder(folderId, trimmed)
      setFolders((prev) => prev.map((f) => (f.id === folderId ? { ...f, name: updated.name } : f)))
      // 若该文件夹已展开，同步更新详情中的文件夹名
      setFolderDetail((prev) => (prev && prev.id === folderId ? { ...prev, name: updated.name } : prev))
      setEditingFolderId(null)
      setEditingName('')
    } catch (err) {
      alert(err instanceof Error ? err.message : '重命名失败')
    } finally {
      setRenaming(false)
    }
  }

  /**
   * 重命名输入框按键处理：Enter 保存，Esc 取消
   * @param folderId - 文件夹 ID
   * @param e - 键盘事件
   */
  function handleRenameKeyDown(folderId: string, e: React.KeyboardEvent<HTMLInputElement>) {
    e.stopPropagation()
    if (e.key === 'Enter') {
      e.preventDefault()
      saveRenameFolder(folderId, e)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancelRenameFolder(e)
    }
  }

  /**
   * 处理文件上传到文件夹
   * @param file - 上传的文件
   */
  async function handleUpload(file: File) {
    if (!expandedFolderId) return

    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      alert(`不支持的文件格式: ${ext}`)
      return
    }

    setUploading(true)
    setUploadStatus('上传中...')

    try {
      const note = await uploadFileToFolder(file, expandedFolderId)
      setUploadStatus('文件已上传，正在转换...')

      // 轮询转换状态
      pollUploadStatus(note.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : '上传失败')
      setUploading(false)
      setUploadStatus(null)
    }
  }

  /**
   * 轮询上传/转换状态
   * 使用 setTimeout 递归代替 setInterval，避免 async 回调请求重叠。
   * @param noteId - 笔记 ID
   */
  async function pollUploadStatus(noteId: string) {
    const maxAttempts = 120
    let attempts = 0

    /** 所有终态：成功或失败 */
    const successStatuses = ['converted', 'cleaned', 'archived', 'learning']
    const failedStatuses = ['failed', 'cleaning_failed', 'learning_failed']

    async function check() {
      if (attempts >= maxAttempts) {
        setUploading(false)
        setUploadStatus(null)
        return
      }
      attempts++

      try {
        const res = await getUploadStatus(noteId)
        setUploadStatus(`状态: ${statusLabels[res.status] || res.status}`)

        if (successStatuses.includes(res.status) || failedStatuses.includes(res.status)) {
          setUploading(false)
          setUploadStatus(null)
          // 刷新文件夹详情
          if (expandedFolderId) {
            const detail = await getFolderDetail(expandedFolderId)
            setFolderDetail(detail)
          }
          return // 终态，停止轮询
        }
      } catch {
        // 出错继续轮询
      }

      // 非终态，5 秒后再检查
      setTimeout(check, 5000)
    }

    check()
  }

  /**
   * 处理文件选择
   */
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (files && files.length > 0) {
      handleUpload(files[0])
    }
    // 重置 input 以便再次选择同一文件
    e.target.value = ''
  }

  /**
   * 根据状态筛选笔记
   */
  const filteredNotes: NoteInFolder[] = folderDetail
    ? folderDetail.notes.filter((note) =>
        statusFilter === 'all' ? true : getStatusCategory(note.status) === statusFilter
      )
    : []

  return (
    <div className="page-enter">
      {/* 页面标题和操作按钮 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-lg)' }}>
        <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem' }}>
          今日资料
        </h1>
        <button
          className="btn btn-primary"
          onClick={handleCreateFolder}
          disabled={creating}
        >
          {creating ? '创建中...' : '新建文件夹'}
        </button>
      </div>

      {/* 文件夹列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchFolders} />
      ) : folders.length === 0 ? (
        <EmptyState
          message="还没有文件夹"
          description="创建一个文件夹来组织今天的学习资料"
          action={
            <button className="btn btn-primary" onClick={handleCreateFolder}>
              新建文件夹
            </button>
          }
        />
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {folders.map((folder) => (
            <div key={folder.id} className="card" style={{ overflow: 'hidden' }}>
              {/* 文件夹头部：点击展开/折叠 */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  cursor: 'pointer',
                  padding: 'var(--space-md)',
                }}
                onClick={() => toggleFolder(folder.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') toggleFolder(folder.id) }}
                aria-expanded={expandedFolderId === folder.id}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
                  {/* 展开/折叠箭头 */}
                  <span
                    style={{
                      transition: 'transform 0.2s',
                      transform: expandedFolderId === folder.id ? 'rotate(90deg)' : 'rotate(0deg)',
                      color: 'var(--color-text-secondary)',
                      fontSize: '0.75rem',
                    }}
                  >
                    ▶
                  </span>
                  <div style={{ flex: 1 }}>
                    {editingFolderId === folder.id ? (
                      <input
                        ref={renameInputRef}
                        type="text"
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        onKeyDown={(e) => handleRenameKeyDown(folder.id, e)}
                        onClick={(e) => e.stopPropagation()}
                        style={{
                          width: '100%',
                          maxWidth: '360px',
                          fontSize: '1rem',
                          fontWeight: 500,
                          padding: '4px 8px',
                          marginBottom: 'var(--space-xs)',
                          border: '1px solid var(--color-primary)',
                          borderRadius: '4px',
                          outline: 'none',
                        }}
                        disabled={renaming}
                        aria-label="文件夹名称"
                      />
                    ) : (
                      <h3 style={{ fontWeight: 500, marginBottom: 'var(--space-xs)' }}>
                        {folder.name}
                      </h3>
                    )}
                    <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                        {formatDate(folder.folder_date)}
                      </span>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                        {folder.note_count} 个文件
                      </span>
                    </div>
                  </div>
                </div>
                {/* 文件夹操作按钮：编辑态显示保存/取消，否则显示重命名/删除 */}
                <div style={{ display: 'flex', gap: 'var(--space-xs)', alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                  {editingFolderId === folder.id ? (
                    <>
                      <button
                        className="btn btn-primary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => saveRenameFolder(folder.id, e)}
                        disabled={renaming}
                        aria-label="保存名称"
                      >
                        {renaming ? '保存中...' : '保存'}
                      </button>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => cancelRenameFolder(e)}
                        disabled={renaming}
                        aria-label="取消重命名"
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => startRenameFolder(folder, e)}
                        aria-label="重命名文件夹"
                      >
                        重命名
                      </button>
                      {/* 仅空文件夹可删除 */}
                      {folder.note_count === 0 && (
                        <button
                          className="btn btn-danger"
                          style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                          onClick={(e) => handleDeleteFolder(folder.id, e)}
                          aria-label="删除文件夹"
                        >
                          删除
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>

              {/* 文件夹详情：展开时显示 */}
              {expandedFolderId === folder.id && (
                <div style={{ borderTop: '1px solid var(--color-border)', padding: 'var(--space-md)' }}>
                  {detailLoading ? (
                    <LoadingSpinner text="加载中..." />
                  ) : detailError ? (
                    <ErrorDisplay message={detailError} onRetry={() => toggleFolder(folder.id)} />
                  ) : (
                    <>
                      {/* 上传按钮和状态筛选 */}
                      <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: 'var(--space-md)',
                        flexWrap: 'wrap',
                        gap: 'var(--space-sm)',
                      }}>
                        <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
                          <button
                            className="btn btn-primary"
                            style={{ fontSize: '0.875rem' }}
                            onClick={() => fileInputRef.current?.click()}
                            disabled={uploading}
                          >
                            {uploading ? '上传中...' : '上传文件'}
                          </button>
                          <input
                            ref={fileInputRef}
                            type="file"
                            accept={ALLOWED_EXTENSIONS.join(',')}
                            onChange={handleFileChange}
                            style={{ display: 'none' }}
                            aria-hidden="true"
                          />
                          {uploadStatus && (
                            <span style={{ fontSize: '0.8rem', color: 'var(--color-primary)' }}>
                              {uploadStatus}
                            </span>
                          )}
                        </div>
                        {/* 状态筛选标签 */}
                        <div style={{ display: 'flex', gap: 'var(--space-xs)' }}>
                          {STATUS_FILTERS.map((filter) => (
                            <button
                              key={filter.key}
                              className={`filter-pill${statusFilter === filter.key ? ' filter-pill-active' : ''}`}
                              onClick={() => setStatusFilter(filter.key)}
                            >
                              {filter.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* 笔记列表 */}
                      {filteredNotes.length === 0 ? (
                        <EmptyState
                          message={statusFilter !== 'all' ? '没有符合筛选条件的文件' : '文件夹为空'}
                          description={statusFilter !== 'all' ? undefined : '点击上方按钮上传学习资料'}
                        />
                      ) : (
                        <div style={{ display: 'grid', gap: 'var(--space-sm)' }}>
                          {filteredNotes.map((note) => (
                            <div
                              key={note.id}
                              className="card card-hover"
                              style={{
                                cursor: 'pointer',
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                padding: 'var(--space-sm) var(--space-md)',
                              }}
                              onClick={() => navigate(`/notes/${note.id}`)}
                              role="button"
                              tabIndex={0}
                              onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/notes/${note.id}`) }}
                            >
                              <div style={{ flex: 1 }}>
                                <h4 style={{ fontWeight: 500, marginBottom: 'var(--space-xs)', fontSize: '0.9rem' }}>
                                  {note.title}
                                </h4>
                                <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center', flexWrap: 'wrap' }}>
                                  {/* 来源类型标签 */}
                                  <span className={`badge badge-${note.source_type}`}>
                                    {sourceTypeLabels[note.source_type] || note.source_type}
                                  </span>
                                  {/* 处理状态标签 */}
                                  <span className={`status-${note.status}`} style={{ fontSize: '0.8rem' }}>
                                    {statusLabels[note.status] || note.status}
                                  </span>
                                  {/* 文件大小 */}
                                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                                    {formatFileSize(note.file_size)}
                                  </span>
                                  {/* 上传时间 */}
                                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                                    {new Date(note.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                                  </span>
                                </div>
                              </div>
                              <span style={{ color: 'var(--color-text-secondary)' }} aria-hidden="true">→</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

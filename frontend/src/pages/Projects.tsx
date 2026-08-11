/**
 * @file 项目页面
 * @description 项目隔离 + 状态旁载 Vault 结构下的项目管理页面。
 *
 * 功能：
 * 1. 创建项目 — 后端会同步在磁盘/对象存储中创建 Vault 目录树（source/output/history/cache）
 * 2. 重命名 / 删除项目（仅空项目可删）
 * 3. 查看项目下的笔记列表
 * 4. 扫描导入 — 用户手动把文件拷入项目 source/ 目录后，点击扫描将其识别为笔记
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
  getProjectDetail,
  scanProject,
  getNotes,
  addNotesToProject,
  removeNoteFromProject,
  type Project,
  type ProjectDetail,
  type Note,
  type NoteInFolder,
  type ScanImportResponse,
} from '../api/client'

/** 来源类型徽章样式映射（与全局 .badge-* 对应） */
const TYPE_BADGE: Record<string, string> = {
  pdf: 'badge-pdf',
  image: 'badge-image',
  docx: 'badge-docx',
  pptx: 'badge-pptx',
  xlsx: 'badge-xlsx',
  audio: 'badge-audio',
  video: 'badge-video',
  markdown: 'badge-markdown',
}

/** 状态颜色映射（与全局 .status-* 对应） */
const STATUS_CLASS: Record<string, string> = {
  uploading: 'status-uploading',
  converting: 'status-converting',
  converted: 'status-converted',
  cleaning: 'status-cleaning',
  cleaned: 'status-cleaned',
  learning: 'status-converting',
  cleaning_failed: 'status-cleaning-failed',
  learning_failed: 'status-failed', // 全局无 .status-learning-failed，复用失败红
  archived: 'status-converted', // 全局无 .status-archived，归档视为完成态，复用成功绿
  failed: 'status-failed',
}

/** 格式化文件大小 */
function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function Projects() {
  const navigate = useNavigate()

  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 新建项目表单
  const [showForm, setShowForm] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState('')

  // 重命名（行内编辑）
  const [renaming, setRenaming] = useState<Record<string, { name: string; description: string }>>({})

  // 展开的笔记列表
  const [expanded, setExpanded] = useState<Record<string, ProjectDetail | null>>({})

  // 扫描导入
  const [scanning, setScanning] = useState<Record<string, boolean>>({})
  const [scanResults, setScanResults] = useState<Record<string, ScanImportResponse | null>>({})

  // 添加笔记面板（同时只打开一个）
  const [addPanelProject, setAddPanelProject] = useState<Project | null>(null)
  const [candidateNotes, setCandidateNotes] = useState<Note[]>([])
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([])
  const [addSearch, setAddSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')

  /** 加载项目列表 */
  async function loadProjects() {
    setLoading(true)
    setError('')
    try {
      const data = await getProjects()
      setProjects(data)
    } catch (err) {
      console.error('加载项目列表失败:', err)
      setError('加载项目列表失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadProjects()
  }, [])

  /** 新建项目 */
  async function handleCreate() {
    const name = newName.trim()
    if (!name) {
      setFormError('请输入项目名称')
      return
    }
    setCreating(true)
    setFormError('')
    try {
      await createProject(name, newDesc.trim() || undefined)
      setNewName('')
      setNewDesc('')
      setShowForm(false)
      await loadProjects()
    } catch (err) {
      console.error('创建项目失败:', err)
      setFormError('创建项目失败，请稍后重试')
    } finally {
      setCreating(false)
    }
  }

  /** 开始重命名 */
  function startRename(p: Project) {
    setRenaming((prev) => ({ ...prev, [p.id]: { name: p.name, description: p.description ?? '' } }))
  }

  /** 提交重命名 */
  async function handleRename(p: Project) {
    const edit = renaming[p.id]
    if (!edit) return
    const name = edit.name.trim()
    if (!name) {
      setError('项目名称不能为空')
      return
    }
    try {
      await updateProject(p.id, edit.name.trim(), edit.description.trim() || undefined)
      setRenaming((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
      await loadProjects()
    } catch (err) {
      console.error('重命名项目失败:', err)
      setError('重命名项目失败，请稍后重试')
    }
  }

  /** 取消重命名 */
  function cancelRename(p: Project) {
    setRenaming((prev) => {
      const next = { ...prev }
      delete next[p.id]
      return next
    })
  }

  /** 删除项目（仅空项目可删） */
  async function handleDelete(p: Project) {
    if (p.note_count > 0) {
      setError('项目内还有笔记，请先删除项目内的笔记后再删除项目')
      return
    }
    if (!window.confirm(`确定删除项目「${p.name}」？该项目目录树（${p.vault_path}）将被一并清理。`)) {
      return
    }
    try {
      await deleteProject(p.id)
      await loadProjects()
    } catch (err) {
      console.error('删除项目失败:', err)
      setError('删除项目失败，请稍后重试')
    }
  }

  /** 展开/收起笔记列表 */
  async function toggleExpand(p: Project) {
    if (expanded[p.id]) {
      setExpanded((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
      return
    }
    try {
      const detail = await getProjectDetail(p.id)
      setExpanded((prev) => ({ ...prev, [p.id]: detail }))
    } catch (err) {
      console.error('加载项目详情失败:', err)
      setError('加载项目笔记失败，请稍后重试')
    }
  }

  /** 扫描导入项目 source/ 目录的新文件 */
  async function handleScan(p: Project) {
    setScanning((prev) => ({ ...prev, [p.id]: true }))
    setScanResults((prev) => ({ ...prev, [p.id]: null }))
    try {
      const result = await scanProject(p.id)
      setScanResults((prev) => ({ ...prev, [p.id]: result }))
      // 有新导入时刷新项目笔记数
      if (result.imported > 0) {
        await loadProjects()
      }
    } catch (err) {
      console.error('扫描导入失败:', err)
      setError('扫描导入失败，请确认后端服务可用后重试')
    } finally {
      setScanning((prev) => {
        const next = { ...prev }
        delete next[p.id]
        return next
      })
    }
  }

  /** 若项目处于展开状态，重新拉取详情以同步笔记列表 */
  async function refreshExpandedDetail(p: Project) {
    if (!expanded[p.id]) return
    try {
      const detail = await getProjectDetail(p.id)
      setExpanded((prev) => ({ ...prev, [p.id]: detail }))
    } catch (err) {
      console.error('刷新项目详情失败:', err)
    }
  }

  /** 打开添加笔记面板：加载候选笔记（不属于当前项目的笔记） */
  async function handleOpenAddPanel(p: Project) {
    setAddPanelProject(p)
    setSelectedNoteIds([])
    setAddSearch('')
    setAddError('')
    try {
      // 拉取全部笔记（分页上限 999），过滤出不属于当前项目的作为候选
      const data = await getNotes(1, 999, undefined, undefined)
      setCandidateNotes(data.items.filter((n) => n.project_id !== p.id))
    } catch (err) {
      console.error('加载候选笔记失败:', err)
      setAddError('加载候选笔记失败，请稍后重试')
      setCandidateNotes([])
    }
  }

  /** 关闭添加笔记面板 */
  function handleCloseAddPanel() {
    setAddPanelProject(null)
    setCandidateNotes([])
    setSelectedNoteIds([])
    setAddSearch('')
    setAddError('')
  }

  /** 勾选/取消勾选候选笔记 */
  function toggleSelectNote(id: string) {
    setSelectedNoteIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  /** 确认添加选中笔记到项目 */
  async function handleConfirmAdd(p: Project) {
    if (selectedNoteIds.length === 0) {
      setAddError('请先勾选要添加的笔记')
      return
    }
    setAdding(true)
    setAddError('')
    try {
      await addNotesToProject(p.id, selectedNoteIds)
      handleCloseAddPanel()
      await loadProjects()
      await refreshExpandedDetail(p)
    } catch (err) {
      console.error('添加笔记失败:', err)
      setAddError('添加笔记失败，请稍后重试')
    } finally {
      setAdding(false)
    }
  }

  /** 将笔记移出项目 */
  async function handleRemoveNote(p: Project, n: NoteInFolder) {
    if (!window.confirm(`确定将笔记「${n.title}」移出项目「${p.name}」？`)) {
      return
    }
    try {
      await removeNoteFromProject(p.id, n.id)
      await loadProjects()
      await refreshExpandedDetail(p)
    } catch (err) {
      console.error('移出笔记失败:', err)
      setError('移出笔记失败，请稍后重试')
    }
  }

  /** 渲染项目卡片 */
  function renderCard(p: Project, index: number) {
    const isRenaming = !!renaming[p.id]
    const isExpanded = !!expanded[p.id]
    const detail = expanded[p.id]
    const notes: NoteInFolder[] = detail?.notes ?? []
    const scanResult = scanResults[p.id]
    const isScanning = !!scanning[p.id]
    const panelOpen = addPanelProject?.id === p.id
    const addKeyword = addSearch.trim().toLowerCase()
    const filteredCandidates = candidateNotes.filter(
      (n) => !addKeyword || n.title.toLowerCase().includes(addKeyword)
    )

    return (
      <div
        key={p.id}
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
                value={renaming[p.id].name}
                onChange={(e) =>
                  setRenaming((prev) => ({
                    ...prev,
                    [p.id]: { ...prev[p.id], name: e.target.value },
                  }))
                }
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
                {p.note_count} 篇笔记
              </span>
              {p.slug !== 'default' && (
                <span className="badge" style={{ background: 'var(--color-accent-light)', color: 'var(--color-accent)' }}>
                  {p.slug}
                </span>
              )}
            </div>
          </div>
          {/* 操作按钮 */}
          <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            <button
              className="btn btn-ghost"
              title="从已有笔记中选择并添加到本项目"
              onClick={() => handleOpenAddPanel(p)}
              style={{ fontSize: '0.8rem', padding: '4px 10px' }}
            >
              添加笔记
            </button>
            <button
              className="btn btn-ghost"
              title="扫描导入 source/ 目录中的新文件"
              onClick={() => handleScan(p)}
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input
              value={renaming[p.id].name}
              onChange={(e) =>
                setRenaming((prev) => ({ ...prev, [p.id]: { ...prev[p.id], name: e.target.value } }))
              }
              placeholder="项目名称"
            />
            <textarea
              value={renaming[p.id].description}
              onChange={(e) =>
                setRenaming((prev) => ({ ...prev, [p.id]: { ...prev[p.id], description: e.target.value } }))
              }
              placeholder="项目描述（可选）"
              rows={2}
              style={{ resize: 'vertical' }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" style={{ fontSize: '0.8rem', padding: '6px 14px' }} onClick={() => handleRename(p)}>
                保存
              </button>
              <button className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '6px 14px' }} onClick={() => cancelRename(p)}>
                取消
              </button>
            </div>
          </div>
        )}

        {/* 扫描结果 */}
        {scanResult && (
          <div
            style={{
              background: 'var(--color-bg)',
              border: '1px solid var(--color-border-light)',
              borderRadius: 'var(--radius-sm)',
              padding: 10,
              fontSize: '0.8rem',
            }}
          >
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>扫描结果</span>
              <span className="status-converted" style={{ fontWeight: 600 }}>新增 {scanResult.imported}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>跳过 {scanResult.skipped}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>不支持 {scanResult.unsupported}</span>
            </div>
            {scanResult.imported === 0 && scanResult.scanned === 0 && (
              <div style={{ marginTop: 6, color: 'var(--color-text-secondary)' }}>
                未在 <code style={{ color: 'var(--color-primary)' }}>source/</code> 目录发现新文件。可把文件拷贝到{' '}
                <code style={{ color: 'var(--color-primary)' }}>{p.vault_path}/source/</code> 后再扫描。
              </div>
            )}
          </div>
        )}

        {/* 添加笔记面板 */}
        {panelOpen && (
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
              <button style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary)' }} onClick={handleCloseAddPanel}>
                ✕
              </button>
            </div>
            <input
              value={addSearch}
              onChange={(e) => setAddSearch(e.target.value)}
              placeholder="按标题搜索候选笔记…"
              style={{ width: '100%', marginBottom: 8, fontSize: '0.8rem' }}
            />
            {addError && <div style={{ color: 'var(--color-error)', fontSize: '0.8rem', marginBottom: 8 }}>{addError}</div>}
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
                      onChange={() => toggleSelectNote(n.id)}
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
                    <span className={STATUS_CLASS[n.status] ?? ''} style={{ fontSize: '0.7rem', flexShrink: 0 }}>
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
                onClick={() => handleConfirmAdd(p)}
                disabled={adding || selectedNoteIds.length === 0}
              >
                {adding ? '添加中…' : `添加（${selectedNoteIds.length}）`}
              </button>
              <button className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '4px 12px' }} onClick={handleCloseAddPanel}>
                取消
              </button>
            </div>
          </div>
        )}

        {/* Vault 路径 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: '0.75rem',
            color: 'var(--color-text-tertiary)',
            background: 'var(--color-bg)',
            borderRadius: 'var(--radius-sm)',
            padding: '6px 10px',
            fontFamily: 'var(--font-mono)',
            wordBreak: 'break-all',
          }}
        >
          <span style={{ flexShrink: 0 }}>📁</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>Vault:</span>
          {p.vault_path}/source/
        </div>

        {/* 底部操作行 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, borderTop: '1px solid var(--color-border-light)', paddingTop: 10, marginTop: 'auto' }}>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.8rem', padding: '4px 8px' }}
            onClick={() => toggleExpand(p)}
          >
            <span className={`collapse-arrow ${isExpanded ? 'collapse-arrow-open' : ''}`}>▶</span>
            {isExpanded ? '收起笔记' : `查看笔记（${p.note_count}）`}
          </button>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="btn btn-ghost"
              style={{ fontSize: '0.8rem', padding: '4px 8px' }}
              onClick={() => startRename(p)}
            >
              重命名
            </button>
            <button
              className="btn btn-ghost"
              style={{ fontSize: '0.8rem', padding: '4px 8px', color: 'var(--color-error)' }}
              onClick={() => handleDelete(p)}
            >
              删除
            </button>
          </div>
        </div>

        {/* 笔记列表 */}
        {isExpanded && (
          <div style={{ borderTop: '1px solid var(--color-border-light)', paddingTop: 10 }}>
            {notes.length === 0 ? (
              <div style={{ fontSize: '0.85rem', color: 'var(--color-text-tertiary)', textAlign: 'center', padding: '16px 0' }}>
                项目暂无笔记。可把文件放入{' '}
                <code style={{ color: 'var(--color-primary)' }}>{p.vault_path}/source/</code>{' '}
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
                    <span className={STATUS_CLASS[n.status] ?? ''} style={{ fontSize: '0.75rem', flexShrink: 0 }}>
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
                        handleRemoveNote(p, n)
                      }}
                    >
                      移出
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="page-enter">
      {/* 页面头部 */}
      <div className="assessment-header">
        <h1 className="assessment-title">项目</h1>
        <p className="assessment-subtitle">
          项目隔离 + 状态旁载：每个项目在 Vault 中对应一个独立文件夹，支持手动放盘后扫描导入。
        </p>
      </div>

      {error && (
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
            onClick={() => setError('')}
          >
            ✕
          </button>
        </div>
      )}

      {/* 新建项目 */}
      <div className="card" style={{ marginBottom: 24, padding: 20 }}>
        {!showForm ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>创建新项目</h3>
              <p style={{ margin: '4px 0 0', fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                创建后会在 Vault 目录下生成项目文件夹（source / output / history / cache）
              </p>
            </div>
            <button className="btn btn-primary" onClick={() => setShowForm(true)}>
              ＋ 新建项目
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>新建项目</h3>
            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>项目名称</label>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="如：Transformer 论文精读"
                autoFocus
              />
            </div>
            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>项目描述（可选）</label>
              <textarea
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="这个项目是做什么的？"
                rows={2}
                style={{ resize: 'vertical' }}
              />
            </div>
            {formError && <div style={{ color: 'var(--color-error)', fontSize: '0.8rem' }}>{formError}</div>}
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
                {creating ? '创建中…' : '创建项目'}
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => {
                  setShowForm(false)
                  setFormError('')
                }}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 项目列表 */}
      {loading ? (
        <div className="state-container">
          <div className="spinner" />
          <p className="state-message">正在加载项目…</p>
        </div>
      ) : projects.length === 0 ? (
        <div className="state-container">
          <div className="state-icon" style={{ fontSize: 40 }}>📂</div>
          <p className="state-message">还没有项目</p>
          <p className="state-description">点击上方「新建项目」创建第一个项目，系统会同步生成对应的 Vault 文件夹。</p>
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
            gap: 16,
            alignItems: 'stretch',
          }}
        >
          {projects.map((p, i) => renderCard(p, i))}
        </div>
      )}

      {/* 使用说明 */}
      <div
        className="card"
        style={{
          marginTop: 24,
          background: 'var(--color-bg)',
          border: '1px dashed var(--color-border)',
          fontSize: '0.85rem',
          color: 'var(--color-text-secondary)',
          lineHeight: 1.8,
        }}
      >
        <strong style={{ color: 'var(--color-text)' }}>📖 使用说明</strong>
        <ol style={{ margin: '8px 0 0 20px', padding: 0 }}>
          <li>创建项目后，在应用数据目录（Vault）下会生成与项目同名的文件夹。</li>
          <li>把要学习的文件直接拷贝到该项目的 <code style={{ color: 'var(--color-primary)' }}>source/</code> 子目录。</li>
          <li>回到本页点击项目卡片上的「扫描导入」，新文件会自动识别为笔记并开始转换。</li>
          <li>也可以在上传页选择该项目，通过网页直接上传文件。</li>
        </ol>
      </div>
    </div>
  )
}

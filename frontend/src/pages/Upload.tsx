/**
 * @file 上传页面
 * @description 文件上传页面，支持拖拽和点击两种上传方式。
 * 上传后自动轮询后端转换状态，转换完成后自动跳转到笔记详情页。
 * 支持的文件格式：PDF、图片、Office 文档、音视频文件。
 */
import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  prepareUpload, commitUpload,
  getUploadStatus, getNotes, getProjects,
  type Note, type Project, type PreparedUpload,
} from '../api/client'

/** 允许上传的文件扩展名列表，与后端支持的格式保持一致 */
const ALLOWED_EXTENSIONS = [
  '.pdf', '.png', '.jpg', '.jpeg', '.docx', '.pptx', '.xlsx',
  '.mp4', '.mp3', '.wav', '.m4a', '.md',
]

/** 解析后端选项 */
const BACKEND_OPTIONS = [
  { value: '', label: '自动', description: '使用后端默认配置' },
  { value: 'pipeline', label: '本地解析', description: '使用本地模型解析（需要本地环境支持）' },
  { value: 'vlm-http-client', label: '云端解析', description: '使用云端API解析（需要API Token）' },
] as const

/** 笔记角色选项 */
const NOTE_ROLE_OPTIONS = [
  { value: 'material', label: '学习资料' },
  { value: 'personal_note', label: '我的笔记' },
] as const

/**
 * 上传页面组件
 *
 * 数据流：
 * 1. 用户选择/拖拽文件 → 前端校验文件格式
 * 2. 调用 uploadFile() 上传文件到后端
 * 3. 上传成功后获取 noteId，开始轮询转换状态
 * 4. 转换完成后自动跳转到笔记详情页
 *
 * 状态管理：
 * - dragActive: 拖拽区域是否激活（文件悬停时高亮）
 * - uploading: 是否正在上传/转换中
 * - error: 错误提示信息
 * - noteId: 上传成功后返回的笔记 ID
 * - status: 当前处理状态文本，用于界面展示
 */
export default function Upload() {
  const navigate = useNavigate()
  /** 隐藏的文件输入框引用，用于点击上传区域时触发文件选择 */
  const fileInputRef = useRef<HTMLInputElement>(null)
  /** 拖拽区域是否激活，用于高亮显示拖拽反馈 */
  const [dragActive, setDragActive] = useState(false)
  /** 是否正在上传/转换中，控制按钮禁用和界面状态 */
  const [uploading, setUploading] = useState(false)
  /** 错误提示信息 */
  const [error, setError] = useState('')
  /** 上传成功后返回的笔记 ID，用于轮询状态和跳转 */
  const [noteId, setNoteId] = useState<string | null>(null)
  /** 当前处理状态文本，展示给用户 */
  const [status, setStatus] = useState<string | null>(null)
  /** 解析后端选择，空字符串表示使用后端默认配置 */
  const [parseBackend, setParseBackend] = useState('')
  /** 笔记角色选择，默认为学习资料 */
  const [noteRole, setNoteRole] = useState('material')
  /** 项目列表 */
  const [projects, setProjects] = useState<Project[]>([])
  /** 选中的项目标签 ID 数组（空表示不归属任何项目） */
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([])
  /** 可关联的学习资料列表（仅 personal_note 时加载） */
  const [availableMaterials, setAvailableMaterials] = useState<Note[]>([])
  /** 已选中的关联资料 ID 列表 */
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  /** 两阶段上传阶段 1 的暂存结果（prepare 成功后的临时文件信息） */
  const [prepared, setPrepared] = useState<PreparedUpload | null>(null)
  /** 重命名后的文件名输入 */
  const [renameName, setRenameName] = useState('')
  /** 重命名输入的内联校验提示 */
  const [renameError, setRenameError] = useState('')
  /** 裁剪页码范围输入 */
  const [cropPageRange, setCropPageRange] = useState('')
  /** 裁剪输入的内联校验提示 */
  const [cropError, setCropError] = useState('')

  // 加载项目列表，默认不选择项目（留空则不归属任何项目）
  useEffect(() => {
    const loadProjects = async () => {
      try {
        const data = await getProjects()
        setProjects(data)
        setSelectedProjectIds([])
      } catch (err) {
        console.error('加载项目列表失败:', err)
      }
    }
    loadProjects()
  }, [])

  // 当选择"我的笔记"角色时加载可关联的学习资料
  useEffect(() => {
    if (noteRole === 'personal_note') {
      const loadMaterials = async () => {
        try {
          const data = await getNotes(1, 100, undefined, 'material')
          setAvailableMaterials(data.items || [])
        } catch (err) {
          console.error('加载资料列表失败:', err)
        }
      }
      loadMaterials()
    } else {
      setSelectedMaterialIds([])
    }
  }, [noteRole])

  /**
   * 处理文件选择（两阶段上传阶段 1：prepare）
   * 校验文件格式后调用 prepareUpload 暂存文件；PDF 返回页数并展示裁剪配置，
   * 非 PDF 直接进入 commit 提交。
   *
   * @param file - 用户选择的文件对象
   */
  async function handleUpload(file: File) {
    // 提取文件扩展名并校验格式
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError(`不支持的文件格式: ${ext}`)
      return
    }

    setError('')
    setRenameError('')
    setCropError('')
    setRenameName('')
    setCropPageRange('')
    setPrepared(null)
    setUploading(true)
    setStatus('上传中...')

    try {
      // 阶段 1：暂存文件，获取页数等信息
      const result = await prepareUpload(file)
      // 统一进入上传设置步骤（可重命名文件；PDF 额外支持按页裁剪）
      setRenameName(result.filename)
      setPrepared(result)
      setUploading(false)
      setStatus(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
      setUploading(false)
    }
  }

  /**
   * 校验页码范围表达式格式（1-based，支持 a、a-b、逗号组合，不越界）
   *
   * @param spec - 页码范围表达式，如 "1-20,25,30-32"
   * @param pageCount - PDF 总页数
   * @returns 空字符串表示合法，否则返回错误提示
   */
  function validatePageSpec(spec: string, pageCount: number): string {
    const trimmed = spec.trim()
    if (!trimmed) return '页码范围不能为空'
    const items = trimmed.split(',')
    const pageRangeRe = /^(\d+)(?:-(\d+))?$/
    const pages: number[] = []
    for (const item of items) {
      const match = pageRangeRe.exec(item.trim())
      if (!match) return `无效的页码范围: ${item.trim()}`
      const start = parseInt(match[1], 10)
      const end = match[2] !== undefined ? parseInt(match[2], 10) : start
      if (start < 1 || end < 1) return `页码必须从 1 开始: ${item.trim()}`
      if (start > pageCount || end > pageCount) return `页码超出范围（文档共 ${pageCount} 页）: ${item.trim()}`
      if (start > end) return `区间起始页不能大于结束页: ${item.trim()}`
      for (let p = start; p <= end; p++) pages.push(p)
    }
    if (pages.length === 0) return '页码范围不能为空'
    return ''
  }

  /**
   * 校验并确定最终文件名
   * 规则：非空、不含路径分隔符；若缺少原文件扩展名则自动补全（扩展名必须保留，
   * 与后端校验一致）。
   *
   * @param name - 用户输入的文件名
   * @param originalFilename - 原始文件名（用于提取扩展名）
   * @returns 空字符串表示合法并返回最终文件名；否则返回错误提示
   */
  function validateRename(name: string, originalFilename: string): { name: string; error: string } {
    const trimmed = name.trim()
    if (!trimmed) return { name: '', error: '文件名不能为空' }
    if (trimmed.includes('/') || trimmed.includes('\\')) {
      return { name: '', error: '文件名不能包含路径分隔符' }
    }
    const dotIndex = originalFilename.lastIndexOf('.')
    const ext = dotIndex >= 0 ? originalFilename.slice(dotIndex) : ''
    const finalName = ext && !trimmed.toLowerCase().endsWith(ext.toLowerCase())
      ? `${trimmed}${ext}`
      : trimmed
    return { name: finalName, error: '' }
  }

  /**
   * 校验上传设置并提交
   * 校验文件名（及 PDF 页码范围）后调用 commitPrepared 正式上传。
   *
   * @param cropRange - 页码范围表达式（可选，仅 PDF；undefined 表示不裁剪）
   */
  function confirmUpload(cropRange: string | undefined) {
    if (!prepared) return
    const { name, error: renameErr } = validateRename(renameName, prepared.filename)
    if (renameErr) {
      setRenameError(renameErr)
      return
    }
    if (prepared.source_type === 'pdf' && cropRange !== undefined) {
      const pageCount = prepared.page_count ?? 0
      const cropErr = validatePageSpec(cropRange, pageCount)
      if (cropErr) {
        setCropError(cropErr)
        return
      }
    }
    commitPrepared(prepared.temp_id, cropRange, name)
  }

  /**
   * 两阶段上传阶段 2：commit
   * 携带可选重命名与裁剪范围调用 commitUpload 正式上传，成功后开始轮询转换状态。
   *
   * @param tempId - prepare 返回的临时上传标识
   * @param cropRange - 页码范围表达式（可选，仅 PDF）
   * @param finalName - 最终文件名（重命名后的，含扩展名）
   */
  async function commitPrepared(tempId: string, cropRange: string | undefined, finalName: string) {
    setError('')
    setUploading(true)
    setStatus('上传中...')

    try {
      const note = await commitUpload(tempId, {
        filename: finalName,
        backend: parseBackend || undefined,
        note_role: noteRole,
        project_ids: selectedProjectIds.length > 0 ? selectedProjectIds : undefined,
        linked_material_ids: selectedMaterialIds,
        crop_page_range: cropRange,
      })
      setNoteId(note.id)
      setPrepared(null)
      setStatus('文件已上传，正在转换...')

      // 开始轮询转换状态
      pollStatus(note.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
      setUploading(false)
    }
  }

  /**
   * 轮询笔记转换状态
   * 每 5 秒查询一次后端状态，直到转换完成、失败或超时。
   *
   * 转换完成条件：status 为 converted / cleaned / archived
   * 失败条件：status 为 failed
   * 超时限制：最多轮询 120 次（约 10 分钟）
   *
   * @param id - 笔记 ID
   */
  async function pollStatus(id: string) {
    const maxAttempts = 120 // 最多轮询 120 次（约 10 分钟）
    let attempts = 0

    const interval = setInterval(async () => {
      attempts++
      try {
        const res = await getUploadStatus(id)
        setStatus(`状态: ${res.status}`)

        // 转换完成：状态为已转换/已清洗/已归档时视为成功
        if (res.status === 'converted' || res.status === 'cleaned' || res.status === 'archived') {
          clearInterval(interval)
          setUploading(false)
          setStatus('转换完成！')
          // 延迟 1 秒后自动跳转到笔记详情页
          setTimeout(() => navigate(`/notes/${id}`), 1000)
        } else if (res.status === 'failed') {
          // 转换失败：停止轮询并显示错误信息
          clearInterval(interval)
          setUploading(false)
          setError(res.error_message || '转换失败')
        }
      } catch {
        // 轮询过程中的网络错误，继续尝试
      }

      // 超时处理：超过最大尝试次数后停止轮询
      if (attempts >= maxAttempts) {
        clearInterval(interval)
        setUploading(false)
        setError('转换超时，请稍后在笔记列表中查看')
      }
    }, 5000) // 每 5 秒轮询一次
  }

  /**
   * 处理文件拖放
   * 获取拖放的第一个文件并触发上传。
   */
  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragActive(false)

    const files = e.dataTransfer.files
    if (files.length > 0) {
      handleUpload(files[0])
    }
  }

  /**
   * 处理拖拽悬停
   * 阻止默认行为并激活拖拽高亮。
   */
  function handleDragOver(e: React.DragEvent) {
    e.preventDefault()
    setDragActive(true)
  }

  /**
   * 处理拖拽离开
   * 取消拖拽高亮状态。
   */
  function handleDragLeave() {
    setDragActive(false)
  }

  /**
   * 处理文件选择框变化
   * 获取选择的第一个文件并触发上传。
   */
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (files && files.length > 0) {
      handleUpload(files[0])
    }
  }

  return (
    <div className="page-enter" style={{ maxWidth: '640px', margin: '0 auto' }}>
      <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem', marginBottom: 'var(--space-lg)' }}>
        上传学习资料
      </h1>

      {/* 拖拽上传区域：支持点击和拖拽两种方式 */}
      <div
        className={`upload-zone${dragActive ? ' upload-zone-active' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        style={{
          cursor: uploading ? 'wait' : 'pointer',
        }}
        role="button"
        tabIndex={0}
        aria-label="点击或拖拽文件上传"
        onKeyDown={(e) => { if (e.key === 'Enter') fileInputRef.current?.click() }}
      >
        {/* 隐藏的文件输入框，通过 ref 触发 */}
        <input
          ref={fileInputRef}
          type="file"
          accept={ALLOWED_EXTENSIONS.join(',')}
          onChange={handleFileChange}
          style={{ display: 'none' }}
          aria-hidden="true"
        />

        <p style={{ fontSize: '1.125rem', fontWeight: 500, marginBottom: 'var(--space-sm)' }}>
          {uploading ? '处理中...' : '点击或拖拽文件到此处'}
        </p>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          支持 PDF、图片、Office 文档、音视频、Markdown 文件
        </p>
        {/* 显示所有支持的文件扩展名 */}
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
          {ALLOWED_EXTENSIONS.join(' ')}
        </p>
      </div>

      {/* 上传设置（prepare 完成后显示，可重命名文件；PDF 额外支持按页裁剪） */}
      {prepared && (
        <div className="card" style={{ marginTop: 'var(--space-md)' }}>
          <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>
            上传设置{prepared.source_type === 'pdf' && `（共 ${prepared.page_count ?? '?'} 页）`}
          </p>

          {/* 文件名（可修改） */}
          <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-xs)' }}>
            文件名
          </p>
          <input
            type="text"
            value={renameName}
            onChange={(e) => { setRenameName(e.target.value); setRenameError('') }}
            disabled={uploading}
            style={{
              width: '100%', padding: 'var(--space-sm)', borderRadius: 'var(--radius-sm)',
              border: `1px solid ${renameError ? 'var(--color-error)' : 'var(--color-border)'}`,
              marginBottom: 'var(--space-xs)',
            }}
            aria-label="文件名"
          />
          {renameError && (
            <p style={{ fontSize: '0.75rem', color: 'var(--color-error)', marginBottom: 'var(--space-xs)' }} role="alert">
              {renameError}
            </p>
          )}

          {/* PDF 按页裁剪配置（仅 PDF 显示） */}
          {prepared.source_type === 'pdf' && (
            <>
              <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-xs)', marginTop: 'var(--space-sm)' }}>
                按页裁剪（可选）：只保留需要的页码范围，其余页面在上传处理前剔除。
              </p>
              <input
                type="text"
                value={cropPageRange}
                onChange={(e) => { setCropPageRange(e.target.value); setCropError('') }}
                placeholder="如 1-20,25,30-32"
                disabled={uploading}
                style={{
                  width: '100%', padding: 'var(--space-sm)', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${cropError ? 'var(--color-error)' : 'var(--color-border)'}`,
                }}
                aria-label="页码范围"
              />
              {cropError && (
                <p style={{ fontSize: '0.75rem', color: 'var(--color-error)', marginBottom: 'var(--space-xs)' }} role="alert">
                  {cropError}
                </p>
              )}
            </>
          )}

          <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap', marginTop: 'var(--space-md)' }}>
            {prepared.source_type === 'pdf' ? (
              <>
                <button
                  className="btn"
                  disabled={uploading}
                  onClick={() => confirmUpload(undefined)}
                >
                  不裁剪，直接上传
                </button>
                <button
                  className="btn btn-primary"
                  disabled={uploading}
                  onClick={() => confirmUpload(cropPageRange)}
                >
                  裁剪后上传
                </button>
              </>
            ) : (
              <button
                className="btn btn-primary"
                disabled={uploading}
                onClick={() => confirmUpload(undefined)}
              >
                确认上传
              </button>
            )}
          </div>
        </div>
      )}

      {/* 解析方式选择 */}
      <div className="card" style={{ marginTop: 'var(--space-md)' }}>
        <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>解析方式</p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {BACKEND_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`filter-pill${parseBackend === option.value ? ' filter-pill-active' : ''}`}
              onClick={() => setParseBackend(option.value)}
              disabled={uploading}
              title={option.description}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
          {BACKEND_OPTIONS.find((o) => o.value === parseBackend)?.description}
        </p>
      </div>

      {/* 项目标签选择（支持多选） */}
      <div className="card" style={{ marginTop: 'var(--space-md)' }}>
        <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>所属项目（标签，可多选）</p>
        {projects.length > 0 ? (
          <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
            {projects.map((project) => {
              const checked = selectedProjectIds.includes(project.id)
              return (
                <button
                  key={project.id}
                  type="button"
                  className={`filter-pill${checked ? ' filter-pill-active' : ''}`}
                  onClick={() => {
                    setSelectedProjectIds((prev) =>
                      checked ? prev.filter((id) => id !== project.id) : [...prev, project.id],
                    )
                  }}
                  disabled={uploading}
                >
                  {project.name}
                </button>
              )
            })}
          </div>
        ) : (
          <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)' }}>暂无项目，可先不选择或稍后创建。</p>
        )}
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
          一篇笔记可归属多个项目（标签）；不选择则稍后可在「项目」页面添加。
        </p>
      </div>

      {/* 笔记角色选择 */}
      <div className="card" style={{ marginTop: 'var(--space-md)' }}>
        <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>笔记类型</p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {NOTE_ROLE_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`filter-pill${noteRole === option.value ? ' filter-pill-active' : ''}`}
              onClick={() => setNoteRole(option.value)}
              disabled={uploading}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {/* 关联学习资料（仅 personal_note 时显示） */}
      {noteRole === 'personal_note' && availableMaterials.length > 0 && (
        <div className="card" style={{ marginTop: 'var(--space-md)' }}>
          <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>关联学习资料（可选）</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-xs)', maxHeight: '200px', overflowY: 'auto' }}>
            {availableMaterials.map(m => (
              <label key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-xs)', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedMaterialIds.includes(m.id)}
                  onChange={(e) => {
                    setSelectedMaterialIds(prev =>
                      e.target.checked ? [...prev, m.id] : prev.filter(id => id !== m.id)
                    )
                  }}
                  disabled={uploading}
                  // 覆盖全局 input{width:100%}，否则 checkbox 撑满整行、标题被挤成 0 宽
                  style={{ width: 'auto', margin: 0, padding: 0, flexShrink: 0 }}
                />
                <span style={{ flex: 1, minWidth: 0 }}>{m.title}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* 处理状态反馈 */}
      {status && (
        <div className="card" style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <p style={{ color: 'var(--color-primary)' }}>{status}</p>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="card" style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <p role="alert" style={{ color: 'var(--color-error)' }}>{error}</p>
        </div>
      )}

      {/* 上传完成后的跳转按钮（当自动跳转未生效时的备用入口） */}
      {noteId && !uploading && !error && (
        <div style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <button className="btn btn-primary" onClick={() => navigate(`/notes/${noteId}`)}>
            查看笔记
          </button>
        </div>
      )}
    </div>
  )
}

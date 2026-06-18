/**
 * @file 上传页面
 * @description 文件上传页面，支持拖拽和点击两种上传方式。
 * 上传后自动轮询后端转换状态，转换完成后自动跳转到笔记详情页。
 * 支持的文件格式：PDF、图片、Office 文档、音视频文件。
 */
import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadFile, getUploadStatus } from '../api/client'

/** 允许上传的文件扩展名列表，与后端支持的格式保持一致 */
const ALLOWED_EXTENSIONS = [
  '.pdf', '.png', '.jpg', '.jpeg', '.docx', '.pptx', '.xlsx',
  '.mp4', '.mp3', '.wav', '.m4a',
]

/** 解析后端选项 */
const BACKEND_OPTIONS = [
  { value: '', label: '自动', description: '使用后端默认配置' },
  { value: 'pipeline', label: '本地解析', description: '使用本地模型解析（需要本地环境支持）' },
  { value: 'vlm-http-client', label: '云端解析', description: '使用云端API解析（需要API Token）' },
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

  /**
   * 处理文件上传
   * 校验文件格式后调用 API 上传，成功后开始轮询转换状态。
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
    setUploading(true)
    setStatus('上传中...')

    try {
      // 调用上传 API，后端创建笔记记录并开始异步转换
      const note = await uploadFile(file, parseBackend || undefined)
      setNoteId(note.id)
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
    <div style={{ padding: 'var(--space-lg) 0', maxWidth: '600px', margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: 'var(--space-lg)' }}>
        上传学习资料
      </h1>

      {/* 拖拽上传区域：支持点击和拖拽两种方式 */}
      <div
        className="card"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        style={{
          // 拖拽激活时边框变为主色调，背景变为主色调浅色
          border: dragActive ? '2px dashed var(--color-primary)' : '2px dashed var(--color-border)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-xl)',
          textAlign: 'center',
          cursor: uploading ? 'wait' : 'pointer', // 上传中显示等待光标
          transition: 'border-color 0.15s ease',
          background: dragActive ? 'var(--color-primary-light)' : 'var(--color-surface)',
        }}
        role="button"
        tabIndex={0}
        aria-label="点击或拖拽文件上传"
        onKeyDown={(e) => { if (e.key === 'Enter') fileInputRef.current?.click() }} // 支持键盘 Enter 键触发文件选择
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
          支持 PDF、图片、Office 文档、音视频文件
        </p>
        {/* 显示所有支持的文件扩展名 */}
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
          {ALLOWED_EXTENSIONS.join(' ')}
        </p>
      </div>

      {/* 解析方式选择 */}
      <div className="card" style={{ marginTop: 'var(--space-md)' }}>
        <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>解析方式</p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {BACKEND_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`btn ${parseBackend === option.value ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setParseBackend(option.value)}
              disabled={uploading}
              title={option.description}
              style={{ fontSize: '0.875rem' }}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: 'var(--space-xs)' }}>
          {BACKEND_OPTIONS.find((o) => o.value === parseBackend)?.description}
        </p>
      </div>

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

/**
 * @file 笔记详情页面
 * @description 展示单条笔记的完整内容，包括：
 * 1. 笔记元信息（标题、来源类型、状态、页数、大小、创建时间）
 * 2. Markdown 内容渲染（支持代码高亮）
 * 3. 原始版/清洗版/对比视图三种模式切换
 * 4. 清洗操作面板（触发清洗、恢复/删除重复块）
 * 5. Diff 对比视图
 * 6. 删除笔记功能
 * 7. 处理中状态的等待提示
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'
import { marked } from '../utils/markdown'
import {
  getNote,
  deleteNote,
  getCleaningDiff,
  startUnderstanding,
  archiveNote,
  getKnowledgeCards,
  getQuestions,
  retryConvert,
  updateNoteRole,
  getToken,
  getAnnotations,
  createAnnotation,
  deleteAnnotation,
  getNotes,
  getNoteLinks,
  updateNoteLinks,
  updateNoteContent,
  type NoteDetail,
  type Note,
  type CleaningDiffResponse,
  type KnowledgeCard,
  type Annotation,
  type NoteLinksResponse,
  type NoteContentTarget,
} from '../api/client'
import CleaningPanel from '../components/CleaningPanel'
import DiffView from '../components/DiffView'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorDisplay from '../components/ErrorDisplay'
import VersionHistory from '../components/VersionHistory'
import { statusLabels } from '../utils/labels'

/** 视图模式 */
type ViewMode = 'original' | 'clean' | 'diff'

/**
 * 笔记详情页面组件
 */
export default function NoteDetail() {
  const { noteId } = useParams<{ noteId: string }>()
  const navigate = useNavigate()
  const [note, setNote] = useState<NoteDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 当前视图模式 */
  const [viewMode, setViewMode] = useState<ViewMode>('original')
  /** diff 数据 */
  const [diffData, setDiffData] = useState<CleaningDiffResponse | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  /** 关联知识卡片 */
  const [relatedCards, setRelatedCards] = useState<KnowledgeCard[]>([])
  /** 是否有关联题目（用于显示"立即复习"按钮） */
  const [hasQuizItems, setHasQuizItems] = useState(false)
  /** 视频播放的 blob URL */
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const viewModeRef = useRef(viewMode)
  viewModeRef.current = viewMode

  /** 批注相关 state */
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [showAnnotationMenu, setShowAnnotationMenu] = useState(false)
  const [annotationMenuPos, setAnnotationMenuPos] = useState({ x: 0, y: 0 })
  const markdownRef = useRef<HTMLElement>(null)

  /** 链接管理相关 state */
  const [noteLinks, setNoteLinks] = useState<NoteLinksResponse | null>(null)
  const [showLinkManager, setShowLinkManager] = useState(false)
  const [linkMaterialIds, setLinkMaterialIds] = useState<string[]>([])
  const [availableMaterials, setAvailableMaterials] = useState<Note[]>([])

  /** 编辑模式相关 state */
  const [editMode, setEditMode] = useState<'view' | 'edit' | 'preview'>('view')
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)

  /** 版本历史面板显示状态 */
  const [showVersionHistory, setShowVersionHistory] = useState(false)

  /** 获取笔记详情 */
  const fetchNote = useCallback(async () => {
    if (!noteId) return
    try {
      const data = await getNote(noteId)
      setNote(data)
      // 如果笔记已清洗/已归档且当前显示原始版，自动切换到清洗版
      if ((data.status === 'cleaned' || data.status === 'archived' || data.status === 'learning_failed') && data.clean_md_content && viewModeRef.current === 'original') {
        setViewMode('clean')
      }
      // 获取关联知识卡片（已学习过的笔记取消审阅后状态为 cleaned，也需加载旧卡片）
      if (data.status === 'archived' || data.status === 'learning' || data.status === 'learning_failed' || data.metadata_?.learned_at !== undefined) {
        try {
          const cardData = await getKnowledgeCards(1, 999, noteId)
          setRelatedCards(cardData.items)
        } catch {
          setRelatedCards([])
        }
        // 检查是否有关联题目
        try {
          const quizData = await getQuestions(1, 1, noteId)
          setHasQuizItems(quizData.total > 0)
        } catch {
          setHasQuizItems(false)
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [noteId])

  // 组件挂载或 noteId 变化时获取笔记详情
  useEffect(() => {
    setLoading(true)
    fetchNote()
  }, [noteId])

  // 切换到 diff 模式时加载 diff 数据
  useEffect(() => {
    if (viewMode === 'diff' && noteId && (note?.status === 'cleaned' || note?.status === 'archived' || note?.status === 'learning_failed') && !diffData) {
      setDiffLoading(true)
      getCleaningDiff(noteId)
        .then(setDiffData)
        .catch(() => setDiffData(null))
        .finally(() => setDiffLoading(false))
    }
  }, [viewMode, noteId, note?.status, diffData])

  // 清洗中状态轮询：每 5 秒刷新笔记数据，直到清洗完成或失败
  useEffect(() => {
    if (note?.status !== 'cleaning' || !noteId) return

    const interval = setInterval(async () => {
      try {
        const data = await getNote(noteId)
        setNote(data)
        if (data.status !== 'cleaning') {
          clearInterval(interval)
          // 清洗完成后自动切换到清洗版
          if (data.status === 'cleaned' && data.clean_md_content) {
            setViewMode('clean')
          }
        }
      } catch {
        // 轮询过程中的网络错误，继续尝试
      }
    }, 5000)

    return () => clearInterval(interval)
  }, [note?.status, noteId])

  // 学习中状态轮询：每 5 秒刷新笔记数据，直到学习完成或失败
  useEffect(() => {
    if (note?.status !== 'learning' || !noteId) return

    const interval = setInterval(async () => {
      try {
        const data = await getNote(noteId)
        setNote(data)
        if (data.status !== 'learning') {
          clearInterval(interval)
        }
      } catch {
        // 轮询过程中的网络错误，继续尝试
      }
    }, 5000)

    return () => clearInterval(interval)
  }, [note?.status, noteId])

  // 视频类型笔记：通过 blob URL 加载视频（需携带 JWT 认证头）
  useEffect(() => {
    if (note?.source_type === 'video' && note?.video_url) {
      const fetchVideo = async () => {
        try {
          const token = getToken()
          const response = await fetch(note.video_url, {
            headers: { 'Authorization': `Bearer ${token}` }
          })
          if (!response.ok) throw new Error('Failed to load video')
          const blob = await response.blob()
          const url = URL.createObjectURL(blob)
          setVideoUrl(url)
        } catch (err) {
          console.error('Failed to load video:', err)
        }
      }
      fetchVideo()
      return () => {
        if (videoUrl) URL.revokeObjectURL(videoUrl)
      }
    }
  }, [note?.source_type, note?.video_url])

  // 加载批注：当 note 加载完成且 viewMode 确定后加载批注
  useEffect(() => {
    if (!note?.id) return
    const loadAnnotations = async () => {
      try {
        const data = await getAnnotations(note.id, viewMode)
        setAnnotations(data.annotations || [])
      } catch (err) {
        console.error('加载批注失败:', err)
      }
    }
    loadAnnotations()
  }, [note?.id, viewMode])

  // 加载链接关系：当 note 加载完成后获取其关联的资料/被引用笔记
  useEffect(() => {
    if (!note?.id) return
    const loadLinks = async () => {
      try {
        const links = await getNoteLinks(note.id)
        setNoteLinks(links)
        if (note.note_role === 'personal_note') {
          setLinkMaterialIds(links.linked_materials.map(m => m.id))
        }
      } catch (err) {
        console.error('加载链接关系失败:', err)
      }
    }
    loadLinks()
  }, [note?.id])

  // 批注恢复：DOM 渲染后应用批注到对应文本节点
  useEffect(() => {
    if (editMode !== 'view') return
    if (!markdownRef.current || annotations.length === 0) return

    // 遍历所有文本节点，匹配批注
    const applyAnnotations = () => {
      annotations.forEach(ann => {
        if (ann.view_mode !== viewMode) return
        if (!markdownRef.current) return

        // 跳过已应用的批注，避免重复包裹 DOM
        if (markdownRef.current.querySelector(`[data-annotation-id="${ann.id}"]`)) return

        // 在 DOM 中查找匹配的文本
        const walker = document.createTreeWalker(
          markdownRef.current,
          NodeFilter.SHOW_TEXT,
          null
        )

        while (walker.nextNode()) {
          const node = walker.currentNode as Text
          const text = node.textContent || ''
          const idx = text.indexOf(ann.text_content)
          if (idx >= 0) {
            // 验证上下文（可选，简单验证）
            const before = text.substring(Math.max(0, idx - 50), idx)
            const after = text.substring(idx + ann.text_content.length, idx + ann.text_content.length + 50)
            if (ann.context_before && !before.endsWith(ann.context_before)) continue
            if (ann.context_after && !after.startsWith(ann.context_after)) continue

            // 创建包裹元素
            const range = document.createRange()
            range.setStart(node, idx)
            range.setEnd(node, idx + ann.text_content.length)

            const wrapper = document.createElement(ann.type === 'highlight' ? 'mark' : 'u')
            wrapper.className = ann.type === 'highlight' ? 'annotation-mark' : 'annotation-underline'
            wrapper.dataset.annotationId = ann.id
            wrapper.addEventListener('click', (e) => {
              e.stopPropagation()
              handleDeleteAnnotation(ann.id)
            })

            try {
              range.surroundContents(wrapper)
            } catch (err) {
              // surroundContents 可能跨节点失败，跳过
            }
            break  // 每个批注只应用一次
          }
        }
      })
    }

    // 延迟执行，确保 DOM 已渲染
    setTimeout(applyAnnotations, 100)
  }, [note?.id, note?.original_md_content, note?.clean_md_content, viewMode, annotations, editMode])

  /** 触发理解管道（开始学习） */
  async function handleStartLearning() {
    if (!note) return
    try {
      await startUnderstanding(note.id)
      handleStatusChange()
    } catch (err) {
      alert(err instanceof Error ? err.message : '启动学习失败')
    }
  }

  /** 处理删除笔记 */
  async function handleDelete() {
    if (!note) return
    const isProcessing = ['converting', 'cleaning', 'learning'].includes(note.status)
    const hasCards = hasLearned
    let msg = '确定删除此笔记？'
    if (isProcessing) {
      msg += '\n\n⚠️ 此笔记正在处理中，删除将中断处理流程。'
    }
    if (hasCards) {
      msg += '\n\n将同时删除：知识卡片、练习题目、复习记录、知识图谱关系。'
    }
    msg += '\n\n此操作不可恢复。'
    if (!confirm(msg)) return
    try {
      await deleteNote(note.id)
      navigate('/notes')
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  /** 处理归档/取消归档 */
  async function handleArchive() {
    if (!note) return
    try {
      await archiveNote(note.id)
      await fetchNote()  // 重新获取完整数据，避免部分数据覆盖
    } catch (err) {
      alert(err instanceof Error ? err.message : '操作失败')
    }
  }

  /** 清洗状态变化后刷新笔记数据 */
  function handleStatusChange() {
    setLoading(true)
    setDiffData(null) // 清除 diff 缓存
    fetchNote()
  }

  /** 处理鼠标抬起：选中文本时弹出批注操作浮层 */
  function handleMouseUp() {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || selection.toString().trim().length === 0) {
      setShowAnnotationMenu(false)
      return
    }

    // 确保选区在 markdown 内容区域内
    const range = selection.getRangeAt(0)
    if (!markdownRef.current?.contains(range.commonAncestorContainer)) {
      setShowAnnotationMenu(false)
      return
    }

    // 计算浮层位置
    const rect = range.getBoundingClientRect()
    setAnnotationMenuPos({
      x: rect.left + rect.width / 2,
      y: rect.top - 10,
    })
    setShowAnnotationMenu(true)
  }

  /** 应用批注：高亮或下划线 */
  async function handleApplyAnnotation(type: 'highlight' | 'underline') {
    if (!note) return
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) return

    const text = selection.toString().trim()
    if (!text || text.length > 5000) {
      alert('选中文本过长或为空')
      return
    }

    const range = selection.getRangeAt(0)

    // 获取上下文
    const container = markdownRef.current
    if (!container) return

    // 获取选区前后的文本作为上下文
    const beforeNode = document.createRange()
    beforeNode.selectNodeContents(container)
    beforeNode.setEnd(range.startContainer, range.startOffset)
    const contextBefore = beforeNode.toString().slice(-50)

    const afterNode = document.createRange()
    afterNode.selectNodeContents(container)
    afterNode.setStart(range.endContainer, range.endOffset)
    const contextAfter = afterNode.toString().slice(0, 50)

    try {
      const newAnn = await createAnnotation(note.id, {
        view_mode: viewMode,
        type,
        text_content: text,
        context_before: contextBefore,
        context_after: contextAfter,
      })

      setAnnotations(prev => [...prev, newAnn])

      // 立即应用到 DOM
      const wrapper = document.createElement(type === 'highlight' ? 'mark' : 'u')
      wrapper.className = type === 'highlight' ? 'annotation-mark' : 'annotation-underline'
      wrapper.dataset.annotationId = newAnn.id
      wrapper.addEventListener('click', (e) => {
        e.stopPropagation()
        handleDeleteAnnotation(newAnn.id)
      })

      try {
        range.surroundContents(wrapper)
      } catch (err) {
        console.warn('应用批注失败:', err)
      }
    } catch (err) {
      alert('保存批注失败')
    }

    setShowAnnotationMenu(false)
    selection.removeAllRanges()
  }

  /** 删除批注 */
  async function handleDeleteAnnotation(annotationId: string) {
    if (!note) return
    if (!confirm('确定删除此批注？')) return

    try {
      await deleteAnnotation(note.id, annotationId)
      setAnnotations(prev => prev.filter(a => a.id !== annotationId))

      // 从 DOM 移除样式
      const elem = markdownRef.current?.querySelector(`[data-annotation-id="${annotationId}"]`)
      if (elem) {
        const parent = elem.parentNode
        while (elem.firstChild) {
          parent?.insertBefore(elem.firstChild, elem)
        }
        parent?.removeChild(elem)
        parent?.normalize()  // 合并相邻文本节点
      }
    } catch (err) {
      alert('删除批注失败')
    }
  }

  /** 打开"管理关联资料"弹窗，加载可关联的学习资料列表 */
  async function handleManageLinks() {
    if (!note) return
    try {
      const data = await getNotes(1, 100, undefined, 'material')
      setAvailableMaterials(data.items || [])
      setShowLinkManager(true)
    } catch (err) {
      alert('加载资料列表失败')
    }
  }

  /** 保存关联资料修改 */
  async function handleSaveLinks() {
    if (!note) return
    try {
      const result = await updateNoteLinks(note.id, linkMaterialIds)
      if (result.changed) {
        // 重新加载链接
        const links = await getNoteLinks(note.id)
        setNoteLinks(links)
        alert('关联资料已更新')
      } else {
        alert('关联资料未变化')
      }
      setShowLinkManager(false)
    } catch (err) {
      alert('保存关联失败')
    }
  }

  /** 进入编辑模式：预填充内容（优先清洗版，无则原始版） */
  function handleEnterEdit() {
    if (!note) return
    setEditContent(note.clean_md_content || note.original_md_content || '')
    setEditMode('edit')
  }

  /** 保存编辑内容 */
  async function handleSaveContent() {
    if (!note) return
    setSaving(true)
    try {
      const target: NoteContentTarget = note.clean_md_content ? 'clean' : 'original'
      await updateNoteContent(note.id, editContent, target)
      await fetchNote()
      setEditMode('view')
      setEditContent('')
    } catch (err) {
      alert(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  /** 取消编辑（有未保存修改时确认） */
  function handleCancelEdit() {
    const original = note?.clean_md_content || note?.original_md_content || ''
    if (editContent !== original && !confirm('放弃当前编辑的修改？')) return
    setEditContent('')
    setEditMode('view')
  }

  // 加载中状态
  if (loading) {
    return <LoadingSpinner />
  }

  // 错误或笔记不存在状态
  if (error || !note) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorDisplay message={error || '笔记不存在'} />
        <button className="btn btn-secondary" onClick={() => navigate('/notes')}>返回列表</button>
      </div>
    )
  }

  /** 选择要显示的 Markdown 内容 */
  const mdContent = viewMode === 'clean' && note.clean_md_content
    ? note.clean_md_content
    : note.original_md_content || ''

  // 将 Markdown 文本解析为 HTML
  const htmlContent = marked.parse(mdContent) as string

  // 编辑预览的 HTML
  const editPreviewHtml = marked.parse(editContent) as string

  // 是否已学习过（metadata 中记录了学习成功时间，或处于已归档/学习失败状态）
  const hasLearned = note?.metadata_?.learned_at !== undefined || note?.status === 'archived' || note?.status === 'learning_failed'
  // 是否可以显示清洗版（cleaned/archived/learning_failed 状态都可以查看）
  const canShowClean = (note.status === 'cleaned' || note.status === 'archived' || note.status === 'learning_failed') && !!note.clean_md_content
  // 是否可以显示 diff（cleaned/archived/learning_failed 状态都可以查看）
  const canShowDiff = (note.status === 'cleaned' || note.status === 'archived' || note.status === 'learning_failed')

  return (
    <div className="page-enter">
      {/* 头部信息区域 */}
      <header style={{ marginBottom: 'var(--space-lg)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-md)' }}>
          <h1 className="heading-serif" style={{ fontSize: '1.5rem' }}>{note.title}</h1>
          <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
            {editMode === 'view' && (
              <button
                className="btn btn-secondary"
                onClick={handleEnterEdit}
                disabled={['uploading', 'converting', 'cleaning', 'learning'].includes(note.status)}
                title={['uploading', 'converting', 'cleaning', 'learning'].includes(note.status) ? '处理中，暂不可编辑' : '编辑笔记内容'}
              >
                编辑
              </button>
            )}
            <button
              className="btn btn-secondary"
              onClick={() => setShowVersionHistory(true)}
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
              <button className="btn btn-secondary" onClick={handleManageLinks}>管理关联资料</button>
            )}
            {(note.status === 'cleaned' || note.status === 'learning_failed' || note.status === 'archived') && (
              <button className="btn btn-primary" onClick={handleStartLearning}>AI预处理</button>
            )}
            {(note.status === 'cleaned' || note.status === 'learning_failed' || note.status === 'converted' || note.status === 'archived') && (
              <button className="btn btn-secondary" onClick={handleArchive}>
                {note.status === 'archived' ? '取消审阅' : '审阅'}
              </button>
            )}
            <button className="btn btn-danger" onClick={handleDelete}>删除</button>
            <button className="btn btn-secondary" onClick={() => navigate('/notes')}>返回</button>
          </div>
        </div>

        {/* 笔记元信息标签行 */}
        <div style={{ display: 'flex', gap: 'var(--space-md)', alignItems: 'center', flexWrap: 'wrap', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
          <span className={`badge badge-${note.source_type}`}>{note.source_type.toUpperCase()}</span>
          {note.project_name && (
            <span className="badge" style={{ backgroundColor: 'var(--color-primary-soft, #eef2ff)', color: 'var(--color-primary, #2563eb)' }}>
              {note.project_name}
            </span>
          )}
          <span className={`status-${note.status}`}>{statusLabels[note.status] || note.status}</span>
          <select
            value={note.note_role || 'material'}
            onChange={async (e) => {
              try {
                const updated = await updateNoteRole(note.id, e.target.value)
                setNote((prev) => prev ? { ...prev, note_role: updated.note_role } : prev)
              } catch (err) {
                alert(err instanceof Error ? err.message : '更新角色失败')
              }
            }}
            style={{
              fontSize: '0.75rem',
              padding: '2px 8px',
              borderRadius: '9999px',
              border: '1px solid var(--color-border)',
              background: note.note_role === 'personal_note' ? '#8b5cf6' : '#3b82f6',
              color: 'white',
              cursor: 'pointer',
              outline: 'none',
            }}
          >
            <option value="material">学习资料</option>
            <option value="personal_note">我的笔记</option>
          </select>
          {note.page_count && <span>{note.page_count} 页</span>}
          <span>{(note.file_size / 1024).toFixed(0)} KB</span>
          <span>创建于 {new Date(note.created_at).toLocaleString('zh-CN')}</span>
        </div>

        {/* 错误信息提示 + 重试按钮 */}
        {note.error_message && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)', marginTop: 'var(--space-sm)' }}>
            <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.875rem', margin: 0 }}>
              错误: {note.error_message}
            </p>
            {note.status === 'failed' && (
              <button
                className="btn btn-primary"
                style={{ fontSize: '0.75rem', padding: '4px 12px' }}
                onClick={async () => {
                  try {
                    const result = await retryConvert(noteId!)
                    setNote((prev) => prev ? { ...prev, status: result.status, error_message: result.error_message } : prev)
                  } catch (err) {
                    alert(err instanceof Error ? err.message : '重试失败')
                  }
                }}
              >
                重试转换
              </button>
            )}
          </div>
        )}

        {/* 视图模式切换按钮 */}
        <div className="segment-control" style={{ marginTop: 'var(--space-sm)' }}>
          <button
            className={`segment-btn ${viewMode === 'original' ? 'segment-btn-active' : ''}`}
            onClick={() => setViewMode('original')}
          >
            原始版
          </button>
          <button
            className={`segment-btn ${viewMode === 'clean' ? 'segment-btn-active' : ''}`}
            onClick={() => setViewMode('clean')}
            disabled={!canShowClean}
          >
            清洗版
          </button>
          <button
            className={`segment-btn ${viewMode === 'diff' ? 'segment-btn-active' : ''}`}
            onClick={() => setViewMode('diff')}
            disabled={!canShowDiff}
          >
            对比视图
          </button>
        </div>
      </header>

      {/* 清洗操作面板（converted/cleaning/cleaning_failed/cleaned 状态时显示） */}
      {(note.status === 'converted' || note.status === 'cleaning' || note.status === 'cleaning_failed' || note.status === 'cleaned') && (
        <CleaningPanel note={note} onStatusChange={handleStatusChange} />
      )}

      {/* 关联的学习资料列表 */}
      {noteLinks && noteLinks.linked_materials.length > 0 && (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>关联的学习资料</h3>
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {noteLinks.linked_materials.map(m => (
              <li key={m.id} style={{ padding: '0.25rem 0' }}>
                <a href={`/notes/${m.id}`} style={{ color: 'var(--color-primary)' }}>{m.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 被引用笔记列表 */}
      {noteLinks && noteLinks.linked_personal_notes.length > 0 && (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>被以下笔记引用</h3>
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {noteLinks.linked_personal_notes.map(n => (
              <li key={n.id} style={{ padding: '0.25rem 0' }}>
                <a href={`/notes/${n.id}`} style={{ color: 'var(--color-primary)' }}>{n.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 视频播放器（仅视频类型笔记显示） */}
      {note.source_type === 'video' && videoUrl && (
        <div style={{ marginBottom: '1.5rem' }}>
          <video
            controls
            style={{ width: '100%', borderRadius: '0.5rem' }}
            src={videoUrl}
          >
            您的浏览器不支持视频播放
          </video>
        </div>
      )}

      {/* 内容区域 */}
      {note.status === 'converting' || note.status === 'uploading' ? (
        /* 处理中状态 */
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-warning)' }}>
            {note.status === 'uploading' ? '正在上传...' : '正在转换中，请稍候...'}
          </p>
        </div>
      ) : editMode === 'edit' ? (
        /* 编辑模式 */
        <div>
          <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-sm)' }}>
            <button className="btn btn-secondary" onClick={() => setEditMode('preview')} disabled={saving}>预览</button>
            <button className="btn btn-primary" onClick={handleSaveContent} disabled={saving}>{saving ? '保存中...' : '保存'}</button>
            <button className="btn btn-secondary" onClick={handleCancelEdit} disabled={saving}>取消</button>
          </div>
          <textarea
            className="markdown-editor card"
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            disabled={saving}
            style={{ width: '100%', minHeight: '60vh', padding: '1.5rem', fontFamily: 'inherit', fontSize: '0.95rem', lineHeight: '1.6', resize: 'vertical', border: '1px solid var(--color-border)', borderRadius: '0.5rem', outline: 'none' }}
          />
        </div>
      ) : editMode === 'preview' ? (
        /* 预览模式 */
        <div>
          <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-sm)' }}>
            <button className="btn btn-secondary" onClick={() => setEditMode('edit')} disabled={saving}>编辑</button>
            <button className="btn btn-primary" onClick={handleSaveContent} disabled={saving}>{saving ? '保存中...' : '保存'}</button>
            <button className="btn btn-secondary" onClick={handleCancelEdit} disabled={saving}>取消</button>
          </div>
          <article
            className="card markdown-body"
            dangerouslySetInnerHTML={{ __html: editPreviewHtml }}
          />
        </div>
      ) : viewMode === 'diff' ? (
        /* diff 对比视图 */
        diffLoading ? (
          <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>加载对比数据...</p>
          </div>
        ) : diffData ? (
          <DiffView
            blocks={diffData.blocks}
            originalLines={diffData.original_lines}
            cleanLines={diffData.clean_lines}
          />
        ) : (
          <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
            <p style={{ color: 'var(--color-text-secondary)' }}>无法加载对比数据</p>
          </div>
        )
      ) : mdContent ? (
        /* Markdown 渲染 */
        <article
          ref={markdownRef}
          className="card markdown-body"
          dangerouslySetInnerHTML={{ __html: htmlContent }}
          onMouseUp={handleMouseUp}
        />
      ) : (
        /* 无内容 */
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-text-secondary)' }}>暂无内容</p>
        </div>
      )}

      {/* 批注操作浮层：选中文本后显示高亮/下划线按钮 */}
      {showAnnotationMenu && editMode === 'view' && (
        <div
          className="selection-menu"
          style={{
            position: 'fixed',
            left: annotationMenuPos.x,
            top: annotationMenuPos.y,
            transform: 'translate(-50%, -100%)',
            zIndex: 1000,
          }}
        >
          <button onClick={() => handleApplyAnnotation('highlight')} title="高亮">高亮</button>
          <button onClick={() => handleApplyAnnotation('underline')} title="下划线">下划线</button>
        </div>
      )}

      {/* 链接管理弹窗：选择关联的学习资料 */}
      {showLinkManager && (
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.5)', zIndex: 1000,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setShowLinkManager(false)}
        >
          <div
            className="card"
            style={{
              maxWidth: '500px', width: '90%', maxHeight: '70vh', overflowY: 'auto',
              padding: '1.5rem',
            }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: '1rem' }}>管理关联资料</h3>
            {availableMaterials.length === 0 ? (
              <p style={{ color: 'var(--color-text-secondary)' }}>暂无可关联的资料</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem' }}>
                {availableMaterials.map(m => (
                  <label key={m.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={linkMaterialIds.includes(m.id)}
                      onChange={(e) => {
                        setLinkMaterialIds(prev =>
                          e.target.checked ? [...prev, m.id] : prev.filter(id => id !== m.id)
                        )
                      }}
                    />
                    <span>{m.title}</span>
                  </label>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowLinkManager(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleSaveLinks}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 版本历史面板：模态浮层形式 */}
      {showVersionHistory && (
        <VersionHistory
          noteId={note.id}
          onClose={() => setShowVersionHistory(false)}
          onRestored={fetchNote}
        />
      )}

      {/* 关联知识卡片区域 */}
      {relatedCards.length > 0 && (
        <div style={{ marginTop: 'var(--space-lg)' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>关联知识卡片 ({relatedCards.length})</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--space-md)' }}>
            {relatedCards.slice(0, 6).map(card => (
              <div
                key={card.id}
                className="card card-hover"
                style={{ cursor: 'pointer' }}
                onClick={() => navigate(`/cards/${card.id}`)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-xs)' }}>
                  <strong style={{ fontSize: '0.9rem' }}>{card.title}</strong>
                  <span style={{
                    fontSize: '0.7rem', padding: '1px 6px', borderRadius: '9999px',
                    background: { concept: '#3b82f6', formula: '#8b5cf6', qa: '#10b981', definition: '#f59e0b' }[card.card_type] || '#6b7280',
                    color: 'white', whiteSpace: 'nowrap',
                  }}>
                    {{ concept: '概念', formula: '公式', qa: '问答', definition: '定义' }[card.card_type] || card.card_type}
                  </span>
                </div>
                <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {card.content}
                </p>
              </div>
            ))}
          </div>
          {relatedCards.length > 6 && (
            <button
              className="btn btn-secondary"
              style={{ marginTop: 'var(--space-sm)', fontSize: '0.85rem' }}
              onClick={() => navigate(`/cards?note_id=${noteId}`)}
            >
              查看全部 {relatedCards.length} 张卡片
            </button>
          )}
        </div>
      )}
    </div>
  )
}

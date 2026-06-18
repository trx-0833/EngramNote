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
import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'
import {
  getNote,
  deleteNote,
  getCleaningDiff,
  startUnderstanding,
  archiveNote,
  getKnowledgeCards,
  type NoteDetail,
  type CleaningDiffResponse,
  type KnowledgeCard,
} from '../api/client'
import CleaningPanel from '../components/CleaningPanel'
import DiffView from '../components/DiffView'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorDisplay from '../components/ErrorDisplay'
import { statusLabels } from '../utils/labels'

// 配置 marked 使用 highlight.js 进行代码块语法高亮
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
}))

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

  /** 获取笔记详情 */
  const fetchNote = useCallback(async () => {
    if (!noteId) return
    try {
      const data = await getNote(noteId)
      setNote(data)
      // 如果笔记已清洗/已归档且当前显示原始版，自动切换到清洗版
      if ((data.status === 'cleaned' || data.status === 'archived' || data.status === 'learning_failed') && data.clean_md_content && viewMode === 'original') {
        setViewMode('clean')
      }
      // 获取关联知识卡片
      if (data.status === 'archived' || data.status === 'learning' || data.status === 'learning_failed') {
        try {
          const cardData = await getKnowledgeCards(1, 50, noteId)
          setRelatedCards(cardData.items)
        } catch {
          setRelatedCards([])
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [noteId, viewMode])

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
    if (!note || !confirm('确定删除此笔记？此操作不可恢复。')) return
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
      const updated = await archiveNote(note.id)
      setNote(updated as unknown as NoteDetail)
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
  const htmlContent = marked.parse(mdContent)

  // 是否可以显示清洗版（cleaned/archived/learning_failed 状态都可以查看）
  const canShowClean = (note.status === 'cleaned' || note.status === 'archived' || note.status === 'learning_failed') && !!note.clean_md_content
  // 是否可以显示 diff（cleaned/archived/learning_failed 状态都可以查看）
  const canShowDiff = (note.status === 'cleaned' || note.status === 'archived' || note.status === 'learning_failed')

  return (
    <div style={{ padding: 'var(--space-lg) 0' }}>
      {/* 头部信息区域 */}
      <header style={{ marginBottom: 'var(--space-lg)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-md)' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>{note.title}</h1>
          <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
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
          <span className={`status-${note.status}`}>{statusLabels[note.status] || note.status}</span>
          {note.page_count && <span>{note.page_count} 页</span>}
          <span>{(note.file_size / 1024).toFixed(0)} KB</span>
          <span>创建于 {new Date(note.created_at).toLocaleString('zh-CN')}</span>
        </div>

        {/* 错误信息提示 */}
        {note.error_message && (
          <p role="alert" style={{ color: 'var(--color-error)', marginTop: 'var(--space-sm)', fontSize: '0.875rem' }}>
            错误: {note.error_message}
          </p>
        )}

        {/* 视图模式切换按钮 */}
        <div style={{ marginTop: 'var(--space-sm)', display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
          <button
            className={`btn ${viewMode === 'original' ? 'btn-primary' : 'btn-secondary'}`}
            style={{ fontSize: '0.8rem' }}
            onClick={() => setViewMode('original')}
          >
            原始版
          </button>
          <button
            className={`btn ${viewMode === 'clean' ? 'btn-primary' : 'btn-secondary'}`}
            style={{ fontSize: '0.8rem' }}
            onClick={() => setViewMode('clean')}
            disabled={!canShowClean}
          >
            清洗版
          </button>
          <button
            className={`btn ${viewMode === 'diff' ? 'btn-primary' : 'btn-secondary'}`}
            style={{ fontSize: '0.8rem' }}
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

      {/* 内容区域 */}
      {note.status === 'converting' || note.status === 'uploading' ? (
        /* 处理中状态 */
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-warning)' }}>
            {note.status === 'uploading' ? '正在上传...' : '正在转换中，请稍候...'}
          </p>
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
        <article className="card markdown-body" dangerouslySetInnerHTML={{ __html: htmlContent }} />
      ) : (
        /* 无内容 */
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-text-secondary)' }}>暂无内容</p>
        </div>
      )}

      {/* 关联知识卡片区域 */}
      {relatedCards.length > 0 && (
        <div style={{ marginTop: 'var(--space-lg)' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>关联知识卡片 ({relatedCards.length})</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--space-md)' }}>
            {relatedCards.slice(0, 6).map(card => (
              <div
                key={card.id}
                className="card"
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

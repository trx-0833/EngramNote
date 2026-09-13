/**
 * @file 笔记详情页的数据层：加载、轮询、引用回跳、视频 blob
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运 ——
 * effect 顺序、依赖数组、5s 轮询间隔、120ms 高亮延迟、blob URL 的
 * cleanup 释放等细节均与拆分前逐字一致。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import {
  getCleaningDiff,
  getKnowledgeCards,
  getNote,
  getQuestions,
  getToken,
  type CleaningDiffResponse,
  type KnowledgeCard,
  type NoteDetail,
} from '../../api/client'
import { useToast } from '../../components/Toast'
import { highlightCitation } from '../../utils/citationJump'
import type { ViewMode } from './types'

interface UseNoteDetailDataOptions {
  /** 路由参数里的笔记 ID */
  noteId: string | undefined
  /** Markdown 正文容器（引用回跳高亮用） */
  markdownRef: RefObject<HTMLElement>
  /** 引用回跳参数（`?cs=&ce=`） */
  jump: { charStart: number; charEnd: number; hasJump: boolean }
  /** 当前 URL 查询串（清理跳转参数用） */
  searchParams: URLSearchParams
  /** 覆写 URL 查询串（清理跳转参数用） */
  setSearchParams: (next: URLSearchParams, options?: { replace?: boolean }) => void
}

/**
 * 管理笔记详情的加载与状态轮询。
 *
 * 返回的 `mutatingRef` 供块操作（恢复/删除重复块）期间挂起轮询 ——
 * 否则 5s 轮询会用旧数据覆盖刚做完的块操作结果。
 */
export function useNoteDetailData({
  noteId,
  markdownRef,
  jump,
  searchParams,
  setSearchParams,
}: UseNoteDetailDataOptions) {
  const toast = useToast()
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
  // latest-ref 模式：fetchNote 需要读取"当前"视图模式，但它只依赖 noteId。
  // 推迟到 effect 会让自动切清洗版的判断读到旧值，故豁免 react-hooks/refs
  // （与 hooks/useAdhdReader.ts 中的既有用法一致）。
  // eslint-disable-next-line react-hooks/refs
  viewModeRef.current = viewMode

  const { charStart: jumpCharStart, charEnd: jumpCharEnd, hasJump } = jump

  /** 块操作（恢复/删除）进行中标记：置 true 时 5s 状态轮询跳过本轮，避免轮询旧数据覆盖块操作结果 */
  const mutatingRef = useRef<boolean>(false)

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

  // 组件挂载或 noteId 变化时获取笔记详情（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    fetchNote()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchNote 依赖仅 noteId,effect 由 noteId 驱动
  }, [noteId])

  // 切换到 diff 模式时加载 diff 数据（条件加载型 effect，同步 setState 豁免）
  useEffect(() => {
    if (viewMode === 'diff' && noteId && (note?.status === 'cleaned' || note?.status === 'archived' || note?.status === 'learning_failed') && !diffData) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDiffLoading(true)
      getCleaningDiff(noteId)
        .then(setDiffData)
        .catch(() => setDiffData(null))
        .finally(() => setDiffLoading(false))
    }
  }, [viewMode, noteId, note?.status, diffData])

  /**
   * 引用回跳（阶段 2.7）：强制切到 clean 视图
   *
   * chunk 偏移基于 clean 副本计算，显示的必须是同一份内容。
   * `clean_md_content` 尚未加载时**不要**强行切换 —— 否则会切到空内容，
   * 等它到位后本 effect 会重跑。
   */
  useEffect(() => {
    if (!hasJump) return
    if (!note?.clean_md_content) return
    if (viewModeRef.current !== 'clean') setViewMode('clean')
  }, [hasJump, note?.clean_md_content])

  /**
   * 引用回跳：内容渲染完成后定位并高亮
   *
   * 依赖 `viewMode` 与内容：切换视图会重建 DOM，此时旧的高亮节点已不存在。
   * 跳转完成后把 `cs/ce` 从 URL 清掉 —— 否则用户手动切换视图时会被
   * 反复拉回同一段，像是页面"卡住了"。
   */
  useEffect(() => {
    if (!hasJump || viewMode !== 'clean' || !note?.clean_md_content) return
    const timer = window.setTimeout(() => {
      const result = highlightCitation(
        markdownRef.current,
        note.clean_md_content || '',
        jumpCharStart,
        jumpCharEnd,
      )
      if (!result.highlighted) {
        // 明说失败，而不是让用户以为"引用就在开头"
        toast.warning('已打开笔记，但未能定位到引用段落（可能因排版差异）')
      }
      // 清掉跳转参数，避免后续视图切换被反复拉回
      const next = new URLSearchParams(searchParams)
      next.delete('cs'); next.delete('ce'); next.delete('view')
      setSearchParams(next, { replace: true })
    }, 120)
    return () => window.clearTimeout(timer)
    // searchParams/setSearchParams 有意不入依赖：会在清理参数后触发无意义重跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasJump, viewMode, note?.clean_md_content, jumpCharStart, jumpCharEnd])

  // 清洗中状态轮询：每 5 秒刷新笔记数据，直到清洗完成或失败
  useEffect(() => {
    if (note?.status !== 'cleaning' || !noteId) return

    const interval = setInterval(async () => {
      if (mutatingRef.current) return
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
      if (mutatingRef.current) return
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
      // 局部持有 blob URL，cleanup 中释放（避免闭包捕获旧 state 导致泄漏）
      let localUrl: string | null = null
      const fetchVideo = async () => {
        try {
          const token = getToken()
          if (!token) throw new Error('未登录')
          const response = await fetch(note.video_url as string, {
            headers: { 'Authorization': `Bearer ${token}` }
          })
          if (!response.ok) throw new Error('Failed to load video')
          const blob = await response.blob()
          const url = URL.createObjectURL(blob)
          localUrl = url
          setVideoUrl(url)
        } catch (err) {
          console.error('Failed to load video:', err)
        }
      }
      fetchVideo()
      return () => {
        if (localUrl) URL.revokeObjectURL(localUrl)
      }
    }
  }, [note?.source_type, note?.video_url])

  return {
    note,
    setNote,
    loading,
    setLoading,
    error,
    viewMode,
    setViewMode,
    diffData,
    setDiffData,
    diffLoading,
    relatedCards,
    hasQuizItems,
    videoUrl,
    /** 块操作进行中标记（CleanPanel 通过 onMutatingChange 写入，两处轮询读取） */
    mutatingRef,
    fetchNote,
  }
}

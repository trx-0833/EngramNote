/**
 * @file 卡片复习的会话状态机（到期队列 → 翻面 → 自评 → 下一张）
 *
 * ## 为什么单独成一个模块
 *
 * 这一段与答题复习的会话状态机（`Review` / `QuickReview` 各自持有的
 * `QuizState[]` + 索引 + 计数）**形状相同但语义不同**，所以刻意不做成
 * 共用 hook：
 *
 * | | 答题复习 | 卡片复习 |
 * |---|---|---|
 * | 一张的状态 | 答案 + 是否已提交 + 判分结果 | 是否已翻面 + 自评结果 |
 * | 结账条件 | 提交即结账（简答题还要自评） | **必须**自评（自评就是提交） |
 * | 计数口径 | 正确数来自判分 | 正确数来自自评档位（>= 3） |
 *
 * 5.12 统一的是**交互件**（四档自评控件 / 进度条 / 回车约定），不是把两条
 * 数据流硬塞进同一个 hook —— 那只会让"结账条件"这类差异藏进可选参数里。
 *
 * ## 两条必须保持的约束
 *
 * 1. 未自评不许推进（`handleNext` 直接返回）：此刻调度尚未推进，
 *    放行会让这张卡停在"看了但没结账"的状态，而界面上看不出来；
 * 2. in-flight 锁（`submittingRef`）：防双击/连按回车重复提交，
 *    重复提交会写两条 ReviewLog 并把间隔叠加两次（见 docs/decisions.md#F-23）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getDueCards,
  submitCardReview,
  type CardReviewListResponse,
  type CardReviewSubmitResponse,
  type DueCard,
} from '../../api/review'
import { useToast } from '../../components/Toast'

/** 一张卡片的会话状态 */
export interface CardState {
  card: DueCard
  /** 是否已翻面（显示正文） */
  revealed: boolean
  /** 自评结果；null = 尚未自评 */
  result: CardReviewSubmitResponse | null
  startTime: number
}

/** 一次加载多少张（到期总数可能上千，见附录 AA.5） */
const PAGE_SIZE = 20

export function useCardReviewSession(pageSize: number = PAGE_SIZE) {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cards, setCards] = useState<CardState[]>([])
  const [totalDue, setTotalDue] = useState(0)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [sessionCount, setSessionCount] = useState(0)
  /** 本次会话里"想起来了"（自评 >= 3，后端折算为 is_correct）的张数 */
  const [sessionPassed, setSessionPassed] = useState(0)
  const submittingRef = useRef(false)

  /** 把一次成功的加载结果落到状态里（初始加载与"再复习一轮"共用） */
  const applyLoaded = useCallback((data: CardReviewListResponse) => {
    setCards(data.items.map(card => ({
      card,
      revealed: false,
      result: null,
      startTime: Date.now(),
    })))
    setTotalDue(data.total)
    setCurrentIndex(0)
    setCompleted(data.items.length === 0)
    setError('')
  }, [])

  /**
   * 拉取到期卡片并落地；失败时只置错误，不抛（调用方不必各自 try/catch）
   *
   * ⚠️ 这里刻意用 `.then()/.catch()` 而不是 `async/await`：
   * react-hooks 的 `set-state-in-effect` 规则不允许 effect 体内触发 setState，
   * 而它对 `await` **之后的** setState 也一并报错（实测：把 setState 全部
   * 放在 await 之后仍然报）。把落地放进回调里，语义完全一样，
   * 静态分析也能看出 setState 不在同步路径上。
   */
  const fetchCards = useCallback(
    () => getDueCards(pageSize).then(applyLoaded).catch(() => setError('加载到期卡片失败')),
    [pageSize, applyLoaded],
  )

  useEffect(() => {
    void fetchCards().finally(() => setLoading(false))
  }, [fetchCards])

  /** 重新加载（首次之外的入口：错误重试、"再复习一轮"） */
  const reload = useCallback(() => {
    setLoading(true)
    setError('')
    void fetchCards().finally(() => setLoading(false))
  }, [fetchCards])

  /** 再复习一轮：清空本次统计后重新拉取到期队列 */
  const restart = useCallback(() => {
    setSessionCount(0)
    setSessionPassed(0)
    setCompleted(false)
    reload()
  }, [reload])

  const current = cards[currentIndex]
  const phase = current?.result ? 'rated' : current?.revealed ? 'revealed' : 'front'

  const handleReveal = useCallback(() => {
    setCards(prev => {
      const next = [...prev]
      if (next[currentIndex]) next[currentIndex] = { ...next[currentIndex], revealed: true }
      return next
    })
  }, [currentIndex])

  const handleRate = useCallback(async (quality: number) => {
    if (submittingRef.current) return
    const target = cards[currentIndex]
    if (!target || target.result) return

    submittingRef.current = true
    setSubmitting(true)
    try {
      const result = await submitCardReview(
        target.card.card_id,
        quality,
        '',
        Date.now() - target.startTime,
      )
      setCards(prev => {
        const next = [...prev]
        if (next[currentIndex]) next[currentIndex] = { ...next[currentIndex], result }
        return next
      })
      setSessionCount(prev => prev + 1)
      if (result.is_correct) setSessionPassed(prev => prev + 1)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '提交失败')
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }, [cards, currentIndex, toast])

  const handleNext = useCallback(() => {
    const target = cards[currentIndex]
    // 未自评不许跳过：此刻调度尚未推进，放行会让这张卡停在
    // "看了但没结账"的状态，而界面上看不出来。
    if (target && !target.result) return
    if (currentIndex < cards.length - 1) {
      const nextIndex = currentIndex + 1
      setCurrentIndex(nextIndex)
      setCards(prev => {
        const next = [...prev]
        next[nextIndex] = { ...next[nextIndex], startTime: Date.now() }
        return next
      })
    } else {
      setCompleted(true)
    }
  }, [cards, currentIndex])

  return {
    loading, error, cards, totalDue, currentIndex, submitting, completed,
    sessionCount, sessionPassed, current, phase,
    reload, restart, handleReveal, handleRate, handleNext,
  }
}

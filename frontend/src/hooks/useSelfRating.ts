/**
 * @file 四档自评提交 hook
 * @description 简答题的自动判分不可信，后端对此采用**两阶段提交**：
 * 第一次提交只落一条占位记录、不推进 SM-2 调度（响应 needs_self_assessment=true），
 * 用户四档自评后第二次提交携带 self_rating，才真正完成判分与调度。
 *
 * 本 hook 承担第二阶段：把用户点选的质量分回传后端，并用返回的 result
 * 替换页面里的占位结果。
 *
 * 为什么抽成 hook 而不是在三页各写一遍：Review / QuickReview / TodayLearn
 * 三个页面的自评逻辑完全一致，而它们连 `QuizState` 结构都是各自复制的一份
 * （见 docs/overhaul-plan.md §2.8 F-21）。再复制三份自评逻辑，必然出现
 * 「一页修了另两页没修」的漂移 —— 这类漂移在本项目已经发生过（statusClass）。
 */
import { useCallback, useRef, useState } from 'react'
import type { SubmitAnswerResponse } from '../api/client'

interface UseSelfRatingOptions {
  /**
   * 提交自评到后端。
   * 普通复习传 submitAnswer，快速复习传 submitQuickReviewAnswer。
   */
  submit: (
    quizId: string,
    userAnswer: string,
    timeSpentMs: number,
    selfRating?: number,
  ) => Promise<SubmitAnswerResponse>
  /** 自评成功后的回调（用于替换 result、刷新统计等） */
  onRated: (result: SubmitAnswerResponse, quality: number) => void
  /** 自评失败时的回调（用于 toast / 设置错误信息） */
  onError: (message: string) => void
}

export function useSelfRating({ submit, onRated, onError }: UseSelfRatingOptions) {
  /** 自评提交 in-flight 锁（防连点重复自评） */
  const inFlightRef = useRef(false)
  const [submitting, setSubmitting] = useState(false)
  /** 本会话已完成自评的题目 ID 集合（用于隐藏四档按钮、禁用"下一题"） */
  const [ratedIds, setRatedIds] = useState<Set<string>>(new Set())

  const submitRating = useCallback(
    async (
      quizId: string,
      userAnswer: string,
      timeSpentMs: number,
      quality: number,
    ): Promise<SubmitAnswerResponse | null> => {
      if (inFlightRef.current) return null
      inFlightRef.current = true
      setSubmitting(true)
      try {
        const result = await submit(quizId, userAnswer, timeSpentMs, quality)
        // 先登记已自评，再回调：即使回调里抛错，按钮也不会重复可点
        setRatedIds(prev => new Set(prev).add(quizId))
        onRated(result, quality)
        return result
      } catch (e: unknown) {
        onError(e instanceof Error ? e.message : '自评提交失败')
        return null
      } finally {
        inFlightRef.current = false
        setSubmitting(false)
      }
    },
    [submit, onRated, onError],
  )

  /** 该题是否已自评（已自评则不再展示四档按钮） */
  const isRated = useCallback((quizId: string) => ratedIds.has(quizId), [ratedIds])

  /**
   * 跳过自评：仅解除前端的"必须自评才能下一题"限制，**不写库**。
   *
   * 为什么需要这个逃生口：自评是发起网络请求的第二阶段，若它持续失败
   * （离线、限流、后端 500），"必须自评才放行"会把用户永久卡在这一题上，
   * 且刷新页面后占位记录仍在，用户依然出不去 —— 一个功能把整页堵死
   * 比丢掉一次自评信号糟得多。
   *
   * 代价：库中会留一条 grading_method='ungraded'、未推进调度的占位记录。
   * 这可以接受 —— 它不推进 SM-2、不污染调度数据，反而如实记录了
   * "这次答题没有完成自评"，正是校准曲线需要的负样本。
   */
  const skipRating = useCallback((quizId: string) => {
    setRatedIds(prev => new Set(prev).add(quizId))
  }, [])

  return { submitRating, submitting, isRated, skipRating }
}

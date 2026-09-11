/**
 * @file 卡片复习页（阶段 3.12 的前端一半）
 *
 * ## 为什么需要这一页
 *
 * 后端早就提供了 `GET /review/cards/due` 与 `POST /review/cards/{id}/submit`
 * —— 目的是解决 overhaul-plan 症状 L-5：**没有生成过题目的卡片永远无法复习**
 * （调度参数原先只挂在 `quiz_items` 上）。但这在前端**没有任何调用方**，
 * 于是"卡片可以直接复习"这件事从做完到现在，用户一次也没法用。
 *
 * 这一页补的就是那一步。它和答题复习是两个独立入口，不是同一条流程的两种皮肤。
 *
 * ## 交互：先回忆 → 再翻面 → 才自评
 *
 * 卡片正文**默认隐藏**。这不是装饰：卡片复习的全部价值在于"先自己想一遍"，
 * 一开始就把内容摊开等于直接看答案，用户会把它当成快速浏览而不是回忆练习
 * —— 那样产生的自评数据是假的，而它**会真的改变调度**（间隔一经写入就
 * 无法事后纠正）。
 *
 * ## 为什么要展示 S / D / R
 *
 * 这是全套改造里第一次把"调度器凭什么这么排"直接摆给用户看：
 *
 * - `predicted_retention`：复习**前**模型认为你还能想起的概率
 * - `stability`：记忆强度（天）—— "下次复习 N 天后"正是由它解出来的
 * - `difficulty`：这张卡对你的难度（1-10）
 *
 * 只给一个"6 天后"等于要求用户盲信调度器。这三个数也是 3.14 校准曲线
 * 将来要给用户看的东西，现在先把原始量露出来。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  getDueCards,
  submitCardReview,
  type CardReviewListResponse,
  type CardReviewSubmitResponse,
  type DueCard,
} from '../api/review'
import SourceContext from '../components/quiz/SourceContext'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import { useToast } from '../components/Toast'
import { selfRatingOptions, cardTypeLabels } from '../utils/labels'

/** 一张卡片的会话状态 */
interface CardState {
  card: DueCard
  /** 是否已翻面（显示正文） */
  revealed: boolean
  /** 自评结果；null = 尚未自评 */
  result: CardReviewSubmitResponse | null
  startTime: number
}

const PAGE_SIZE = 20

/** 格式化时间到"日期 时:分"（本地时区），空值返回占位 */
function formatDue(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

export default function CardReview() {
  const navigate = useNavigate()
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cards, setCards] = useState<CardState[]>([])
  const [totalDue, setTotalDue] = useState(0)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [sessionCount, setSessionCount] = useState(0)
  // 本次会话里"答对"（自评 >= 3）的张数，用于结束时的汇总
  const [sessionPassed, setSessionPassed] = useState(0)
  // in-flight 锁：防双击/连按回车重复提交（重复 ReviewLog + 重复推进调度）
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
    () => getDueCards(PAGE_SIZE).then(applyLoaded).catch(() => setError('加载到期卡片失败')),
    [applyLoaded],
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

  const current = cards[currentIndex]

  // 让"回车翻面 / 回车下一张"真的可用。
  //
  // ⚠️ 只把 onKeyDown 挂在 div 上是不够的：键盘事件从**获得焦点的元素**
  // 冒泡上来，而点击按钮之后焦点留在被卸载的按钮上、最终回到 body，
  // 事件根本到不了这个 div —— 快捷键会变成"时灵时不灵"。
  // 每次换卡或换阶段时把焦点收回容器（tabIndex={-1} 只允许程序聚焦，
  // 不会插进 Tab 顺序里打扰键盘用户）。
  const containerRef = useRef<HTMLDivElement>(null)
  const phase = current?.result ? 'rated' : current?.revealed ? 'revealed' : 'front'
  useEffect(() => {
    containerRef.current?.focus()
  }, [currentIndex, phase])

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

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key !== 'Enter' || e.shiftKey) return
    // 自评按钮本身会处理回车（button 的原生行为），这里只兜住
    // "翻面"与"下一张"两个阶段
    if (e.target instanceof HTMLButtonElement) return
    e.preventDefault()
    if (!current) return
    if (current.result) handleNext()
    else if (!current.revealed) handleReveal()
  }

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text="加载到期卡片..." />

  // --- 错误 ---
  if (error && cards.length === 0) return <ErrorDisplay message={error} onRetry={reload} />

  // --- 无到期卡片 ---
  if (!loading && cards.length === 0) {
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <EmptyState
          message="没有到期的卡片"
          description={
            '卡片复习只包含「已进入复习计划」的卡片。'
            + '刚导入的卡片会在你第一次复习它们时加入计划；'
            + '若刚重建过数据，可能需要先运行一次复习状态迁移。'
          }
          action={
            <button className="btn btn-secondary" onClick={() => navigate('/cards')}>
              去看知识卡片
            </button>
          }
        />
      </div>
    )
  }

  // --- 本次完成汇总 ---
  if (completed) {
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 'var(--space-lg)' }}>本轮卡片复习完成</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3 style={{ marginBottom: 'var(--space-sm)' }}>本次统计</h3>
          <p>复习卡片: {sessionCount} 张</p>
          <p>想起来了: {sessionPassed} 张</p>
          <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.9rem', marginTop: 'var(--space-sm)' }}>
            {totalDue > cards.length
              ? `本次加载了 ${cards.length} 张，全部到期共 ${totalDue} 张 —— 可以再来一轮。`
              : '到期队列已经清空。'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button
            className="btn btn-primary"
            onClick={() => {
              setSessionCount(0)
              setSessionPassed(0)
              setCompleted(false)
              reload()
            }}
          >
            再复习一轮
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/today')}>
            回到今日学习
          </button>
        </div>
      </div>
    )
  }

  if (!current) return null

  const { card, revealed, result } = current
  const typeLabel = cardTypeLabels[card.card_type] || card.card_type

  return (
    <div
      className="page-enter"
      ref={containerRef}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      style={{ maxWidth: 760, margin: '0 auto', outline: 'none' }}
    >
      {/* 进度与到期总量 */}
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
          <h2 style={{ fontSize: '1.2rem' }}>卡片复习</h2>
          <span style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
            第 {currentIndex + 1} / {cards.length} 张
            {totalDue > cards.length && <> · 到期共 {totalDue} 张</>}
          </span>
        </div>
        <div className="progress-bar">
          <div
            className="progress-bar-fill"
            style={{ width: `${((currentIndex + (result ? 1 : 0)) / cards.length) * 100}%` }}
          />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        {/* 卡片头部：类型 / 章节 / 复习元信息 */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-md)',
          gap: 'var(--space-sm)',
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
            <span style={{
              padding: '2px 8px', borderRadius: 4, fontSize: '0.8rem',
              background: 'var(--color-primary)', color: '#fff',
            }}>
              {typeLabel}
            </span>
            {card.chapter_title && (
              <span style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                {card.chapter_title}
              </span>
            )}
          </div>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
            已复习 {card.review_count} 次
            {card.review_count > 0 && <> · 间隔 {card.interval_days} 天</>}
            {card.lapses > 0 && <> · 遗忘 {card.lapses} 次</>}
          </span>
        </div>

        {/* 正面：标题（提示） */}
        <h3 style={{ fontSize: '1.15rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>
          {card.title}
        </h3>

        {!revealed ? (
          <>
            <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.9rem', marginBottom: 'var(--space-md)' }}>
              先在心里把这张卡的内容讲一遍，再看答案 —— 直接翻面等于看答案，
              这次自评就不准了。
            </p>
            <div style={{ textAlign: 'right' }}>
              <button className="btn btn-primary" onClick={handleReveal}>
                显示答案
              </button>
            </div>
          </>
        ) : (
          <>
            {/* 背面：正文 + 摘要 */}
            <div
              style={{
                padding: 'var(--space-md)',
                background: 'var(--color-bg)',
                borderRadius: 6,
                lineHeight: 1.8,
                whiteSpace: 'pre-wrap',
                marginBottom: 'var(--space-md)',
              }}
            >
              {card.content}
            </div>
            {card.summary && (
              <p style={{
                fontSize: '0.9rem',
                color: 'var(--color-text-secondary)',
                marginBottom: 'var(--space-md)',
              }}>
                <strong>摘要:</strong> {card.summary}
              </p>
            )}

            {/* 四档自评：**唯一**的评分来源（卡片没有可自动判分的答案） */}
            {!result && (
              <>
                <p style={{ fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
                  刚才想得起来吗？
                </p>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: 'var(--space-sm)',
                  marginBottom: 'var(--space-md)',
                }}>
                  {selfRatingOptions.map(opt => (
                    <button
                      key={opt.quality}
                      className="btn self-rating-btn"
                      onClick={() => void handleRate(opt.quality)}
                      disabled={submitting}
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'flex-start',
                        gap: 2,
                        padding: 'var(--space-sm) var(--space-md)',
                        border: `1px solid ${opt.color}`,
                        borderLeft: `4px solid ${opt.color}`,
                        borderRadius: 6,
                        background: 'transparent',
                        color: 'inherit',
                        cursor: submitting ? 'wait' : 'pointer',
                        textAlign: 'left',
                      }}
                    >
                      <span style={{ fontWeight: 600, color: opt.color }}>{opt.label}</span>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                        {opt.hint}
                      </span>
                    </button>
                  ))}
                </div>
                {submitting && (
                  <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                    正在记录自评...
                  </p>
                )}
              </>
            )}

            {/* 调度依据（阶段 3.6 / 3.9）：把"凭什么排到 N 天后"摆出来 */}
            {result && (
              <div
                style={{
                  padding: 'var(--space-md)',
                  background: 'var(--color-bg)',
                  borderLeft: `3px solid ${result.is_correct ? '#2d8a56' : '#c0392b'}`,
                  borderRadius: 4,
                  marginBottom: 'var(--space-md)',
                  fontSize: '0.9rem',
                }}
              >
                <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
                  已记录：
                  {selfRatingOptions.find(o => o.quality === result.quality)?.label
                    ?? `quality=${result.quality}`}
                </p>
                <p>
                  下次复习: <strong>{result.interval_days} 天后</strong>
                  {result.next_review_at && <> （{formatDue(result.next_review_at)}）</>}
                </p>
                {typeof result.predicted_retention === 'number'
                  && result.predicted_retention < 0.999 && (
                  <p>
                    复习前模型认为你还能想起:
                    {' '}<strong>{Math.round(result.predicted_retention * 100)}%</strong>
                    {' '}—— 越接近遗忘，这次答对后间隔涨得越多
                  </p>
                )}
                {(result.stability != null || result.difficulty != null) && (
                  <p style={{ color: 'var(--color-text-secondary)' }}>
                    记忆强度 {result.stability != null ? `${result.stability.toFixed(1)} 天` : '—'}
                    {' · '}
                    难度 {result.difficulty != null ? result.difficulty.toFixed(1) : '—'}
                    {' · '}
                    掌握度 {card.mastery_level.toFixed(0)} → {result.mastery_level.toFixed(0)}
                  </p>
                )}
              </div>
            )}

            {/* 原文语境（阶段 3.13）：想不起来时最该做的事就是回原文 */}
            {result && <SourceContext cardId={card.card_id} noteId={card.note_id} />}

            <div style={{ textAlign: 'right' }}>
              <button
                className="btn btn-primary"
                onClick={handleNext}
                disabled={!result}
                title={!result ? '请先完成自评' : undefined}
              >
                {currentIndex >= cards.length - 1 ? '完成本轮' : '下一张'}
              </button>
            </div>
          </>
        )}
      </div>

      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', textAlign: 'center' }}>
        卡片复习是轻量回顾，不占用每日答题限额。
        {' '}想复习带题目的内容请到 <Link to="/review">答题复习</Link>。
      </p>
    </div>
  )
}

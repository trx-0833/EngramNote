/**
 * @file 仪表盘页面
 * @description 应用首页/仪表盘，展示欢迎信息、学习报告、薄弱点和最近笔记概要。
 * 是用户登录后看到的第一个页面，提供快速入口：
 * 1. 上传新资料按钮
 * 2. 今日学习报告（新掌握数、复习时长、正确率）
 * 3. 薄弱点列表（top 3）
 * 4. 7天趋势柱状图
 * 5. 最近笔记列表
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getNotes, getReviewStats, getDailyReport, getWeeklyTrend, getWeakPoints,
  getGoals, getDailyPlan,
  type Note, type ReviewStats, type DailyReport, type WeeklyTrendItem, type WeakPoint,
  type LearningGoal, type DailyPlanResponse, type RecommendedTask,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import ReminderBanner from '../components/ReminderBanner'
import { sourceTypeLabels, statusLabels, cardTypeLabels, questionTypeLabels } from '../utils/labels'

/**
 * 仪表盘页面组件
 */
export default function Dashboard() {
  const navigate = useNavigate()
  const [recentNotes, setRecentNotes] = useState<Note[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reviewStats, setReviewStats] = useState<ReviewStats | null>(null)
  const [dailyReport, setDailyReport] = useState<DailyReport | null>(null)
  const [weeklyTrend, setWeeklyTrend] = useState<WeeklyTrendItem[]>([])
  const [weakPoints, setWeakPoints] = useState<WeakPoint[]>([])
  /** 激活学习目标列表（用于今日目标概要） */
  const [goals, setGoals] = useState<LearningGoal[]>([])
  /** 今日每日推荐任务 */
  const [dailyPlan, setDailyPlan] = useState<DailyPlanResponse | null>(null)

  async function fetchRecent() {
    setLoading(true)
    setError('')
    // F-24：统计类接口失败不再静默吞掉——记录失败集合，非关键失败轻提示，
    // 关键失败（notes）仍走整页错误
    // 修复：getDailyPlan 在"无活跃目标"时返回 400（正常业务状态，不是加载失败），
    // 不计数到 failedCount，避免新用户每次打开都误报"部分数据加载失败"
    let failedCount = 0
    try {
      const [notesRes, statsRes, reportRes, trendRes, weakRes, goalsRes, planRes] = await Promise.all([
        getNotes(1, 5),
        getReviewStats().catch(() => { failedCount++; return null }),
        getDailyReport().catch(() => { failedCount++; return null }),
        getWeeklyTrend().catch(() => { failedCount++; return null }),
        getWeakPoints(3).catch(() => { failedCount++; return null }),
        getGoals('active').catch(() => { failedCount++; return null }),
        getDailyPlan().catch((e: unknown) => {
          // 无活跃目标 → 400 "No active goals..."：正常状态，不算失败
          const msg = e instanceof Error ? e.message : ''
          if (!msg.includes('active goals')) {
            failedCount++
          }
          return null
        }),
      ])
      setRecentNotes(notesRes.items)
      if (statsRes) setReviewStats(statsRes)
      if (reportRes) setDailyReport(reportRes)
      if (trendRes) setWeeklyTrend(trendRes.items)
      if (weakRes) setWeakPoints(weakRes.items)
      if (goalsRes) setGoals(goalsRes.goals)
      if (planRes) setDailyPlan(planRes)
      // 非关键失败：显示提示条而不是假装"暂无数据"
      if (failedCount > 0) {
        setError(`部分数据加载失败（${failedCount} 项），其余内容正常显示，可刷新重试`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRecent()
  }, [])

  /** 格式化复习时长 */
  function formatTime(ms: number): string {
    if (ms < 60000) return `${Math.round(ms / 1000)}秒`
    if (ms < 3600000) return `${Math.round(ms / 60000)}分钟`
    return `${(ms / 3600000).toFixed(1)}小时`
  }

  // 趋势图最大值（用于计算柱高百分比）
  const maxReviewCount = Math.max(...weeklyTrend.map(d => d.review_count), 1)

  return (
    <div className="page-enter">
      {/* 复习提醒横幅（自包含组件，根据权限与待复习数自动展示） */}
      <ReminderBanner />

      {/* 欢迎区域 */}
      <section style={{ marginBottom: 'var(--space-xl)' }}>
        <h1 className="heading-serif gradient-text" style={{ fontSize: '2rem', marginBottom: 'var(--space-sm)' }}>
          欢迎使用 EngramNote
        </h1>
        <p className="fade-in" style={{ color: 'var(--color-text-secondary)', marginBottom: 'var(--space-md)' }}>
          AI 驱动的学习笔记管理与知识库工具
        </p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={() => navigate('/upload')}>
            上传新资料
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/today')}>
            今日学习
          </button>
        </div>
        {/* 新用户引导 */}
        {recentNotes.length === 0 && !loading && (
          <div className="card card-accent-gold" style={{ marginTop: 'var(--space-md)', background: 'var(--color-primary-light)' }}>
            <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>快速开始</p>
            <ol style={{ paddingLeft: 'var(--space-lg)', color: 'var(--color-text-secondary)', fontSize: '0.9rem', lineHeight: 1.8 }}>
              <li>点击「上传新资料」上传 PDF、Word 或图片文件</li>
              <li>系统自动转换并清洗内容，生成知识卡片</li>
              <li>AI 自动生成复习题目，点击「今日学习」开始答题</li>
              <li>间隔重复算法帮你高效记忆，薄弱点一目了然</li>
            </ol>
          </div>
        )}
      </section>

      {/* 今日学习目标概要 + 每日推荐任务 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-lg)', marginBottom: 'var(--space-xl)' }}>
        {/* 今日学习目标卡片 */}
        <div
          className="card card-accent-left"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate('/goals')}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter') navigate('/goals') }}
        >
          <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-sm)' }}>今日学习目标</h3>
          {goals.length === 0 ? (
            <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
              还没有学习目标，点击创建
            </p>
          ) : (
            <>
              <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-sm)' }}>
                进行中目标 {goals.length} 个
              </p>
              {/* 整体平均进度 */}
              <div style={{ marginBottom: 'var(--space-xs)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginBottom: 4 }}>
                  <span>平均进度</span>
                  <span>
                    {Math.round(
                      goals.reduce((sum, g) => sum + (g.progress_percentage ?? 0), 0) / goals.length
                    )}%
                  </span>
                </div>
                <div className="progress-bar">
                  <div
                    className="progress-bar-fill"
                    style={{
                      width: `${Math.min(
                        Math.max(
                          goals.reduce((sum, g) => sum + (g.progress_percentage ?? 0), 0) / goals.length,
                          0
                        ),
                        100
                      )}%`,
                    }}
                  />
                </div>
              </div>
              {/* 列出前 3 个目标名称 */}
              {goals.slice(0, 3).map(g => (
                <div
                  key={g.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '4px 0',
                    fontSize: '0.85rem',
                  }}
                >
                  <span style={{ fontWeight: 500 }}>{g.name}</span>
                  <span style={{ color: 'var(--color-text-secondary)' }}>
                    {g.progress_percentage ?? 0}%
                  </span>
                </div>
              ))}
            </>
          )}
        </div>

        {/* 每日推荐任务卡片 */}
        <div className="card card-accent-gold">
          <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-sm)' }}>每日推荐任务</h3>
          {!dailyPlan || dailyPlan.total_count === 0 ? (
            <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
              今日暂无推荐任务
            </p>
          ) : (
            <>
              {/* 总体进度 */}
              <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-sm)' }}>
                {dailyPlan.plan_date} · 已完成 {dailyPlan.completed_count} / {dailyPlan.total_count}
              </p>
              {/* 按类别统计任务数 */}
              <DailyTaskBreakdown plan={dailyPlan} navigate={navigate} />
            </>
          )}
        </div>
      </div>

      {/* 今日学习报告 */}
      {dailyReport && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 className="heading-serif" style={{ fontSize: '1.25rem', marginBottom: 'var(--space-md)' }}>
            今日学习报告 ({dailyReport.date})
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 'var(--space-md)' }}>
            <div className="stat-card stat-card-blue">
              <div className="stat-number">
                {dailyReport.new_mastered}
              </div>
              <div className="stat-label">新掌握</div>
            </div>
            <div className="stat-card stat-card-green">
              <div className="stat-number">
                {dailyReport.total_reviews}
              </div>
              <div className="stat-label">复习次数</div>
            </div>
            <div className="stat-card stat-card-gold">
              <div className="stat-number">
                {dailyReport.today_accuracy}%
              </div>
              <div className="stat-label">正确率</div>
            </div>
            <div className="stat-card stat-card-purple">
              <div className="stat-number">
                {formatTime(dailyReport.total_review_time_ms)}
              </div>
              <div className="stat-label">复习时长</div>
            </div>
          </div>

          {/* 各题型正确率 */}
          {dailyReport.question_type_accuracy.length > 0 && (
            <div style={{ display: 'flex', gap: 'var(--space-md)', marginTop: 'var(--space-md)', flexWrap: 'wrap' }}>
              {dailyReport.question_type_accuracy.filter(t => t.total > 0).map(t => (
                <span key={t.question_type} style={{
                  fontSize: '0.85rem',
                  padding: '4px 10px',
                  borderRadius: 4,
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                }}>
                  {questionTypeLabels[t.question_type] || t.question_type}: {t.accuracy}% ({t.correct}/{t.total})
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      {/* 复习提醒 + 薄弱点 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-lg)', marginBottom: 'var(--space-xl)' }}>
        {/* 待复习卡片 */}
        {reviewStats && reviewStats.due_count > 0 && (
          <div
            className="card card-accent-left"
            style={{
              cursor: 'pointer',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
            onClick={() => navigate('/review')}
            role="button"
            tabIndex={0}
          >
            <div>
              <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
                今日待复习: {reviewStats.due_count} 题
              </h3>
              <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                今日已完成 {reviewStats.today_done} 题 | 正确率 {reviewStats.today_accuracy}%
              </p>
            </div>
            <button className="btn btn-primary">开始复习</button>
          </div>
        )}

        {/* 薄弱点列表 */}
        {weakPoints.length > 0 && (
          <div className="card card-accent-error">
            <h3 style={{ fontWeight: 600, marginBottom: 'var(--space-sm)' }}>薄弱点</h3>
            {weakPoints.map(wp => (
              <div
                key={wp.card_id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: 'var(--space-xs) 0',
                  borderBottom: '1px solid var(--color-border)',
                  cursor: 'pointer',
                }}
                onClick={() => navigate(`/cards/${wp.card_id}`)}
              >
                <div>
                  <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>{wp.card_title}</span>
                  <span style={{
                    fontSize: '0.75rem',
                    marginLeft: 'var(--space-sm)',
                    padding: '1px 6px',
                    borderRadius: 3,
                    background: '#f4433620',
                    color: '#f44336',
                  }}>
                    {cardTypeLabels[wp.card_type] || wp.card_type}
                  </span>
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                  错{wp.error_count}次 | {wp.accuracy}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 7天趋势柱状图 */}
      {weeklyTrend.length > 0 && (
        <section style={{ marginBottom: 'var(--space-xl)' }}>
          <h2 className="heading-serif" style={{ fontSize: '1.25rem', marginBottom: 'var(--space-md)' }}>
            本周复习趋势
          </h2>
          <div className="card" style={{ padding: 'var(--space-lg)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 'var(--space-sm)', height: 120 }}>
              {weeklyTrend.map(day => (
                <div key={day.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  {/* 柱状图 */}
                  <div className={`trend-bar${day.accuracy < 60 ? ' trend-bar-warning' : ''}`} style={{
                    height: Math.max(day.review_count / maxReviewCount * 80, 4),
                  }}>
                    {/* 数量标签 */}
                    {day.review_count > 0 && (
                      <span style={{
                        position: 'absolute',
                        top: -18,
                        left: '50%',
                        transform: 'translateX(-50%)',
                        fontSize: '0.7rem',
                        color: 'var(--color-text-secondary)',
                        whiteSpace: 'nowrap',
                      }}>
                        {day.review_count}
                      </span>
                    )}
                  </div>
                  {/* 日期标签 */}
                  <span style={{ fontSize: '0.7rem', color: 'var(--color-text-secondary)', marginTop: 4 }}>
                    {day.date.slice(5)}
                  </span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 'var(--space-lg)', marginTop: 'var(--space-sm)', fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              <span>
                <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: 'var(--color-primary)', marginRight: 4 }} />
                正确率 ≥ 60%
              </span>
              <span>
                <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#ff9800', marginRight: 4 }} />
                正确率 &lt; 60%
              </span>
            </div>
          </div>
        </section>
      )}

      {/* 最近笔记区域 */}
      <section>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-md)' }}>
          <h2 className="heading-serif" style={{ fontSize: '1.25rem' }}>最近笔记</h2>
          <button className="btn btn-secondary" onClick={() => navigate('/notes')}>
            查看全部
          </button>
        </div>

        {loading ? (
          <LoadingSpinner />
        ) : error ? (
          <ErrorDisplay message={error} onRetry={fetchRecent} />
        ) : recentNotes.length === 0 ? (
          <EmptyState message="还没有笔记" description="上传你的第一份学习资料" action={<button className="btn btn-primary" onClick={() => navigate('/upload')}>上传资料</button>} />
        ) : (
          <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
            {recentNotes.map((note) => (
              <article
                key={note.id}
                className="card card-hover"
                style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                onClick={() => navigate(`/notes/${note.id}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/notes/${note.id}`) }}
              >
                <div>
                  <h3 style={{ fontWeight: 500, marginBottom: 'var(--space-xs)' }}>{note.title}</h3>
                  <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
                    <span className={`badge badge-${note.source_type}`}>
                      {sourceTypeLabels[note.source_type] || note.source_type}
                    </span>
                    <span className={`status-${note.status}`} style={{ fontSize: '0.8rem' }}>
                      {statusLabels[note.status] || note.status}
                    </span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      {new Date(note.created_at).toLocaleDateString('zh-CN')}
                    </span>
                  </div>
                </div>
                <span style={{ color: 'var(--color-text-secondary)' }} aria-hidden="true">→</span>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

/**
 * 每日推荐任务分类展示
 * 后端可能返回数组形式或对象形式，统一做兼容处理
 */
interface DailyTaskBreakdownProps {
  plan: DailyPlanResponse
  navigate: (path: string) => void
}

/** 任务类型到中文标签的映射 */
const taskTypeLabels: Record<string, string> = {
  weak_points: '薄弱点',
  review: '复习',
  new_materials: '新资料',
}

function DailyTaskBreakdown({ plan, navigate }: DailyTaskBreakdownProps) {
  // 将 recommended_tasks 统一规整为 RecommendedTask[] 数组
  // 兼容两种后端结构：数组形式 或 按类别分组的对象形式
  const tasks: RecommendedTask[] = (() => {
    const raw = plan.recommended_tasks as unknown
    if (Array.isArray(raw)) {
      return raw as RecommendedTask[]
    }
    // 对象形式：{"weak_points": [...], "review": [...], "new_materials": [...]}
    if (raw && typeof raw === 'object') {
      const obj = raw as Record<string, RecommendedTask[]>
      const merged: RecommendedTask[] = []
      for (const key of Object.keys(obj)) {
        const arr = obj[key]
        if (Array.isArray(arr)) {
          merged.push(...arr)
        }
      }
      return merged
    }
    return []
  })()

  // 按任务类型分组统计
  const grouped: Record<string, RecommendedTask[]> = {}
  for (const t of tasks) {
    if (!grouped[t.task_type]) grouped[t.task_type] = []
    grouped[t.task_type].push(t)
  }

  return (
    <div>
      {(['weak_point', 'review', 'new_material'] as const).map(cat => {
        const arr = grouped[cat]
        if (!arr || arr.length === 0) return null
        return (
          <div key={cat} style={{ marginBottom: 'var(--space-xs)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>
                {taskTypeLabels[cat] || cat}
              </span>
              <span style={{
                fontSize: '0.75rem',
                padding: '1px 6px',
                borderRadius: 3,
                background: 'var(--color-primary-light, #e3f2fd)',
                color: 'var(--color-primary)',
              }}>
                {arr.length} 个
              </span>
            </div>
            {/* 展示前 2 条任务标题作为示例 */}
            {arr.slice(0, 2).map((task, idx) => {
              // 含 quiz_id 的任务点击跳转复习页
              const clickable = !!task.quiz_id || !!task.note_id
              return (
                <div
                  key={idx}
                  style={{
                    fontSize: '0.8rem',
                    color: 'var(--color-text-secondary)',
                    paddingLeft: 'var(--space-sm)',
                    padding: '2px 0 2px var(--space-sm)',
                    cursor: clickable ? 'pointer' : 'default',
                  }}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (task.quiz_id) navigate('/review')
                    else if (task.note_id) navigate(`/notes/${task.note_id}`)
                  }}
                >
                  · {task.title}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

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
import { Link, useNavigate } from 'react-router-dom'
import {
  getNotes, getReviewStats, getDailyReport, getWeeklyTrend, getWeakPoints,
  getGoals, getDailyPlan,
  getUserReminderSettings, updateUserReminderSettings,
  type Note, type ReviewStats, type DailyReport, type WeeklyTrendItem, type WeakPoint,
  type LearningGoal, type DailyPlanResponse, type RecommendedTask,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import ReminderBanner from '../components/ReminderBanner'
// 统计卡片（overhaul-plan 5.6 第三批）：`.stat-card*` / `.stat-number` /
// `.stat-label` 原是全局类名、被本页与 TodayLearn 以同一 DOM 结构共用，
// 抽成组件后样式随组件进模块 —— 见 StatCard.module.css 文件头
import StatCard from '../components/StatCard'
import { useToast } from '../components/Toast'
import { sourceTypeLabels, statusLabels, statusClass, cardTypeLabels, questionTypeLabels, weakPointBadge } from '../utils/labels'
// 仪表盘私有样式（overhaul-plan 5.6）：从 src/styles/dashboard.css 拆出，
// 连带 768px 的两条响应式规则（见 Dashboard.module.css 文件头）
import styles from './Dashboard.module.css'

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
    // 统计类接口失败不再静默吞掉——记录失败集合，非关键失败轻提示，
    // 关键失败（notes）仍走整页错误，见 docs/decisions.md#F-24。
    // getDailyPlan 在"无活跃目标"时返回 400（正常业务状态，不是加载失败），
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

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
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

      {/* 邮件复习提醒开关（用户级设置，独立于浏览器通知权限） */}
      <EmailReminderToggle />

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

      {/* 今日学习目标概要 + 每日推荐任务（两栏布局见 Dashboard.module.css，
          窄屏收成一列的媒体查询也在那里 —— 类名哈希后 responsive.css 选不中它）

          ── 这一段的两个改动（a11y-audit F-07 / §4.1）──
          1. **标题级别**：卡片标题原来全是 `h3`，而 h1「欢迎使用 EngramNote」
             与本页的区块标题（`h2`：今日学习报告 / 本周复习趋势 / 最近笔记）
             之间缺一级 —— axe 的 `heading-order` 报的就是这个跳级。
             修法是把这四张卡片**提升为 `h2`**：它们本来就是这一页的顶层区块
             （与「最近笔记」平级），名字一个都没新起、一个字都没加。
             字号用 `1.17em` 显式钉住 —— 这正是 UA 样式表里 `h3` 的字号，
             所以计算后的字号与改动前逐像素相同（`em` 相对父元素，父元素没变）。
          2. **键盘可达**：第一张卡片原来是 `div[role="button"][tabIndex=0]`，
             而且只监听 `Enter`、不监听 `Space`（与已修的 F-09 是同一个洞的
             另一处入口；axe 报不出来，因为它里面没有可聚焦后代）。
             改法与 F-09 一致 —— 卡片回到"盒子"，行为落在**真链接**上：
             进「学习目标」是导航，链接比按钮更准（可右键、可新标签页）。 */}
      <div className={styles.dashboardTwoCol}>
        {/* 今日学习目标卡片 */}
        <div className="card card-accent-left">
          <h2 style={{ fontSize: '1.17em', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>
            {/* 下划线显式关掉：这是"整块可点的卡片标题"，不是正文里的行内链接
                （正文链接必须带下划线，见 base.css 与 F-13） */}
            <Link to="/goals" style={{ color: 'inherit', textDecoration: 'none' }}>
              今日学习目标
            </Link>
          </h2>
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
          <h2 style={{ fontSize: '1.17em', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>每日推荐任务</h2>
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
            <StatCard variant="blue" value={dailyReport.new_mastered} label="新掌握" />
            <StatCard variant="green" value={dailyReport.total_reviews} label="复习次数" />
            <StatCard variant="gold" value={`${dailyReport.today_accuracy}%`} label="正确率" />
            <StatCard variant="purple" value={formatTime(dailyReport.total_review_time_ms)} label="复习时长" />
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
      <div className={styles.dashboardTwoCol}>
        {/* 待复习卡片。
            ⚠️ 这里**没有** role="button"，容器也不再可聚焦 —— 这不是漏写：
            原来外层是 `div[role="button"][tabindex="0"]` 而里面又有一个真
            `<button>开始复习</button>`，axe 判 nested-interactive（F-09），
            且外层只监听 Enter、不监听 Space，与 role="button" 的约定不符。
            改法与已修掉的「Sidebar 按钮里嵌按钮」同形：**一张卡片一个控件**，
            卡片本身只是盒子，可访问名与行为都长在按钮上。 */}
        {reviewStats && reviewStats.due_count > 0 && (
          <div className={`card card-accent-left ${styles.dashboardReviewCard}`}>
            <div>
              <h2 style={{ fontSize: '1.17em', fontWeight: 600, marginBottom: 'var(--space-xs)' }}>
                今日待复习: {reviewStats.due_count} 题
              </h2>
              <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)' }}>
                今日已完成 {reviewStats.today_done} 题 | 正确率 {reviewStats.today_accuracy}%
              </p>
            </div>
            <button className="btn btn-primary" onClick={() => navigate('/review')}>开始复习</button>
          </div>
        )}

        {/* 薄弱点列表 */}
        {weakPoints.length > 0 && (
          <div className="card card-accent-error">
            <h2 style={{ fontSize: '1.17em', fontWeight: 600, marginBottom: 'var(--space-sm)' }}>薄弱点</h2>
            {/* 每一行是**真链接**：原来外层是 `div[onClick]`（没有 role/tabIndex），
                键盘到不了 —— 而"跳到那张卡片"是这一行唯一的行为。
                与 F-17（笔记列表卡片）/ CardDetail 的「来源笔记」同一次改法。
                `link-in-text-block` 不会命中它：链接是块级（`display:flex`）且
                自成一个区块，不是正文里被文字包住的行内链接。 */}
            {weakPoints.map(wp => (
              <Link
                key={wp.card_id}
                to={`/cards/${wp.card_id}`}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: 'var(--space-xs) 0',
                  borderBottom: '1px solid var(--color-border)',
                  color: 'inherit',
                  textDecoration: 'none',
                }}
              >
                <div>
                  <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>{wp.card_title}</span>
                  <span style={{
                    fontSize: '0.75rem',
                    marginLeft: 'var(--space-sm)',
                    padding: '1px 6px',
                    borderRadius: 3,
                    // 与今日学习页那份**共用同一个常量**（utils/labels.ts 的 weakPointBadge）。
                    // F-35 是在今日学习页报出来的（3.13:1），但这段 JSX 在**这一页上也有**
                    // 且字面相同 —— 它之所以没在本页报警，是因为默认桩的字段名与
                    // `WeakPoint` 契约不一致，渲染出的是 undefined 文本 + 空徽章，
                    // 而**空文本没有对比度可判**（见 e2e/a11y-fixtures.ts 的说明）。
                    background: weakPointBadge.background, color: weakPointBadge.color,
                  }}>
                    {cardTypeLabels[wp.card_type] || wp.card_type}
                  </span>
                </div>
                <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                  错{wp.error_count}次 | {wp.accuracy}%
                </span>
              </Link>
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
                  <div className={`${styles.trendBar}${day.accuracy < 60 ? ` ${styles.trendBarWarning}` : ''}`} style={{
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
              /* 卡片本身**不是**控件：原来写的是 `article[role="button"]`
                 （ARIA 不允许 article 用 button 角色，F-03/F-21）＋ 手写的
                 Enter 处理。现在卡片里**只有一个**控件：标题那个真链接
                 （可右键、可新标签页、可被读屏当作"链接"列出，Tab 一次即达）。
                 点击面确实比"整张卡片"小了，但那是这类修法必然的取舍：
                 一个可点区域必须有名字，而"整张卡片"作为可点区域时，
                 它的名字只能是卡片里所有文字拼成的一长串。
                 右侧的箭头**刻意不是第二个链接**（两个链接指向同一处，
                 读屏的链接列表里就会多出一条没有信息量的重复项）——
                 它只是装饰，`aria-hidden` 后由标题链接承担全部语义。
                 与 NotesList 的笔记卡片同形。 */
              <article
                key={note.id}
                className="card card-hover"
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <div>
                  <h3 style={{ fontWeight: 500, marginBottom: 'var(--space-xs)' }}>
                    {/* 下划线显式关掉，理由与 NotesList 的笔记卡片标题相同 */}
                    <Link
                      to={`/notes/${note.id}`}
                      style={{ color: 'inherit', textDecoration: 'none' }}
                    >
                      {note.title}
                    </Link>
                  </h3>
                  <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
                    <span className={`badge badge-${note.source_type}`}>
                      {sourceTypeLabels[note.source_type] || note.source_type}
                    </span>
                    <span className={statusClass(note.status)} style={{ fontSize: '0.8rem' }}>
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
 * 邮件复习提醒开关
 * 读写当前用户的 email_reminder_enabled 设置；独立于浏览器通知权限，
 * 初始加载与更新均容错（失败静默，保持原值，不打断页面）。
 */
function EmailReminderToggle() {
  const toast = useToast()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)

  // 挂载时读取当前设置（数据获取型 effect，异步 setState）
  useEffect(() => {
    let cancelled = false
    getUserReminderSettings()
      .then(res => { if (!cancelled) setEnabled(res.email_reminder_enabled) })
      .catch(() => { /* 容错：读取失败保持未加载状态，不打断页面 */ })
    return () => { cancelled = true }
  }, [])

  async function handleToggle(checked: boolean) {
    setBusy(true)
    try {
      const res = await updateUserReminderSettings(checked)
      setEnabled(res.email_reminder_enabled)
    } catch (err) {
      // 关键修复（§2.8 F-8）：原实现静默吞掉失败 —— 复选框会弹回原位、
      // 没有任何提示，用户以为开关坏了而反复点击，且无从判断原因。
      // 开关类控件必须给出明确反馈，否则"没生效"与"没点中"无法区分。
      toast.error(
        checked ? '开启邮件提醒失败' : '关闭邮件提醒失败',
        err instanceof Error ? err.message : undefined,
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="card"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 'var(--space-md)',
        marginBottom: 'var(--space-lg)',
        padding: 'var(--space-md) var(--space-lg)',
      }}
    >
      <div style={{ flex: 1 }}>
        <p style={{ fontWeight: 600, marginBottom: 'var(--space-xs)' }}>邮件复习提醒</p>
        <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
          每日通过邮件发送到期复习提醒
        </p>
      </div>
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-sm)',
          cursor: enabled === null || busy ? 'default' : 'pointer',
        }}
      >
        <input
          type="checkbox"
          checked={enabled ?? false}
          onChange={(e) => handleToggle(e.target.checked)}
          disabled={enabled === null || busy}
          style={{ accentColor: 'var(--color-primary)' }}
        />
        <span style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
          {enabled === null ? '加载中…' : (enabled ? '已开启' : '已关闭')}
        </span>
      </label>
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

/**
 * 任务类型到中文标签的映射。
 *
 * ⚠️ 键必须与契约一致：`RecommendedTask.task_type` 是
 * `'review' | 'new_material' | 'weak_point'`（**单数**，见 api/goals.ts:32）。
 * 这里原来写的是复数（`weak_points` / `new_materials`），于是标签查不到、
 * 页面上**原样渲染出英文键** `weak_point` —— 与 TodayLearn 的
 * `dailyTaskTypeLabels` 同源的那一份是对的，这一份写错了。
 * 之所以一直没被发现：默认桩的 `/api/goals/daily-plan` 给的是 **400**
 * （"无活跃目标"），「每日推荐任务」整块**从来不渲染**。
 * 本轮的 `dashboard` 场景第一次把这块渲染出来（覆盖 `DAILY_PLAN`），
 * 顺带把这两个键改对 —— 与 F-35 那次的"桩的字段名写错、页面照常渲染"同一类：
 * **没被渲染过的分支里，任何错误都不会报出来。**
 */
const taskTypeLabels: Record<string, string> = {
  weak_point: '薄弱点',
  review: '复习',
  new_material: '新资料',
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
              // 含 quiz_id / note_id 的任务可以点击跳转
              const clickable = !!task.quiz_id || !!task.note_id
              const go = () => {
                if (task.quiz_id) navigate('/review')
                else if (task.note_id) navigate(`/notes/${task.note_id}`)
              }
              const rowStyle: React.CSSProperties = {
                display: 'block',
                width: '100%',
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
                padding: '2px 0 2px var(--space-sm)',
                textAlign: 'left',
              }
              // ⚠️ 可点的那些必须是**真按钮**：原来无论可不可点都渲染
              // `div[onClick]`（没有 role/tabIndex），键盘**到不了**那一行。
              // 不可点的保持普通 div —— 渲染一个按不动的按钮是另一种误导。
              // `fontFamily` / `lineHeight` 显式继承：按钮的 UA 样式会把它们
              // 换成系统字体与 `normal`，不复位就是一次改版（字号/颜色本来就在
              // rowStyle 里显式写着）。`stopPropagation` 不再需要 —— 外层卡片
              // 已经不是可点区域（这一页的另一处修复）。
              return clickable ? (
                <button
                  key={idx}
                  type="button"
                  onClick={go}
                  style={{
                    ...rowStyle,
                    fontFamily: 'inherit',
                    lineHeight: 'inherit',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                >
                  · {task.title}
                </button>
              ) : (
                <div key={idx} style={rowStyle}>· {task.title}</div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

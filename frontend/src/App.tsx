/**
 * @file 应用根组件
 * @description EngramNote 前端应用的入口组件，负责：
 * 1. 通过 AuthProvider 提供全局认证状态
 * 2. 根据认证状态切换未登录/已登录两套路由
 * 3. 已登录时渲染侧边栏和主内容区域
 *
 * 代码分割（见 docs/overhaul-plan.md §2.8 F-7）：
 * 此前 18 个页面**全部静态 import**，且重量级依赖都在模块顶层进入口 chunk：
 *   - KnowledgeGraph → react-force-graph-2d（连带 d3 生态）
 *   - utils/markdown → highlight.js 完整构建（384 种语言）+ katex + 字体 CSS
 * 后果是"只想看仪表盘"的用户也要先下载并解析整个图谱引擎与高亮引擎。
 * 现改为按路由 React.lazy 懒加载；首屏只保留登录/仪表盘所需的代码。
 */
import { lazy, Suspense, useState } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Sidebar from './components/Sidebar'
import ErrorBoundary from './components/ErrorBoundary'
import LoadingSpinner from './components/LoadingSpinner'

// ── 登录前页面（首屏必需，保持静态导入以最快呈现登录框） ──
import Login from './pages/Login'
import Register from './pages/Register'

// ── 登录后页面：全部懒加载 ──
const Dashboard = lazy(() => import('./pages/Dashboard'))
const NotesList = lazy(() => import('./pages/NotesList'))
const NoteDetail = lazy(() => import('./pages/NoteDetail'))
const Trash = lazy(() => import('./pages/Trash'))
const Upload = lazy(() => import('./pages/Upload'))
const KnowledgeCards = lazy(() => import('./pages/KnowledgeCards'))
const KnowledgeGraph = lazy(() => import('./pages/KnowledgeGraph'))
const CardDetail = lazy(() => import('./pages/CardDetail'))
const QA = lazy(() => import('./pages/QA'))
const Review = lazy(() => import('./pages/Review'))
const QuestionSets = lazy(() => import('./pages/QuestionSets'))
const TodayLearn = lazy(() => import('./pages/TodayLearn'))
const QuickReview = lazy(() => import('./pages/QuickReview'))
const CardReview = lazy(() => import('./pages/CardReview'))
const DailyMaterials = lazy(() => import('./pages/DailyMaterials'))
const Projects = lazy(() => import('./pages/Projects'))
const LearningAssessment = lazy(() => import('./pages/LearningAssessment'))
const LearningGoals = lazy(() => import('./pages/LearningGoals'))

/** 路由级加载占位 */
function RouteFallback() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '64px 0' }}>
      <LoadingSpinner />
    </div>
  )
}

/** 未找到页面（原先 `*` 会静默重定向到 "/" 或登录页，链路失效时用户无从判断） */
function NotFound() {
  return (
    <div style={{ textAlign: 'center', padding: '64px 16px' }}>
      <h1 className="heading-serif" style={{ fontSize: '2rem', marginBottom: 8 }}>404</h1>
      <p style={{ color: 'var(--color-text-secondary)', marginBottom: 20 }}>
        页面不存在，可能是链接已失效。
      </p>
      <a className="btn btn-primary" href="/">返回首页</a>
    </div>
  )
}

/**
 * 路由组件
 * 根据认证状态渲染不同路由
 */
function AppRoutes() {
  const { isAuthenticated } = useAuth()
  const location = useLocation()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/register" element={<Register />} />
        <Route path="/login" element={<Login />} />
        {/* 未登录访问其它路径时才回到登录页 */}
        <Route path="*" element={<Login />} />
      </Routes>
    )
  }

  return (
    <>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(c => !c)}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      {/* 移动端汉堡菜单（桌面由 CSS 隐藏；抽屉打开时抽屉会盖住它，见 layout.css） */}
      <button
        className="sidebar-mobile-toggle"
        onClick={() => setMobileOpen(true)}
        aria-label="打开菜单"
        aria-expanded={mobileOpen}
      >
        {'\u2630'}
      </button>
      <div className={`app-layout${sidebarCollapsed ? ' app-layout-collapsed' : ''}`}>
        <main className="container page-enter">
          {/* 按路由重置的错误边界：某条数据触发渲染异常后，
              切换到别的页面即可自动恢复，不必刷新（§2.8 F-2） */}
          <ErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                {/* 认证入口的已登录兜底（与未登录分支里那两条同路径路由对应）：
                    令牌在 localStorage 里长期有效，用户完全可能带着已登录状态
                    回到 /login（历史记录、书签，或注册成功后回退一格）。
                    这里若不拦，pathname 会停在 /login 而本表没有这条路由，
                    于是落到下面的 path="*" 渲染出 404 —— 与"登录成功后停在
                    /login"是同一个洞的另一个入口，只修跳转等于把它留着。 */}
                <Route path="/login" element={<Navigate to="/" replace />} />
                <Route path="/register" element={<Navigate to="/" replace />} />
                <Route path="/" element={<Dashboard />} />
                <Route path="/notes" element={<NotesList />} />
                <Route path="/notes/:noteId" element={<NoteDetail />} />
                <Route path="/trash" element={<Trash />} />
                <Route path="/cards" element={<KnowledgeCards />} />
                <Route path="/graph" element={<KnowledgeGraph />} />
                <Route path="/cards/:cardId" element={<CardDetail />} />
                <Route path="/questions" element={<QuestionSets />} />
                <Route path="/qa" element={<QA />} />
                <Route path="/review" element={<Review />} />
                <Route path="/today" element={<TodayLearn />} />
                <Route path="/daily" element={<DailyMaterials />} />
                <Route path="/projects" element={<Projects />} />
                <Route path="/review/quick/:noteId" element={<QuickReview />} />
                {/* 卡片直接复习（阶段 3.12）：与答题复习是并行的两条路径 */}
                <Route path="/review/cards" element={<CardReview />} />
                <Route path="/assessment" element={<LearningAssessment />} />
                <Route path="/goals" element={<LearningGoals />} />
                <Route path="/upload" element={<Upload />} />
                {/* 真正的 404，而不是静默重定向 */}
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}

export default App

/**
 * @file 应用根组件
 * @description EngramNote 前端应用的入口组件，负责：
 * 1. 通过 AuthProvider 提供全局认证状态
 * 2. 根据认证状态切换未登录/已登录两套路由
 * 3. 已登录时渲染侧边栏和主内容区域
 */
import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import NotesList from './pages/NotesList'
import NoteDetail from './pages/NoteDetail'
import Trash from './pages/Trash'
import Upload from './pages/Upload'
import KnowledgeCards from './pages/KnowledgeCards'
import KnowledgeGraph from './pages/KnowledgeGraph'
import CardDetail from './pages/CardDetail'
import QA from './pages/QA'
import Review from './pages/Review'
import QuestionSets from './pages/QuestionSets'
import TodayLearn from './pages/TodayLearn'
import QuickReview from './pages/QuickReview'
import DailyMaterials from './pages/DailyMaterials'
import Projects from './pages/Projects'
import LearningAssessment from './pages/LearningAssessment'
import LearningGoals from './pages/LearningGoals'
import Sidebar from './components/Sidebar'

/**
 * 路由组件
 * 根据认证状态渲染不同路由
 */
function AppRoutes() {
  const { isAuthenticated } = useAuth()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/register" element={<Register />} />
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
      {/* 移动端汉堡菜单 */}
      <button className="sidebar-mobile-toggle" onClick={() => setMobileOpen(true)} aria-label="打开菜单">
        {'\u2630'}
      </button>
      <div className={`app-layout${sidebarCollapsed ? ' app-layout-collapsed' : ''}`}>
        <main className="container page-enter">
          <Routes>
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
            <Route path="/assessment" element={<LearningAssessment />} />
            <Route path="/goals" element={<LearningGoals />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
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

/**
 * @file 应用根组件
 * @description EngramNote 前端应用的入口组件，负责：
 * 1. 通过 AuthProvider 提供全局认证状态
 * 2. 根据认证状态切换未登录/已登录两套路由
 * 3. 已登录时渲染导航栏和主内容区域
 */
import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import NotesList from './pages/NotesList'
import NoteDetail from './pages/NoteDetail'
import Upload from './pages/Upload'
import KnowledgeCards from './pages/KnowledgeCards'
import CardDetail from './pages/CardDetail'
import QA from './pages/QA'
import Review from './pages/Review'
import QuestionSets from './pages/QuestionSets'
import TodayLearn from './pages/TodayLearn'
import Navbar from './components/Navbar'

/**
 * 路由组件
 * 根据认证状态渲染不同路由
 */
function AppRoutes() {
  const { isAuthenticated } = useAuth()

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
      <Navbar />
      <main className="container" style={{ paddingTop: '80px' }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/notes" element={<NotesList />} />
          <Route path="/notes/:noteId" element={<NoteDetail />} />
          <Route path="/cards" element={<KnowledgeCards />} />
          <Route path="/cards/:cardId" element={<CardDetail />} />
          <Route path="/questions" element={<QuestionSets />} />
          <Route path="/qa" element={<QA />} />
          <Route path="/review" element={<Review />} />
          <Route path="/today" element={<TodayLearn />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
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

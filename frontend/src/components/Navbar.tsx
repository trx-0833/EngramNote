/**
 * @file 顶部导航栏组件
 * @description 固定在页面顶部的导航栏，通过 AuthContext 管理退出登录。
 */
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function Navbar() {
  const navigate = useNavigate()
  const { logout } = useAuth()

  return (
    <nav
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        height: '60px',
        background: 'var(--color-surface)',
        borderBottom: '1px solid var(--color-border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 var(--space-lg)',
        zIndex: 100,
        boxShadow: 'var(--shadow-sm)',
      }}
      role="navigation"
      aria-label="主导航"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-lg)' }}>
        <button
          onClick={() => navigate('/')}
          style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--color-primary)' }}
        >
          EngramNote
        </button>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button className="btn btn-secondary" onClick={() => navigate('/')}>
            仪表盘
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/notes')}>
            笔记
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/cards')}>
            知识库
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/questions')}>
            问题集
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/qa')}>
            问答
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/today')}>
            今日学习
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/upload')}>
            上传
          </button>
        </div>
      </div>
      <button className="btn btn-secondary" onClick={logout}>
        退出
      </button>
    </nav>
  )
}

/**
 * @file 侧边栏组件
 * @description Notion 式左侧边栏，分组导航，支持折叠/展开
 * 视觉重构：替代原有顶部 Navbar
 */
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

/** 导航分组定义 */
const NAV_SECTIONS = [
  {
    title: '',
    items: [
      { path: '/', label: '仪表盘', icon: '\u2302' },
    ],
  },
  {
    title: '学习',
    items: [
      { path: '/today', label: '今日学习', icon: '\u2618' },
      { path: '/daily', label: '今日资料', icon: '\u25B7' },
      { path: '/projects', label: '项目', icon: '\u25A3' },
      { path: '/assessment', label: '学习评估', icon: '\u2713' },
      { path: '/goals', label: '学习目标', icon: '\u25C9' },
    ],
  },
  {
    title: '笔记',
    items: [
      { path: '/notes', label: '笔记列表', icon: '\u2630' },
    ],
  },
  {
    title: '知识',
    items: [
      { path: '/cards', label: '知识卡片', icon: '\u25C8' },
      { path: '/graph', label: '知识图谱', icon: '\u25CE' },
      { path: '/qa', label: '问答', icon: '\u2753' },
      { path: '/questions', label: '问题集', icon: '\u2611' },
    ],
  },
]

interface SidebarProps {
  collapsed: boolean
  onToggleCollapse: () => void
  mobileOpen: boolean
  onMobileClose: () => void
}

export default function Sidebar({ collapsed, onToggleCollapse, mobileOpen, onMobileClose }: SidebarProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { logout } = useAuth()

  function handleNav(path: string) {
    navigate(path)
    onMobileClose()
  }

  function isActive(path: string) {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  const sidebarClass = `sidebar${collapsed ? ' sidebar-collapsed' : ''}${mobileOpen ? ' sidebar-mobile-open' : ''}`

  return (
    <>
      {/* 移动端遮罩 */}
      {mobileOpen && <div className="sidebar-overlay" onClick={onMobileClose} />}

      <nav className={sidebarClass} role="navigation" aria-label="主导航">
        {/* 头部：Logo + 折叠按钮 */}
        <div className="sidebar-header">
          <button className="sidebar-logo" onClick={() => handleNav('/')}>
            {collapsed ? 'E' : 'EngramNote'}
          </button>
          <button className="sidebar-collapse-btn" onClick={onToggleCollapse} aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}>
            {collapsed ? '\u00BB' : '\u00AB'}
          </button>
        </div>

        {/* 导航分组 */}
        <div className="sidebar-body">
          {NAV_SECTIONS.map(section => (
            <div key={section.title || 'home'} className="sidebar-section">
              {section.title && (
                <div className="sidebar-section-title">{section.title}</div>
              )}
              {section.items.map(item => (
                <button
                  key={item.path}
                  className={`sidebar-item${isActive(item.path) ? ' sidebar-item-active' : ''}`}
                  onClick={() => handleNav(item.path)}
                >
                  <span className="sidebar-item-icon">{item.icon}</span>
                  <span className="sidebar-item-label">{item.label}</span>
                  {/* 上传快捷入口在笔记分组 */}
                  {item.path === '/notes' && !collapsed && (
                    <button
                      className="sidebar-item-action"
                      onClick={(e) => { e.stopPropagation(); handleNav('/upload') }}
                      aria-label="上传资料"
                    >
                      +
                    </button>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>

        {/* 底部：退出 */}
        <div className="sidebar-footer">
          <button className="sidebar-item" onClick={logout}>
            <span className="sidebar-item-icon">{'\u2190'}</span>
            <span className="sidebar-item-label">退出</span>
          </button>
        </div>
      </nav>
    </>
  )
}

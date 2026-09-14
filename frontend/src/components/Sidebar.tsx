/**
 * @file 侧边栏组件
 * @description Notion 式左侧边栏，分组导航，支持折叠/展开
 * 视觉重构：替代原有顶部 Navbar
 *
 * 移动端（overhaul-plan 5.10）：≤768px 时侧边栏变抽屉，由 App.tsx 的汉堡按钮打开。
 * 本组件在这里补两件手机上必须有的东西：
 * 1. 抽屉内的「关闭」按钮 —— 遮罩和"点条目自动关闭"都在，但手指够不到遮罩边缘时
 *    （大屏手机上抽屉占 86vw）需要一个明确的关闭入口；
 * 2. 打开时锁住页面滚动 —— 否则在遮罩上滑动会把背后的页面滚走，
 *    关掉抽屉后用户会发现自己被带到别处了。
 */
import { useEffect } from 'react'
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
      { path: '/review/cards', label: '卡片复习', icon: '\u21BB' },
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
      { path: '/trash', label: '回收站', icon: '\u2672' },
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

  // 抽屉打开期间锁住 body 滚动（样式见 layout.css 的 body.sidebar-open-lock）。
  // 放在 effect 里而不是渲染期，是为了让"打开/关闭/卸载"三种路径都必然解绑 ——
  // 直接在渲染里 add 会在组件卸载时把锁留在 body 上，整页再也滚不动。
  useEffect(() => {
    if (!mobileOpen) return
    document.body.classList.add('sidebar-open-lock')
    return () => document.body.classList.remove('sidebar-open-lock')
  }, [mobileOpen])

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
        {/* 头部：Logo + 折叠按钮（桌面）/ 关闭按钮（移动抽屉） */}
        <div className="sidebar-header">
          <button className="sidebar-logo" onClick={() => handleNav('/')}>
            {collapsed ? 'E' : 'EngramNote'}
          </button>
          <button className="sidebar-collapse-btn" onClick={onToggleCollapse} aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}>
            {collapsed ? '\u00BB' : '\u00AB'}
          </button>
          {/* 仅移动端抽屉打开时出现：手机上抽屉占 86vw，手指够不到遮罩边缘时
              需要一个明确的关闭入口（显示/隐藏由 mobileOpen 决定，见本文件顶部说明） */}
          {mobileOpen && (
            <button className="sidebar-mobile-close" onClick={onMobileClose} aria-label="关闭菜单">
              {'\u2715'}
            </button>
          )}
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

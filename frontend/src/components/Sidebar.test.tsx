/**
 * @file 移动端导航抽屉的行为测试（overhaul-plan 5.10 响应式）
 *
 * ## 为什么这一页需要测试
 *
 * 手机上唯一的导航入口就是这颗汉堡按钮 + 抽屉。而抽屉的三条"关掉它"的路径
 * （点关闭按钮 / 点遮罩 / 点任意导航项）**都只在运行时存在**：
 * 少任何一条，用户在手机上要么被抽屉挡住页面，要么以为已经跳转却没跳。
 * 5.10 又新增了两处行为（抽屉内的关闭按钮、打开时锁 body 滚动），
 * 它们没有 CSS 兜底 —— 只有断言能钉住。
 *
 * ## jsdom 测不到的部分（本文件刻意不假装覆盖）
 *
 * jsdom 不做布局、也不加载样式表，所以下面这些**只能靠人眼/真浏览器**验证：
 * - 抽屉实际宽度（86vw）、是否真的从左侧滑出、是否盖住汉堡按钮；
 * - `.sidebar-item` 的 44px 触控高度、折叠态（collapsed）下标签由 CSS 恢复显示；
 * - 页面顶部内边距是否刚好让开汉堡按钮。
 * 本文件只钉"行为"：谁能关掉抽屉、点了之后状态变成什么。
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from './Sidebar'

// useAuth 由 AuthContext 提供；这里只用到 logout 一个字段（vi.hoisted 让工厂能引用它）
const auth = vi.hoisted(() => ({ logout: vi.fn() }))
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ logout: auth.logout }),
}))

/** 用当前路由路径做导航断言（而不是去查 history，模拟"用户看到的位置"） */
function LocationProbe() {
  const location = useLocation()
  return <span data-testid="pathname">{location.pathname}</span>
}

interface RenderOptions {
  collapsed?: boolean
  mobileOpen?: boolean
}

function renderSidebar({ collapsed = false, mobileOpen = false }: RenderOptions = {}) {
  const onMobileClose = vi.fn()
  const onToggleCollapse = vi.fn()
  const utils = render(
    <MemoryRouter initialEntries={['/']}>
      <Sidebar
        collapsed={collapsed}
        onToggleCollapse={onToggleCollapse}
        mobileOpen={mobileOpen}
        onMobileClose={onMobileClose}
      />
      <LocationProbe />
    </MemoryRouter>,
  )
  return { ...utils, onMobileClose, onToggleCollapse }
}

/** 侧边栏里的全部导航目的地（与 NAV_SECTIONS 一一对应） */
const DESTINATIONS = [
  '仪表盘',
  '今日学习',
  '卡片复习',
  '今日资料',
  '项目',
  '学习评估',
  '学习目标',
  '笔记列表',
  '回收站',
  '知识卡片',
  '知识图谱',
  '问答',
  '问题集',
]

describe('移动端抽屉：每个入口都还在', () => {
  it('★ 全部导航目的地都渲染成可点的按钮（加抽屉不能顺手删掉入口）', () => {
    renderSidebar({ mobileOpen: true })
    for (const label of DESTINATIONS) {
      // 按钮的可访问名里含图标字符（如 "⌂ 仪表盘"），所以用正则匹配文案
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument()
    }
  })

  it('抽屉里仍能到达"上传资料"（笔记分组上的 + 快捷入口）', () => {
    renderSidebar({ mobileOpen: true })
    expect(screen.getByRole('button', { name: '上传资料' })).toBeInTheDocument()
  })

  it('退出登录按钮仍在抽屉底部，点它调用 logout', async () => {
    auth.logout.mockClear()
    renderSidebar({ mobileOpen: true })
    await userEvent.click(screen.getByRole('button', { name: /退出/ }))
    expect(auth.logout).toHaveBeenCalledTimes(1)
  })
})

describe('移动端抽屉：三条关闭路径', () => {
  it('关闭按钮只在抽屉打开时出现（关着的时候不该有个点不动的 ✕）', () => {
    const { unmount } = renderSidebar({ mobileOpen: false })
    expect(screen.queryByRole('button', { name: '关闭菜单' })).not.toBeInTheDocument()
    unmount()

    renderSidebar({ mobileOpen: true })
    expect(screen.getByRole('button', { name: '关闭菜单' })).toBeInTheDocument()
  })

  it('★ 点关闭按钮 → 通知父组件关闭抽屉', async () => {
    const { onMobileClose } = renderSidebar({ mobileOpen: true })
    await userEvent.click(screen.getByRole('button', { name: '关闭菜单' }))
    expect(onMobileClose).toHaveBeenCalledTimes(1)
  })

  it('★ 遮罩只在抽屉打开时渲染，点遮罩关闭抽屉', async () => {
    const { unmount } = renderSidebar({ mobileOpen: false })
    expect(document.querySelector('.sidebar-overlay')).toBeNull()
    unmount()

    const { onMobileClose } = renderSidebar({ mobileOpen: true })
    const overlay = document.querySelector('.sidebar-overlay')
    expect(overlay).not.toBeNull()
    await userEvent.click(overlay as HTMLElement)
    expect(onMobileClose).toHaveBeenCalledTimes(1)
  })

  it('★ 点任意导航项：既跳转，又关掉抽屉（否则抽屉会一直挡着新页面）', async () => {
    const { onMobileClose } = renderSidebar({ mobileOpen: true })
    await userEvent.click(screen.getByRole('button', { name: /知识图谱/ }))

    expect(screen.getByTestId('pathname')).toHaveTextContent('/graph')
    expect(onMobileClose).toHaveBeenCalledTimes(1)
  })

  it('点 Logo 回到仪表盘，并同样关掉抽屉', async () => {
    const { onMobileClose } = renderSidebar({ mobileOpen: true })
    await userEvent.click(screen.getByRole('button', { name: 'EngramNote' }))
    expect(screen.getByTestId('pathname')).toHaveTextContent('/')
    expect(onMobileClose).toHaveBeenCalledTimes(1)
  })
})

describe('移动端抽屉：打开时锁住页面滚动', () => {
  it('★ 打开加锁、关闭解锁', () => {
    const { rerender } = render(
      <MemoryRouter>
        <Sidebar collapsed={false} onToggleCollapse={vi.fn()} mobileOpen onMobileClose={vi.fn()} />
      </MemoryRouter>,
    )
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(true)

    rerender(
      <MemoryRouter>
        <Sidebar collapsed={false} onToggleCollapse={vi.fn()} mobileOpen={false} onMobileClose={vi.fn()} />
      </MemoryRouter>,
    )
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })

  it('★ 抽屉还开着就被卸载时也必须解锁（锁留在 body 上整页会再也滚不动）', () => {
    const { unmount } = render(
      <MemoryRouter>
        <Sidebar collapsed={false} onToggleCollapse={vi.fn()} mobileOpen onMobileClose={vi.fn()} />
      </MemoryRouter>,
    )
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(true)
    unmount()
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })

  it('抽屉没打开时不加锁（桌面端不该受影响）', () => {
    renderSidebar({ mobileOpen: false })
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })
})

describe('桌面形态不回归', () => {
  it('折叠按钮在渲染时始终存在且可点（它的显示与否由 CSS 决定，这里只保证没被拆掉）', async () => {
    const { onToggleCollapse } = renderSidebar({ collapsed: false })
    const collapse = screen.getByRole('button', { name: '收起侧边栏' })
    await userEvent.click(collapse)
    expect(onToggleCollapse).toHaveBeenCalledTimes(1)
  })

  it('折叠态下当前路由的条目仍然带 active 类（收起不等于丢失选中态）', () => {
    renderSidebar({ collapsed: true, mobileOpen: true })
    const dashboard = screen.getByRole('button', { name: /仪表盘/ })
    expect(dashboard.className).toContain('sidebar-item-active')
  })
})

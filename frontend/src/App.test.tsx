/**
 * @file 移动端汉堡菜单的行为测试（overhaul-plan 5.10 响应式）
 *
 * ## 为什么在 App 层级测
 *
 * 汉堡按钮（`.sidebar-mobile-toggle`）与抽屉的开关状态都住在 `App.tsx`：
 * 按钮点下去做了什么、导航之后抽屉有没有自动收起，只有把 App 渲染起来才看得见。
 * 这是手机上唯一的导航入口 —— 它坏掉等于整站在手机上进不去，
 * 而在此之前没有任何用例覆盖它。
 *
 * ## 关于桩
 *
 * 认证状态直接给"已登录"（真实 AuthProvider 会去校验 token，与本文件无关）；
 * 路由页面换成桩，避免把各页面的数据请求拖进来。
 *
 * ## jsdom 测不到的（不假装覆盖）
 *
 * 汉堡按钮在 ≥769px 时由 CSS 隐藏、抽屉的滑出动画、44px 触控尺寸、
 * 顶部内边距是否刚好让开按钮 —— 这些都需要真浏览器/人眼。
 * 本文件只钉"点了之后状态怎么变"。
 *
 * ## 关于类名（overhaul-plan 5.6 序 9 改过这一文件）
 *
 * 抽屉、遮罩、body 滚动锁的类名原来是**全局**的（`layout.css` / `responsive.css`），
 * 序 9 把它们连同窄屏规则一起搬进了 `components/Sidebar.module.css` /
 * `App.module.css` —— 类名在产物里是哈希的，字面量 `'sidebar-mobile-open'`
 * 之类的查询必然落空。所以这里改成两种更结实的写法：
 *
 * 1. **语义查询优先**：抽屉开着还是关着，用户与读屏能感知的是汉堡按钮的
 *    `aria-expanded`（同一份状态渲染出来的），断言它比断言类名更接近"用户看到的东西"；
 * 2. 语义表达不了的（遮罩这个纯装饰 div 没有角色、body 的滚动锁没有 ARIA 等价物）
 *    才 `import styles from ...` 用**模块导出的类名**——这与实现绑定得紧一些，
 *    但比"把类名留在全局只为让测试能查"好：后者会让响应式规则永远无法随组件搬走。
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import App from './App'
// 遮罩 / 滚动锁的类名（哈希后只在模块里对得上）—— 见文件头
import sidebarStyles from './components/Sidebar.module.css'

// 已登录 + AuthProvider 透传：本文件不测认证
vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => ({
    isAuthenticated: true,
    user: { id: 'u-1', email: 'a@b.c', username: 'tester' },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}))

// 懒加载页面换成桩：本文件只关心导航抽屉
vi.mock('./pages/Dashboard', () => ({ default: () => <div data-testid="page-dashboard" /> }))
vi.mock('./pages/NotesList', () => ({ default: () => <div data-testid="page-notes" /> }))

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
}

/** 抽屉（`<nav role="navigation">`）当前的类名 */
function sidebarNav() {
  return screen.getByRole('navigation', { name: '主导航' })
}

/** 遮罩（纯装饰 div，没有角色可查 → 用模块导出的类名） */
function overlay() {
  return document.querySelector(`.${sidebarStyles.sidebarOverlay}`)
}

/** 汉堡按钮：抽屉开合状态的语义出口（与抽屉同一个 state 渲染） */
function menuToggle() {
  return screen.getByRole('button', { name: '打开菜单' })
}

describe('移动端汉堡菜单', () => {
  it('★ 点汉堡按钮打开抽屉：抽屉带 mobile-open 类、出现遮罩、并锁住页面滚动', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    const toggle = menuToggle()
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(sidebarNav().className).not.toContain(sidebarStyles.sidebarMobileOpen)
    expect(overlay()).toBeNull()

    await userEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(sidebarNav().className).toContain(sidebarStyles.sidebarMobileOpen)
    expect(overlay()).not.toBeNull()
    expect(document.body.classList.contains(sidebarStyles.sidebarOpenLock)).toBe(true)
  })

  it('★ 点导航项：跳转成功，且抽屉自动收起（否则新页面被抽屉挡着）', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(menuToggle())
    await userEvent.click(screen.getByRole('button', { name: /笔记列表/ }))

    expect(await screen.findByTestId('page-notes')).toBeInTheDocument()
    expect(menuToggle()).toHaveAttribute('aria-expanded', 'false')
    expect(sidebarNav().className).not.toContain(sidebarStyles.sidebarMobileOpen)
    expect(overlay()).toBeNull()
    expect(document.body.classList.contains(sidebarStyles.sidebarOpenLock)).toBe(false)
  })

  it('★ 点遮罩：抽屉收起、解锁滚动', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(menuToggle())
    await userEvent.click(overlay() as HTMLElement)

    expect(menuToggle()).toHaveAttribute('aria-expanded', 'false')
    expect(sidebarNav().className).not.toContain(sidebarStyles.sidebarMobileOpen)
    expect(document.body.classList.contains(sidebarStyles.sidebarOpenLock)).toBe(false)
  })

  it('抽屉里的关闭按钮同样能收起抽屉', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(menuToggle())
    await userEvent.click(screen.getByRole('button', { name: '关闭菜单' }))

    expect(menuToggle()).toHaveAttribute('aria-expanded', 'false')
    expect(sidebarNav().className).not.toContain(sidebarStyles.sidebarMobileOpen)
    expect(document.body.classList.contains(sidebarStyles.sidebarOpenLock)).toBe(false)
  })
})

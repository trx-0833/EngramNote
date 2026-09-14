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
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import App from './App'

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

/** 抽屉（<nav class="sidebar">）当前的类名 */
function sidebarNav() {
  return screen.getByRole('navigation', { name: '主导航' })
}

describe('移动端汉堡菜单', () => {
  it('★ 点汉堡按钮打开抽屉：抽屉带 mobile-open 类、出现遮罩、并锁住页面滚动', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    const toggle = screen.getByRole('button', { name: '打开菜单' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(sidebarNav().className).not.toContain('sidebar-mobile-open')
    expect(document.querySelector('.sidebar-overlay')).toBeNull()

    await userEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(sidebarNav().className).toContain('sidebar-mobile-open')
    expect(document.querySelector('.sidebar-overlay')).not.toBeNull()
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(true)
  })

  it('★ 点导航项：跳转成功，且抽屉自动收起（否则新页面被抽屉挡着）', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(screen.getByRole('button', { name: '打开菜单' }))
    await userEvent.click(screen.getByRole('button', { name: /笔记列表/ }))

    expect(await screen.findByTestId('page-notes')).toBeInTheDocument()
    expect(sidebarNav().className).not.toContain('sidebar-mobile-open')
    expect(document.querySelector('.sidebar-overlay')).toBeNull()
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })

  it('★ 点遮罩：抽屉收起、解锁滚动', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(screen.getByRole('button', { name: '打开菜单' }))
    await userEvent.click(document.querySelector('.sidebar-overlay') as HTMLElement)

    expect(sidebarNav().className).not.toContain('sidebar-mobile-open')
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })

  it('抽屉里的关闭按钮同样能收起抽屉', async () => {
    renderApp()
    await screen.findByTestId('page-dashboard')

    await userEvent.click(screen.getByRole('button', { name: '打开菜单' }))
    await userEvent.click(screen.getByRole('button', { name: '关闭菜单' }))

    expect(sidebarNav().className).not.toContain('sidebar-mobile-open')
    expect(document.body.classList.contains('sidebar-open-lock')).toBe(false)
  })
})

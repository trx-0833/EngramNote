/**
 * E2E：登录全链路（对桩掉的 /api）（overhaul-plan 5.13）
 *
 * ## 这条链路跨越了什么
 *
 *   Login.tsx（受控表单 + 原生校验）
 *     → AuthContext.login
 *       → api/client.ts 的 request()（超时、错误归一化、凭据类 401 的特判）
 *         → fetch /api/auth/login
 *           → setTokens()（localStorage 里**一对**令牌）
 *             → setIsAuthenticated(true)
 *               → App.tsx 切换到已登录路由 + Sidebar + Dashboard
 *
 * 这条链上的每一环在 Vitest 里都被**单独 mock 过**，因此没有一条用例
 * 真正把它们串起来跑过。这里跑的是真浏览器、真 fetch、真 localStorage、
 * 真 React 路由切换 —— 唯一被替换掉的是后端的 HTTP 响应。
 *
 * ## 网络是被桩掉的，这一点必须说清楚
 *
 * `/api` 全部由 `stubApi` 拦截（见 support.ts）。所以本文件证明的是
 * **前端链路自洽**，不证明后端行为。后端契约由 `backend/tests/` 负责
 * （尤其是会话生命周期与刷新令牌那几组）。
 *
 * ## 起点为什么曾经只有 `/`
 *
 * 本文件最初只有 2 条用例，且都从 `/` 开始 —— 因为从 `/login` 登录会落到 404
 * （本轮 E2E 发现的产品缺陷，当时留成了 `test.fixme`）。
 * 现在产品代码已修（`Login.tsx` 成功后 `navigate('/')` + `App.tsx` 已登录分支
 * 给 `/login`、`/register` 加 `Navigate` 兜底），那条 `fixme` 也变成了真断言：
 * 从 `/login` 走完整条链路，收敛到同一个仪表盘。
 * 缺陷记录见 `frontend/docs/e2e.md` §5。
 */
import { expect, test } from '@playwright/test'

import { blockThirdParty, collectPageErrors, stubApi } from './support'

/** 读 localStorage 里的一对令牌 */
async function readTokens(page: import('@playwright/test').Page) {
  return page.evaluate(() => ({
    access: localStorage.getItem('engramnote_token'),
    refresh: localStorage.getItem('engramnote_refresh_token'),
  }))
}

test.describe('登录（桩 API）', () => {
  test('凭据正确：令牌成对落盘 → 路由切到已登录外壳 → 仪表盘渲染', async ({ page }) => {
    const errors = collectPageErrors(page)
    await stubApi(page, 'ok')
    await blockThirdParty(page)

    // 未登录时 `/` 由 `path="*"` 渲染登录页（App.tsx），这正是真实用户
    // 打开应用时的入口 —— 所以从 `/` 开始才是被支持的路径。
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.locator('#email').fill('e2e@example.com')
    await page.locator('#password').fill('secret123')
    await page.getByRole('button', { name: '登录' }).click()

    // 1. 进入已登录外壳：侧边栏出现（未登录时它整棵都不渲染）
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()

    // 2. 一对令牌都落盘。只存访问令牌是 6.3 明确修掉的缺陷
    //    （会话无法续期、也无法撤销），这里把它钉住。
    expect(await readTokens(page)).toEqual({
      access: 'e2e-access-token',
      refresh: 'e2e-refresh-token',
    })

    // 3. 默认路由真的渲染了 Dashboard（桩返回空列表 → 空态而不是错误态）
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '最近笔记' })).toBeVisible()

    expect(errors).toEqual([])
  })

  test('凭据错误（401）：显示统一文案、不写令牌、留在登录页', async ({ page }) => {
    const errors = collectPageErrors(page)
    await stubApi(page, 'unauthorized')
    await blockThirdParty(page)

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.locator('#email').fill('e2e@example.com')
    await page.locator('#password').fill('wrong-password')
    await page.getByRole('button', { name: '登录' }).click()

    // 凭据类接口的 401 文案是固定的「邮箱或密码错误」，
    // 不是后端 detail（避免把"账号是否存在"泄露给调用方，见 6.2）
    await expect(page.getByRole('alert')).toHaveText('邮箱或密码错误')

    // 失败不能留下半个会话
    expect(await readTokens(page)).toEqual({ access: null, refresh: null })
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible()
    await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0)

    expect(errors).toEqual([])
  })

  /**
   * 回归守卫：**从 `/login` 登录**成功后会落到 404（本轮 E2E 发现的产品缺陷）
   *
   * 复现路径（每一步在真实使用中都能走到）：
   *   1. 打开 `/`（未登录 → 渲染登录页）
   *   2. 点页脚「注册」→ 客户端路由到 `/register`
   *   3. 点页脚「登录」→ 客户端路由到 `/login`
   *   4. 输入**正确**凭据并提交
   *   5. 曾经：登录成功、令牌落盘、进入已登录外壳 —— 但 `main` 里是 **404 页面**
   *
   * 这条用例断言的是**修好之后期望的行为**，不是那个缺陷本身（断言"有 404"
   * 等于把缺陷固化下来）。它必须留在浏览器里：`Login.tsx` 的 `navigate('/')`
   * 与 `App.tsx` 已登录分支的 `/login` 兜底，任何一半被删掉，
   * jsdom 里都可能照样通过 —— 而真实用户会再次看到 404。
   *
   * 为什么还要断言地址栏：跳转与路由兜底是两种修法，只断言"看得到仪表盘"
   * 的话，半吊子实现（例如把 `/login` 也渲染成 Dashboard）也能过。
   * 地址栏真的变成 `/` 才是用户看到的样子。
   */
  test('从 /login 登录成功：跳到 /（仪表盘），而不是停在 404 页', async ({ page }) => {
    const errors = collectPageErrors(page)
    await stubApi(page, 'ok')
    await blockThirdParty(page)

    await page.goto('/login', { waitUntil: 'domcontentloaded' })
    await page.locator('#email').fill('e2e@example.com')
    await page.locator('#password').fill('secret123')
    await page.getByRole('button', { name: '登录' }).click()

    // 1. 地址栏不再停在 /login
    await expect(page).toHaveURL(/\/$/)

    // 2. 仪表盘真的渲染了（不是 `path="*"` 的 404）
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '最近笔记' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '404' })).toHaveCount(0)

    // 3. 令牌照旧成对落盘（跳转不能把登录结果弄丢）
    expect(await readTokens(page)).toEqual({
      access: 'e2e-access-token',
      refresh: 'e2e-refresh-token',
    })

    expect(errors).toEqual([])
  })

  /**
   * 已登录时认证入口必须"无害"（与上一条是同一个洞的两个入口）
   *
   * 令牌在 localStorage 里长期有效，用户完全可能带着已登录状态回到 `/login`
   * （历史记录、书签、注册成功后回退）。这条验的是 `App.tsx` 已登录分支里的
   * `Navigate` 兜底，而不是登录动作本身 —— 所以走完一次真实登录后，
   * 直接把路径敲进地址栏，且每条都重新 `goto`（要的不是客户端跳转，
   * 而是"带着已登录令牌直接请求这个地址"）。
   */
  test('已登录后直接访问 /login 与 /register：重定向到 /，不出现 404', async ({ page }) => {
    const errors = collectPageErrors(page)
    await stubApi(page, 'ok')
    await blockThirdParty(page)

    // 先真的登录一次让令牌落盘（不往 localStorage 直接塞令牌 —— 那会绕过
    // AuthContext 的初始化，测不到"重新打开这个地址时仍是已登录"这个真实情形）
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.locator('#email').fill('e2e@example.com')
    await page.locator('#password').fill('secret123')
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()

    for (const path of ['/login', '/register']) {
      await page.goto(path, { waitUntil: 'domcontentloaded' })

      await expect(page, `${path} 已登录时应重定向到 /`).toHaveURL(/\/$/)
      await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
      await expect(page.getByRole('heading', { name: '404' })).toHaveCount(0)
    }

    expect(errors).toEqual([])
  })
})

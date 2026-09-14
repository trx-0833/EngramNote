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
 * ## 起点为什么是 `/` 而不是 `/login`
 *
 * 见文件末尾那条 `test.fixme` —— 从 `/login` 登录会落到 404，
 * 那是本轮 E2E 发现的**产品缺陷**（已记入 `frontend/docs/e2e.md`，
 * 未修改产品代码）。
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
   * ⚠️ 已知产品缺陷（本轮 E2E 发现，**未修改产品代码**）
   *
   * 复现路径（每一步在真实使用中都能走到）：
   *   1. 打开 `/`（未登录 → 渲染登录页）
   *   2. 点页脚「注册」→ 客户端路由到 `/register`
   *   3. 点页脚「登录」→ 客户端路由到 `/login`
   *   4. 输入**正确**凭据并提交
   *   5. 登录成功、令牌落盘、进入已登录外壳 —— 但 `main` 里是 **404 页面**
   *
   * 原因：`App.tsx` 的已登录路由表里**没有 `/login`**（它只在未登录分支注册），
   * 而登录成功后没有任何"跳到 `/`"的动作，`location.pathname` 仍是 `/login`，
   * 于是落到 `path="*"` 的 `NotFound`。用户必须手动把地址改回 `/`。
   *
   * 为什么标注成 `fixme` 而不是悄悄删掉：
   * - 它不是"环境跑不起来"，是一条**可执行复现**，删掉就等于丢失证据；
   * - 它也不该被写成一个"断言 404 存在"的用例 —— 那等于把缺陷固化下来；
   * - `fixme` 会在每次运行的汇总里以 "skipped" 出现（不是静默通过）。
   * 修好产品代码后把 `test.fixme` 改回 `test` 即可，用例体断言的正是**期望行为**。
   */
  test.fixme('已知缺陷：从 /login 登录成功后会落到 404（应有跳转或路由兜底）', async ({ page }) => {
    await stubApi(page, 'ok')
    await blockThirdParty(page)

    await page.goto('/login', { waitUntil: 'domcontentloaded' })
    await page.locator('#email').fill('e2e@example.com')
    await page.locator('#password').fill('secret123')
    await page.getByRole('button', { name: '登录' }).click()

    // 期望：登录成功后应当看到仪表盘
    await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
  })
})

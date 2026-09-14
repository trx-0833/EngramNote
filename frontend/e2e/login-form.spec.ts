/**
 * E2E：登录表单的原生约束校验（overhaul-plan 5.13）
 *
 * ## 为什么这条只能在这里测
 *
 * `Login.tsx` 的两个输入框是 `type="email" required` / `required`，
 * 拦截空表单和非法邮箱的是**浏览器自己的约束校验**（Constraint Validation API）。
 * jsdom **不实现**这一层：`matches(':invalid')` 恒为 false、
 * `validationMessage` 恒为空串、`checkValidity()` 恒为 true。
 * 也就是说，在 Vitest 里写这条断言，无论产品代码对不对都会"通过" ——
 * 那是一条永远绿、什么都不守护的用例，比没有更糟。
 *
 * ## 这条用例守护的真实故障
 *
 * 有人为了让"点登录时给出中文提示"而给 `<form>` 加上 `noValidate`，
 * 或者在提交按钮上写 `onClick` 而不走 `onSubmit` —— 两者都会让
 * 空邮箱/空密码的请求真的发到后端。这里用"是否发出 `/api` 请求"
 * 把这件事钉死：**校验没通过时，一个请求都不该发出去**。
 */
import { expect, test, type Page } from '@playwright/test'

import { blockThirdParty, isApiUrl } from './support'

/**
 * 记录页面发出的所有后端请求（用于断言"没提交"）。
 *
 * 判据是**路径以 `/api/` 开头**，不是"URL 里含有 /api/"——
 * Vite 开发服务器从源码路径提供模块，`/src/api/client.ts` 这类模块地址
 * 同样含有 `/api/`，用子串判断会把 12 条模块请求误记成 API 请求
 * （本文件第一版就是这么错的，实测把 `expect([])` 打成了 12 条）。
 */
function trackApiRequests(page: Page): string[] {
  const requests: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (isApiUrl(url)) requests.push(url.pathname)
  })
  return requests
}

test.describe('登录表单的原生校验', () => {
  test('空表单提交被浏览器拦下：输入框进入 :invalid，且不发出任何 /api 请求', async ({ page }) => {
    const apiRequests = trackApiRequests(page)
    await blockThirdParty(page)
    await page.goto('/login', { waitUntil: 'domcontentloaded' })

    await page.getByRole('button', { name: '登录' }).click()

    // 原生校验失败：两个 required 输入框都处于 :invalid
    await expect(page.locator('#email')).toHaveJSProperty('validity.valid', false)
    await expect(page.locator('#password')).toHaveJSProperty('validity.valid', false)

    const message = await page
      .locator('#email')
      .evaluate((el) => (el as HTMLInputElement).validationMessage)
    expect(message.length, '浏览器应给出非空的校验提示文案').toBeGreaterThan(0)

    // 产品自己的错误提示（role=alert）**不该**出现：这一步根本没到发请求
    await expect(page.getByRole('alert')).toHaveCount(0)

    // 核心断言：校验没过，就没有请求
    expect(apiRequests).toEqual([])
  })

  test('邮箱格式非法被拦下（type=email 生效），密码非空仍不放行', async ({ page }) => {
    const apiRequests = trackApiRequests(page)
    await blockThirdParty(page)
    await page.goto('/login', { waitUntil: 'domcontentloaded' })

    await page.locator('#email').fill('not-an-email')
    await page.locator('#password').fill('secret123')
    await page.getByRole('button', { name: '登录' }).click()

    // type=email 的格式校验：`not-an-email` 没有 @，必然非法
    await expect(page.locator('#email')).toHaveJSProperty('validity.typeMismatch', true)
    expect(await page.locator('#email').evaluate((el) => el.matches(':invalid'))).toBe(true)

    // 密码本身合法（只判了 required），所以"没有提交"这件事只能是邮箱拦下来的
    await expect(page.locator('#password')).toHaveJSProperty('validity.valid', true)

    expect(apiRequests).toEqual([])
  })
})

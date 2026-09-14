/**
 * E2E：应用外壳与样式是否真的在浏览器里生效（overhaul-plan 5.13）
 *
 * ## 这两条用例存在的理由
 *
 * Vitest 那一层是拿 `render(<App />)` 在 jsdom 里跑的：它**不加载 index.html**、
 * **不做布局**、**不解析 CSS**。所以下面这些失败形态它一条都发现不了 ——
 * 而它们在本项目里全都真实发生过：
 *
 *   - `index.html` 的 `<script type="module" src="/src/main.tsx">` 路径写错
 *     → 页面是空白的，而所有 jsdom 用例照常全绿；
 *   - `main.tsx` 的样式表 import 顺序被改动 → CSS 级联反转，
 *     按钮肉眼变形，而"样式文件都还在、规则文本一个没少"（overhaul-plan 5.6 的实测坑）；
 *   - CSS Module 的类名哈希后没进产物 → 元素拿不到任何样式，文本差集看不出来。
 *
 * 这两条用例**只**证明"外壳能起来、样式真的作用到了元素上"。
 * 业务逻辑、接口契约、交互流程都不在这里 —— 那些是 Vitest 与后端测试的职责。
 */
import { expect, test } from '@playwright/test'

import { blockThirdParty, collectPageErrors } from './support'

test.describe('应用外壳（真浏览器）', () => {
  test('首屏：HTML 外壳返回 200、React 挂载成功、无未捕获异常', async ({ page }) => {
    const errors = collectPageErrors(page)
    await blockThirdParty(page)

    const response = await page.goto('/', { waitUntil: 'domcontentloaded' })

    // 1. 开发服务器真的把 index.html 发出来了
    expect(response?.status()).toBe(200)
    expect(await page.title()).toBe('EngramNote - AI 学习笔记管理')

    // 2. React 真的挂载了。判据刻意选"只有 React 才渲染得出来"的元素：
    //    h1「登录 EngramNote」住在 Login.tsx 里，静态 HTML 里不存在。
    //    只断言 `#root` 非空是不够的 —— 一个报错的 React 也会往里面塞东西。
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible()

    // 3. 整个加载过程没有未捕获异常
    expect(errors).toEqual([])
  })

  test('样式真的生效：全局令牌层 + CSS Module 层都作用到了元素上', async ({ page }) => {
    await blockThirdParty(page)
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible()

    // ── 全局层（src/styles/base.css 的 :root 令牌）──
    const primaryToken = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim(),
    )
    expect(primaryToken).toBe('#0f3460')

    // ── CSS Module 层（src/pages/Auth.module.css）──
    // 判据用**计算后的布局值**而不是类名：模块类名会被哈希，
    // 拼类名等于把构建工具的命名规则钉进测试；而 `min-height: 100vh`
    // 算出来必须等于视口高度 —— 这是只有真浏览器（有布局引擎）才给得出的答案，
    // jsdom 的 getComputedStyle 会原样返回 `100vh` 字符串。
    const bg = page.getByRole('heading', { name: '登录 EngramNote' }).locator('xpath=../..')
    const minHeight = await bg.evaluate((el) => getComputedStyle(el).minHeight)
    expect(minHeight).toBe('720px') // 视口高度，见 playwright.config.ts 的 viewport

    // 样式表确实被解析了（不是"文件在但没生效"）
    const ruleCount = await page.evaluate(() =>
      Array.from(document.styleSheets).reduce((total, sheet) => {
        try {
          return total + sheet.cssRules.length
        } catch {
          // 跨源样式表读不到 cssRules；本用例阻断外链后应不存在，计入 0
          return total
        }
      }, 0),
    )
    expect(ruleCount).toBeGreaterThan(50)
  })
})

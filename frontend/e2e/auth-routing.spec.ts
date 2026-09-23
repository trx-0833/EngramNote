/**
 * E2E：未登录时的路由行为（overhaul-plan 5.13）
 *
 * ## 这一条为什么必须放在浏览器里
 *
 * `App.tsx` 里未登录时是两条 `path="*"` 兜底路由在起作用（5.7 的成果）。
 * jsdom 里能测"给 MemoryRouter 一个初始路径之后渲染了什么"，
 * 但测不到**真实地址栏**这条路：浏览器输入 `/notes` 是一次真导航，
 * 会走 `BrowserRouter` 的 history 解析、Vite 的 SPA fallback（`index.html`）、
 * 再进 React Router。开发服务器没配 history fallback 时，
 * 直接访问深层路径会拿到 404 —— 那是部署级故障，jsdom 永远看不见。
 *
 * ## 刻意不覆盖
 *
 * 已登录后的深层路径刷新（例如 `/notes/xxx` 直接打开）需要真实后端与真实数据，
 * 本套件用桩 API，证明不了"刷新后仍停在原页面"这件事，所以不写。
 */
import { expect, test } from '@playwright/test';

import { blockThirdParty, collectPageErrors } from './support';

test.describe('未登录的访问路径', () => {
  test('直接访问任意深层路径都落到登录页（SPA fallback + 兜底路由）', async ({ page }) => {
    const errors = collectPageErrors(page);
    await blockThirdParty(page);

    // 这些路径都只有在**已登录**时才存在对应页面；未登录时全部应落到登录页。
    // 逐条 goto 而不是 SPA 内跳：要验的正是"浏览器直接请求这个地址会怎样"。
    for (const path of ['/', '/notes', '/graph', '/upload', '/review/cards']) {
      const response = await page.goto(path, { waitUntil: 'domcontentloaded' });

      // 关键判据：开发服务器为深层路径回的仍是 200 + index.html，
      // 而不是 404（没有 SPA fallback 时就是这个症状）
      expect(response?.status(), `${path} 应返回 200（SPA fallback）`).toBe(200);
      await expect(
        page.getByRole('heading', { name: '登录 EngramNote' }),
        `${path} 未登录时应显示登录页`,
      ).toBeVisible();
    }

    expect(errors).toEqual([]);
  });

  test('登录页 → 注册页是客户端路由，不是整页刷新', async ({ page }) => {
    await blockThirdParty(page);
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible();

    // 在 window 上留个标记：整页刷新会把它清掉，客户端路由不会。
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__e2eProbe = 'alive';
    });

    await page.getByRole('link', { name: '注册' }).click();

    await expect(page).toHaveURL(/\/register$/);
    await expect(page.getByRole('heading', { name: '注册 EngramNote' })).toBeVisible();
    expect(
      await page.evaluate(() => (window as unknown as Record<string, unknown>).__e2eProbe),
    ).toBe('alive');
  });
});

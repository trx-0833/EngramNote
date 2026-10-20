/**
 * E2E：语言切换在**真浏览器**里是否真的生效（i18n 阶段 5）
 *
 * ## 为什么这条只能在这里测
 *
 * `src/i18n/*.test.tsx` 已经覆盖了消息表形状、`t()` 的三级回落、
 * `setLocale` 的三处同步。但那 53 条都跑在 jsdom 里，而 jsdom：
 *
 * - **不加载 `index.html`** → 断言不了 `<html lang>` 真的被改了
 *   （jsdom 里 `document.documentElement.lang` 只是个普通属性，
 *   它不参与任何渲染或无障碍语义）；
 * - **不发真实网络请求** → 断言不了 `Accept-Language` 请求头真的带上了
 *   （后端按它翻译错误文案，见 `backend/app/core/i18n.py`）；
 * - **不刷新页面** → 断言不了"用户选过的语言在刷新后还在"
 *   （localStorage 写入时机错了只有刷新才看得出来）。
 *
 * 所以这一条守的是**三件只有真浏览器才成立的事**：
 * 界面文案、`<html lang>`、请求头 —— 而且要在**刷新之后**仍然成立。
 *
 * ## 为什么必须先登录
 *
 * 语言切换器住在侧边栏里（`Sidebar.tsx`），登录页没有它。
 * 所以这里用 `stubApi` 把 `/api` 全桩掉，走一遍真实登录再切语言。
 */
import { expect, test, type Page } from '@playwright/test';

import { blockThirdParty, collectPageErrors, stubApi } from './support';

/** 走一遍登录，停在已登录外壳（Dashboard） */
async function login(page: Page): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#email').fill('e2e@example.com');
  await page.locator('#password').fill('secret123');
  await page.getByRole('button', { name: '登录' }).click();
  // 侧边栏出现即视为登录完成
  await expect(page.getByRole('group', { name: '语言' })).toBeVisible();
}

/** 语言切换器里那个按钮的可访问名会随语言变，所以按 `aria-pressed` 之外的位置取 */
function langButton(page: Page, index: 0 | 1) {
  return page
    .getByRole('group')
    .filter({ has: page.locator('.segment-btn') })
    .locator('.segment-btn')
    .nth(index);
}

test.describe('语言切换（真浏览器）', () => {
  test('★ 切到英文：界面、`<html lang>`、localStorage 三处同时变', async ({ page }) => {
    const errors = collectPageErrors(page);
    await blockThirdParty(page);
    await stubApi(page, 'ok');
    await login(page);

    // 起始状态：中文
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('zh-CN');
    await expect(page.getByRole('group', { name: '语言' })).toBeVisible();

    // 切到英文（第二个按钮是 English）
    await langButton(page, 1).click();

    // ① 界面文案变了 —— 侧边栏的导航项是最稳的判据
    await expect(page.getByRole('group', { name: 'Language' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible();

    // ② `<html lang>` 变了 —— 读屏与浏览器断词规则都依赖它
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('en');

    // ③ localStorage 写进去了
    expect(await page.evaluate(() => localStorage.getItem('engramnote_locale'))).toBe('en');

    expect(errors).toEqual([]);
  });

  test('★ 刷新后语言仍然是英文（证明持久化真的生效）', async ({ page }) => {
    await blockThirdParty(page);
    await stubApi(page, 'ok');
    await login(page);

    await langButton(page, 1).click();
    await expect(page.getByRole('group', { name: 'Language' })).toBeVisible();

    // 刷新：React 树重建，语言只能来自 localStorage
    await page.reload({ waitUntil: 'domcontentloaded' });

    expect(await page.evaluate(() => document.documentElement.lang)).toBe('en');
    await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible();
  });

  test('★ 请求头带上 `Accept-Language`，且切语言后跟着变', async ({ page }) => {
    // 后端按这个头翻译错误文案（`backend/app/core/i18n.py`）。
    // 它错了不会报错，只会让"英文界面弹中文错误"—— 正是要防的形态。
    const seen: Array<{ path: string; accept: string }> = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/')) {
        seen.push({
          path: url.pathname,
          accept: request.headers()['accept-language'] ?? '',
        });
      }
    });

    await blockThirdParty(page);
    await stubApi(page, 'ok');
    await login(page);

    // 中文状态下发过的请求应当是 zh-CN
    expect(seen.some((r) => r.accept === 'zh-CN')).toBe(true);

    seen.length = 0;
    await langButton(page, 1).click();
    await expect(page.getByRole('group', { name: 'Language' })).toBeVisible();

    // 切完之后**新发出**的请求必须是 en。
    // 这里主动触发一个请求：展开/收起不产生请求，所以用一次刷新后的首屏请求。
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible();

    expect(seen.length, '刷新后应当发出过 /api 请求（否则这条断言是空转的）').toBeGreaterThan(0);
    expect(
      seen.every((r) => r.accept === 'en'),
      `刷新后所有 /api 请求都应带 Accept-Language: en，实际：${JSON.stringify(seen.slice(0, 5))}`,
    ).toBe(true);
  });

  test('切回中文同样生效（不是单向的）', async ({ page }) => {
    await blockThirdParty(page);
    await stubApi(page, 'ok');
    await login(page);

    await langButton(page, 1).click();
    await expect(page.getByRole('group', { name: 'Language' })).toBeVisible();

    await langButton(page, 0).click();
    await expect(page.getByRole('group', { name: '语言' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('zh-CN');
    expect(await page.evaluate(() => localStorage.getItem('engramnote_locale'))).toBe('zh-CN');
  });
});

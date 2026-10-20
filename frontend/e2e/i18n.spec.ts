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
 * 所以这几条守的是**三件只有真浏览器才成立的事**：
 * 界面文案、`<html lang>`、请求头 —— 而且要在**刷新之后**仍然成立。
 *
 * ## 为什么必须先登录
 *
 * 语言切换器住在侧边栏里（`Sidebar.tsx`），登录页没有它。
 * 所以这里用 `stubApi` 把 `/api` 全桩掉，走一遍真实登录再切语言。
 *
 * ## ⚠️ 请求头那条踩过的坑：`reload()` 之前清空记录数组是不够的
 *
 * 第一版在 `reload()` **之前**清空记录数组，然后断言"记录里的请求都带 en"。
 * 实测红了，报出 `zh-CN` —— 看起来像"切语言后请求头没跟着变"这个真 bug。
 *
 * 实际是**测试自己的问题**：`seen.length = 0` 之后、`reload()` 之前，
 * 首屏那批**在途**请求（Dashboard 并发 8 个）才真正被 `request` 事件捕获并记进来。
 * 于是数组里混着"切换前发出的请求"，断言自然失败。
 *
 * 定位它靠的是在页面里埋探针（包装 `window.fetch` 读模块级状态），
 * 证据是每个 `fetch:/api/*` 记录的 `current` **都是 `en`**，
 * 而 `localStorage` / `<html lang>` / 模块状态在刷新后也全是 `en` ——
 * 也就是**应用是对的，断言捕错了请求**。
 *
 * 修法：**在 `reload()` 之后**才清空数组 —— 这样"清空之后记到的"必然是新页面的请求。
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

/** 语言切换器里的两个按钮：0 = 中文，1 = English */
function langButton(page: Page, index: 0 | 1) {
  return page.locator('.segment-btn').nth(index);
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
    let seen: Array<{ path: string; accept: string }> = [];
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

    // 中文状态下发过的请求应当是 zh-CN（否则这条用例的前提就不成立）
    expect(
      seen.some((r) => r.accept === 'zh-CN'),
      `首屏应当有带 zh-CN 的 /api 请求，实际：${JSON.stringify(seen.slice(0, 5))}`,
    ).toBe(true);

    await langButton(page, 1).click();
    await expect(page.getByRole('group', { name: 'Language' })).toBeVisible();

    // 刷新，让"新页面发出的每一个请求"都可判定
    await page.reload({ waitUntil: 'domcontentloaded' });

    // ⚠️ 在 `reload()` **之后**才清空：见文件头那个坑的说明。
    // 清空之后再记到的，必然是刷新后新页面的请求。
    seen = [];
    await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible();

    // 先确认持久化三处都是 en —— 若这里就挂了，问题在初始化；
    // 若这里过了而请求头不对，问题才在请求层。两种成因的修法完全不同。
    const persisted = await page.evaluate(() => ({
      stored: localStorage.getItem('engramnote_locale'),
      htmlLang: document.documentElement.lang,
    }));
    expect(persisted, `刷新后持久化状态：${JSON.stringify(persisted)}`).toEqual({
      stored: 'en',
      htmlLang: 'en',
    });

    // 等 Dashboard 那批并发请求发出来（`reload` 之后才清空，所以这里必有新请求）
    await expect
      .poll(() => seen.length, {
        message: '刷新后应当发出过 /api 请求（否则这条断言是空转的）',
      })
      .toBeGreaterThan(0);

    expect(
      seen.every((r) => r.accept === 'en'),
      `刷新后所有 /api 请求都应带 Accept-Language: en，实际：${JSON.stringify(seen.slice(0, 6))}`,
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
